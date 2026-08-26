"""Что именно уходит на биржу.

Самый ценный тест проекта: он ловит неверные параметры ордера до того,
как они станут деньгами. Настоящая сеть не нужна — подставляем заглушку
вместо HTTP-клиента и смотрим на итоговый запрос.
"""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.exchange import Exchange
from bot.models import Action, Instrument, Position

INST = Instrument("ETHUSDT", tick_size=Decimal("0.01"), qty_step=Decimal("0.01"),
                  min_qty=Decimal("0.01"), min_notional=Decimal("5"))


class FakeHTTP:
    """Заглушка биржи: запоминает запросы, отвечает успехом."""

    def __init__(self, position=None):
        self.orders: list[dict] = []
        self.cancelled = 0
        self._position = position or {"size": "0", "side": "", "avgPrice": "0",
                                      "unrealisedPnl": "0", "markPrice": "2463",
                                      "positionIdx": 0}

    def place_order(self, **kw):
        self.orders.append(kw)
        return {"retCode": 0, "retMsg": "OK", "result": {"orderId": f"id{len(self.orders)}"}}

    def cancel_all_orders(self, **kw):
        self.cancelled += 1
        return {"retCode": 0, "result": {}}

    def get_positions(self, **kw):
        return {"retCode": 0, "result": {"list": [self._position]}}

    def get_tickers(self, **kw):
        return {"retCode": 0, "result": {"list": [
            {"bid1Price": "2463.28", "ask1Price": "2463.30", "lastPrice": "2463.29"}]}}


def make(position=None) -> tuple[Exchange, FakeHTTP]:
    ex = Exchange.__new__(Exchange)
    ex.symbol, ex.category, ex.testnet, ex.dry_run = "ETHUSDT", "linear", True, False
    ex._instrument = INST
    http = FakeHTTP(position)
    ex.http = http
    return ex, http


# ------------------------------------------------------------- рыночный вход
def test_market_order_params():
    ex, http = make()
    ex.execute(Action(kind="market", side="Buy", qty=Decimal("0.02"),
                      take_profit=Decimal("2478.253"), stop_loss=Decimal("2453.117")))
    o = http.orders[0]
    assert o["orderType"] == "Market"
    assert o["side"] == "Buy"
    assert o["qty"] == "0.02"
    assert "price" not in o, "у рыночного ордера не должно быть цены"
    assert "timeInForce" not in o
    assert o["reduceOnly"] is False
    # TP/SL обязаны лежать на сетке тиков, иначе биржа отклонит ордер
    assert o["takeProfit"] == "2478.25"
    assert o["stopLoss"] == "2453.12"
    assert o["orderLinkId"].startswith("bot-")


def test_limit_post_only_params():
    ex, http = make()
    ex.execute(Action(kind="limit", side="Buy", qty=Decimal("0.02"),
                      price=Decimal("2463.284"), post_only=True))
    o = http.orders[0]
    assert o["orderType"] == "Limit"
    assert o["timeInForce"] == "PostOnly", "без PostOnly вход платит комиссию тейкера"
    assert o["price"] == "2463.28"


def test_limit_without_post_only_uses_gtc():
    ex, http = make()
    ex.execute(Action(kind="limit", side="Sell", qty=Decimal("0.02"),
                      price=Decimal("2463.30"), post_only=False))
    assert http.orders[0]["timeInForce"] == "GTC"


# ------------------------------------------------------------- округления
def test_qty_rounded_down_never_up():
    """Округление вверх = ордер больше, чем позволяет депозит."""
    ex, http = make()
    ex.execute(Action(kind="market", side="Buy", qty=Decimal("0.02999")))
    assert http.orders[0]["qty"] == "0.02"


def test_order_below_min_qty_is_not_sent():
    ex, http = make()
    ex.execute(Action(kind="market", side="Buy", qty=Decimal("0.004")))
    assert http.orders == [], "ордер ниже минимального лота не должен уходить"


def test_order_below_min_notional_is_not_sent():
    ex, http = make()
    ex._instrument = Instrument("ETHUSDT", Decimal("0.01"), Decimal("0.01"),
                                Decimal("0.01"), min_notional=Decimal("100"))
    ex.execute(Action(kind="market", side="Buy", qty=Decimal("0.02")))   # ~49 USDT
    assert http.orders == [], "ордер ниже минимальной суммы не должен уходить"


# ------------------------------------------------------------- выход из позиции
def test_close_sends_opposite_side_reduce_only():
    ex, http = make({"size": "0.02", "side": "Buy", "avgPrice": "2478",
                     "unrealisedPnl": "-0.3", "markPrice": "2463", "positionIdx": 0})
    ex.execute(Action(kind="close", reason="разворот"))
    o = http.orders[0]
    assert o["side"] == "Sell", "закрытие лонга — это продажа"
    assert o["reduceOnly"] is True
    assert o["qty"] == "0.02"
    assert o["orderType"] == "Market"


def test_dust_position_can_still_be_closed():
    """Остаток меньше минимального лота обязан закрываться, иначе выйти нельзя."""
    ex, http = make({"size": "0.005", "side": "Buy", "avgPrice": "2478",
                     "unrealisedPnl": "0", "markPrice": "2463", "positionIdx": 0})
    ex.execute(Action(kind="close", reason="закрытие остатка"))
    assert len(http.orders) == 1, "закрытие остатка не должно блокироваться фильтрами входа"
    assert http.orders[0]["reduceOnly"] is True
    assert Decimal(http.orders[0]["qty"]) > 0, "количество не должно округлиться в ноль"


def test_close_of_flat_position_sends_nothing():
    ex, http = make()
    ex.execute(Action(kind="close", reason="нечего закрывать"))
    assert http.orders == []


# ------------------------------------------------------------- прочее
def test_cancel_all_goes_through():
    ex, http = make()
    ex.execute(Action(kind="cancel_all", reason="перестановка"))
    assert http.cancelled == 1


def test_each_order_gets_unique_link_id():
    """Одинаковый orderLinkId у разных ордеров = второй будет отвергнут как дубль."""
    ex, http = make()
    for _ in range(5):
        ex.execute(Action(kind="market", side="Buy", qty=Decimal("0.02")))
    ids = [o["orderLinkId"] for o in http.orders]
    assert len(set(ids)) == 5
    assert all(len(i) <= 36 for i in ids), "orderLinkId длиннее 36 символов Bybit не примет"


def test_dry_run_sends_nothing():
    ex, http = make()
    ex.dry_run = True
    ex.execute(Action(kind="market", side="Buy", qty=Decimal("0.02")))
    assert http.orders == [], "в режиме dry_run ордера не должны уходить на биржу"


def test_post_only_rejection_is_not_an_error():
    """PostOnly, ставший бы тейкером, биржа отклоняет — это нормальный исход."""
    ex, http = make()

    def reject(**kw):
        raise RuntimeError("Order would immediately match and take (retCode=30208)")

    http.place_order = reject
    ex.execute(Action(kind="limit", side="Buy", qty=Decimal("0.02"),
                      price=Decimal("2463.28"), post_only=True))   # не должно бросить

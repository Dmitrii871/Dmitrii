"""Тесты мультисимвольного режима."""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.models import Account, Action, Position, RiskReject
from bot.paper import PaperTrader
from bot.risk import RiskManager
from bot.worker import SymbolWorker, aggregate_summary

ACCOUNT = Account(Decimal("500"), Decimal("500"))
MID = Decimal("2463")


def rm(**over):
    cfg = {"max_position_usdt": 30, "max_daily_loss_usdt": 5.0,
           "min_free_margin_ratio": 0.25, "max_orders_per_hour": 500,
           "kill_switch_file": "./__no_stop__"}
    cfg.update(over)
    return RiskManager(cfg)


FLAT = Position("ETHUSDT", "", Decimal(0), Decimal(0), Decimal(0), Decimal(0))


def test_limit_applies_to_whole_account_not_per_symbol():
    """Десять символов по лимиту дали бы десятикратный риск — этого быть не должно."""
    action = Action(kind="market", side="Buy", qty=Decimal("0.01"), price=MID)  # ~25 USDT
    r = rm(max_position_usdt=30)
    r.validate(action, FLAT, ACCOUNT, MID, other_exposure=Decimal(0))   # проходит
    try:
        r.validate(action, FLAT, ACCOUNT, MID, other_exposure=Decimal(20))
    except RiskReject as exc:
        assert "другим символам" in str(exc)
    else:
        raise AssertionError("экспозиция по другим символам обязана учитываться")


def test_other_exposure_defaults_to_zero():
    """Одиночный режим не должен ломаться от нового параметра."""
    rm().validate(Action(kind="market", side="Buy", qty=Decimal("0.01"), price=MID),
                  FLAT, ACCOUNT, MID)


def _worker(sym, trades):
    pt = PaperTrader()
    for net in trades:
        pt.trades.append({"side": "Buy", "entry": 1.0, "exit": 1.0, "size": 1.0,
                          "gross": net, "fees": 0.05, "net": net,
                          "reason": "тест", "opened_at": ""})
        pt.realized += Decimal(str(net))
    return SymbolWorker(symbol=sym, exchange=None, strategy=None,
                        interval="60", warmup=60, paper=pt)


def test_aggregate_sums_across_symbols():
    ws = [_worker("ETHUSDT", [1.0, -0.5]), _worker("BTCUSDT", [2.0])]
    s = aggregate_summary(ws)
    assert s["trades"] == 3
    assert abs(s["net_usdt"] - 2.5) < 1e-9
    assert s["per_symbol"][0][0] == "BTCUSDT", "сортировка по результату"


def test_aggregate_handles_symbols_without_trades():
    s = aggregate_summary([_worker("ETHUSDT", []), _worker("BTCUSDT", [1.0])])
    assert s["trades"] == 1
    assert len(s["per_symbol"]) == 1, "символы без сделок в разбивку не попадают"


def test_aggregate_on_empty_list():
    assert aggregate_summary([])["trades"] == 0


def test_worker_reports_paper_position_when_keyless():
    """Без ключей позиция берётся из симуляции, а не с биржи."""
    class FakeEx:
        public_only = True

    pt = PaperTrader()
    w = SymbolWorker("ETHUSDT", FakeEx(), None, "60", 60, paper=pt)
    assert w.position().is_flat
    pt.on_action(Action(kind="market", side="Buy", qty=Decimal("0.02"),
                        take_profit=Decimal("2500"), stop_loss=Decimal("2400")),
                 Decimal("2463"))
    pos = w.position()
    assert pos.side == "Buy" and pos.size == Decimal("0.02")

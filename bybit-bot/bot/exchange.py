"""Обёртка над Bybit v5 (pybit.unified_trading).

Отвечает за три вещи, на которых чаще всего ломаются самописные боты:
округление до tick_size / qty_step, повторы при сетевых сбоях
и режим dry_run, в котором ордера логируются, но не отправляются.
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any

from .models import Account, Action, Instrument, MarketData, Position

log = logging.getLogger(__name__)

RETRYABLE = (
    "timeout", "timed out", "connection", "temporarily", "too many visits",
    "system busy", "service unavailable", "502", "503", "504",
)


def _d(value: Any, default: str = "0") -> Decimal:
    """Bybit отдаёт числа строками, пустая строка = отсутствие значения."""
    if value in (None, "", "null"):
        return Decimal(default)
    return Decimal(str(value))


def quantize(value: Decimal, step: Decimal, mode=ROUND_DOWN) -> Decimal:
    """Приводит значение к сетке биржи. Цены — HALF_UP, количества — DOWN."""
    if step <= 0:
        return value
    return (value / step).quantize(Decimal(1), rounding=mode) * step


class Exchange:
    def __init__(self, cfg: dict, api_key: str, api_secret: str, dry_run: bool):
        self.symbol: str = cfg["symbol"]
        self.category: str = cfg.get("category", "linear")
        self.testnet: bool = bool(cfg.get("testnet", True))
        self.dry_run = dry_run
        self._instrument: Instrument | None = None

        from pybit.unified_trading import HTTP  # импорт здесь: тесты не требуют pybit

        self.http = HTTP(
            testnet=self.testnet,
            api_key=api_key,
            api_secret=api_secret,
            recv_window=10_000,
        )
        log.info(
            "Подключение к Bybit %s | символ %s | dry_run=%s",
            "TESTNET" if self.testnet else "MAINNET", self.symbol, dry_run,
        )

    # ---------------------------------------------------------------- вызовы
    def _call(self, method: str, **kwargs) -> dict:
        """Вызов API с экспоненциальным ретраем на временных ошибках."""
        last_err: Exception | None = None
        for attempt in range(4):
            try:
                resp = getattr(self.http, method)(**kwargs)
                if resp.get("retCode") != 0:
                    raise RuntimeError(f"{method}: {resp.get('retMsg')} (retCode={resp.get('retCode')})")
                return resp.get("result", {})
            except Exception as exc:  # noqa: BLE001 — классифицируем по тексту
                last_err = exc
                if not any(m in str(exc).lower() for m in RETRYABLE) or attempt == 3:
                    raise
                delay = 2 ** attempt
                log.warning("%s: %s — повтор через %ss", method, exc, delay)
                time.sleep(delay)
        raise RuntimeError(f"{method} не удался") from last_err

    # ------------------------------------------------------------ справочник
    def instrument(self) -> Instrument:
        if self._instrument is not None:
            return self._instrument
        res = self._call("get_instruments_info", category=self.category, symbol=self.symbol)
        item = res["list"][0]
        lot, price = item["lotSizeFilter"], item["priceFilter"]
        self._instrument = Instrument(
            symbol=self.symbol,
            tick_size=_d(price["tickSize"]),
            qty_step=_d(lot["qtyStep"]),
            min_qty=_d(lot["minOrderQty"]),
            min_notional=_d(lot.get("minNotionalValue"), "5"),
        )
        log.info(
            "Фильтры %s: tick=%s, шаг=%s, мин.кол-во=%s, мин.нотионал=%s USDT",
            self.symbol, self._instrument.tick_size, self._instrument.qty_step,
            self._instrument.min_qty, self._instrument.min_notional,
        )
        return self._instrument

    def check_position_mode(self) -> str:
        """Бот рассчитан на односторонний режим (One-Way).

        В хедж-режиме Bybit требует positionIdx у каждого ордера, иначе
        заявки отклоняются. Молча торговать в таком режиме нельзя, поэтому
        несовпадение — это ошибка запуска, а не предупреждение.
        """
        items = self._call("get_positions", category=self.category, symbol=self.symbol).get("list", [])
        idx = {int(p.get("positionIdx", 0)) for p in items}
        if idx - {0}:
            raise RuntimeError(
                f"Символ {self.symbol} в хедж-режиме (positionIdx={sorted(idx)}). "
                "Бот работает в одностороннем режиме: переключите позиционный режим "
                "на Bybit в 'Односторонний' (One-Way) для этого символа."
            )
        log.info("Режим позиций: односторонний (лонг и шорт по очереди, не одновременно)")
        return "one-way"

    def set_leverage(self, leverage: int) -> None:
        try:
            self._call(
                "set_leverage", category=self.category, symbol=self.symbol,
                buyLeverage=str(leverage), sellLeverage=str(leverage),
            )
            log.info("Плечо установлено: %sx", leverage)
        except Exception as exc:  # noqa: BLE001
            # "leverage not modified" — не ошибка, значение уже такое
            if "not modified" in str(exc).lower():
                log.info("Плечо уже %sx", leverage)
            else:
                raise

    # ------------------------------------------------------------- состояние
    def market_data(self, interval: str, bars: int = 200) -> MarketData:
        tick = self._call("get_tickers", category=self.category, symbol=self.symbol)["list"][0]
        kl = self._call(
            "get_kline", category=self.category, symbol=self.symbol,
            interval=interval, limit=min(bars, 1000),
        )["list"]
        # Bybit отдаёт свечи от новых к старым; последняя ещё не закрыта — отбрасываем
        rows = list(reversed(kl))[:-1]
        return MarketData(
            symbol=self.symbol,
            bid=_d(tick["bid1Price"]),
            ask=_d(tick["ask1Price"]),
            last=_d(tick["lastPrice"]),
            closes=[float(r[4]) for r in rows],
            bar_time=int(rows[-1][0]) if rows else 0,
        )

    def position(self) -> Position:
        res = self._call("get_positions", category=self.category, symbol=self.symbol)
        items = res.get("list", [])
        if not items:
            return Position(self.symbol, "", Decimal(0), Decimal(0), Decimal(0), Decimal(0))
        p = items[0]
        size = _d(p.get("size"))
        return Position(
            symbol=self.symbol,
            side=p.get("side", "") if size > 0 else "",
            size=size,
            entry_price=_d(p.get("avgPrice")),
            unrealised_pnl=_d(p.get("unrealisedPnl")),
            mark_price=_d(p.get("markPrice")),
        )

    def account(self) -> Account:
        res = self._call("get_wallet_balance", accountType="UNIFIED")
        acc = res["list"][0]
        return Account(
            equity=_d(acc.get("totalEquity")),
            available=_d(acc.get("totalAvailableBalance")),
        )

    def open_orders(self) -> list[dict]:
        return self._call("get_open_orders", category=self.category, symbol=self.symbol).get("list", [])

    # --------------------------------------------------------------- ордера
    def execute(self, action: Action) -> None:
        if action.kind == "cancel_all":
            self._cancel_all(action)
        elif action.kind == "close":
            self._close(action)
        else:
            self._place(action)

    def _cancel_all(self, action: Action) -> None:
        if self.dry_run:
            log.info("[DRY] снять все ордера — %s", action.reason)
            return
        self._call("cancel_all_orders", category=self.category, symbol=self.symbol)
        log.info("Все ордера сняты — %s", action.reason)

    def _close(self, action: Action) -> None:
        pos = self.position()
        if pos.is_flat:
            return
        side = "Sell" if pos.side == "Buy" else "Buy"
        self._place(Action(
            kind="market", side=side, qty=pos.size,
            reduce_only=True, reason=action.reason or "закрытие позиции",
        ))

    def _place(self, action: Action) -> None:
        inst = self.instrument()
        qty = quantize(action.qty or Decimal(0), inst.qty_step, ROUND_DOWN)
        if qty < inst.min_qty:
            log.warning(
                "Ордер пропущен: количество %s меньше минимума %s", qty, inst.min_qty
            )
            return

        params: dict[str, Any] = {
            "category": self.category,
            "symbol": self.symbol,
            "side": action.side,
            "orderType": "Limit" if action.kind == "limit" else "Market",
            "qty": str(qty),
            "reduceOnly": action.reduce_only,
        }
        if action.kind == "limit":
            price = quantize(action.price, inst.tick_size, ROUND_HALF_UP)
            params["price"] = str(price)
            params["timeInForce"] = "PostOnly" if action.post_only else "GTC"
        if action.take_profit is not None:
            params["takeProfit"] = str(quantize(action.take_profit, inst.tick_size, ROUND_HALF_UP))
        if action.stop_loss is not None:
            params["stopLoss"] = str(quantize(action.stop_loss, inst.tick_size, ROUND_HALF_UP))

        if self.dry_run:
            log.info("[DRY] %s | %s", action.describe(), action.reason)
            return

        try:
            res = self._call("place_order", **params)
            log.info("Ордер %s: %s | %s", res.get("orderId", "?"), action.describe(), action.reason)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            # PostOnly, который сразу стал бы тейкером, биржа отклоняет — это норма
            if "post only" in msg or "would immediately match" in msg:
                log.debug("PostOnly отклонён (стал бы тейкером): %s", action.describe())
                return
            raise

"""Вход по совпадению RSI + MACD + Bollinger %B.

Логика намеренно простая и проверяемая: каждый индикатор голосует
"лонг" / "шорт" / "воздержался", вход только при согласии min_confluence штук.
Выход — по take-profit / stop-loss, выставленным на самой бирже,
чтобы позиция была защищена даже если бот упадёт.
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal

from ..indicators import bollinger_pct_b, macd, rsi
from ..models import Action
from .base import Context, Strategy

log = logging.getLogger(__name__)


class SignalStrategy(Strategy):
    name = "signal"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.rsi_period = int(cfg.get("rsi_period", 14))
        self.rsi_buy = float(cfg.get("rsi_buy", 35.0))
        self.rsi_sell = float(cfg.get("rsi_sell", 65.0))
        self.macd_params = tuple(cfg.get("macd", [12, 26, 9]))
        self.bb_period = int(cfg.get("bb_period", 20))
        self.bb_mult = float(cfg.get("bb_mult", 2.0))
        self.bb_buy = float(cfg.get("bb_buy", 0.15))
        self.bb_sell = float(cfg.get("bb_sell", 0.85))
        self.min_confluence = int(cfg.get("min_confluence", 2))
        self.notional = Decimal(str(cfg.get("order_notional_usdt", 25)))
        self.tp_pct = Decimal(str(cfg.get("take_profit_pct", 1.2))) / 100
        self.sl_pct = Decimal(str(cfg.get("stop_loss_pct", 0.8))) / 100
        self.cooldown_bars = int(cfg.get("cooldown_bars", 2))
        # market  -> вход рыночным ордером, комиссия тейкера
        # post_only -> вход лимиткой у ближней цены, комиссия мейкера
        self.entry_type = str(cfg.get("entry_type", "market")).lower()
        # both -> торгуем в обе стороны; long_only / short_only -> одна сторона.
        # Односторонний режим осмыслен на выраженном тренде или когда
        # фандинг устойчиво платит в одну сторону.
        self.direction = str(cfg.get("direction", "both")).lower()
        self.entry_ttl_seconds = int(cfg.get("entry_ttl_seconds", 90))
        self._last_entry_bar = -10**9   # кулдаун не должен блокировать первый вход
        self._bars_seen = 0
        self._prev_bar_time = 0
        self._pending_since: float | None = None

    def warmup_bars(self) -> int:
        return max(self.macd_params[1] + self.macd_params[2], self.bb_period, self.rsi_period) + 30

    def validate(self, fees: dict) -> None:
        if self.entry_type not in ("market", "post_only"):
            raise ValueError("entry_type должен быть 'market' или 'post_only'")
        if self.direction not in ("both", "long_only", "short_only"):
            raise ValueError("direction должен быть 'both', 'long_only' или 'short_only'")
        taker = float(fees.get("taker_bps", 5.5))
        maker = float(fees.get("maker_bps", 2.0))
        # вход мейкером + выход по TP/SL тейкером; при market обе стороны тейкер
        entry_fee = maker if self.entry_type == "post_only" else taker
        round_trip_bps = entry_fee + taker
        tp_bps = float(self.tp_pct) * 10_000
        if tp_bps <= round_trip_bps:
            raise ValueError(
                f"take_profit_pct={float(self.tp_pct)*100:.2f}% даёт {tp_bps:.1f} bp, "
                f"а круг при входе '{self.entry_type}' стоит {round_trip_bps:.1f} bp. "
                f"Поставьте тейк-профит минимум {round_trip_bps*3/100:.3f}%."
            )
        if tp_bps < round_trip_bps * 3:
            log.warning(
                "Тейк-профит %.3f%% всего в %.1f раза больше комиссии (%.1f bp). "
                "Запас на проскальзывание почти отсутствует.",
                float(self.tp_pct) * 100, tp_bps / round_trip_bps, round_trip_bps,
            )
        if self.sl_pct <= 0 or self.tp_pct <= 0:
            raise ValueError("take_profit_pct и stop_loss_pct должны быть > 0")

    # ------------------------------------------------------------- голосование
    def votes(self, closes: list[float]) -> tuple[int, int, dict]:
        """Возвращает (голосов в лонг, голосов в шорт, значения индикаторов)."""
        longs = shorts = 0
        snapshot: dict = {}

        r = rsi(closes, self.rsi_period)
        if r:
            snapshot["rsi"] = round(r[-1], 2)
            if r[-1] <= self.rsi_buy:
                longs += 1
            elif r[-1] >= self.rsi_sell:
                shorts += 1

        line, sig, hist = macd(closes, *self.macd_params)
        if len(hist) >= 2:
            snapshot["macd_hist"] = round(hist[-1], 4)
            # пересечение гистограммы через ноль — смена импульса
            if hist[-2] <= 0 < hist[-1]:
                longs += 1
            elif hist[-2] >= 0 > hist[-1]:
                shorts += 1

        b = bollinger_pct_b(closes, self.bb_period, self.bb_mult)
        if b:
            snapshot["pct_b"] = round(b[-1], 3)
            if b[-1] <= self.bb_buy:
                longs += 1
            elif b[-1] >= self.bb_sell:
                shorts += 1

        return longs, shorts, snapshot

    # ------------------------------------------------------------------ решение
    def decide(self, ctx: Context) -> list[Action]:
        closes = ctx.md.closes
        if len(closes) < self.warmup_bars():
            return []

        # Незалившаяся лимитка на вход устаревает: сигнал был на своей цене,
        # держать её вечно — значит войти уже в другом рынке.
        if self.entry_type == "post_only" and ctx.position.is_flat and ctx.open_orders:
            if self._pending_since is None:
                self._pending_since = time.time()
            elif time.time() - self._pending_since > self.entry_ttl_seconds:
                self._pending_since = None
                return [Action(kind="cancel_all",
                               reason=f"лимитка на вход не залилась за {self.entry_ttl_seconds}с")]
        else:
            self._pending_since = None

        # считаем сигналы только один раз на закрытии свечи
        if ctx.md.bar_time != self._prev_bar_time:
            self._prev_bar_time = ctx.md.bar_time
            self._bars_seen += 1
        else:
            return []

        longs, shorts, snap = self.votes(closes)
        log.info("Индикаторы %s | голоса: лонг=%d шорт=%d", snap, longs, shorts)

        pos = ctx.position

        # В позиции: закрываем, если рынок развернулся против нас
        if not pos.is_flat:
            against = shorts if pos.side == "Buy" else longs
            if against >= self.min_confluence:
                return [Action(kind="close", reason=f"разворот сигнала против позиции {pos.side}")]
            return []

        if self._bars_seen - self._last_entry_bar < self.cooldown_bars:
            return []

        allow_long = self.direction in ("both", "long_only")
        allow_short = self.direction in ("both", "short_only")

        if allow_long and longs >= self.min_confluence and longs > shorts:
            return [self._entry("Buy", ctx, longs, snap)]
        if allow_short and shorts >= self.min_confluence and shorts > longs:
            return [self._entry("Sell", ctx, shorts, snap)]
        return []

    def _entry(self, side: str, ctx: Context, votes: int, snap: dict) -> Action:
        self._last_entry_bar = self._bars_seen
        if self.entry_type == "post_only":
            # встаём в очередь на ближней стороне книги — комиссия мейкера,
            # цена которой является риск неисполнения
            price = ctx.md.bid if side == "Buy" else ctx.md.ask
            kind, post_only = "limit", True
        else:
            price = ctx.md.last
            kind, post_only = "market", False

        qty = self.qty_for_notional(self.notional, price, ctx.instrument)
        if side == "Buy":
            tp, sl = price * (1 + self.tp_pct), price * (1 - self.sl_pct)
        else:
            tp, sl = price * (1 - self.tp_pct), price * (1 + self.sl_pct)
        return Action(
            kind=kind, side=side, qty=qty,
            price=price if kind == "limit" else None,
            post_only=post_only,
            take_profit=tp, stop_loss=sl,
            reason=f"{votes} индикатора за {side} ({self.entry_type}): {snap}",
        )

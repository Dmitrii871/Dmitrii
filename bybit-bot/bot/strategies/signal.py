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

from ..indicators import adx, bollinger_pct_b, macd, rsi
from ..models import Action
from ..plan import TradingPlan
from .base import Context, Strategy

log = logging.getLogger(__name__)


class SignalStrategy(Strategy):
    name = "signal"

    def __init__(self, cfg: dict, plan: TradingPlan | None = None):
        super().__init__(cfg)
        # Внешняя разметка рынка. Может только запретить сделку или добавить
        # один голос — открыть позицию сама она не может.
        self.plan = plan
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
        # reversion — покупаем перепроданность (ловим разворот);
        # momentum  — покупаем силу (идём за трендом).
        # Что из этого работает, решает оптимизатор на ваших данных,
        # а не убеждения автора кода.
        self.mode = str(cfg.get("mode", "reversion")).lower()
        # Режим auto выбирает логику по силе тренда: ADX ниже нижнего порога —
        # боковик, работает возврат к среднему; выше верхнего — тренд, работает
        # движение по нему; между порогами режим неясен и сделок нет.
        self.adx_period = int(cfg.get("adx_period", 14))
        self.adx_range_max = float(cfg.get("adx_range_max", 20.0))
        self.adx_trend_min = float(cfg.get("adx_trend_min", 25.0))
        self.entry_ttl_seconds = int(cfg.get("entry_ttl_seconds", 90))
        # exchange_tpsl — выход по TP/SL на бирже, всегда тейкер (5.5 bp)
        # maker_chase  — выход лимиткой у края книги, мейкер (2 bp);
        #   круг падает с 7.5 до 4 bp, и найденный край в 6.7 bp его перекрывает.
        #   Цена ухода: лимитка может не исполниться, поэтому есть аварийный
        #   выход тейкером по сроку и по движению против позиции.
        self.exit_type = str(cfg.get("exit_type", "exchange_tpsl")).lower()
        self.exit_ttl_bars = int(cfg.get("exit_ttl_bars", 12))
        self.exit_panic_pct = Decimal(str(cfg.get("exit_panic_pct", 1.5))) / 100
        self._pos_opened_bar = 0
        self._last_entry_bar = -10**9   # кулдаун не должен блокировать первый вход
        self._bars_seen = 0
        self._prev_bar_time = 0
        self._pending_since: float | None = None
        self.last_snapshot: dict = {}     # для журнала и строки состояния

    def warmup_bars(self) -> int:
        return max(self.macd_params[1] + self.macd_params[2], self.bb_period, self.rsi_period) + 30

    def validate(self, fees: dict) -> None:
        if self.entry_type not in ("market", "post_only"):
            raise ValueError("entry_type должен быть 'market' или 'post_only'")
        if self.exit_type not in ("exchange_tpsl", "maker_chase"):
            raise ValueError("exit_type должен быть 'exchange_tpsl' или 'maker_chase'")
        if self.direction not in ("both", "long_only", "short_only"):
            raise ValueError("direction должен быть 'both', 'long_only' или 'short_only'")
        if self.mode not in ("reversion", "momentum", "auto"):
            raise ValueError("mode должен быть 'reversion', 'momentum' или 'auto'")
        if self.adx_range_max >= self.adx_trend_min:
            raise ValueError("adx_range_max должен быть меньше adx_trend_min")
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
    def regime(self, highs: list[float], lows: list[float],
               closes: list[float]) -> tuple[str, float | None]:
        """Какой режим рынка сейчас: 'reversion', 'momentum' или 'unclear'."""
        if self.mode != "auto":
            return self.mode, None
        if not highs or not lows:
            return "unclear", None
        a, _, _ = adx(highs, lows, closes, self.adx_period)
        if not a:
            return "unclear", None
        value = a[-1]
        if value <= self.adx_range_max:
            return "reversion", value
        if value >= self.adx_trend_min:
            return "momentum", value
        return "unclear", value

    def votes(self, closes: list[float], highs: list[float] | None = None,
              lows: list[float] | None = None) -> tuple[int, int, dict]:
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

        mode, adx_val = self.regime(highs or [], lows or [], closes)
        if adx_val is not None:
            snapshot["adx"] = round(adx_val, 1)
            snapshot["режим"] = mode
        if mode == "unclear":
            return 0, 0, snapshot          # сила тренда между порогами — не торгуем
        if mode == "momentum":
            longs, shorts = shorts, longs
        return longs, shorts, snapshot

    def votes_series(self, closes: list[float], highs: list[float] | None = None,
                     lows: list[float] | None = None) -> list[tuple[int, int]]:
        """Голоса для КАЖДОГО бара за один проход.

        Индикаторы причинные — значение на баре i зависит только от прошлого,
        поэтому один расчёт по всей истории даёт ровно те же числа, что и
        пересчёт на каждом баре, но за O(n) вместо O(n^2). Эквивалентность
        проверяется тестом test_votes_series_matches_per_bar.
        """
        n = len(closes)
        out: list[tuple[int, int]] = [(0, 0)] * n

        r = rsi(closes, self.rsi_period)
        r_off = n - len(r)
        line, sig, hist = macd(closes, *self.macd_params)
        h_off = n - len(hist)
        b = bollinger_pct_b(closes, self.bb_period, self.bb_mult)
        b_off = n - len(b)

        # Режим на каждом баре: ADX причинный, считается один раз по всей истории
        modes: list[str] = [self.mode] * n
        if self.mode == "auto":
            if highs and lows:
                a, _, _ = adx(highs, lows, closes, self.adx_period)
                a_off = n - len(a)
                for i in range(n):
                    j = i - a_off
                    if 0 <= j < len(a):
                        v = a[j]
                        modes[i] = ("reversion" if v <= self.adx_range_max else
                                    "momentum" if v >= self.adx_trend_min else "unclear")
                    else:
                        modes[i] = "unclear"
            else:
                modes = ["unclear"] * n

        for i in range(n):
            longs = shorts = 0
            j = i - r_off
            if 0 <= j < len(r):
                if r[j] <= self.rsi_buy:
                    longs += 1
                elif r[j] >= self.rsi_sell:
                    shorts += 1
            k = i - h_off
            if 1 <= k < len(hist):
                if hist[k - 1] <= 0 < hist[k]:
                    longs += 1
                elif hist[k - 1] >= 0 > hist[k]:
                    shorts += 1
            m = i - b_off
            if 0 <= m < len(b):
                if b[m] <= self.bb_buy:
                    longs += 1
                elif b[m] >= self.bb_sell:
                    shorts += 1
            m = modes[i]
            if m == "unclear":
                out[i] = (0, 0)
            elif m == "momentum":
                out[i] = (shorts, longs)
            else:
                out[i] = (longs, shorts)
        return out

    # ------------------------------------------------------------------ решение
    def decide(self, ctx: Context) -> list[Action]:
        closes = ctx.md.closes
        if len(closes) < self.warmup_bars():
            # Молчать здесь нельзя: именно так потерялись недели теста —
            # бот выглядел работающим, но не считал ни одного сигнала.
            self.last_snapshot = {"причина": f"мало данных: {len(closes)} баров "
                                             f"из {self.warmup_bars()}"}
            log.warning("Мало данных: %d баров при необходимых %d — сигналы не считаются",
                        len(closes), self.warmup_bars())
            return []

        # Частичное исполнение: позиция уже открыта, но часть заявки висит.
        # Остаток надо снять — иначе позиция будет тихо расти сверх расчёта,
        # а TP/SL считались от исходного размера.
        if not ctx.position.is_flat and ctx.open_orders:
            leftovers = [o for o in ctx.open_orders if not o.get("reduceOnly")]
            if leftovers:
                self._pending_since = None
                return [Action(kind="cancel_all",
                               reason="снятие остатка частично исполненной заявки")]

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

        # Индикаторы считаем КАЖДЫЙ цикл — они идут в журнал. Раньше расчёт
        # стоял после выхода по неизменившейся свече, и при часовом
        # таймфрейме журнал час подряд получал пустые колонки: по нему
        # нельзя было понять, насколько бот был близок к сигналу.
        longs, shorts, snap = self.votes(closes, ctx.md.highs, ctx.md.lows)
        snap = {**snap, "голоса_лонг": longs, "голоса_шорт": shorts}
        self.last_snapshot = snap

        # Решения — по-прежнему только на закрытии свечи
        if ctx.md.bar_time != self._prev_bar_time:
            self._prev_bar_time = ctx.md.bar_time
            self._bars_seen += 1
        else:
            return []
        plan_note = ""
        if self.plan is not None:
            pl, ps, plan_note = self.plan.extra_votes(ctx.md.last)
            longs, shorts = longs + pl, shorts + ps
        log.info("Индикаторы %s | голоса: лонг=%d шорт=%d%s",
                 snap, longs, shorts, f" | план: {plan_note}" if plan_note else "")

        pos = ctx.position

        # Выход лимиткой: держим reduce-only заявку у цели и переставляем её,
        # пока не истечёт срок или цена не уйдёт против позиции слишком далеко.
        if not pos.is_flat and self.exit_type == "maker_chase":
            exit_act = self._manage_exit(ctx, pos)
            if exit_act is not None:
                return exit_act

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
        if self.plan is not None:
            # План действует как фильтр: против его смещения бот не входит.
            allow_long = allow_long and self.plan.allows("Buy", ctx.md.last)
            allow_short = allow_short and self.plan.allows("Sell", ctx.md.last)

        if allow_long and longs >= self.min_confluence and longs > shorts:
            return [self._entry("Buy", ctx, longs, snap)]
        if allow_short and shorts >= self.min_confluence and shorts > longs:
            return [self._entry("Sell", ctx, shorts, snap)]
        return []

    def _manage_exit(self, ctx: Context, pos) -> list[Action] | None:
        """Ведение выхода лимиткой. None — вмешательство не требуется."""
        held = self._bars_seen - self._pos_opened_bar
        entry = pos.entry_price
        last = ctx.md.last
        if entry <= 0:
            return None

        # движение против позиции сверх порога — выходим тейкером, не торгуясь
        adverse = (entry - last) / entry if pos.side == "Buy" else (last - entry) / entry
        if adverse >= self.exit_panic_pct:
            return [Action(kind="close",
                           reason=f"движение против позиции {float(adverse)*100:.2f}%")]
        if held >= self.exit_ttl_bars:
            return [Action(kind="close", reason=f"срок удержания {held} баров истёк")]

        # целевая цена выхода и сторона закрытия
        side = "Sell" if pos.side == "Buy" else "Buy"
        target = entry * (1 + self.tp_pct) if pos.side == "Buy" else entry * (1 - self.tp_pct)
        # не даём заявке уйти вглубь книги: держим её у ближнего края
        touch = ctx.md.ask if side == "Sell" else ctx.md.bid
        price = max(target, touch) if side == "Sell" else min(target, touch)

        existing = [o for o in ctx.open_orders if o.get("reduceOnly")]
        if existing:
            try:
                cur = Decimal(str(existing[0].get("price", "0")))
            except Exception:  # noqa: BLE001
                cur = Decimal(0)
            if cur > 0 and abs(price - cur) / price < Decimal("0.0005"):
                return []              # заявка уже там, где надо
            return [Action(kind="cancel_all", reason="перестановка выходной лимитки")]

        return [Action(kind="limit", side=side, qty=pos.size, price=price,
                       post_only=True, reduce_only=True,
                       reason=f"выход мейкером, держим {held} баров")]

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

        # Уровень плана перед расчётным тейком — более реалистичная цель:
        # там стоят чужие заявки, и цена скорее развернётся, чем пройдёт насквозь.
        if self.plan is not None:
            level = self.plan.target_for(side, price)
            if level is not None and (level < tp if side == "Buy" else level > tp):
                min_move = price * self.tp_pct / 2      # но не ближе половины тейка
                ok = level >= price + min_move if side == "Buy" else level <= price - min_move
                if ok:
                    log.info("Тейк-профит подтянут к уровню плана: %s вместо %s",
                             level, round(float(tp), 2))
                    tp = level
        self._pos_opened_bar = self._bars_seen
        # В режиме maker_chase выход ведём сами лимитками; биржевой стоп
        # оставляем как страховку на случай падения бота.
        on_exchange_tp = None if self.exit_type == "maker_chase" else tp
        return Action(
            kind=kind, side=side, qty=qty,
            price=price if kind == "limit" else None,
            post_only=post_only,
            take_profit=on_exchange_tp, stop_loss=sl,
            reason=f"{votes} индикатора за {side} ({self.entry_type}): {snap}",
        )

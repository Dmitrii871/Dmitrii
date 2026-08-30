"""Следование за трендом на дневных свечах — канал Дончиана.

Единственный класс, прошедший проверку историей: 5/5 монет в плюсе за
5-6 лет, профит-фактор 1.4-1.75, каждый год кроме 2021 положительный
(tools/trend.py). Возврат к среднему на часовиках отвергнут измерениями.

Механика без предсказаний: цена выше максимума последних N закрытых
дней — покупаем; ниже минимума — продаём. Выход — обратный экстремум
за M дней. Винрейт у такой системы ~35-40%: много мелких стопов,
редкие крупные выигрыши. Это нормально; ломается человек, а не система,
поэтому просадки до полутора размеров позиции и серии до 9 убыточных
подряд написаны здесь, чтобы их ждали.

Вход тейкером: пробой лимиткой не берётся — цена уходит от уровня.
Издержки при движениях в проценты роли не играют.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from ..models import Action
from .base import Context, Strategy

log = logging.getLogger(__name__)


class TrendStrategy(Strategy):
    name = "trend"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.enter_days = int(cfg.get("enter_days", 20))
        self.exit_days = int(cfg.get("exit_days", 10))
        self.notional = Decimal(str(cfg.get("order_notional_usdt", 20)))
        self.direction = cfg.get("direction", "both")
        # Аварийный стоп на бирже — страховка на случай смерти бота.
        # Канал выходит раньше почти всегда; стоп должен быть ДАЛЬШЕ
        # типичного канала, иначе он подменит собой механику выхода.
        self.disaster_stop_pct = Decimal(str(cfg.get("disaster_stop_pct", 15))) / 100
        self.last_snapshot: dict = {}

    def warmup_bars(self) -> int:
        return self.enter_days + 5

    def validate(self, fees: dict) -> None:
        if self.exit_days >= self.enter_days:
            raise ValueError(
                f"exit_days ({self.exit_days}) должен быть меньше enter_days "
                f"({self.enter_days}): выходной канал уже входного — иначе "
                "позиция закрывается тем же движением, что её открыло")
        if self.direction not in ("both", "long_only", "short_only"):
            raise ValueError("direction: both | long_only | short_only")

    # ------------------------------------------------------------------
    def decide(self, ctx: Context) -> list[Action]:
        highs, lows = ctx.md.highs, ctx.md.lows
        if len(highs) < self.warmup_bars():
            self.last_snapshot = {"причина": f"мало данных: {len(highs)} дней "
                                             f"из {self.warmup_bars()}"}
            return []

        # Каналы считаются по ЗАКРЫТЫМ дням (текущая свеча уже отброшена
        # в market_data): внутри дня уровень не дрожит.
        enter_hi = max(highs[-self.enter_days:])
        enter_lo = min(lows[-self.enter_days:])
        exit_hi = max(highs[-self.exit_days:])
        exit_lo = min(lows[-self.exit_days:])
        price = ctx.md.last
        pos = ctx.position

        self.last_snapshot = {
            "канал": f"{float(enter_lo):g}..{float(enter_hi):g} "
                     f"(выход {float(exit_lo):g}/{float(exit_hi):g})",
        }

        if not pos.is_flat:
            if pos.side == "Buy" and price <= exit_lo:
                return [Action(kind="close",
                               reason=f"цена {price} пробила минимум за {self.exit_days} баров: {exit_lo}")]
            if pos.side == "Sell" and price >= exit_hi:
                return [Action(kind="close",
                               reason=f"цена {price} пробила максимум за {self.exit_days} баров: {exit_hi}")]
            return []

        long_break = price >= enter_hi
        short_break = price <= enter_lo
        if long_break and short_break:
            return []                       # хаос: оба канала за один день
        if long_break and self.direction in ("both", "long_only"):
            act = self._entry("Buy", price, ctx)
            return [act] if act else []
        if short_break and self.direction in ("both", "short_only"):
            act = self._entry("Sell", price, ctx)
            return [act] if act else []
        return []

    def _entry(self, side: str, price: Decimal, ctx: Context) -> Action | None:
        qty = self.qty_for_notional(self.notional, price, ctx.instrument)
        # Минимальный лот биржи: у BTC это ~0.001 BTC (десятки USDT).
        # Позиция меньше лота округлится в ноль — молча пропустить пробой
        # нельзя, это потерянный валидированный сигнал. Кричим в лог.
        min_needed = max(ctx.instrument.min_qty * price, ctx.instrument.min_notional)
        if self.notional < min_needed:
            log.warning(
                "%s: позиция %s USDT меньше минимального лота (%s USDT) — "
                "вход пропущен. Задайте notional_overrides в конфиге.",
                ctx.md.symbol, self.notional, min_needed)
            self.last_snapshot["причина"] = (
                f"позиция {self.notional} USDT < минимума {min_needed:.0f} USDT")
            return None
        sl = (price * (1 - self.disaster_stop_pct) if side == "Buy"
              else price * (1 + self.disaster_stop_pct))
        return Action(
            kind="market", side=side, qty=qty, stop_loss=sl,
            reason=f"пробой канала за {self.enter_days} баров "
                   f"({'вверх' if side == 'Buy' else 'вниз'}) на {price}",
        )

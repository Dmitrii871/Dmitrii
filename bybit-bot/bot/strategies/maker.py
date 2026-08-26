"""Маркет-мейкинг: заработок на спреде.

Бот держит лимитные заявки с обеих сторон от середины рынка. Доход —
разница между ценой покупки и продажи минус комиссии. Это законная
торговля с реальными контрагентами, а не самоматчинг.

Ключевое ограничение: половина спреда должна превышать комиссию мейкера,
иначе стратегия убыточна по построению. Проверяется в validate().
"""
from __future__ import annotations

import logging
from decimal import Decimal

from ..models import Action
from .base import Context, Strategy

log = logging.getLogger(__name__)


class MakerStrategy(Strategy):
    name = "maker"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.spread_bps = float(cfg.get("spread_bps", 8.0))
        self.notional = Decimal(str(cfg.get("order_notional_usdt", 25)))
        self.max_inventory = Decimal(str(cfg.get("max_inventory_usdt", 60)))
        self.refresh_bps = float(cfg.get("refresh_bps", 3.0))
        self.skew = float(cfg.get("inventory_skew", 1.0))
        self._quoted_mid: Decimal | None = None

    def warmup_bars(self) -> int:
        return 0

    def validate(self, fees: dict) -> None:
        maker_bps = float(fees.get("maker_bps", 2.0))
        if self.spread_bps <= maker_bps:
            raise ValueError(
                f"spread_bps={self.spread_bps} не покрывает комиссию мейкера "
                f"{maker_bps} bp с каждой стороны. Полный круг стоит {2*maker_bps:.1f} bp — "
                f"поставьте spread_bps минимум {maker_bps*2:.1f}, лучше выше."
            )
        edge = 2 * (self.spread_bps - maker_bps)
        log.info(
            "Ожидаемая маржа на полный круг: %.1f bp (%.4f%%) до учёта риска движения цены",
            edge, edge / 100,
        )

    def decide(self, ctx: Context) -> list[Action]:
        mid = ctx.md.mid
        if mid <= 0:
            return []

        # Переставляем котировки только когда рынок реально ушёл —
        # иначе сожжём лимит запросов и получим отказы биржи.
        if self._quoted_mid is not None and ctx.open_orders:
            drift_bps = abs(float((mid - self._quoted_mid) / self._quoted_mid)) * 10_000
            if drift_bps < self.refresh_bps:
                return []

        inventory = ctx.position.signed_size * mid          # знаковый нотионал, USDT
        ratio = float(inventory / self.max_inventory) if self.max_inventory > 0 else 0.0
        ratio = max(-1.0, min(1.0, ratio))

        # Перекос инвентаря смещает обе котировки в сторону его сокращения:
        # длинная позиция -> котируем ниже, чтобы охотнее продать.
        shift_bps = -ratio * self.skew * self.spread_bps
        half = Decimal(str(self.spread_bps / 10_000))
        center = mid * (1 + Decimal(str(shift_bps / 10_000)))

        bid_price = center * (1 - half)
        ask_price = center * (1 + half)
        qty = self.qty_for_notional(self.notional, mid, ctx.instrument)

        actions = [Action(kind="cancel_all", reason="перестановка котировок")]

        # Не наращиваем сторону, по которой инвентарь уже на пределе
        if ratio < 0.999:
            actions.append(Action(
                kind="limit", side="Buy", qty=qty, price=bid_price, post_only=True,
                reason=f"бид, инвентарь {float(inventory):.1f} USDT",
            ))
        if ratio > -0.999:
            actions.append(Action(
                kind="limit", side="Sell", qty=qty, price=ask_price, post_only=True,
                reason=f"аск, инвентарь {float(inventory):.1f} USDT",
            ))

        self._quoted_mid = mid
        return actions

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal

from ..models import Account, Action, Instrument, MarketData, Position


@dataclass
class Context:
    md: MarketData
    position: Position
    account: Account
    instrument: Instrument
    open_orders: list[dict] = field(default_factory=list)
    maker_bps: float = 2.0
    taker_bps: float = 5.5


class Strategy(ABC):
    name = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    @abstractmethod
    def decide(self, ctx: Context) -> list[Action]:
        """Вернуть список действий. Пустой список = ничего не делать."""

    @abstractmethod
    def warmup_bars(self) -> int:
        """Сколько свечей нужно, чтобы индикаторы были валидны."""

    def validate(self, fees: dict) -> None:
        """Проверка экономической осмысленности настроек. Бросает ValueError."""

    @staticmethod
    def qty_for_notional(notional: Decimal, price: Decimal, inst: Instrument) -> Decimal:
        if price <= 0:
            return Decimal(0)
        return notional / price

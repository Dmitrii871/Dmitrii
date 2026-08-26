"""Общие типы, которыми обмениваются стратегия, риск-менеджер и биржа."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class Instrument:
    """Торговые фильтры символа — без них ордера отклоняются биржей."""
    symbol: str
    tick_size: Decimal
    qty_step: Decimal
    min_qty: Decimal
    min_notional: Decimal


@dataclass
class Position:
    symbol: str
    side: str            # "Buy" | "Sell" | "" (нет позиции)
    size: Decimal        # всегда >= 0
    entry_price: Decimal
    unrealised_pnl: Decimal
    mark_price: Decimal

    @property
    def is_flat(self) -> bool:
        return self.size <= 0

    @property
    def signed_size(self) -> Decimal:
        if self.is_flat:
            return Decimal(0)
        return self.size if self.side == "Buy" else -self.size

    def notional(self) -> Decimal:
        return self.size * self.mark_price


@dataclass
class Account:
    equity: Decimal
    available: Decimal

    @property
    def free_margin_ratio(self) -> float:
        if self.equity <= 0:
            return 0.0
        return float(self.available / self.equity)


@dataclass
class MarketData:
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    closes: list[float] = field(default_factory=list)
    bar_time: int = 0     # время открытия последней закрытой свечи, мс

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        if self.mid <= 0:
            return 0.0
        return float((self.ask - self.bid) / self.mid) * 10_000


@dataclass
class Action:
    """Одно намерение стратегии. Риск-менеджер может его отклонить."""
    kind: str                      # "cancel_all" | "limit" | "market" | "close"
    side: str | None = None        # "Buy" | "Sell"
    qty: Decimal | None = None
    price: Decimal | None = None
    post_only: bool = False
    reduce_only: bool = False
    take_profit: Decimal | None = None
    stop_loss: Decimal | None = None
    reason: str = ""

    def describe(self) -> str:
        if self.kind == "cancel_all":
            return "снять все ордера"
        bits = [self.kind, self.side or "", f"{self.qty}"]
        if self.price is not None:
            bits.append(f"@ {self.price}")
        if self.post_only:
            bits.append("PostOnly")
        if self.reduce_only:
            bits.append("ReduceOnly")
        return " ".join(b for b in bits if b)


class RiskHalt(Exception):
    """Фатальное нарушение лимитов — бот останавливается."""


class RiskReject(Exception):
    """Отдельное действие отклонено, работа продолжается."""

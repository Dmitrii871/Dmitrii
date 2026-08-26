"""Торговый план: внешняя разметка рынка как набор ограничений для бота.

Волновой анализ субъективен и пересматривается. Поэтому бот не пытается
размечать волны сам — он принимает готовый план как ГИПОТЕЗУ с тремя
обязательными атрибутами: сроком годности, ценой отмены и направлением.

План может только ЗАПРЕТИТЬ сделку или сместить предпочтение. Он никогда
не открывает позицию сам и не отменяет риск-менеджмент.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

log = logging.getLogger(__name__)

KINDS = {"support", "resistance", "target"}


@dataclass(frozen=True)
class Level:
    price: Decimal
    kind: str
    label: str

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind должен быть одним из {sorted(KINDS)}, получено '{self.kind}'")


@dataclass
class TradingPlan:
    symbol: str
    source: str
    bias: str                    # long | short | neutral
    invalidation: Decimal        # пробой этой цены отменяет сценарий
    invalidation_side: str       # above | below — с какой стороны считается пробой
    expires: date
    levels: list[Level] = field(default_factory=list)
    zone_bps: float = 50.0       # ширина зоны вокруг уровня, базисные пункты
    note: str = ""
    _dead: bool = field(default=False, init=False, repr=False)

    # ------------------------------------------------------------- загрузка
    @classmethod
    def load(cls, path: str | Path) -> "TradingPlan":
        import yaml

        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        expires = raw["expires"]
        if isinstance(expires, str):
            expires = date.fromisoformat(expires)
        if raw["bias"] not in ("long", "short", "neutral"):
            raise ValueError("bias должен быть 'long', 'short' или 'neutral'")
        if raw["invalidation_side"] not in ("above", "below"):
            raise ValueError("invalidation_side должен быть 'above' или 'below'")

        plan = cls(
            symbol=raw["symbol"],
            source=raw.get("source", "не указан"),
            bias=raw["bias"],
            invalidation=Decimal(str(raw["invalidation"])),
            invalidation_side=raw["invalidation_side"],
            expires=expires,
            levels=[Level(Decimal(str(l["price"])), l["kind"], l.get("label", ""))
                    for l in raw.get("levels", [])],
            zone_bps=float(raw.get("zone_bps", 50.0)),
            note=raw.get("note", ""),
        )
        log.info("План загружен: %s | смещение '%s' | источник: %s | годен до %s",
                 plan.symbol, plan.bias, plan.source, plan.expires)
        if plan.is_expired():
            log.warning("План ПРОСРОЧЕН (%s) — бот его игнорирует. "
                        "Волновые сценарии пересматриваются, обновите файл.", plan.expires)
        return plan

    # -------------------------------------------------------------- статус
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc).date() > self.expires

    def is_active(self, price: Decimal) -> bool:
        """План действует, пока не просрочен и не отменён пробоем."""
        if self._dead or self.is_expired():
            return False
        broken = (price > self.invalidation if self.invalidation_side == "above"
                  else price < self.invalidation)
        if broken:
            self._dead = True
            log.error(
                "СЦЕНАРИЙ ОТМЕНЁН: цена %s пробила уровень отмены %s (%s). "
                "План отключён до конца сессии — разметка требует пересмотра.",
                price, self.invalidation, self.invalidation_side,
            )
            return False
        return True

    # ------------------------------------------------------------- решения
    def allows(self, side: str, price: Decimal) -> bool:
        """Разрешает ли план сделку в эту сторону."""
        if not self.is_active(price):
            return True          # неактивный план ничего не запрещает
        if self.bias == "neutral":
            return True
        return (side == "Buy") == (self.bias == "long")

    def near(self, price: Decimal) -> Level | None:
        """Ближайший уровень, если цена внутри его зоны."""
        if not self.is_active(price) or price <= 0:
            return None
        best, best_dist = None, None
        for lvl in self.levels:
            dist_bps = abs(float((price - lvl.price) / price)) * 10_000
            if dist_bps <= self.zone_bps and (best_dist is None or dist_bps < best_dist):
                best, best_dist = lvl, dist_bps
        return best

    def extra_votes(self, price: Decimal) -> tuple[int, int, str]:
        """Дополнительные голоса от плана: (лонг, шорт, пояснение).

        Отскок от поддержки — довод за лонг, упор в сопротивление — за шорт.
        Ровно один голос: план подсказывает, а не решает.
        """
        lvl = self.near(price)
        if lvl is None:
            return 0, 0, ""
        if lvl.kind == "support":
            return 1, 0, f"цена в зоне поддержки {lvl.price} ({lvl.label})"
        if lvl.kind == "resistance":
            return 0, 1, f"цена в зоне сопротивления {lvl.price} ({lvl.label})"
        return 0, 0, f"цена у целевого уровня {lvl.price} ({lvl.label})"

    def target_for(self, side: str, price: Decimal) -> Decimal | None:
        """Ближайшая цель плана по ходу сделки — кандидат в тейк-профит."""
        if not self.is_active(price):
            return None
        ahead = [l.price for l in self.levels
                 if l.kind in ("target", "resistance" if side == "Buy" else "support")
                 and (l.price > price if side == "Buy" else l.price < price)]
        if not ahead:
            return None
        return min(ahead) if side == "Buy" else max(ahead)

from .base import Context, Strategy
from .maker import MakerStrategy
from .signal import SignalStrategy

REGISTRY = {"signal": SignalStrategy, "maker": MakerStrategy}


def build(name: str, cfg: dict) -> Strategy:
    if name not in REGISTRY:
        raise ValueError(f"Неизвестная стратегия '{name}'. Доступны: {list(REGISTRY)}")
    return REGISTRY[name](cfg)

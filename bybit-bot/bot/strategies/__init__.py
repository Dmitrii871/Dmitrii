from .base import Context, Strategy
from .maker import MakerStrategy
from .signal import SignalStrategy

REGISTRY = {"signal": SignalStrategy, "maker": MakerStrategy}


def build(name: str, cfg: dict, plan=None) -> Strategy:
    if name not in REGISTRY:
        raise ValueError(f"Неизвестная стратегия '{name}'. Доступны: {list(REGISTRY)}")
    if name == "signal":
        return SignalStrategy(cfg, plan=plan)
    return REGISTRY[name](cfg)

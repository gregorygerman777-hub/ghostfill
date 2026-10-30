from __future__ import annotations

from .base import Strategy
from .ma_crossover import MovingAverageCrossover
from .zscore_reversion import ZScoreReversion

REGISTRY: dict[str, type[Strategy]] = {
    MovingAverageCrossover.name: MovingAverageCrossover,
    ZScoreReversion.name: ZScoreReversion,
}


def build_strategy(name: str, params: dict) -> Strategy:
    if name not in REGISTRY:
        raise ValueError(f"unknown strategy {name!r}; choose from {sorted(REGISTRY)}")
    return REGISTRY[name](**params)


__all__ = ["Strategy", "MovingAverageCrossover", "ZScoreReversion", "REGISTRY", "build_strategy"]

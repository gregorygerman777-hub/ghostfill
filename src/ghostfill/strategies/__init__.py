from __future__ import annotations

from .base import Strategy
from .donchian_breakout import DonchianBreakout
from .ma_crossover import MovingAverageCrossover
from .rsi_pullback import RsiPullback
from .zscore_reversion import ZScoreReversion

REGISTRY: dict[str, type[Strategy]] = {
    MovingAverageCrossover.name: MovingAverageCrossover,
    ZScoreReversion.name: ZScoreReversion,
    DonchianBreakout.name: DonchianBreakout,
    RsiPullback.name: RsiPullback,
}


def build_strategy(name: str, params: dict) -> Strategy:
    if name not in REGISTRY:
        raise ValueError(f"unknown strategy {name!r}; choose from {sorted(REGISTRY)}")
    return REGISTRY[name](**params)


__all__ = ["Strategy", "MovingAverageCrossover", "ZScoreReversion", "DonchianBreakout", "RsiPullback",
           "REGISTRY", "build_strategy"]

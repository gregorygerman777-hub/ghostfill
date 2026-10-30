"""Strategy interface.

generate_signals(df) returns a target position per bar in [0, 1] (fraction of
the configured position size; long only). The value at bar t may use only
rows up to and including t. tests/test_signals.py enforces this.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    name: str = "base"

    def __init__(self, **params):
        self.params = params
        self.validate()

    def validate(self) -> None:
        """Raise ValueError for out-of-range parameters."""

    @property
    def warmup(self) -> int:
        """Bars needed before the first meaningful signal."""
        return 0

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        ...

    def __repr__(self) -> str:
        args = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}({args})"

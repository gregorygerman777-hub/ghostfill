from __future__ import annotations

import pandas as pd

from .base import Strategy


class MovingAverageCrossover(Strategy):
    """Long while the fast simple moving average is above the slow one."""

    name = "ma_crossover"

    def validate(self) -> None:
        fast, slow = int(self.params.get("fast", 0)), int(self.params.get("slow", 0))
        if fast < 2 or slow <= fast:
            raise ValueError(f"ma_crossover needs 2 <= fast < slow, got fast={fast} slow={slow}")
        self.fast, self.slow = fast, slow

    @property
    def warmup(self) -> int:
        return self.slow

    def indicators(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        close = df["close"]
        return {
            f"SMA {self.fast}": close.rolling(self.fast, min_periods=self.fast).mean(),
            f"SMA {self.slow}": close.rolling(self.slow, min_periods=self.slow).mean(),
        }

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        fast = close.rolling(self.fast, min_periods=self.fast).mean()  # trailing windows only
        slow = close.rolling(self.slow, min_periods=self.slow).mean()
        return (fast > slow).astype("float64").rename("target")

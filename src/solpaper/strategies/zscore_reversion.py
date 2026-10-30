from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class ZScoreReversion(Strategy):
    """Buy when price is `entry_z` standard deviations below its trailing mean;
    exit when the z-score recovers to `exit_z`."""

    name = "zscore_reversion"

    def validate(self) -> None:
        self.window = int(self.params.get("window", 0))
        self.entry_z = float(self.params.get("entry_z", 0))
        self.exit_z = float(self.params.get("exit_z", 0))
        if self.window < 5:
            raise ValueError(f"zscore_reversion window must be >= 5, got {self.window}")
        if self.entry_z <= 0:
            raise ValueError(f"zscore_reversion entry_z must be > 0, got {self.entry_z}")
        if self.exit_z <= -self.entry_z:
            raise ValueError("zscore_reversion exit_z must be above -entry_z")

    @property
    def warmup(self) -> int:
        return self.window

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        mean = close.rolling(self.window, min_periods=self.window).mean()
        std = close.rolling(self.window, min_periods=self.window).std(ddof=0)
        z = ((close - mean) / std.replace(0.0, np.nan)).to_numpy()
        out = np.zeros(len(z))
        held = False
        for i, zi in enumerate(z):  # sequential state machine, sees only z[0..i]
            if np.isnan(zi):
                held = False
            elif not held and zi <= -self.entry_z:
                held = True
            elif held and zi >= self.exit_z:
                held = False
            out[i] = 1.0 if held else 0.0
        return pd.Series(out, index=df.index, name="target")

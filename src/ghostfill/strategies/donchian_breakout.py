from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class DonchianBreakout(Strategy):
    """Trend following: go long when the close breaks above the highest high of the
    previous `entry` bars; exit when it breaks below the lowest low of the previous
    `exit` bars."""

    name = "donchian_breakout"

    def validate(self) -> None:
        self.entry = int(self.params.get("entry", 0))
        self.exit = int(self.params.get("exit", 0))
        if self.entry < 5 or not 2 <= self.exit <= self.entry:
            raise ValueError(f"donchian_breakout needs entry >= 5 and 2 <= exit <= entry, got {self.entry}/{self.exit}")

    @property
    def warmup(self) -> int:
        return self.entry + 1

    def _channels(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        # shift(1): the channel at bar t uses bars before t only
        upper = df["high"].rolling(self.entry, min_periods=self.entry).max().shift(1)
        lower = df["low"].rolling(self.exit, min_periods=self.exit).min().shift(1)
        return upper, lower

    def indicators(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        upper, lower = self._channels(df)
        return {f"high {self.entry}": upper, f"low {self.exit}": lower}

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        upper, lower = self._channels(df)
        close = df["close"].to_numpy()
        up, lo = upper.to_numpy(), lower.to_numpy()
        out = np.zeros(len(close))
        held = False
        for i in range(len(close)):
            if np.isnan(up[i]) or np.isnan(lo[i]):
                held = False
            elif not held and close[i] > up[i]:
                held = True
            elif held and close[i] < lo[i]:
                held = False
            out[i] = 1.0 if held else 0.0
        return pd.Series(out, index=df.index, name="target")

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


def wilder_rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0.0, np.nan)
    return (100 - 100 / (1 + rs)).where(loss > 0, 100.0)


class RsiPullback(Strategy):
    """Buy short-term weakness inside an uptrend: long when price is above its
    `trend` SMA and RSI(`rsi`) falls below `entry`; exit when RSI recovers above
    `exit` or price closes below the trend line."""

    name = "rsi_pullback"

    def validate(self) -> None:
        self.trend = int(self.params.get("trend", 0))
        self.rsi_n = int(self.params.get("rsi", 14))
        self.entry = float(self.params.get("entry", 30))
        self.exit = float(self.params.get("exit", 55))
        if self.trend < 10 or self.rsi_n < 2:
            raise ValueError("rsi_pullback needs trend >= 10 and rsi >= 2")
        if not 0 < self.entry < self.exit < 100:
            raise ValueError("rsi_pullback needs 0 < entry < exit < 100")

    @property
    def warmup(self) -> int:
        return max(self.trend, self.rsi_n * 3)

    def indicators(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {f"SMA {self.trend}": df["close"].rolling(self.trend, min_periods=self.trend).mean()}

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        sma = close.rolling(self.trend, min_periods=self.trend).mean().to_numpy()
        rsi = wilder_rsi(close, self.rsi_n).to_numpy()
        c = close.to_numpy()
        out = np.zeros(len(c))
        held = False
        for i in range(len(c)):
            if np.isnan(sma[i]) or np.isnan(rsi[i]):
                held = False
            elif not held and c[i] > sma[i] and rsi[i] < self.entry:
                held = True
            elif held and (rsi[i] > self.exit or c[i] < sma[i]):
                held = False
            out[i] = 1.0 if held else 0.0
        return pd.Series(out, index=df.index, name="target")

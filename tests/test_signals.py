"""Look-ahead tests: altering future rows must never change any past signal."""

import numpy as np
import pandas as pd
import pytest

from conftest import ohlcv
from ghostfill.strategies import (REGISTRY, DonchianBreakout, MovingAverageCrossover, RsiPullback, ZScoreReversion,
                                  build_strategy)

STRATEGIES = [
    MovingAverageCrossover(fast=5, slow=20),
    MovingAverageCrossover(fast=12, slow=48),
    ZScoreReversion(window=20, entry_z=1.5, exit_z=0.0),
    ZScoreReversion(window=48, entry_z=2.0, exit_z=0.5),
    DonchianBreakout(entry=20, exit=10),
    DonchianBreakout(entry=55, exit=20),
    RsiPullback(trend=50, rsi=14, entry=30, exit=55),
    RsiPullback(trend=100, rsi=7, entry=25, exit=60),
]


def _perturb_future(df: pd.DataFrame, t: int, rng) -> pd.DataFrame:
    out = df.copy()
    k = len(df) - t - 1
    shock = np.exp(rng.normal(0, 0.3, k))
    for col in ("open", "high", "low", "close"):
        out.iloc[t + 1 :, out.columns.get_loc(col)] = df[col].to_numpy()[t + 1 :] * shock
    out.iloc[t + 1 :, out.columns.get_loc("volume")] = rng.uniform(0, 1e7, k)
    return out


@pytest.mark.parametrize("strategy", STRATEGIES, ids=repr)
def test_future_rows_do_not_change_past_signals(strategy):
    rng = np.random.default_rng(0)
    df = ohlcv(500, seed=3)
    base = strategy.generate_signals(df)
    assert len(base) == len(df) and base.index.equals(df.index)
    for t in [strategy.warmup, 100, 250, 400, 498]:
        for _ in range(3):
            changed = strategy.generate_signals(_perturb_future(df, t, rng))
            pd.testing.assert_series_equal(base.iloc[: t + 1], changed.iloc[: t + 1])


@pytest.mark.parametrize("strategy", STRATEGIES, ids=repr)
def test_truncated_history_gives_same_signals(strategy):
    df = ohlcv(500, seed=5)
    full = strategy.generate_signals(df)
    for t in (60, 200, 333):
        part = strategy.generate_signals(df.iloc[: t + 1])
        pd.testing.assert_series_equal(full.iloc[: t + 1], part)


def test_lookahead_detector_catches_a_cheating_strategy():
    """Sanity check that the perturbation test would fail on a strategy that peeks ahead."""

    class Peeking(MovingAverageCrossover):
        def generate_signals(self, df):
            return (df["close"].shift(-1) > df["close"]).astype("float64")

    s = Peeking(fast=2, slow=3)
    df = ohlcv(100)
    base = s.generate_signals(df)
    changed = s.generate_signals(_perturb_future(df, 50, np.random.default_rng(1)))
    assert not base.iloc[:51].equals(changed.iloc[:51])


def test_ma_crossover_logic():
    idx = pd.date_range("2026-01-01", periods=40, freq="1h", tz="UTC")
    close = np.r_[np.linspace(10, 5, 20), np.linspace(5, 15, 20)]
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=idx)
    sig = MovingAverageCrossover(fast=3, slow=10).generate_signals(df)
    assert sig.iloc[:10].eq(0).all()      # warmup and downtrend: flat
    assert sig.iloc[-1] == 1.0            # uptrend: long
    assert set(sig.unique()) <= {0.0, 1.0}


def test_zscore_enters_on_dip_and_exits_on_recovery():
    idx = pd.date_range("2026-01-01", periods=60, freq="1h", tz="UTC")
    close = np.full(60, 10.0) + np.sin(np.arange(60)) * 0.05
    close[40] = 9.0  # sharp dip
    close[41:] = 10.0
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=idx)
    sig = ZScoreReversion(window=20, entry_z=2.0, exit_z=0.0).generate_signals(df)
    assert sig.iloc[39] == 0 and sig.iloc[40] == 1
    assert sig.iloc[-1] == 0


@pytest.mark.parametrize("name,params", [
    ("ma_crossover", {"fast": 10, "slow": 5}),
    ("ma_crossover", {"fast": 1, "slow": 5}),
    ("zscore_reversion", {"window": 2, "entry_z": 2}),
    ("zscore_reversion", {"window": 20, "entry_z": 0}),
    ("donchian_breakout", {"entry": 10, "exit": 20}),
    ("rsi_pullback", {"trend": 50, "entry": 60, "exit": 50}),
    ("nope", {}),
])
def test_bad_params_rejected(name, params):
    with pytest.raises(ValueError):
        build_strategy(name, params)


def test_registry_names():
    assert set(REGISTRY) == {"ma_crossover", "zscore_reversion", "donchian_breakout", "rsi_pullback"}


def test_donchian_enters_on_breakout_and_exits_on_breakdown():
    idx = pd.date_range("2026-01-01", periods=40, freq="1h", tz="UTC")
    close = np.r_[np.full(20, 10.0), 12.0, np.full(10, 12.5), np.full(9, 8.0)]
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=idx)
    sig = DonchianBreakout(entry=10, exit=5).generate_signals(df)
    assert sig.iloc[19] == 0 and sig.iloc[20] == 1      # breaks the 10-bar high
    assert sig.iloc[-1] == 0                           # broke the 5-bar low


def test_rsi_pullback_needs_uptrend():
    idx = pd.date_range("2026-01-01", periods=120, freq="1h", tz="UTC")
    down = np.linspace(20, 10, 120)  # persistent downtrend: never above its SMA, never long
    df = pd.DataFrame({"open": down, "high": down, "low": down, "close": down, "volume": 1.0}, index=idx)
    assert RsiPullback(trend=50, rsi=14, entry=30, exit=55).generate_signals(df).eq(0).all()

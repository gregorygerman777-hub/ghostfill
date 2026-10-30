import random
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from ghostfill.config import FeesCfg, RiskCfg, ScreenCfg, Settings
from ghostfill.costs import CostModel
from ghostfill.journal import Journal
from ghostfill.portfolio import Market, Portfolio
from ghostfill.risk import RiskManager

JUP = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def make_settings(**over) -> Settings:
    base = dict(
        tokens=[{"mint": JUP, "label": "JUP"}, {"mint": BONK, "label": "BONK"}],
        strategy={"name": "ma_crossover", "params": {"fast": 5, "slow": 20}},
    )
    base.update(over)
    return Settings.model_validate(base)


def ohlcv(n=400, seed=1, start=NOW - timedelta(hours=400)) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 1.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.005, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.005, n))
    idx = pd.date_range(start, periods=n, freq="1h", tz="UTC", name="ts")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1e5}, index=idx)


def no_fail_fees(**kw) -> FeesCfg:
    return FeesCfg(**{"failure_probability": 0.0, **kw})


@pytest.fixture
def portfolio():
    def build(risk_kw=None, fee_kw=None, seed=0):
        risk = RiskManager(RiskCfg(**(risk_kw or {})))
        costs = CostModel(no_fail_fees(**(fee_kw or {})), risk.cfg.max_slippage_bps, random.Random(seed))
        return Portfolio(risk, costs, Journal(":memory:"))
    return build


DEEP = Market(price=1.0, liquidity_usd=10_000_000, dex_id="raydium", sol_price_usd=150.0)

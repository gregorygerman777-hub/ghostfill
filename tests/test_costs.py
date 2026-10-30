import random

import pytest

from ghostfill.config import FeesCfg
from ghostfill.costs import LAMPORTS_PER_SOL, CostModel


def model(seed=0, max_slip=10_000, **kw):
    fees = FeesCfg(**{"failure_probability": 0.0, "base_slippage_bps": 0.0, **kw})
    return CostModel(fees, max_slip, random.Random(seed))


def test_buy_cost_components_add_up():
    m = model(default_pool_fee_bps=30, platform_fee_bps=10, base_fee_lamports=5000, priority_fee_lamports=95_000)
    f = m.simulate("buy", 2.0, 2_000_000, 100.0, "unknown", spend_usd=1000)
    assert f.ok
    assert f.pool_fee_usd == pytest.approx(3.0)
    assert f.platform_fee_usd == pytest.approx(1.0)
    assert f.network_fee_usd == pytest.approx(100_000 / LAMPORTS_PER_SOL * 100)
    # impact: 1000 / (2e6 / 2) = 10 bps
    assert f.slippage_bps == pytest.approx(10.0)
    assert f.fill_price == pytest.approx(2.0 * 1.001)
    assert f.qty == pytest.approx(1000 / (2.0 * 1.001))
    assert f.cash_delta == pytest.approx(-(1000 + 3 + 1 + f.network_fee_usd))


def test_pool_fee_per_dex():
    m = model(default_pool_fee_bps=30, pool_fee_bps_by_dex={"raydium": 25})
    assert m.pool_fee_bps("raydium") == 25
    assert m.pool_fee_bps("orca") == 30


def test_sell_receives_less_than_quote():
    m = model(default_pool_fee_bps=25)
    f = m.simulate("sell", 1.0, 1_000_000, 150.0, qty=1000)
    assert f.ok and f.fill_price < 1.0
    assert f.cash_delta < 1000 - f.pool_fee_usd + 1e-9
    assert f.slippage_usd == pytest.approx(1000 * (1.0 - f.fill_price))


def test_slippage_grows_with_size_relative_to_liquidity():
    m = model()
    small = m.slippage_frac("buy", 1_000, 1_000_000)
    big = m.slippage_frac("buy", 10_000, 1_000_000)
    thin = m.slippage_frac("buy", 1_000, 100_000)
    assert big == pytest.approx(10 * small)
    assert thin == pytest.approx(10 * small)
    assert m.slippage_frac("sell", 10_000, 1_000_000) < big  # sell impact saturates


def test_base_slippage_added():
    m = model(base_slippage_bps=10)
    assert m.slippage_frac("buy", 0.0001, 1e12) == pytest.approx(0.001, rel=1e-6)


def test_max_slippage_rejects_without_fees():
    m = model(max_slip=100)
    f = m.simulate("buy", 1.0, 100_000, 150.0, spend_usd=1000)  # 200 bps
    assert not f.ok and "slippage" in f.reason
    assert f.cash_delta == 0 and f.fees_usd == 0


def test_failed_transaction_pays_network_fee_only():
    m = model(failure_probability=0.999999)
    f = m.simulate("buy", 1.0, 1e7, 200.0, spend_usd=1000)
    assert not f.ok and f.reason == "simulated transaction failure"
    assert f.qty == 0 and f.pool_fee_usd == 0
    assert f.cash_delta == pytest.approx(-m.network_fee_usd(200.0)) and f.cash_delta < 0


def test_failure_rate_matches_probability():
    m = model(seed=42, failure_probability=0.2)
    fails = sum(not m.simulate("buy", 1.0, 1e7, 150.0, spend_usd=100).ok for _ in range(5000))
    assert 0.17 < fails / 5000 < 0.23


def test_round_trip_always_loses_on_flat_price():
    m = model(default_pool_fee_bps=25, base_slippage_bps=5)
    buy = m.simulate("buy", 1.0, 1e6, 150.0, spend_usd=1000)
    sell = m.simulate("sell", 1.0, 1e6, 150.0, qty=buy.qty)
    assert buy.cash_delta + sell.cash_delta < 0


def test_invalid_quote_rejected():
    m = model()
    assert not m.simulate("buy", 0.0, 1e6, 150.0, spend_usd=10).ok
    assert not m.simulate("buy", 1.0, 0.0, 150.0, spend_usd=10).ok
    with pytest.raises(ValueError):
        m.simulate("short", 1.0, 1e6, 150.0, spend_usd=10)

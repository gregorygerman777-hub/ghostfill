import math

import numpy as np
import pandas as pd
import pytest

from conftest import BONK, JUP, make_settings, ohlcv
from ghostfill.backtest import (LIMITATIONS, BacktestInput, compute_metrics, format_walk_forward, run_backtest,
                               walk_forward)
from ghostfill.strategies import MovingAverageCrossover


def inp(n=600, liq=5_000_000):
    return BacktestInput({JUP: ohlcv(n, seed=11), BONK: ohlcv(n, seed=12)}, {JUP: liq, BONK: liq},
                         {JUP: "orca", BONK: "raydium"}, 150.0, {JUP: "JUP", BONK: "BONK"})


def test_metrics_on_known_curve():
    idx = pd.date_range("2026-01-01", periods=5, freq="1D", tz="UTC")
    eq = pd.Series([100.0, 110.0, 99.0, 120.0, 108.0], index=idx)
    trades = pd.DataFrame({"pnl_usd": [10.0, -5.0, 20.0, -5.0]})
    m = compute_metrics(eq, trades, 100.0, "1d", 3.5)
    assert m["total_return_pct"] == pytest.approx(8.0)
    assert m["max_drawdown_pct"] == pytest.approx(-10.0)
    assert m["win_rate_pct"] == 50.0
    assert m["profit_factor"] == pytest.approx(3.0)
    assert m["trades"] == 4 and m["total_fees_usd"] == 3.5
    years = 4 / 365.25
    assert m["cagr_pct"] == pytest.approx(100 * (1.08 ** (1 / years) - 1))
    assert m["sharpe"] is not None and not math.isnan(m["sharpe"])


def test_backtest_runs_and_charges_costs():
    s = make_settings()
    res = run_backtest(inp(), MovingAverageCrossover(fast=5, slow=20), s)
    assert res.metrics["trades"] > 5
    assert res.total_fees_usd > 0 and res.total_slippage_usd > 0
    assert len(res.equity) == 600
    assert res.trades["exit_reason"].isin({"signal", "stop_loss", "take_profit", "end_of_backtest"}).all()


def test_flat_market_loses_money_after_costs():
    """With no price movement every round trip pays fees and slippage: no free profit."""
    data = inp()
    for m in data.bars:
        df = data.bars[m]
        rng = np.random.default_rng(0)
        wiggle = 1 + rng.normal(0, 1e-4, len(df))  # crossovers happen, but no trend to capture
        for c in ("open", "high", "low", "close"):
            df[c] = wiggle
    res = run_backtest(data, MovingAverageCrossover(fast=3, slow=10), make_settings())
    assert res.metrics["trades"] > 0
    assert res.equity.iloc[-1] < make_settings().risk.starting_equity_usd


def test_signal_executes_next_bar_open():
    s = make_settings(fees={"failure_probability": 0.0})
    res = run_backtest(inp(), MovingAverageCrossover(fast=5, slow=20), s)
    df = inp().bars[JUP]
    sig = MovingAverageCrossover(fast=5, slow=20).generate_signals(df)
    first = res.trades[res.trades["mint"] == JUP].iloc[0]
    entry_ts = pd.Timestamp(first["entry_ts"])
    prev = df.index[df.index.get_loc(entry_ts) - 1]
    assert sig.loc[prev] == 1.0  # decided on the previous close
    assert first["entry_price"] > df.loc[entry_ts, "open"]  # filled at next open plus slippage


def test_open_positions_liquidated_at_end():
    res = run_backtest(inp(), MovingAverageCrossover(fast=5, slow=20), make_settings())
    assert (res.trades["status"] == "closed").all()


def test_walk_forward_reports_folds_and_flags_degradation():
    s = make_settings(strategy={"name": "ma_crossover", "params": {"fast": 5, "slow": 20},
                                "grid": {"fast": [3, 5], "slow": [20, 40]}},
                      walk_forward={"splits": 3, "in_sample_fraction": 0.7, "degradation_ratio": 0.5})
    rep = walk_forward(inp(900), s)
    assert len(rep.folds) == 3
    for f in rep.folds:
        assert f.oos_start > f.is_start
        assert f.params["fast"] in (3, 5) and f.params["slow"] in (20, 40)
    text = format_walk_forward(rep)
    assert "WALK-FORWARD" in text
    # Random-walk data has no edge: either IS is not positive, or OOS degrades. Either way it is flagged.
    assert rep.flagged and ("FLAGGED" in text)


def test_flag_rule_triggers_on_is_oos_gap(monkeypatch):
    import ghostfill.backtest as bt

    def fake_run(inp, strat, settings, start=None, end=None, seed=None):
        good = start is not None and seed is not None and seed < settings.seed + 100
        m = {"sharpe": 3.0 if good else 0.2, "total_return_pct": 5.0 if good else -1.0}
        return type("R", (), {"metrics": m})()

    monkeypatch.setattr(bt, "run_backtest", fake_run)
    rep = bt.walk_forward(inp(600), make_settings())
    assert rep.flagged
    assert any("below" in r for r in rep.flag_reasons)
    assert any("negative out of sample" in r for r in rep.flag_reasons)


def test_limitations_text_covers_required_points():
    t = " ".join(LIMITATIONS.lower().split())
    assert "survivorship bias" in t and "liquidity history" in t and "mev" in t
    assert "lose money after costs" in t

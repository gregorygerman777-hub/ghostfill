from datetime import timedelta

import pytest

from conftest import BONK, DEEP, JUP, NOW
from ghostfill.config import RiskCfg
from ghostfill.portfolio import Market
from ghostfill.risk import RiskManager

OTHER = "So11111111111111111111111111111111111111112"


def test_position_size_is_percent_of_equity(portfolio):
    pf = portfolio({"position_size_pct": 10, "starting_equity_usd": 10_000})
    f = pf.try_enter(JUP, NOW, DEEP)
    assert f.ok
    assert f.trade_value_usd == pytest.approx(1_000, rel=1e-9)
    # the second entry sizes off current equity, which is now lower by the fees paid
    equity = pf.equity()
    f2 = pf.try_enter(BONK, NOW, DEEP)
    assert f2.trade_value_usd == pytest.approx(0.10 * equity)
    assert f2.trade_value_usd < 1_000


def test_max_open_positions_enforced(portfolio):
    pf = portfolio({"max_open_positions": 2, "position_size_pct": 10})
    assert pf.try_enter(JUP, NOW, DEEP).ok
    assert pf.try_enter(BONK, NOW, DEEP).ok
    assert pf.try_enter(OTHER, NOW, DEEP) is None
    assert len(pf.positions) == 2
    rej = pf.journal.conn.execute("SELECT stage, reason FROM rejections").fetchall()
    assert rej[-1]["stage"] == "risk" and "max open positions" in rej[-1]["reason"]


def test_no_duplicate_position(portfolio):
    pf = portfolio()
    assert pf.try_enter(JUP, NOW, DEEP).ok
    assert pf.try_enter(JUP, NOW, DEEP) is None


def test_stop_loss_and_take_profit_levels():
    r = RiskManager(RiskCfg(stop_loss_pct=10, take_profit_pct=20))
    assert r.exit_trigger(100, 95, 105) is None
    assert r.exit_trigger(100, 89, 101) == ("stop_loss", 90)
    assert r.exit_trigger(100, 95, 125) == ("take_profit", 120)
    # gap through the stop: exit at the bar's high, not the better stop level
    assert r.exit_trigger(100, 70, 80) == ("stop_loss", 80)
    # both touched in one bar: assume the stop hit first
    assert r.exit_trigger(100, 85, 130)[0] == "stop_loss"


def test_stop_loss_closes_position_and_blocks_reentry(portfolio):
    pf = portfolio({"stop_loss_pct": 5})
    pf.try_enter(JUP, NOW, DEEP)
    entry = pf.positions[JUP].entry_price
    f = pf.check_stops(JUP, NOW, entry * 0.90, entry * 0.99, DEEP)
    assert f.ok and JUP not in pf.positions
    trade = pf.journal.closed_trades()[0]
    assert trade["exit_reason"] == "stop_loss" and trade["pnl_usd"] < 0
    assert not pf.wants_order(JUP, 1.0)  # still long signal: no re-entry
    pf.wants_order(JUP, 0.0)             # signal resets
    assert pf.wants_order(JUP, 1.0)


def test_take_profit_closes_position(portfolio):
    pf = portfolio({"take_profit_pct": 10})
    pf.try_enter(JUP, NOW, DEEP)
    entry = pf.positions[JUP].entry_price
    f = pf.check_stops(JUP, NOW, entry, entry * 1.2, DEEP)
    assert f.ok
    assert pf.journal.closed_trades()[0]["exit_reason"] == "take_profit"


def test_daily_loss_halt_latches_and_resets_next_day():
    r = RiskManager(RiskCfg(daily_loss_limit_pct=3))
    assert not r.observe_equity(NOW, 10_000)
    assert not r.observe_equity(NOW, 9_750)
    assert r.observe_equity(NOW, 9_700)          # -3.0%
    assert r.observe_equity(NOW, 10_100)         # recovery does not unlatch
    assert "daily loss" in r.check_entry(open_positions=0, already_held=False, cash=1e6, cost_estimate=1)
    assert not r.observe_equity(NOW + timedelta(days=1), 10_100)


def test_daily_halt_blocks_entries_in_portfolio(portfolio):
    pf = portfolio({"daily_loss_limit_pct": 2})
    pf.observe(NOW)
    pf.cash -= 300  # simulate a 3% loss
    assert pf.observe(NOW)
    assert pf.try_enter(JUP, NOW, DEEP) is None
    assert "daily loss" in pf.journal.conn.execute("SELECT reason FROM rejections").fetchone()[0]


def test_max_slippage_blocks_fill(portfolio):
    pf = portfolio({"max_slippage_bps": 50, "position_size_pct": 10})
    thin = Market(price=1.0, liquidity_usd=50_000, dex_id="", sol_price_usd=150.0)  # $1000 into $25k reserve = 400 bps
    f = pf.try_enter(JUP, NOW, thin)
    assert not f.ok and "slippage" in f.reason
    assert JUP not in pf.positions
    assert pf.cash == pf.risk.cfg.starting_equity_usd  # nothing sent, nothing paid


def test_insufficient_cash(portfolio):
    pf = portfolio({"position_size_pct": 45, "max_open_positions": 3})
    assert pf.try_enter(JUP, NOW, DEEP).ok
    assert pf.try_enter(BONK, NOW, DEEP).ok  # leaves ~10% cash, less than a 45% position
    assert pf.try_enter(OTHER, NOW, DEEP) is None
    assert "insufficient simulated cash" in pf.journal.conn.execute(
        "SELECT reason FROM rejections ORDER BY id DESC").fetchone()[0]


def test_state_survives_restart(tmp_path):
    import random

    from ghostfill.costs import CostModel
    from ghostfill.journal import Journal
    from ghostfill.portfolio import Portfolio
    from conftest import no_fail_fees

    db = str(tmp_path / "j.sqlite3")

    def build():
        risk = RiskManager(RiskCfg())
        return Portfolio(risk, CostModel(no_fail_fees(), risk.cfg.max_slippage_bps, random.Random(0)), Journal(db))

    pf = build()
    pf.try_enter(JUP, NOW, DEEP)
    pf.observe(NOW)
    cash, qty = pf.cash, pf.positions[JUP].qty
    pf.journal.close()

    pf2 = build()
    assert pf2.cash == pytest.approx(cash)
    assert pf2.positions[JUP].qty == pytest.approx(qty)
    assert pf2.risk.daily is not None
    f = pf2.try_exit(JUP, NOW, DEEP, "signal")
    assert f.ok and len(pf2.journal.closed_trades()) == 1

from ghostfill.config import ReadinessCfg
from ghostfill.readiness import evaluate, max_drawdown_pct

CFG = ReadinessCfg(min_paper_days=30, min_closed_trades=50, max_drawdown_pct=10)
OK_WF = {"flagged": False, "flag_reasons": [], "mean_oos_sharpe": 1.2}


def run(**kw):
    args = dict(paper_days=31, start_equity=10_000, equity_now=10_500, equity_history=[10_000, 10_200],
                closed_trades=60, validation=OK_WF)
    args.update(kw)
    return evaluate(CFG, **args)


def gate(r, key):
    return next(g for g in r["gates"] if g["key"] == key)


def test_all_gates_pass_only_with_full_evidence():
    r = run()
    assert r["ready"] and r["passed"] == r["total"] == 5


def test_each_gate_blocks_readiness():
    assert not gate(run(paper_days=10), "days")["passed"]
    assert not gate(run(closed_trades=49), "trades")["passed"]
    assert not gate(run(equity_now=9_999), "profit")["passed"]
    assert not gate(run(closed_trades=0), "profit")["passed"]  # no trades: no evidence of profit
    assert not gate(run(equity_history=[10_000, 12_000, 10_500]), "drawdown")["passed"]  # -12.5%
    assert not gate(run(validation={"flagged": True, "flag_reasons": ["x"], "mean_oos_sharpe": -1}), "walkforward")["passed"]
    assert not gate(run(validation=None), "walkforward")["passed"]
    for kw in ({"paper_days": 10}, {"closed_trades": 49}, {"validation": None}):
        assert not run(**kw)["ready"]


def test_max_drawdown():
    assert max_drawdown_pct([100, 120, 90, 130]) == -25.0
    assert max_drawdown_pct([100, 101, 102]) == 0.0

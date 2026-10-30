"""Live-readiness gates: honest evidence checks over the paper record.

Ghostfill has no live mode. A strategy that passes every gate has only earned a
closer look; it is still not a promise of profit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import ReadinessCfg


@dataclass
class Gate:
    key: str
    name: str
    value: float | None  # current measurement
    target: float  # threshold
    progress: float  # 0..1 for the meter
    passed: bool
    detail: str


def max_drawdown_pct(equity: list[float]) -> float:
    peak, worst = float("-inf"), 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            worst = min(worst, 100.0 * (e / peak - 1))
    return worst


def evaluate(
    cfg: ReadinessCfg,
    *,
    paper_days: float,
    start_equity: float,
    equity_now: float,
    equity_history: list[float],
    closed_trades: int,
    validation: dict | None,
) -> dict:
    pnl_pct = 100.0 * (equity_now / start_equity - 1) if start_equity else 0.0
    dd = max_drawdown_pct(equity_history + [equity_now])
    clamp = lambda x: max(0.0, min(1.0, x))
    gates = [
        Gate("days", "Paper track record", paper_days, cfg.min_paper_days, clamp(paper_days / cfg.min_paper_days),
             paper_days >= cfg.min_paper_days, f"{paper_days:.1f} of {cfg.min_paper_days:g} days"),
        Gate("trades", "Closed trades", closed_trades, cfg.min_closed_trades,
             clamp(closed_trades / cfg.min_closed_trades), closed_trades >= cfg.min_closed_trades,
             f"{closed_trades} of {cfg.min_closed_trades} trades"),
        Gate("profit", "Profit after all costs", pnl_pct, 0.0, 1.0 if pnl_pct > 0 else 0.0,
             pnl_pct > 0 and closed_trades > 0,
             f"{pnl_pct:+.2f}% net" + ("" if closed_trades else " (no closed trades yet)")),
        Gate("drawdown", "Max drawdown", dd, -cfg.max_drawdown_pct,
             clamp(1 - abs(dd) / cfg.max_drawdown_pct), abs(dd) <= cfg.max_drawdown_pct,
             f"{dd:.2f}% (limit -{cfg.max_drawdown_pct:g}%)"),
    ]
    if validation is None:
        gates.append(Gate("walkforward", "Walk-forward backtest", None, 0, 0.0, False, "not run yet"))
    else:
        oos = validation.get("mean_oos_sharpe")
        gates.append(Gate(
            "walkforward", "Walk-forward backtest", oos, 0, 0.0 if validation["flagged"] else 1.0,
            not validation["flagged"],
            ("flagged: " + "; ".join(validation["flag_reasons"])) if validation["flagged"]
            else f"not flagged (mean OOS Sharpe {oos:.2f})",
        ))
    passed = sum(g.passed for g in gates)
    return {"ready": passed == len(gates), "passed": passed, "total": len(gates),
            "gates": [asdict(g) for g in gates]}

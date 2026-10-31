"""Strategy research: run every strategy family through the same walk-forward test
the readiness gate uses, across bar intervals, and rank the results.

Ranking is by out-of-sample results only. A strategy that is flagged here would
fail the readiness gate.
"""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path

from .backtest import BacktestInput, run_backtest, walk_forward
from .config import Settings, StrategyCfg
from .strategies import build_strategy

GRIDS: dict[str, dict[str, list]] = {
    "ma_crossover": {"fast": [6, 12, 24], "slow": [48, 96]},
    "zscore_reversion": {"window": [24, 48, 96], "entry_z": [1.5, 2.0, 2.5], "exit_z": [0.0]},
    "donchian_breakout": {"entry": [20, 55], "exit": [10, 20]},
    "rsi_pullback": {"trend": [50, 100, 200], "rsi": [14], "entry": [35, 40, 45], "exit": [55, 65]},
}


@dataclass
class ResearchRow:
    strategy: str
    interval: str
    bars: int
    default_params: dict
    full_return_pct: float | None
    full_sharpe: float | None
    full_trades: int
    full_fees_usd: float
    is_sharpe: float | None
    oos_sharpe: float | None
    oos_return_pct: float
    flagged: bool
    flag_reasons: list[str]


def _first_valid(name: str, grid: dict[str, list]) -> dict:
    keys = sorted(grid)
    for values in itertools.product(*(grid[k] for k in keys)):
        params = dict(zip(keys, values))
        try:
            build_strategy(name, params)
            return params
        except ValueError:
            continue
    raise ValueError(f"no valid parameters in grid for {name}")


def research(inp: BacktestInput, settings: Settings, interval: str, grids: dict | None = None) -> list[ResearchRow]:
    rows = []
    for name, grid in (grids or GRIDS).items():
        params = _first_valid(name, grid)
        s = settings.model_copy(update={
            "strategy": StrategyCfg(name=name, params=params, grid=grid),
            "data": settings.data.model_copy(update={"interval": interval}),
        })
        full = run_backtest(inp, build_strategy(name, params), s)
        wf = walk_forward(inp, s)
        rows.append(ResearchRow(
            name, interval, max(len(df) for df in inp.bars.values()), params,
            full.metrics.get("total_return_pct"), full.metrics.get("sharpe"), int(full.metrics.get("trades", 0)),
            full.total_fees_usd, wf.mean_is_sharpe, wf.mean_oos_sharpe, wf.mean_oos_return_pct, wf.flagged,
            wf.flag_reasons,
        ))
    return rows


def _score(r: ResearchRow) -> tuple:
    oos = r.oos_sharpe if r.oos_sharpe is not None and not math.isnan(r.oos_sharpe) else -math.inf
    return (not r.flagged, oos)


def format_table(rows: list[ResearchRow]) -> str:
    f = lambda v, spec=".2f": "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else format(v, spec)
    out = ["RESEARCH (ranked by out-of-sample walk-forward results)",
           f"  {'strategy':<18} {'bars':<5} {'full ret':>9} {'trades':>6} {'fees':>9} {'IS Sharpe':>9} {'OOS Sharpe':>10} {'OOS ret':>8}  verdict"]
    for r in sorted(rows, key=_score, reverse=True):
        out.append(
            f"  {r.strategy:<18} {r.interval:<5} {f(r.full_return_pct, '+.2f') + '%':>9} {r.full_trades:>6} "
            f"{'$' + f(r.full_fees_usd, ',.0f'):>9} {f(r.is_sharpe):>9} {f(r.oos_sharpe):>10} "
            f"{f(r.oos_return_pct, '+.2f') + '%':>8}  {'FLAGGED' if r.flagged else 'holds up out of sample'}"
        )
    passed = [r for r in rows if not r.flagged]
    out.append("")
    out.append(f"  {len(passed)} of {len(rows)} strategy/interval combinations were not flagged."
               + ("" if passed else " None has shown an out-of-sample edge on this data."))
    return "\n".join(out)


def save(rows: list[ResearchRow], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([r.__dict__ for r in rows], indent=2, default=str))

"""Event-driven backtester using the same Portfolio, CostModel and RiskManager as the paper engine.

Execution rule: a signal computed on the close of bar t is acted on at the
open of bar t+1. Stops and take profits are checked against each bar's
low/high. Open positions are liquidated (with costs) at the last bar.
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Settings
from .costs import CostModel
from .data.sources import INTERVAL_SECONDS
from .journal import Journal
from .portfolio import Market, Portfolio
from .risk import RiskManager
from .strategies import Strategy, build_strategy

LIMITATIONS = """\
LIMITATIONS (read before trusting any number above)
  * Survivorship bias: the token list is chosen today, from tokens that still
    exist and still trade. Tokens that rugged or died are absent, which
    flatters every result.
  * Incomplete liquidity history: historical pool liquidity is not available,
    so slippage uses today's liquidity snapshot for every bar. Liquidity in the
    past may have been much lower, making real costs higher.
  * No MEV modeling: sandwich attacks, frontrunning and priority-fee auctions
    are not simulated. Real fills on Solana are often worse than modeled.
  * Bars come from a third-party aggregator (GeckoTerminal) for one pool per
    token; other pools and intra-bar price paths are not seen.
  * Past results do not predict future results. Most strategies lose money
    after costs.
"""


@dataclass
class BacktestInput:
    """Per-mint bars plus the cost context the simulator needs."""

    bars: dict[str, pd.DataFrame]  # mint -> OHLCV, UTC DatetimeIndex
    liquidity_usd: dict[str, float]
    dex_id: dict[str, str]
    sol_price: pd.Series | float  # SOL/USD per timestamp, or a constant
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: pd.DataFrame
    total_fees_usd: float
    total_slippage_usd: float
    failed_tx: int  # simulated transaction failures (network fee paid)
    slippage_rejections: int  # orders not sent because slippage would exceed the max
    metrics: dict[str, float | int | None]


def _sol_at(sol: pd.Series | float, ts: pd.Timestamp) -> float:
    if isinstance(sol, (int, float)):
        return float(sol)
    v = sol.asof(ts)
    if pd.isna(v):
        v = sol.iloc[0]
    return float(v)


def run_backtest(
    inp: BacktestInput,
    strategy: Strategy,
    settings: Settings,
    trade_start: pd.Timestamp | None = None,
    trade_end: pd.Timestamp | None = None,
    seed: int | None = None,
) -> BacktestResult:
    """Simulate trading between trade_start and trade_end (inclusive). Bars before
    trade_start are used only as indicator history."""
    rng = random.Random(settings.seed if seed is None else seed)
    risk = RiskManager(settings.risk)
    costs = CostModel(settings.fees, settings.risk.max_slippage_bps, rng)
    journal = Journal(":memory:")
    pf = Portfolio(risk, costs, journal, inp.labels)

    bars = {m: df.loc[:trade_end] if trade_end is not None else df for m, df in inp.bars.items()}
    signals = {m: strategy.generate_signals(df) for m, df in bars.items()}
    index = sorted(set().union(*[df.index for df in bars.values()]))
    if trade_start is not None:
        index = [t for t in index if t >= trade_start]
    pending: dict[str, float] = {}
    equity_points: list[tuple[pd.Timestamp, float]] = []

    for ts in index:
        pf.observe(ts.to_pydatetime())
        for mint, df in bars.items():
            if ts not in df.index:
                continue
            row = df.loc[ts]
            mkt = Market(float(row["open"]), inp.liquidity_usd[mint], inp.dex_id.get(mint, ""), _sol_at(inp.sol_price, ts))
            target = pending.pop(mint, None)
            if target is not None:
                if target > 0 and mint not in pf.positions:
                    pf.try_enter(mint, ts.to_pydatetime(), mkt)
                elif target <= 0 and mint in pf.positions:
                    pf.try_exit(mint, ts.to_pydatetime(), mkt, "signal")
            if mint in pf.positions:
                pf.check_stops(mint, ts.to_pydatetime(), float(row["low"]), float(row["high"]), mkt)
            pf.mark(mint, float(row["close"]), ts.to_pydatetime(), persist=False)
            sig = signals[mint].get(ts)
            if sig is not None and not pd.isna(sig) and pf.wants_order(mint, float(sig)):
                pending[mint] = float(sig)
        pf.observe(ts.to_pydatetime())
        equity_points.append((ts, pf.equity()))

    if index:
        last = index[-1]
        for mint in list(pf.positions):
            df = bars[mint]
            px = float(df.loc[:last, "close"].iloc[-1])
            pf.try_exit(mint, last.to_pydatetime(),
                        Market(px, inp.liquidity_usd[mint], inp.dex_id.get(mint, ""), _sol_at(inp.sol_price, last)),
                        "end_of_backtest")
        equity_points[-1] = (last, pf.equity())

    equity = pd.Series({t: e for t, e in equity_points}, dtype="float64", name="equity")
    trades = pd.DataFrame([dict(r) for r in journal.closed_trades()])
    fills = pd.read_sql_query("SELECT * FROM fills", journal.conn)
    result = BacktestResult(
        equity=equity,
        trades=trades,
        total_fees_usd=float((fills["pool_fee_usd"] + fills["platform_fee_usd"] + fills["network_fee_usd"]).sum())
        if len(fills) else 0.0,
        total_slippage_usd=float(fills["slippage_usd"].sum()) if len(fills) else 0.0,
        failed_tx=int((fills["reason"] == "simulated transaction failure").sum()) if len(fills) else 0,
        slippage_rejections=int(fills["reason"].str.startswith("slippage").sum()) if len(fills) else 0,
        metrics={},
    )
    result.metrics = compute_metrics(equity, trades, settings.risk.starting_equity_usd, settings.data.interval,
                                     result.total_fees_usd)
    journal.close()
    return result


def compute_metrics(
    equity: pd.Series, trades: pd.DataFrame, start_equity: float, interval: str, total_fees: float
) -> dict[str, float | int | None]:
    m: dict[str, float | int | None] = {"trades": int(len(trades)), "total_fees_usd": total_fees}
    if equity.empty:
        return {**m, "total_return_pct": None, "cagr_pct": None, "sharpe": None, "max_drawdown_pct": None,
                "win_rate_pct": None, "profit_factor": None}
    curve = pd.concat([pd.Series([start_equity]), equity.reset_index(drop=True)])
    end = float(equity.iloc[-1])
    m["total_return_pct"] = 100.0 * (end / start_equity - 1)
    years = (equity.index[-1] - equity.index[0]).total_seconds() / (365.25 * 86_400)
    m["cagr_pct"] = 100.0 * ((end / start_equity) ** (1 / years) - 1) if years > 0 and end > 0 else None
    rets = curve.pct_change().dropna()
    sd = float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
    bars_per_year = 365.25 * 86_400 / INTERVAL_SECONDS[interval]
    m["sharpe"] = float(rets.mean() / sd * math.sqrt(bars_per_year)) if sd > 0 else None
    m["max_drawdown_pct"] = float(100.0 * (curve / curve.cummax() - 1).min())
    if len(trades):
        pnl = trades["pnl_usd"].astype(float)
        wins, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
        m["win_rate_pct"] = 100.0 * float((pnl > 0).mean())
        m["profit_factor"] = float(wins / losses) if losses > 0 else (None if wins == 0 else float("inf"))
    else:
        m["win_rate_pct"] = None
        m["profit_factor"] = None
    return m


# walk-forward ---------------------------------------------------------------------


@dataclass
class FoldResult:
    fold: int
    is_start: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp
    params: dict
    is_metrics: dict
    oos_metrics: dict


@dataclass
class WalkForwardReport:
    folds: list[FoldResult]
    mean_is_sharpe: float | None
    mean_oos_sharpe: float | None
    mean_is_return_pct: float
    mean_oos_return_pct: float
    flagged: bool
    flag_reasons: list[str]


def _grid(settings: Settings) -> list[dict]:
    grid = settings.strategy.grid
    if not grid:
        return [dict(settings.strategy.params)]
    keys = sorted(grid)
    combos = []
    for values in itertools.product(*(grid[k] for k in keys)):
        params = {**settings.strategy.params, **dict(zip(keys, values))}
        try:
            build_strategy(settings.strategy.name, params)
        except ValueError:
            continue  # e.g. fast >= slow
        combos.append(params)
    return combos


def _score(metrics: dict) -> float:
    s = metrics.get("sharpe")
    return -math.inf if s is None else float(s)


def _mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None and not math.isnan(v)]
    return float(np.mean(vals)) if vals else None


def walk_forward(inp: BacktestInput, settings: Settings) -> WalkForwardReport:
    wf = settings.walk_forward
    index = sorted(set().union(*[df.index for df in inp.bars.values()]))
    n = len(index)
    size = n // wf.splits
    combos = _grid(settings)
    folds: list[FoldResult] = []
    for k in range(wf.splits):
        seg = index[k * size : (k + 1) * size if k < wf.splits - 1 else n]
        cut = int(len(seg) * wf.in_sample_fraction)
        if cut < 10 or len(seg) - cut < 5:
            continue
        is_start, is_end, oos_start, oos_end = seg[0], seg[cut - 1], seg[cut], seg[-1]
        best, best_m = None, None
        for params in combos:
            strat = build_strategy(settings.strategy.name, params)
            res = run_backtest(inp, strat, settings, is_start, is_end, seed=settings.seed + k)
            if best_m is None or _score(res.metrics) > _score(best_m):
                best, best_m = params, res.metrics
        oos = run_backtest(inp, build_strategy(settings.strategy.name, best), settings, oos_start, oos_end,
                           seed=settings.seed + 100 + k)
        folds.append(FoldResult(k + 1, is_start, oos_start, oos_end, best, best_m, oos.metrics))

    mis = _mean([f.is_metrics["sharpe"] for f in folds])
    moos = _mean([f.oos_metrics["sharpe"] for f in folds])
    ris = float(np.mean([f.is_metrics["total_return_pct"] or 0.0 for f in folds])) if folds else 0.0
    roos = float(np.mean([f.oos_metrics["total_return_pct"] or 0.0 for f in folds])) if folds else 0.0
    reasons = []
    if not folds:
        reasons.append("not enough data for walk-forward folds")
    if mis is not None and mis > 0 and (moos is None or moos < wf.degradation_ratio * mis):
        reasons.append(
            f"mean OOS Sharpe {moos if moos is not None else float('nan'):.2f} is below "
            f"{wf.degradation_ratio:.0%} of mean IS Sharpe {mis:.2f}"
        )
    if ris > 0 and roos < 0:
        reasons.append(f"in-sample return {ris:+.2f}% turns negative out of sample ({roos:+.2f}%)")
    if mis is None or mis <= 0:
        reasons.append("no positive in-sample Sharpe even after parameter search")
    return WalkForwardReport(folds, mis, moos, ris, roos, bool(reasons), reasons)


# report ---------------------------------------------------------------------------


def _fmt(v, spec=".2f", suffix=""):
    if v is None:
        return "n/a"
    if isinstance(v, float) and math.isinf(v):
        return "inf"
    return f"{v:{spec}}{suffix}"


def format_metrics(title: str, m: dict) -> str:
    return "\n".join([
        title,
        f"  CAGR            {_fmt(m.get('cagr_pct'), '+.2f', '%')}",
        f"  Total return    {_fmt(m.get('total_return_pct'), '+.2f', '%')}",
        f"  Sharpe          {_fmt(m.get('sharpe'))}",
        f"  Max drawdown    {_fmt(m.get('max_drawdown_pct'), '.2f', '%')}",
        f"  Win rate        {_fmt(m.get('win_rate_pct'), '.1f', '%')}",
        f"  Profit factor   {_fmt(m.get('profit_factor'))}",
        f"  Trades          {m.get('trades', 0)}",
        f"  Total fees      ${_fmt(m.get('total_fees_usd'), ',.2f')}",
    ])


def format_walk_forward(r: WalkForwardReport) -> str:
    lines = ["WALK-FORWARD VALIDATION",
             "  fold  OOS window                          params                         IS Sharpe  OOS Sharpe  IS ret   OOS ret"]
    for f in r.folds:
        params = ",".join(f"{k}={v}" for k, v in sorted(f.params.items()))
        lines.append(
            f"  {f.fold:<4}  {f.oos_start:%Y-%m-%d %H:%M} -> {f.oos_end:%Y-%m-%d %H:%M}  {params:<30} "
            f"{_fmt(f.is_metrics['sharpe']):>9}  {_fmt(f.oos_metrics['sharpe']):>10}  "
            f"{_fmt(f.is_metrics['total_return_pct'], '+.2f', '%'):>7}  {_fmt(f.oos_metrics['total_return_pct'], '+.2f', '%'):>7}"
        )
    lines.append(f"  mean IS Sharpe {_fmt(r.mean_is_sharpe)} | mean OOS Sharpe {_fmt(r.mean_oos_sharpe)} | "
                 f"mean IS return {r.mean_is_return_pct:+.2f}% | mean OOS return {r.mean_oos_return_pct:+.2f}%")
    if r.flagged:
        lines.append("  FLAGGED: " + "; ".join(r.flag_reasons))
    else:
        lines.append("  Not flagged (OOS held up relative to IS). This is not evidence of future profit.")
    return "\n".join(lines)

"""Simulated portfolio shared by the paper engine and the backtester.

Both paths go through the same CostModel and RiskManager, and record into the
same Journal schema (the backtester uses an in-memory database).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .costs import CostModel, Fill
from .journal import Journal
from .risk import DailyState, RiskManager


@dataclass
class Position:
    trade_id: int
    mint: str
    qty: float
    entry_price: float
    entry_cost_usd: float  # cash spent including fees


@dataclass
class Market:
    """What the simulator knows about a mint at one moment."""

    price: float
    liquidity_usd: float
    dex_id: str
    sol_price_usd: float


def _iso(ts: datetime) -> str:
    return ts.isoformat()


class Portfolio:
    def __init__(self, risk: RiskManager, costs: CostModel, journal: Journal, labels: dict[str, str] | None = None):
        self.risk = risk
        self.costs = costs
        self.journal = journal
        self.labels = labels or {}
        self.cash = risk.cfg.starting_equity_usd
        self.realized_pnl = 0.0
        self.positions: dict[str, Position] = {}
        self.marks: dict[str, float] = {}
        # Mints exited by stop/take-profit. No re-entry until the strategy's target drops to zero.
        self.stopped_out: set[str] = set()
        self._load()

    # persistence -------------------------------------------------------------
    def _load(self) -> None:
        j = self.journal
        cash = j.get_state("cash")
        if cash is None:
            j.set_state(cash=self.cash, realized_pnl=0.0, starting_equity=self.cash)
            return
        self.cash = float(cash)
        self.realized_pnl = float(j.get_state("realized_pnl", 0.0))
        for t in j.open_trades():
            self.positions[t["mint"]] = Position(t["id"], t["mint"], t["qty"], t["entry_price"], t["entry_cost_usd"])
        self.marks = {m: p for m, (p, _) in j.marks().items()}
        self.stopped_out = set(j.get_state("stopped_out", []))
        daily = j.get_state("daily")
        if daily:
            self.risk.daily = DailyState(date.fromisoformat(daily["day"]), float(daily["start_equity"]),
                                         bool(daily["halted"]))

    def _save(self) -> None:
        d = self.risk.daily
        self.journal.set_state(
            cash=self.cash,
            realized_pnl=self.realized_pnl,
            stopped_out=sorted(self.stopped_out),
            daily=None if d is None else {"day": d.day.isoformat(), "start_equity": d.start_equity, "halted": d.halted},
        )

    # accounting --------------------------------------------------------------
    def equity(self) -> float:
        return self.cash + sum(p.qty * self.marks.get(m, p.entry_price) for m, p in self.positions.items())

    def unrealized_pnl(self) -> float:
        return sum(p.qty * self.marks.get(m, p.entry_price) - p.entry_cost_usd for m, p in self.positions.items())

    def mark(self, mint: str, price: float, ts: datetime, persist: bool = True) -> None:
        self.marks[mint] = price
        if persist:
            self.journal.set_mark(mint, price, _iso(ts))

    def observe(self, ts: datetime) -> bool:
        halted = self.risk.observe_equity(ts, self.equity())
        self._save()
        return halted

    def _record_fill(self, ts: datetime, mint: str, f: Fill, trade_id: int | None) -> None:
        self.journal.insert(
            "fills", ts=_iso(ts), mint=mint, side=f.side, ok=int(f.ok), reason=f.reason,
            quoted_price=f.quoted_price, fill_price=f.fill_price, qty=f.qty, trade_value_usd=f.trade_value_usd,
            pool_fee_usd=f.pool_fee_usd, platform_fee_usd=f.platform_fee_usd, network_fee_usd=f.network_fee_usd,
            slippage_bps=f.slippage_bps, slippage_usd=f.slippage_usd, cash_delta=f.cash_delta, trade_id=trade_id,
        )
        if not f.ok:
            # Failed transactions still pay network fees.
            self.cash += f.cash_delta
            self.realized_pnl += f.cash_delta
            self.journal.reject(_iso(ts), mint, "fill", f"{f.side}: {f.reason}")

    # trading -----------------------------------------------------------------
    def wants_order(self, mint: str, target: float) -> bool:
        """True when the target differs from what is held and an order should be tried."""
        if target <= 0:
            self.stopped_out.discard(mint)
            return mint in self.positions
        return mint not in self.positions and mint not in self.stopped_out

    def try_enter(self, mint: str, ts: datetime, mkt: Market) -> Fill | None:
        spend = self.risk.entry_notional(self.equity())
        est = spend * (1 + (self.costs.pool_fee_bps(mkt.dex_id) + self.costs.fees.platform_fee_bps) / 1e4) \
            + self.costs.network_fee_usd(mkt.sol_price_usd)
        reason = self.risk.check_entry(
            open_positions=len(self.positions), already_held=mint in self.positions, cash=self.cash, cost_estimate=est
        )
        if reason:
            self.journal.reject(_iso(ts), mint, "risk", reason)
            return None
        f = self.costs.simulate("buy", mkt.price, mkt.liquidity_usd, mkt.sol_price_usd, mkt.dex_id, spend_usd=spend)
        if not f.ok:
            self._record_fill(ts, mint, f, None)
            self._save()
            return f
        cost = -f.cash_delta
        trade_id = self.journal.insert(
            "trades", mint=mint, label=self.labels.get(mint, ""), status="open", entry_ts=_iso(ts),
            entry_price=f.fill_price, qty=f.qty, entry_cost_usd=cost, entry_fees_usd=f.fees_usd,
            entry_slippage_usd=f.slippage_usd,
        )
        self._record_fill(ts, mint, f, trade_id)
        self.cash += f.cash_delta
        self.positions[mint] = Position(trade_id, mint, f.qty, f.fill_price, cost)
        self.mark(mint, mkt.price, ts, persist=self.journal.path != ":memory:")
        self._save()
        return f

    def try_exit(self, mint: str, ts: datetime, mkt: Market, reason: str) -> Fill | None:
        pos = self.positions.get(mint)
        if pos is None:
            return None
        f = self.costs.simulate("sell", mkt.price, mkt.liquidity_usd, mkt.sol_price_usd, mkt.dex_id, qty=pos.qty)
        if not f.ok:
            self._record_fill(ts, mint, f, pos.trade_id)
            self._save()
            return f
        self._record_fill(ts, mint, f, pos.trade_id)
        self.cash += f.cash_delta
        pnl = f.cash_delta - pos.entry_cost_usd
        self.realized_pnl += pnl
        self.journal.update_trade(
            pos.trade_id, status="closed", exit_ts=_iso(ts), exit_price=f.fill_price, exit_proceeds_usd=f.cash_delta,
            exit_fees_usd=f.fees_usd, exit_slippage_usd=f.slippage_usd, exit_reason=reason, pnl_usd=pnl,
            pnl_pct=100.0 * pnl / pos.entry_cost_usd,
        )
        del self.positions[mint]
        self._save()
        return f

    def check_stops(self, mint: str, ts: datetime, low: float, high: float, mkt: Market) -> Fill | None:
        """Exit at the stop or take-profit level if [low, high] crosses it."""
        pos = self.positions.get(mint)
        if pos is None:
            return None
        hit = self.risk.exit_trigger(pos.entry_price, low, high)
        if hit is None:
            return None
        reason, level = hit
        f = self.try_exit(mint, ts, Market(level, mkt.liquidity_usd, mkt.dex_id, mkt.sol_price_usd), reason)
        if f is not None and f.ok:
            self.stopped_out.add(mint)
            self._save()
        return f

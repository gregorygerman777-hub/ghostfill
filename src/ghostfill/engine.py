"""Paper engine: polls live mainnet data, runs the strategy on closed bars and
records simulated fills. It never signs or sends anything."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime


from .config import Settings
from .backtest import BacktestInput, walk_forward
from .costs import CostModel
from .data.sources import Snapshot, closed_bars, utcnow
from .data.stream import SlotStream
from .journal import Journal
from .market import MarketData
from .portfolio import Market, Portfolio
from .risk import RiskManager
from .screen import ScreenResult, screen_token
from .strategies import build_strategy

log = logging.getLogger(__name__)


class PaperEngine:
    def __init__(self, settings: Settings, market: MarketData, journal: Journal, stream: SlotStream | None = None):
        self.s = settings
        self.market = market
        self.journal = journal
        self.stream = stream
        self.labels = {t.mint: t.label for t in settings.tokens}
        self.portfolio = Portfolio(
            RiskManager(settings.risk),
            CostModel(settings.fees, settings.risk.max_slippage_bps, random.Random(settings.seed)),
            journal,
            self.labels,
        )
        self.strategy = build_strategy(settings.strategy.name, dict(settings.strategy.params))
        self.snapshots: dict[str, Snapshot] = {}
        self.screens: dict[str, tuple[ScreenResult, datetime]] = {}
        self.last_bar: dict[str, str] = journal.get_state("last_signal_bar", {}) or {}
        self.last_signals: dict[str, dict] = journal.get_state("last_signals", {}) or {}
        self._last_equity_at: datetime | None = None
        self.cycles: int = journal.get_state("cycles", 0) or 0
        journal.set_state(mode="PAPER", strategy=repr(self.strategy), interval=settings.data.interval,
                          tokens=[{"mint": t.mint, "label": t.label} for t in settings.tokens],
                          risk=settings.risk.model_dump(), readiness_cfg=settings.readiness.model_dump())

    # helpers ---------------------------------------------------------------
    def _mints(self) -> list[str]:
        mints = [t.mint for t in self.s.tokens]
        mints += [m for m in self.portfolio.positions if m not in mints]  # keep managing removed tokens
        return mints

    def _sol_price(self) -> float:
        sol = self.snapshots.get(self.s.sol_mint)
        return sol.price_usd if sol else 150.0

    def _market(self, snap: Snapshot) -> Market:
        return Market(snap.price_usd, snap.liquidity_usd, snap.dex_id, self._sol_price())

    async def _screen(self, mint: str, now: datetime) -> ScreenResult:
        cached = self.screens.get(mint)
        if cached:
            ttl = self.s.screen.retry_unavailable_seconds if cached[0].data_missing else self.s.screen.refresh_seconds
            if (now - cached[1]).total_seconds() < ttl:
                return cached[0]
        facts = await self.market.facts(mint, self.snapshots.get(mint))
        result = screen_token(facts, self.s.screen, now)
        previous = self.screens.get(mint)
        self.screens[mint] = (result, now)
        changed = previous is None or previous[0].passed != result.passed or previous[0].reasons != result.reasons
        if changed:  # the activity log shows verdict changes, not every identical re-check
            self.journal.insert("screen_log", ts=now.isoformat(), mint=mint, passed=int(result.passed),
                                detail="; ".join(result.reasons) or "all checks passed")
        self.journal.set_state(screens={
            m: {"passed": r.passed, "reasons": r.reasons, "metrics": r.metrics, "checked_at": t.isoformat()}
            for m, (r, t) in self.screens.items()
        })
        return result

    # ticks -----------------------------------------------------------------
    async def tick_snapshots(self) -> None:
        snaps = await self.market.snapshots(self._mints() + [self.s.sol_mint])
        now = utcnow()
        self.snapshots.update(snaps)
        for mint, snap in snaps.items():
            self.journal.insert(
                "snapshots", ts=now.isoformat(), mint=mint, price_usd=snap.price_usd,
                liquidity_usd=snap.liquidity_usd, volume_24h_usd=snap.volume_24h_usd,
                pool_address=snap.pool_address, dex_id=snap.dex_id, source=snap.source,
                fetched_at=snap.fetched_at.isoformat(),
            )
            if mint == self.s.sol_mint and mint not in self.labels:
                continue
            self.portfolio.mark(mint, snap.price_usd, now)
            if mint in self.portfolio.positions:
                self.portfolio.check_stops(mint, now, snap.price_usd, snap.price_usd, self._market(snap))
        self.portfolio.observe(now)
        self._record_equity(now)
        self.cycles += 1
        latest = max((s.fetched_at for s in snaps.values()), default=None)
        self.journal.set_state(
            last_data_ts=latest.isoformat() if latest else None,
            cycles=self.cycles,
            last_slot=self.stream.last_slot if self.stream else None,
            last_slot_at=self.stream.last_slot_at.isoformat() if self.stream and self.stream.last_slot_at else None,
        )

    async def tick_bars(self) -> None:
        interval = self.s.data.interval
        history: dict = {}
        for mint in self._mints():
            snap = self.snapshots.get(mint)
            if snap is None:
                continue
            df = await self.market.history(mint, snap.pool_address, interval, self.s.data.history_bars)
            bars = closed_bars(df, interval)
            if mint in self.labels:
                history[mint] = bars
            if len(bars) < self.strategy.warmup + 1:
                continue
            bar_ts = bars.index[-1].isoformat()
            self._store_chart(mint, bars)
            if self.last_bar.get(mint) == bar_ts and mint in self.last_signals:
                continue  # already acted on this bar (re-evaluating is harmless: orders only fire on a change)
            self.last_bar[mint] = bar_ts
            self.journal.set_state(last_signal_bar=self.last_bar)
            target = float(self.strategy.generate_signals(bars).iloc[-1])
            action = await self._act(mint, target, snap)
            now = utcnow()
            self.journal.insert("signals", ts=now.isoformat(), mint=mint, bar_ts=bar_ts, target=target, action=action)
            self.last_signals[mint] = {"bar_ts": bar_ts, "target": target, "action": action, "ts": now.isoformat()}
            self.journal.set_state(last_signals=self.last_signals)

        await self._maybe_validate(history)

    async def _maybe_validate(self, history: dict) -> None:
        """Re-run the walk-forward backtest on cached bars every validation_every_hours."""
        last = self.journal.get_state("validation")
        if last and (utcnow() - datetime.fromisoformat(last["ran_at"])).total_seconds() < \
                self.s.readiness.validation_every_hours * 3600:
            return
        usable = {m: df[["open", "high", "low", "close", "volume"]] for m, df in history.items()
                  if m in self.snapshots and len(df) > 200}
        if not usable:
            return
        inp = BacktestInput(usable, {m: self.snapshots[m].liquidity_usd for m in usable},
                            {m: self.snapshots[m].dex_id for m in usable}, self._sol_price(), self.labels)
        wf = await asyncio.to_thread(walk_forward, inp, self.s)
        self.journal.set_state(validation={
            "ran_at": utcnow().isoformat(), "flagged": wf.flagged, "flag_reasons": wf.flag_reasons,
            "mean_is_sharpe": wf.mean_is_sharpe, "mean_oos_sharpe": wf.mean_oos_sharpe,
            "mean_is_return_pct": wf.mean_is_return_pct, "mean_oos_return_pct": wf.mean_oos_return_pct,
            "folds": len(wf.folds), "bars": max(len(df) for df in usable.values()),
        })
        log.info("walk-forward validation: %s", "FLAGGED" if wf.flagged else "not flagged")

    async def _act(self, mint: str, target: float, snap: Snapshot) -> str:
        """Act on a target and return what happened, for the signal log."""
        held = mint in self.portfolio.positions
        if not self.portfolio.wants_order(mint, target):
            if target > 0 and not held:
                return "waiting for signal reset after stop/take-profit"
            return "hold" if held else "flat"
        now = utcnow()
        if target <= 0:
            f = self.portfolio.try_exit(mint, now, self._market(snap), "signal")
            return "exit" if f and f.ok else f"exit failed: {f.reason if f else 'no position'}"
        if mint not in self.labels:
            return "no entry: token removed from config"
        result = await self._screen(mint, now)
        if not result.passed:
            for reason in result.reasons:
                self.journal.reject(now.isoformat(), mint, "screen", reason)
            return "screen rejected: " + "; ".join(result.reasons)
        f = self.portfolio.try_enter(mint, now, self._market(snap))
        if f is None:
            return "risk rejected"
        return "enter" if f.ok else f"entry failed: {f.reason}"

    def _store_chart(self, mint: str, bars, n: int = 96) -> None:
        """Last n closed bars plus the strategy's indicator lines, for the dashboard."""
        sig = self.strategy.generate_signals(bars).iloc[-n:]
        tail = bars.iloc[-n:]
        lines = {k: [None if v != v else round(float(v), 12) for v in s.iloc[-n:]]
                 for k, s in self.strategy.indicators(bars).items()}
        charts = self.journal.get_state("charts", {}) or {}
        charts[mint] = {
            "ts": [t.isoformat() for t in tail.index],
            "close": [round(float(v), 12) for v in tail["close"]],
            "lines": lines,
            "target": [float(v) for v in sig],
            "interval": self.s.data.interval,
        }
        self.journal.set_state(charts=charts)

    def _record_equity(self, now: datetime) -> None:
        """Equity history, at most one row a minute."""
        if self._last_equity_at and (now - self._last_equity_at).total_seconds() < 60:
            return
        self._last_equity_at = now
        pf = self.portfolio
        self.journal.insert("equity", ts=now.isoformat(), equity_usd=pf.equity(), cash_usd=pf.cash,
                            realized_pnl_usd=pf.realized_pnl, unrealized_pnl_usd=pf.unrealized_pnl())

    # loop ------------------------------------------------------------------
    async def _every(self, seconds: float, tick, name: str) -> None:
        while True:
            try:
                await tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s tick failed; continuing", name)
            await asyncio.sleep(seconds)

    async def run(self) -> None:
        """Snapshots (marks, stops, daily halt) and bars (signals) run as separate loops,
        so slow OHLCV fetches never delay stop checks."""
        now_iso = utcnow().isoformat()
        self.journal.set_state(started_at=now_iso)
        if not self.journal.get_state("first_started_at"):
            self.journal.set_state(first_started_at=now_iso)  # start of the paper track record
        log.info("paper engine started: %s on %s, %d tokens, mode PAPER",
                 self.strategy, self.s.data.interval, len(self.s.tokens))
        snaps = asyncio.create_task(self._every(self.s.data.snapshot_seconds, self.tick_snapshots, "snapshot"))
        while not self.snapshots:  # bars need pool addresses from the first snapshot
            if snaps.done():
                await snaps
            await asyncio.sleep(1)
        bars = asyncio.create_task(self._every(self.s.data.ohlcv_refresh_seconds, self.tick_bars, "bars"))
        # Screen every token in the background so /status always shows current verdicts.
        screens = asyncio.create_task(self._every(min(60, self.s.screen.retry_unavailable_seconds),
                                                  self.tick_screens, "screen"))
        tasks = [snaps, bars, screens]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()

    async def tick_screens(self) -> None:
        now = utcnow()
        for t in self.s.tokens:
            if t.mint not in self.snapshots:
                continue
            result = await self._screen(t.mint, now)  # cached; only refetches when the TTL expires
            await self._retry_blocked_entry(t.mint, result)

    async def _retry_blocked_entry(self, mint: str, result: ScreenResult) -> None:
        """A long signal blocked only by missing data gets another try within the same bar
        once the screen passes, instead of waiting for the next bar."""
        sig = self.last_signals.get(mint)
        if not sig or sig["target"] <= 0 or not result.passed or "unavailable" not in sig["action"]:
            return
        if sig["bar_ts"] != self.last_bar.get(mint) or not self.portfolio.wants_order(mint, sig["target"]):
            return
        snap = self.snapshots[mint]
        action = await self._act(mint, sig["target"], snap)
        now = utcnow()
        self.journal.insert("signals", ts=now.isoformat(), mint=mint, bar_ts=sig["bar_ts"], target=sig["target"],
                            action=f"retry after data recovered: {action}")
        self.last_signals[mint] = {**sig, "action": f"retry after data recovered: {action}", "ts": now.isoformat()}
        self.journal.set_state(last_signals=self.last_signals)


def unrealized_from_rows(open_rows: list, marks: dict[str, tuple[float, str]]) -> list[dict]:
    """Shared by /status: value open positions at their last mark."""
    out = []
    for t in open_rows:
        mark = marks.get(t["mint"], (t["entry_price"], None))[0]
        value = t["qty"] * mark
        out.append({
            "trade_id": t["id"], "mint": t["mint"], "label": t["label"], "qty": t["qty"],
            "entry_ts": t["entry_ts"], "entry_price": t["entry_price"], "mark_price": mark,
            "entry_cost_usd": t["entry_cost_usd"], "market_value_usd": value,
            "unrealized_pnl_usd": value - t["entry_cost_usd"],
        })
    return out


"""Risk limits, enforced in code for every simulated entry and exit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .config import RiskCfg


@dataclass
class DailyState:
    day: date
    start_equity: float
    halted: bool = False


class RiskManager:
    def __init__(self, cfg: RiskCfg):
        self.cfg = cfg
        self.daily: DailyState | None = None

    def entry_notional(self, equity: float) -> float:
        """Trade value for a new position: a fixed percentage of current equity."""
        return max(0.0, equity) * self.cfg.position_size_pct / 100.0

    def observe_equity(self, now: datetime, equity: float) -> bool:
        """Track the UTC day's starting equity and latch the halt once the day's
        loss reaches the limit. Returns True while halted."""
        today = now.date()
        if self.daily is None or self.daily.day != today:
            self.daily = DailyState(today, equity)
        floor = self.daily.start_equity * (1 - self.cfg.daily_loss_limit_pct / 100.0)
        if equity <= floor:
            self.daily.halted = True
        return self.daily.halted

    @property
    def halted(self) -> bool:
        return bool(self.daily and self.daily.halted)

    def check_entry(self, *, open_positions: int, already_held: bool, cash: float, cost_estimate: float) -> str | None:
        """Return a rejection reason, or None if the entry is allowed."""
        if self.halted:
            return f"daily loss limit {self.cfg.daily_loss_limit_pct}% reached; entries halted until next UTC day"
        if already_held:
            return "position already open"
        if open_positions >= self.cfg.max_open_positions:
            return f"max open positions ({self.cfg.max_open_positions}) reached"
        if cost_estimate <= 0:
            return "position size is zero"
        if cash < cost_estimate:
            return f"insufficient simulated cash (${cash:,.2f} < ${cost_estimate:,.2f})"
        return None

    def stop_price(self, entry_price: float) -> float:
        return entry_price * (1 - self.cfg.stop_loss_pct / 100.0)

    def take_profit_price(self, entry_price: float) -> float:
        return entry_price * (1 + self.cfg.take_profit_pct / 100.0)

    def exit_trigger(self, entry_price: float, low: float, high: float) -> tuple[str, float] | None:
        """Stop loss or take profit hit within [low, high]. If both are inside
        one bar the stop is assumed to hit first (the conservative choice)."""
        stop, tp = self.stop_price(entry_price), self.take_profit_price(entry_price)
        if low <= stop:
            return "stop_loss", min(stop, high)
        if high >= tp:
            return "take_profit", max(tp, low)
        return None

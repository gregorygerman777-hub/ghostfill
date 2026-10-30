"""Cost model shared by the paper engine and the backtester.

A simulated swap pays:
  * pool fee (bps of trade value, per DEX from config)
  * platform fee (bps of trade value)
  * network fee: base fee + priority fee in lamports, converted at the SOL price
  * slippage: base bps plus a constant-product price impact term that grows
    with trade size relative to pool liquidity
A random fraction of transactions fail. A failed transaction still pays the
network fee (as on Solana) but moves no tokens.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .config import FeesCfg

LAMPORTS_PER_SOL = 1_000_000_000


@dataclass(frozen=True)
class Fill:
    ok: bool
    side: str  # "buy" or "sell"
    reason: str  # "" when filled, otherwise why it did not fill
    quoted_price: float
    fill_price: float
    qty: float
    trade_value_usd: float  # qty * fill_price
    pool_fee_usd: float
    platform_fee_usd: float
    network_fee_usd: float
    slippage_bps: float
    slippage_usd: float
    cash_delta: float  # change to simulated cash

    @property
    def fees_usd(self) -> float:
        return self.pool_fee_usd + self.platform_fee_usd + self.network_fee_usd


class CostModel:
    def __init__(self, fees: FeesCfg, max_slippage_bps: float, rng: random.Random):
        self.fees = fees
        self.max_slippage_bps = max_slippage_bps
        self.rng = rng

    def pool_fee_bps(self, dex_id: str) -> float:
        return self.fees.pool_fee_bps_by_dex.get(dex_id, self.fees.default_pool_fee_bps)

    def network_fee_usd(self, sol_price_usd: float) -> float:
        lamports = self.fees.base_fee_lamports + self.fees.priority_fee_lamports
        return lamports / LAMPORTS_PER_SOL * sol_price_usd

    def slippage_frac(self, side: str, trade_usd: float, liquidity_usd: float) -> float:
        """Base slippage plus constant-product impact against the quote-side reserve
        (assumed to be half of total pool liquidity)."""
        reserve = max(liquidity_usd / 2.0, 1e-9)
        if side == "buy":
            impact = trade_usd / reserve
        else:
            impact = trade_usd / (reserve + trade_usd)
        return self.fees.base_slippage_bps / 1e4 + self.fees.impact_multiplier * impact

    @staticmethod
    def _rejected(side: str, reason: str, price: float, network_fee: float = 0.0, slip_bps: float = 0.0) -> Fill:
        return Fill(False, side, reason, price, 0.0, 0.0, 0.0, 0.0, 0.0, network_fee, slip_bps, 0.0, -network_fee)

    def simulate(
        self,
        side: str,
        quoted_price: float,
        liquidity_usd: float,
        sol_price_usd: float,
        dex_id: str = "",
        *,
        spend_usd: float = 0.0,
        qty: float = 0.0,
    ) -> Fill:
        """Buy with `spend_usd` of trade value, or sell `qty` tokens."""
        if side not in ("buy", "sell"):
            raise ValueError(side)
        if quoted_price <= 0 or liquidity_usd <= 0:
            return self._rejected(side, "no valid quote", quoted_price)
        trade_usd = spend_usd if side == "buy" else qty * quoted_price
        if trade_usd <= 0:
            return self._rejected(side, "zero trade size", quoted_price)

        slip = self.slippage_frac(side, trade_usd, liquidity_usd)
        slip_bps = slip * 1e4
        if slip_bps > self.max_slippage_bps:
            # Not sent: equivalent to a slippage tolerance the swap would exceed.
            return self._rejected(side, f"slippage {slip_bps:.0f} bps > max {self.max_slippage_bps:.0f} bps",
                                  quoted_price, slip_bps=slip_bps)

        network = self.network_fee_usd(sol_price_usd)
        if self.rng.random() < self.fees.failure_probability:
            return self._rejected(side, "simulated transaction failure", quoted_price, network_fee=network)

        pool_bps = self.pool_fee_bps(dex_id)
        pool_fee = trade_usd * pool_bps / 1e4
        platform_fee = trade_usd * self.fees.platform_fee_bps / 1e4
        if side == "buy":
            fill_price = quoted_price * (1 + slip)
            got_qty = trade_usd / fill_price
            value = got_qty * fill_price
            slip_usd = got_qty * (fill_price - quoted_price)
            cash_delta = -(trade_usd + pool_fee + platform_fee + network)
        else:
            fill_price = quoted_price * (1 - slip)
            got_qty = qty
            value = qty * fill_price
            slip_usd = qty * (quoted_price - fill_price)
            cash_delta = value - pool_fee - platform_fee - network
        return Fill(True, side, "", quoted_price, fill_price, got_qty, value, pool_fee, platform_fee, network,
                    slip_bps, slip_usd, cash_delta)

"""Token screen applied before any simulated entry.

These checks reduce, but do not eliminate, rug pull and honeypot risk. A token
can pass every check and still be untradeable or go to zero.

Missing data fails closed: if a fact cannot be fetched, the token is rejected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from .config import ScreenCfg
from .data.rpc import HolderAccount, MintInfo
from .data.sources import Snapshot

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenFacts:
    mint: str
    mint_info: MintInfo | None
    holders: list[HolderAccount] | None
    snapshot: Snapshot | None
    holder_source: str = "rpc"


@dataclass
class ScreenResult:
    mint: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, float | str | None] = field(default_factory=dict)

    @property
    def data_missing(self) -> bool:
        return any("unavailable" in r for r in self.reasons)


def top_holder_pct(holders: list[HolderAccount], supply: int, n: int, exclude_owners: set[str]) -> float:
    kept = [h for h in holders if h.owner is None or h.owner not in exclude_owners]
    kept.sort(key=lambda h: h.amount, reverse=True)
    return 100.0 * sum(h.amount for h in kept[:n]) / supply


def screen_token(facts: TokenFacts, cfg: ScreenCfg, now: datetime) -> ScreenResult:
    reasons: list[str] = []
    metrics: dict[str, float | str | None] = {}
    allow = facts.mint in set(cfg.authority_allowlist)
    mi = facts.mint_info

    if mi is None:
        reasons.append("mint account data unavailable")
    else:
        if mi.mint_authority and not allow:
            reasons.append(f"active mint authority {mi.mint_authority}")
        if mi.freeze_authority and not allow:
            reasons.append(f"active freeze authority {mi.freeze_authority}")
        risky = sorted(set(mi.extensions) & set(cfg.reject_token2022_extensions))
        if risky and not allow:
            reasons.append(f"risky Token-2022 extensions {risky}")

    if facts.holders is None or mi is None:
        reasons.append("holder data unavailable")
    elif mi.supply <= 0:
        reasons.append("token supply is zero")
    else:
        exclude = set(cfg.holder_exclude_owners)
        if facts.snapshot and facts.snapshot.pool_address:
            exclude.add(facts.snapshot.pool_address)  # pool vaults owned by the pool account
        pct = top_holder_pct(facts.holders, mi.supply, cfg.top_holder_count, exclude)
        metrics["top_holder_pct"] = pct
        metrics["holder_source"] = facts.holder_source
        if pct > cfg.max_top_holder_pct:
            reasons.append(
                f"top {cfg.top_holder_count} holders own {pct:.1f}% (> {cfg.max_top_holder_pct:.1f}%)"
            )

    s = facts.snapshot
    if s is None:
        reasons.append("no market snapshot")
    else:
        metrics.update(liquidity_usd=s.liquidity_usd, volume_24h_usd=s.volume_24h_usd)
        if s.liquidity_usd < cfg.min_liquidity_usd:
            reasons.append(f"liquidity ${s.liquidity_usd:,.0f} < ${cfg.min_liquidity_usd:,.0f}")
        if s.volume_24h_usd < cfg.min_volume_24h_usd:
            reasons.append(f"24h volume ${s.volume_24h_usd:,.0f} < ${cfg.min_volume_24h_usd:,.0f}")
        if s.pool_created_at is None:
            reasons.append("pool creation time unknown")
            metrics["pool_age_days"] = None
        else:
            age = (now - s.pool_created_at).total_seconds() / 86_400
            metrics["pool_age_days"] = age
            if age < cfg.min_pool_age_days:
                reasons.append(f"pool age {age:.1f}d < {cfg.min_pool_age_days:.1f}d")

    result = ScreenResult(facts.mint, not reasons, reasons, metrics)
    for r in reasons:
        log.info("screen rejected %s: %s", facts.mint, r)
    return result

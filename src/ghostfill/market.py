"""Market data facade used by the engine and the backtest CLI."""

from __future__ import annotations

import json
import logging
import math
import time

import pandas as pd

from .data.cache import OhlcvCache
from .data.http import HttpClient, HttpError
from .data.rpc import HolderAccount, RpcError, SolanaRpc
from .data.sources import INTERVAL_SECONDS, Snapshot, fetch_ohlcv, fetch_rugcheck_holders, fetch_snapshots, utcnow
from .screen import TokenFacts

log = logging.getLogger(__name__)

RPC_HOLDER_COOLDOWN_S = 1800
HOLDER_CACHE_MAX_AGE_S = 6 * 3600  # holder lists change slowly; reuse a recent good one if both sources fail


class MarketData:
    def __init__(self, http: HttpClient, cache: OhlcvCache, rpc: SolanaRpc | None = None,
                 holder_fallback: str = "none"):
        self.http = http
        self.cache = cache
        self.rpc = rpc
        self.holder_fallback = holder_fallback
        # After the RPC rate-limits the holder lookup, skip it for a while instead of retrying per token.
        self._rpc_holders_blocked_until = 0.0
        # Last good holder list per mint, keyed by wall-clock time and saved next to the OHLCV cache
        # so a restart during a RugCheck outage does not lose it.
        self._holder_path = cache.root.parent / "holders.json"
        self._holder_cache: dict[str, tuple[float, list, str]] = self._load_holders()

    def _load_holders(self) -> dict[str, tuple[float, list, str]]:
        try:
            raw = json.loads(self._holder_path.read_text())
            return {m: (v["at"], [HolderAccount(*h) for h in v["holders"]], v["source"]) for m, v in raw.items()}
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def _save_holders(self) -> None:
        data = {m: {"at": at, "source": src, "holders": [[h.token_account, h.owner, h.amount] for h in hs]}
                for m, (at, hs, src) in self._holder_cache.items()}
        tmp = self._holder_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data))
            tmp.replace(self._holder_path)
        except OSError as e:
            log.warning("could not save holder cache: %s", e)

    async def snapshots(self, mints: list[str]) -> dict[str, Snapshot]:
        snaps = await fetch_snapshots(self.http, mints)
        for m in mints:
            if m not in snaps:
                log.warning("no Solana pool found with %s as base token", m)
        return snaps

    async def history(self, mint: str, pool: str, interval: str, bars: int, offline: bool = False) -> pd.DataFrame:
        """Cached OHLCV, topped up with any bars newer than the cache."""
        cached = self.cache.load(mint, interval)
        if offline:
            if cached is None:
                raise FileNotFoundError(f"no cached {interval} bars for {mint}; run without --offline first")
            return cached[0]
        now = utcnow()
        if cached is not None and len(cached[0]):
            last = cached[0].index[-1]
            need = math.ceil((pd.Timestamp(now) - last).total_seconds() / INTERVAL_SECONDS[interval]) + 2
            if len(cached[0]) < bars:
                need = bars  # cache shorter than requested: refetch the full window
            need = min(max(need, 2), bars)
        else:
            need = bars
        fresh = await fetch_ohlcv(self.http, pool, mint, interval, need)
        if fresh.empty:
            if cached is None:
                raise HttpError(None, "https://api.geckoterminal.com", f"no OHLCV returned for pool {pool}")
            return cached[0]
        return self.cache.merge_save(mint, interval, fresh, "geckoterminal", pool, now)

    async def facts(self, mint: str, snapshot: Snapshot | None) -> TokenFacts:
        """On-chain facts for the screen. Failures leave a field as None, which the screen rejects."""
        mint_info = holders = None
        source = "rpc"
        if self.rpc is not None:
            try:
                mint_info = await self.rpc.get_mint_info(mint)
            except (HttpError, RpcError) as e:
                log.warning("mint info unavailable for %s: %s", mint, e)
            if time.monotonic() >= self._rpc_holders_blocked_until:
                try:
                    holders = await self.rpc.get_top_holders(mint)
                except (HttpError, RpcError) as e:
                    if isinstance(e, HttpError) and e.status == 429:
                        self._rpc_holders_blocked_until = time.monotonic() + RPC_HOLDER_COOLDOWN_S
                        log.info("RPC rate-limits the holder lookup; using %s for %d min",
                                 self.holder_fallback, RPC_HOLDER_COOLDOWN_S // 60)
                    else:
                        log.warning("holder data unavailable from RPC for %s: %s", mint, e)
        if holders is None and self.holder_fallback == "rugcheck":
            try:
                holders = await fetch_rugcheck_holders(self.http, mint)
                source = "rugcheck"
            except (HttpError, ValueError) as e:
                log.warning("holder data unavailable from RugCheck for %s: %s", mint, e)
        now = time.time()
        if holders is not None:
            self._holder_cache[mint] = (now, holders, source)
            self._save_holders()
        elif (mint in self._holder_cache and now - self._holder_cache[mint][0] <= HOLDER_CACHE_MAX_AGE_S
              and (self.holder_fallback == "rugcheck" or "rugcheck" not in self._holder_cache[mint][2])):
            # a cached list is reused only if its source is still enabled
            at, holders, src = self._holder_cache[mint]
            source = f"{src}, cached {int((now - at) // 60)}m ago"
            log.info("using cached holder data for %s (%s)", mint, source)
        return TokenFacts(mint, mint_info, holders, snapshot, source)

"""Mainnet market data: price/liquidity snapshots (DexScreener) and OHLCV (GeckoTerminal).

Every result is keyed by mint address. Symbols from the APIs are kept for
display only, and a pair is used only if its base token address equals the
requested mint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from .http import HttpClient
from .rpc import HolderAccount

DEXSCREENER_TOKENS = "https://api.dexscreener.com/tokens/v1/solana/{mints}"
RUGCHECK_REPORT = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
GECKO_OHLCV = "https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/{timeframe}"

INTERVAL_TO_GECKO = {
    "1m": ("minute", 1),
    "5m": ("minute", 5),
    "15m": ("minute", 15),
    "1h": ("hour", 1),
    "4h": ("hour", 4),
    "1d": ("day", 1),
}
INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14_400, "1d": 86_400}
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Snapshot:
    mint: str
    symbol: str  # display only
    price_usd: float
    liquidity_usd: float
    volume_24h_usd: float
    pool_address: str
    dex_id: str
    quote_mint: str
    pool_created_at: datetime | None
    source: str
    fetched_at: datetime


def parse_dexscreener(pairs: list[dict], mints: list[str], fetched_at: datetime) -> dict[str, Snapshot]:
    """Pick the deepest Solana pool per mint where the mint is the base token."""
    best: dict[str, Snapshot] = {}
    wanted = set(mints)
    for p in pairs or []:
        if p.get("chainId") != "solana":
            continue
        base = (p.get("baseToken") or {}).get("address")
        if base not in wanted:
            continue
        try:
            price = float(p["priceUsd"])
            liq = float((p.get("liquidity") or {}).get("usd") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if price <= 0 or liq <= 0:
            continue
        created_ms = p.get("pairCreatedAt")
        snap = Snapshot(
            mint=base,
            symbol=str((p.get("baseToken") or {}).get("symbol", "")),
            price_usd=price,
            liquidity_usd=liq,
            volume_24h_usd=float((p.get("volume") or {}).get("h24") or 0.0),
            pool_address=str(p.get("pairAddress", "")),
            dex_id=str(p.get("dexId", "")),
            quote_mint=str((p.get("quoteToken") or {}).get("address", "")),
            pool_created_at=datetime.fromtimestamp(created_ms / 1000, timezone.utc) if created_ms else None,
            source="dexscreener",
            fetched_at=fetched_at,
        )
        if base not in best or snap.liquidity_usd > best[base].liquidity_usd:
            best[base] = snap
    return best


async def fetch_snapshots(http: HttpClient, mints: list[str]) -> dict[str, Snapshot]:
    out: dict[str, Snapshot] = {}
    for i in range(0, len(mints), 30):  # endpoint accepts up to 30 addresses
        chunk = mints[i : i + 30]
        fetched_at = utcnow()
        pairs = await http.get_json(DEXSCREENER_TOKENS.format(mints=",".join(chunk)))
        out.update(parse_dexscreener(pairs if isinstance(pairs, list) else [], chunk, fetched_at))
    return out


def parse_gecko_ohlcv(body: dict) -> pd.DataFrame:
    rows = (((body or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    df = pd.DataFrame(rows, columns=["ts", *OHLCV_COLUMNS])
    if df.empty:
        return empty_ohlcv()
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="s", utc=True)
    df = df.astype({c: "float64" for c in OHLCV_COLUMNS})
    return df.drop_duplicates("ts").set_index("ts").sort_index()


def empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV_COLUMNS, index=pd.DatetimeIndex([], tz="UTC", name="ts"), dtype="float64")


async def fetch_ohlcv(
    http: HttpClient, pool: str, mint: str, interval: str, bars: int, before: datetime | None = None
) -> pd.DataFrame:
    """Fetch up to `bars` bars ending before `before`, paging backwards 1000 at a time."""
    timeframe, aggregate = INTERVAL_TO_GECKO[interval]
    frames = []
    cursor = int((before or utcnow()).timestamp())
    remaining = bars
    while remaining > 0:
        limit = min(1000, remaining)
        params = {"aggregate": aggregate, "limit": limit, "currency": "usd", "token": mint,
                  "before_timestamp": cursor}
        df = parse_gecko_ohlcv(await http.get_json(GECKO_OHLCV.format(pool=pool, timeframe=timeframe), params))
        if df.empty:
            break
        frames.append(df)
        remaining -= len(df)
        cursor = int(df.index[0].timestamp())
        if len(df) < limit:
            break
    if not frames:
        return empty_ohlcv()
    out = pd.concat(frames)
    return out[~out.index.duplicated(keep="last")].sort_index()


def parse_rugcheck_holders(body: dict, mint: str) -> list[HolderAccount]:
    """Top holders from a RugCheck report. Rejects a report for any other mint."""
    if not isinstance(body, dict) or body.get("mint") != mint:
        raise ValueError(f"RugCheck report is not for mint {mint}")
    out = []
    for h in body.get("topHolders") or []:
        try:
            out.append(HolderAccount(str(h["address"]), h.get("owner"), int(h["amount"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not out:
        raise ValueError("RugCheck report has no holder data")
    return out


async def fetch_rugcheck_holders(http: HttpClient, mint: str) -> list[HolderAccount]:
    return parse_rugcheck_holders(await http.get_json(RUGCHECK_REPORT.format(mint=mint)), mint)


def closed_bars(df: pd.DataFrame, interval: str, now: datetime | None = None) -> pd.DataFrame:
    """Drop the bar that is still forming, so signals only see completed bars."""
    if df.empty:
        return df
    now = now or utcnow()
    cutoff = pd.Timestamp(now) - pd.Timedelta(seconds=INTERVAL_SECONDS[interval])
    return df[df.index <= cutoff]

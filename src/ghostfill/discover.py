"""Token discovery: top Solana pools by 24h volume from GeckoTerminal, each base
token run through the full token screen. Tokens are matched by mint address."""

from __future__ import annotations

from .data.http import HttpClient
from .mints import is_valid_mint

GECKO_TOP_POOLS = "https://api.geckoterminal.com/api/v2/networks/solana/pools"
QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112",   # wrapped SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


def parse_top_pools(body: dict) -> list[str]:
    """Base-token mints in the order GeckoTerminal ranks them, deduplicated."""
    seen, out = set(), []
    for pool in (body or {}).get("data") or []:
        ref = (((pool.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id", "")
        mint = ref.split("_", 1)[1] if ref.startswith("solana_") else ""
        if is_valid_mint(mint) and mint not in QUOTE_MINTS and mint not in seen:
            seen.add(mint)
            out.append(mint)
    return out


async def top_mints(http: HttpClient, pages: int = 3) -> list[str]:
    mints: list[str] = []
    for page in range(1, pages + 1):
        body = await http.get_json(GECKO_TOP_POOLS, {"page": page, "sort": "h24_volume_usd_desc"})
        for m in parse_top_pools(body):
            if m not in mints:
                mints.append(m)
    return mints

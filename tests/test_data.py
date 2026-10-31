from datetime import timedelta

import pandas as pd

from conftest import JUP, NOW, ohlcv
from ghostfill.data.cache import OhlcvCache
from ghostfill.data.sources import closed_bars, parse_dexscreener, parse_gecko_ohlcv

SOL = "So11111111111111111111111111111111111111112"


def pair(base, liq, symbol="JUP", chain="solana"):
    return {"chainId": chain, "dexId": "orca", "pairAddress": f"pool{liq}", "priceUsd": "0.25",
            "baseToken": {"address": base, "symbol": symbol}, "quoteToken": {"address": SOL},
            "liquidity": {"usd": liq}, "volume": {"h24": 1000}, "pairCreatedAt": 1706675725000}


def test_dexscreener_picks_deepest_pool_by_mint_not_symbol():
    pairs = [pair(JUP, 100), pair(JUP, 900), pair(SOL, 10_000, symbol="JUP"), pair(JUP, 5000, chain="ethereum")]
    snaps = parse_dexscreener(pairs, [JUP], NOW)
    assert list(snaps) == [JUP]
    assert snaps[JUP].liquidity_usd == 900 and snaps[JUP].pool_address == "pool900"
    assert snaps[JUP].pool_created_at.year == 2024


def test_gecko_parse_sorts_ascending():
    body = {"data": {"attributes": {"ohlcv_list": [[7200, 1, 2, 0.5, 1.5, 10], [3600, 1, 1, 1, 1, 5]]}}}
    df = parse_gecko_ohlcv(body)
    assert [int(t.timestamp()) for t in df.index] == [3600, 7200]
    assert df["close"].tolist() == [1.0, 1.5]


def test_closed_bars_drops_forming_bar():
    df = ohlcv(10, start=NOW - timedelta(hours=9, minutes=30))
    assert len(closed_bars(df, "1h", NOW)) == 9


def test_cache_merge_records_source_and_fetch_time(tmp_path):
    cache = OhlcvCache(tmp_path)
    df = ohlcv(50)
    cache.merge_save(JUP, "1h", df.iloc[:40], "geckoterminal", "poolA", NOW)
    newer = df.iloc[35:].copy()
    newer.iloc[0, newer.columns.get_loc("close")] = 999.0  # refetched bar replaces cached one
    merged = cache.merge_save(JUP, "1h", newer, "geckoterminal", "poolA", NOW + timedelta(minutes=5))
    assert len(merged) == 50
    loaded, meta = cache.load(JUP, "1h")
    assert len(loaded) == 50 and loaded["close"].iloc[35] == 999.0
    assert meta["source"] == "geckoterminal" and meta["pool"] == "poolA" and meta["rows"] == 50
    assert {"source", "fetched_at"} <= set(loaded.columns)
    assert loaded["fetched_at"].iloc[0] == pd.Timestamp(NOW)
    assert loaded["fetched_at"].iloc[-1] == pd.Timestamp(NOW + timedelta(minutes=5))
    assert str(loaded.index.tz) == "UTC"


def test_holder_cache_survives_restart(tmp_path):
    import asyncio

    from ghostfill.data.rpc import HolderAccount
    from ghostfill.market import MarketData

    class DownHttp:  # RugCheck unavailable
        async def get_json(self, *a, **k):
            from ghostfill.data.http import HttpError
            raise HttpError(400, "https://api.rugcheck.xyz/x", "unable to generate report")

    cache = OhlcvCache(tmp_path)
    m1 = MarketData(DownHttp(), cache, holder_fallback="rugcheck")
    m1._holder_cache[JUP] = (__import__("time").time(), [HolderAccount("a", "o", 5)], "rugcheck")
    m1._save_holders()

    m2 = MarketData(DownHttp(), cache, holder_fallback="rugcheck")  # "restart"
    facts = asyncio.run(m2.facts(JUP, None))
    assert facts.holders and facts.holders[0].amount == 5
    assert "cached" in facts.holder_source


def test_cached_rugcheck_data_ignored_when_fallback_off(tmp_path):
    import asyncio
    import time

    from ghostfill.data.rpc import HolderAccount
    from ghostfill.market import MarketData

    cache = OhlcvCache(tmp_path)
    m = MarketData(None, cache, holder_fallback="none")
    m._holder_cache[JUP] = (time.time(), [HolderAccount("a", "o", 5)], "rugcheck")
    assert asyncio.run(m.facts(JUP, None)).holders is None

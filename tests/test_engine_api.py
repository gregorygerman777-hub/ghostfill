import asyncio
from datetime import timedelta

import pandas as pd
from fastapi.testclient import TestClient

from conftest import JUP, NOW, make_settings, ohlcv
from ghostfill.data.rpc import HolderAccount, MintInfo
from ghostfill.data.sources import Snapshot, utcnow
from ghostfill.engine import PaperEngine
from ghostfill.journal import Journal
from ghostfill.screen import TokenFacts
from ghostfill.service.app import create_app

SOL = "So11111111111111111111111111111111111111112"


class FakeMarket:
    def __init__(self, df, mint_auth=None):
        self.df, self.mint_auth = df, mint_auth

    async def snapshots(self, mints):
        now = utcnow()
        out = {SOL: Snapshot(SOL, "SOL", 150.0, 1e8, 1e8, "solpool", "orca", "", now - timedelta(days=900), "fake", now)}
        out[JUP] = Snapshot(JUP, "JUP", float(self.df["close"].iloc[-1]), 5e6, 5e6, "jpool", "orca", SOL,
                            now - timedelta(days=500), "fake", now)
        return out

    async def history(self, mint, pool, interval, bars, offline=False):
        return self.df

    async def facts(self, mint, snap):
        mi = MintInfo(mint, "spl-token", 6, 10**12, self.mint_auth, None)
        return TokenFacts(mint, mi, [HolderAccount("a", "o", 10**9)], snap)


def uptrend():
    df = ohlcv(200, start=pd.Timestamp(utcnow()).floor("h") - pd.Timedelta(hours=200))
    df["close"] = df["open"] = pd.Series(range(200), index=df.index, dtype=float) * 0.01 + 1
    df["high"], df["low"] = df["close"] * 1.001, df["close"] * 0.999
    return df


def settings():
    return make_settings(tokens=[{"mint": JUP, "label": "JUP"}], fees={"failure_probability": 0.0})


def test_engine_enters_on_signal_and_journals(tmp_path):
    db = str(tmp_path / "j.sqlite3")
    eng = PaperEngine(settings(), FakeMarket(uptrend()), Journal(db))

    async def go():
        await eng.tick_snapshots()
        await eng.tick_bars()
        await eng.tick_bars()  # same bar again: no duplicate action
        await eng.tick_snapshots()

    asyncio.run(go())
    assert JUP in eng.portfolio.positions
    j = eng.journal
    assert len(j.open_trades()) == 1
    assert j.conn.execute("SELECT COUNT(*) FROM fills WHERE ok=1").fetchone()[0] == 1
    assert j.get_state("last_data_ts") is not None

    # restart: the new engine reloads the open position from SQLite
    j.close()
    eng2 = PaperEngine(settings(), FakeMarket(uptrend()), Journal(db))
    assert JUP in eng2.portfolio.positions

    client = TestClient(create_app(db))
    st = client.get("/status").json()
    assert st["mode"] == "PAPER"
    assert len(st["open_positions"]) == 1 and st["open_positions"][0]["mint"] == JUP
    assert "unrealized_pnl_usd" in st and "realized_pnl_usd" in st and st["last_data_timestamp"]
    jr = client.get("/journal", params={"limit": 5}).json()
    assert jr["trades"][0]["mint"] == JUP
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/journal", params={"limit": 0}).status_code == 422


def test_engine_screen_rejection_blocks_entry(tmp_path):
    eng = PaperEngine(settings(), FakeMarket(uptrend(), mint_auth=SOL), Journal(str(tmp_path / "j.sqlite3")))

    async def go():
        await eng.tick_snapshots()
        await eng.tick_bars()

    asyncio.run(go())
    assert not eng.portfolio.positions
    rows = eng.journal.conn.execute("SELECT stage, reason FROM rejections").fetchall()
    assert rows and rows[0]["stage"] == "screen" and "mint authority" in rows[0]["reason"]


def test_api_is_read_only(tmp_path):
    db = str(tmp_path / "j.sqlite3")
    Journal(db).close()
    app = create_app(db)
    methods = {m for r in app.routes if getattr(r, "path", "") in ("/health", "/status", "/journal")
               for m in r.methods}
    assert methods <= {"GET", "HEAD"}
    client = TestClient(app)
    assert client.post("/status").status_code == 405
    assert client.delete("/journal").status_code == 405
    assert client.get("/status").json()["open_positions"] == []

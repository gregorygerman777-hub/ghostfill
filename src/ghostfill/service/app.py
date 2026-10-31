"""Read-only status API. Every route is GET, and the API reads the journal
through a read-only SQLite connection."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone

from importlib.resources import files

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from .. import MODE, __version__
from ..config import Settings
from ..config import ReadinessCfg
from ..engine import unrealized_from_rows
from ..readiness import evaluate
from ..journal import open_readonly

log = logging.getLogger(__name__)


def _state(conn) -> dict:
    return {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM state")}


def create_app(db_path: str, lifespan=None) -> FastAPI:
    app = FastAPI(title="ghostfill", version=__version__, lifespan=lifespan,
                  description="Read-only status of a PAPER trading simulation. No live trading.")

    def conn():
        try:
            return open_readonly(db_path)
        except Exception:
            raise HTTPException(503, "journal not available yet") from None

    dashboard = files("ghostfill.service").joinpath("static/dashboard.html").read_text()

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        return dashboard

    @app.get("/health")
    def health():
        c = conn()
        try:
            c.execute("SELECT 1").fetchone()
        finally:
            c.close()
        return {"status": "ok", "mode": MODE}

    @app.get("/status")
    def status():
        c = conn()
        try:
            st = _state(c)
            open_rows = c.execute("SELECT * FROM trades WHERE status='open' ORDER BY id").fetchall()
            marks = {r["mint"]: (r["price"], r["ts"]) for r in c.execute("SELECT * FROM marks")}
            fees = c.execute(
                "SELECT COALESCE(SUM(pool_fee_usd + platform_fee_usd + network_fee_usd), 0) FROM fills"
            ).fetchone()[0]
            closed = c.execute("SELECT COUNT(*) FROM trades WHERE status='closed'").fetchone()[0]
            equity_hist = [r[0] for r in c.execute("SELECT equity_usd FROM equity ORDER BY ts")]
            wins, losses = c.execute(
                "SELECT COALESCE(SUM(pnl_usd > 0), 0), COALESCE(SUM(pnl_usd <= 0), 0) FROM trades WHERE status='closed'"
            ).fetchone()
            latest = {r["mint"]: dict(r) for r in c.execute(
                "SELECT s.* FROM snapshots s JOIN (SELECT mint, MAX(id) AS id FROM snapshots GROUP BY mint) m "
                "ON s.id = m.id")}
        finally:
            c.close()
        positions = unrealized_from_rows(open_rows, marks)
        unrealized = sum(p["unrealized_pnl_usd"] for p in positions)
        cash = float(st.get("cash", 0.0))
        last = st.get("last_data_ts")
        age = None
        if last:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
        daily = st.get("daily") or {}
        equity = cash + sum(p["market_value_usd"] for p in positions)
        day_start = daily.get("start_equity")
        held = {p["mint"] for p in positions}
        first = st.get("first_started_at")
        paper_days = (datetime.now(timezone.utc) - datetime.fromisoformat(first)).total_seconds() / 86_400 if first else 0.0
        readiness = evaluate(
            ReadinessCfg(**(st.get("readiness_cfg") or {})),
            paper_days=paper_days, start_equity=float(st.get("starting_equity") or 0), equity_now=equity,
            equity_history=equity_hist, closed_trades=int(closed), validation=st.get("validation"),
        )
        tokens = []
        for t in st.get("tokens", []):
            snap = latest.get(t["mint"], {})
            tokens.append({
                "mint": t["mint"], "label": t["label"], "held": t["mint"] in held,
                "price_usd": snap.get("price_usd"), "liquidity_usd": snap.get("liquidity_usd"),
                "volume_24h_usd": snap.get("volume_24h_usd"), "dex_id": snap.get("dex_id"),
                "pool_address": snap.get("pool_address"), "snapshot_ts": snap.get("fetched_at"),
                "signal": (st.get("last_signals") or {}).get(t["mint"]),
                "screen": (st.get("screens") or {}).get(t["mint"]),
                "chart": (st.get("charts") or {}).get(t["mint"]),
            })
        return {
            "mode": MODE,
            "strategy": st.get("strategy"),
            "interval": st.get("interval"),
            "starting_equity_usd": st.get("starting_equity"),
            "cash_usd": cash,
            "equity_usd": equity,
            "today_pnl_usd": None if day_start is None else equity - float(day_start),
            "realized_pnl_usd": float(st.get("realized_pnl", 0.0)),
            "unrealized_pnl_usd": unrealized,
            "total_fees_usd": float(fees),
            "closed_trades": int(closed),
            "open_positions": positions,
            "entries_halted_today": bool(daily.get("halted", False)),
            "last_data_timestamp": last,
            "data_age_seconds": age,
            "last_slot": st.get("last_slot"),
            "started_at": st.get("started_at"),
            "cycles": st.get("cycles", 0),
            "wins": wins,
            "losses": losses,
            "risk_limits": st.get("risk"),
            "readiness": readiness,
            "validation": st.get("validation"),
            "research": st.get("research"),
            "tokens": tokens,
            "last_signals": st.get("last_signals", {}),
            "screens": st.get("screens", {}),
        }

    @app.get("/journal")
    def journal(limit: int = Query(50, ge=1, le=500)):
        c = conn()
        try:
            trades = [dict(r) for r in c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))]
            rejections = [dict(r) for r in c.execute(
                "SELECT * FROM rejections ORDER BY id DESC LIMIT ?", (limit,))]
            signals = [dict(r) for r in c.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,))]
            equity = [dict(r) for r in c.execute("SELECT * FROM equity ORDER BY ts DESC LIMIT ?", (limit,))]
            fills = [dict(r) for r in c.execute("SELECT * FROM fills ORDER BY id DESC LIMIT ?", (limit,))]
            screens = [dict(r) for r in c.execute("SELECT * FROM screen_log ORDER BY id DESC LIMIT ?", (limit,))]
        finally:
            c.close()
        return {"mode": MODE, "trades": trades, "rejections": rejections, "signals": signals, "equity": equity,
                "fills": fills, "screens": screens}

    return app


def serve(settings: Settings, host: str, port: int) -> None:
    import uvicorn

    from ..data.cache import OhlcvCache
    from ..data.http import HttpClient
    from ..data.rpc import SolanaRpc
    from ..data.stream import SlotStream
    from ..engine import PaperEngine
    from ..journal import Journal
    from ..market import MarketData

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        d = settings.data
        http = HttpClient(d.http_timeout_seconds, d.max_retries, d.backoff_base_seconds, d.backoff_max_seconds,
                          seed=settings.seed)
        market = MarketData(http, OhlcvCache(settings.cache_dir), SolanaRpc(settings.rpc_url, http),
                            settings.screen.holder_fallback)
        journal = Journal(settings.db_path)
        stream = SlotStream(settings.ws_url, d.backoff_base_seconds, d.backoff_max_seconds) \
            if d.stream_slots and settings.ws_url else None
        engine = PaperEngine(settings, market, journal, stream)
        tasks = [asyncio.create_task(engine.run(), name="engine")]
        if stream:
            tasks.append(asyncio.create_task(stream.run(), name="slots"))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await http.aclose()
            journal.close()

    Journal(settings.db_path).close()  # create the schema before the read-only API opens it
    app = create_app(settings.db_path, lifespan=lifespan)
    uvicorn.run(app, host=host, port=port, log_config=None, access_log=False)

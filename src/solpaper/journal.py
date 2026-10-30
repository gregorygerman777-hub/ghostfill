"""SQLite journal of simulated fills, trades, rejections, snapshots and portfolio state.

Every write commits immediately, so a restart loses nothing that was recorded.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, mint TEXT NOT NULL, side TEXT NOT NULL, ok INTEGER NOT NULL, reason TEXT NOT NULL,
  quoted_price REAL, fill_price REAL, qty REAL, trade_value_usd REAL,
  pool_fee_usd REAL, platform_fee_usd REAL, network_fee_usd REAL,
  slippage_bps REAL, slippage_usd REAL, cash_delta REAL, trade_id INTEGER
);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mint TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
  entry_ts TEXT NOT NULL, entry_price REAL NOT NULL, qty REAL NOT NULL,
  entry_cost_usd REAL NOT NULL, entry_fees_usd REAL NOT NULL, entry_slippage_usd REAL NOT NULL,
  exit_ts TEXT, exit_price REAL, exit_proceeds_usd REAL, exit_fees_usd REAL, exit_slippage_usd REAL,
  exit_reason TEXT, pnl_usd REAL, pnl_pct REAL
);
CREATE INDEX IF NOT EXISTS trades_status ON trades(status);
CREATE TABLE IF NOT EXISTS rejections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, mint TEXT NOT NULL, stage TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, mint TEXT NOT NULL, price_usd REAL, liquidity_usd REAL, volume_24h_usd REAL,
  pool_address TEXT, dex_id TEXT, source TEXT, fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, mint TEXT NOT NULL, bar_ts TEXT NOT NULL, target REAL NOT NULL, action TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS equity (
  ts TEXT PRIMARY KEY, equity_usd REAL NOT NULL, cash_usd REAL NOT NULL,
  realized_pnl_usd REAL NOT NULL, unrealized_pnl_usd REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS marks (mint TEXT PRIMARY KEY, price REAL NOT NULL, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Journal:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # state -----------------------------------------------------------------
    def get_state(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])

    def set_state(self, **items: Any) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(k, json.dumps(v, default=str)) for k, v in items.items()],
            )

    # writes ----------------------------------------------------------------
    def insert(self, table: str, **row: Any) -> int:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self.conn:
            cur = self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))
        return int(cur.lastrowid)

    def update_trade(self, trade_id: int, **fields: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.conn:
            self.conn.execute(f"UPDATE trades SET {sets} WHERE id=?", (*fields.values(), trade_id))

    def set_mark(self, mint: str, price: float, ts: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO marks(mint, price, ts) VALUES(?, ?, ?) "
                "ON CONFLICT(mint) DO UPDATE SET price=excluded.price, ts=excluded.ts",
                (mint, price, ts),
            )

    def reject(self, ts: str, mint: str, stage: str, reason: str) -> None:
        self.insert("rejections", ts=ts, mint=mint, stage=stage, reason=reason)

    # reads -----------------------------------------------------------------
    def open_trades(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM trades WHERE status='open' ORDER BY id").fetchall()

    def closed_trades(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM trades WHERE status='closed' ORDER BY id").fetchall()

    def recent_trades(self, limit: int) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def marks(self) -> dict[str, tuple[float, str]]:
        return {r["mint"]: (r["price"], r["ts"]) for r in self.conn.execute("SELECT * FROM marks")}

    def total_fees(self) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(pool_fee_usd + platform_fee_usd + network_fee_usd), 0) AS f FROM fills"
        ).fetchone()
        return float(row["f"])


def open_readonly(path: str) -> sqlite3.Connection:
    """Read-only connection for the status service. It cannot write the journal."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

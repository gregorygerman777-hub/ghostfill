"""Parquet OHLCV cache keyed by (mint, interval), with source and fetch time recorded."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..mints import is_valid_mint
from .sources import OHLCV_COLUMNS

_META_KEY = b"ghostfill"


class OhlcvCache:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root) / "ohlcv"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, mint: str, interval: str) -> Path:
        if not is_valid_mint(mint):
            raise ValueError(f"invalid mint {mint!r}")
        return self.root / f"{mint}__{interval}.parquet"

    def load(self, mint: str, interval: str) -> tuple[pd.DataFrame, dict] | None:
        p = self.path(mint, interval)
        if not p.exists():
            return None
        table = pq.read_table(p)
        meta = json.loads((table.schema.metadata or {}).get(_META_KEY, b"{}"))
        df = table.to_pandas()
        if "ts" in df.columns:
            df = df.set_index("ts")
        idx = pd.DatetimeIndex(df.index)
        df.index = (idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")).rename("ts")
        return df.sort_index(), meta

    def merge_save(
        self, mint: str, interval: str, new: pd.DataFrame, source: str, pool: str, fetched_at: datetime
    ) -> pd.DataFrame:
        """Merge new bars into the cache. A refetched bar replaces the cached one
        (the last bar is often partial when first fetched)."""
        new = new[OHLCV_COLUMNS].copy()
        new["source"] = source
        new["pool"] = pool
        new["fetched_at"] = pd.Timestamp(fetched_at)
        existing = self.load(mint, interval)
        df = new if existing is None else pd.concat([existing[0], new])
        df = df[~df.index.duplicated(keep="last")].sort_index()
        meta = {"mint": mint, "interval": interval, "source": source, "pool": pool,
                "fetched_at": pd.Timestamp(fetched_at).isoformat(), "rows": int(len(df))}
        table = pa.Table.from_pandas(df.reset_index(), preserve_index=False)
        table = table.replace_schema_metadata({**(table.schema.metadata or {}), _META_KEY: json.dumps(meta).encode()})
        p = self.path(mint, interval)
        tmp = p.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp)
        os.replace(tmp, p)  # atomic, so a crash never leaves a half-written cache
        return df

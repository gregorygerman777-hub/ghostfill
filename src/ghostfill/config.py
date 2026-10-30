"""Settings: YAML file for strategy/risk/cost parameters, environment for secrets.

Validation happens once at startup. Any problem raises ConfigError with every
issue listed, and the CLI turns that into a non-zero exit with a clear message.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .mints import is_valid_mint

INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")


class ConfigError(Exception):
    """Raised when the configuration is missing or out of range."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TokenCfg(_Strict):
    mint: str
    label: str = ""  # display only, never used to identify a token

    @field_validator("mint")
    @classmethod
    def _mint(cls, v: str) -> str:
        if not is_valid_mint(v):
            raise ValueError(f"not a valid base58 32-byte mint address: {v!r}")
        return v


class StrategyCfg(_Strict):
    name: Literal["ma_crossover", "zscore_reversion"]
    params: dict[str, float | int] = Field(default_factory=dict)
    # Parameter grid searched in-sample during walk-forward validation.
    grid: dict[str, list[float | int]] = Field(default_factory=dict)


class FeesCfg(_Strict):
    default_pool_fee_bps: float = Field(30.0, ge=0, le=1000)
    pool_fee_bps_by_dex: dict[str, float] = Field(default_factory=dict)
    platform_fee_bps: float = Field(0.0, ge=0, le=500)
    base_fee_lamports: int = Field(5000, ge=0, le=1_000_000)
    priority_fee_lamports: int = Field(50_000, ge=0, le=100_000_000)
    base_slippage_bps: float = Field(10.0, ge=0, le=1000)
    impact_multiplier: float = Field(1.0, ge=0, le=20)
    failure_probability: float = Field(0.03, ge=0, lt=1)

    @field_validator("pool_fee_bps_by_dex")
    @classmethod
    def _dex_fees(cls, v: dict[str, float]) -> dict[str, float]:
        for dex, bps in v.items():
            if not 0 <= bps <= 1000:
                raise ValueError(f"pool fee for {dex!r} must be in [0, 1000] bps, got {bps}")
        return v


class RiskCfg(_Strict):
    starting_equity_usd: float = Field(10_000.0, gt=0)
    position_size_pct: float = Field(5.0, gt=0, le=100)
    stop_loss_pct: float = Field(8.0, gt=0, lt=100)
    take_profit_pct: float = Field(15.0, gt=0, le=1000)
    daily_loss_limit_pct: float = Field(3.0, gt=0, lt=100)
    max_open_positions: int = Field(3, ge=1, le=100)
    max_slippage_bps: float = Field(150.0, gt=0, le=5000)


class ScreenCfg(_Strict):
    authority_allowlist: list[str] = Field(default_factory=list)
    max_top_holder_pct: float = Field(50.0, gt=0, le=100)
    top_holder_count: int = Field(10, ge=1, le=20)
    holder_exclude_owners: list[str] = Field(default_factory=list)
    min_liquidity_usd: float = Field(100_000.0, ge=0)
    min_volume_24h_usd: float = Field(250_000.0, ge=0)
    min_pool_age_days: float = Field(30.0, ge=0)
    reject_token2022_extensions: list[str] = Field(
        default_factory=lambda: ["permanentDelegate", "transferHook", "nonTransferable", "pausableConfig"]
    )
    refresh_seconds: int = Field(1800, ge=60)
    # When a fact is unavailable the token is rejected; retry sooner than refresh_seconds.
    retry_unavailable_seconds: int = Field(120, ge=10)
    # Holder data source if the RPC call fails (the public RPC rate-limits getTokenLargestAccounts).
    holder_fallback: Literal["none", "rugcheck"] = "rugcheck"

    @field_validator("authority_allowlist", "holder_exclude_owners")
    @classmethod
    def _addresses(cls, v: list[str]) -> list[str]:
        bad = [a for a in v if not is_valid_mint(a)]
        if bad:
            raise ValueError(f"invalid base58 addresses: {bad}")
        return v


class DataCfg(_Strict):
    interval: Literal["1m", "5m", "15m", "1h", "4h", "1d"] = "1h"
    history_bars: int = Field(2000, ge=100, le=20_000)
    snapshot_seconds: int = Field(30, ge=5, le=3600)
    ohlcv_refresh_seconds: int = Field(300, ge=30, le=86_400)
    http_timeout_seconds: float = Field(15.0, gt=0, le=120)
    max_retries: int = Field(5, ge=0, le=12)
    backoff_base_seconds: float = Field(1.0, gt=0, le=60)
    backoff_max_seconds: float = Field(60.0, gt=0, le=900)
    stream_slots: bool = True


class WalkForwardCfg(_Strict):
    splits: int = Field(4, ge=2, le=20)
    in_sample_fraction: float = Field(0.7, gt=0.3, lt=0.95)
    # Flag when OOS Sharpe < ratio * IS Sharpe (IS > 0) or OOS return is negative while IS is positive.
    degradation_ratio: float = Field(0.5, ge=0, le=1)


class ReadinessCfg(_Strict):
    """Evidence a strategy must show in paper trading before real money is even worth discussing.
    Ghostfill has no live mode; these gates only report."""

    min_paper_days: float = Field(30, ge=1, le=365)
    min_closed_trades: int = Field(50, ge=1, le=10_000)
    max_drawdown_pct: float = Field(10, gt=0, le=100)
    validation_every_hours: float = Field(24, ge=1, le=168)


class Settings(_Strict):
    tokens: list[TokenCfg] = Field(min_length=1)
    strategy: StrategyCfg
    fees: FeesCfg = FeesCfg()
    risk: RiskCfg = RiskCfg()
    screen: ScreenCfg = ScreenCfg()
    data: DataCfg = DataCfg()
    walk_forward: WalkForwardCfg = WalkForwardCfg()
    readiness: ReadinessCfg = ReadinessCfg()
    sol_mint: str = "So11111111111111111111111111111111111111112"
    seed: int = 7

    # From environment, never from YAML.
    rpc_url: str = ""
    ws_url: str = ""
    db_path: str = ""
    cache_dir: str = ""

    @model_validator(mode="after")
    def _unique(self) -> "Settings":
        mints = [t.mint for t in self.tokens]
        dupes = {m for m in mints if mints.count(m) > 1}
        if dupes:
            raise ValueError(f"duplicate token mints: {sorted(dupes)}")
        if self.risk.max_open_positions * self.risk.position_size_pct > 100:
            raise ValueError("max_open_positions * position_size_pct exceeds 100% of the portfolio")
        return self


def _validate_rpc_url(url: str) -> list[str]:
    errs = []
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        errs.append("RPC_URL must be an http(s) URL with a host")
    return errs


def load_settings(path: str | os.PathLike, env: dict[str, str] | None = None, require_rpc: bool = True) -> Settings:
    env = dict(os.environ if env is None else env)
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"config file is not valid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError("config file must contain a mapping at the top level")
    for key in ("rpc_url", "ws_url", "db_path", "cache_dir"):
        if key in raw:
            raise ConfigError(f"{key} must come from the environment, not the YAML file")

    errors: list[str] = []
    rpc_url = env.get("RPC_URL", "").strip()
    if require_rpc:
        if not rpc_url:
            errors.append("RPC_URL environment variable is required")
        else:
            errors += _validate_rpc_url(rpc_url)
    ws_url = env.get("WS_URL", "").strip()
    if not ws_url and rpc_url:
        ws_url = "ws" + rpc_url[4:] if rpc_url.startswith("http") else ""
    raw.update(
        rpc_url=rpc_url,
        ws_url=ws_url,
        db_path=env.get("JOURNAL_DB_PATH", "./data/journal.sqlite3"),
        cache_dir=env.get("CACHE_DIR", "./data/cache"),
    )
    try:
        settings = Settings.model_validate(raw)
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"]) or "<root>"
            errors.append(f"{loc}: {err['msg']}")
        settings = None
    if errors:
        raise ConfigError("invalid configuration:\n  - " + "\n  - ".join(errors))
    return settings

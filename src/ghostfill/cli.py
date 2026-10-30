"""Command line: `ghostfill check-config`, `ghostfill backtest`, `ghostfill run`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

import pandas as pd

from . import MODE
from .config import ConfigError, Settings, load_settings
from .logutil import redact_url, setup_logging
from .strategies import build_strategy

log = logging.getLogger("ghostfill")


def _load(path: str, require_rpc: bool) -> Settings:
    try:
        settings = load_settings(path, require_rpc=require_rpc)
        build_strategy(settings.strategy.name, dict(settings.strategy.params))
    except (ConfigError, ValueError) as e:
        print(f"ghostfill: configuration error\n{e}", file=sys.stderr)
        sys.exit(2)
    return settings


def _make_market(settings: Settings, with_rpc: bool):
    from .data.cache import OhlcvCache
    from .data.http import HttpClient
    from .data.rpc import SolanaRpc
    from .market import MarketData

    d = settings.data
    http = HttpClient(d.http_timeout_seconds, d.max_retries, d.backoff_base_seconds, d.backoff_max_seconds,
                      seed=settings.seed)
    rpc = SolanaRpc(settings.rpc_url, http) if with_rpc and settings.rpc_url else None
    return http, MarketData(http, OhlcvCache(settings.cache_dir), rpc)


async def _backtest(settings: Settings) -> int:
    from .backtest import (LIMITATIONS, BacktestInput, format_metrics, format_walk_forward, run_backtest,
                           walk_forward)

    http, market = _make_market(settings, with_rpc=False)
    mints = [t.mint for t in settings.tokens]
    labels = {t.mint: t.label for t in settings.tokens}
    interval, n = settings.data.interval, settings.data.history_bars
    bars, liq, dex = {}, {}, {}
    sol_price: pd.Series | float = 150.0
    try:
        snaps = await market.snapshots(mints + [settings.sol_mint])
        for m in mints:
            s = snaps.get(m)
            if s is None:
                print(f"skipping {labels[m] or m}: no market snapshot")
                continue
            df = await market.history(m, s.pool_address, interval, n)
            if len(df):
                bars[m] = df[["open", "high", "low", "close", "volume"]]
                liq[m], dex[m] = s.liquidity_usd, s.dex_id
        sol = snaps.get(settings.sol_mint)
        if sol is not None:
            sol_price = (await market.history(settings.sol_mint, sol.pool_address, interval, n))["close"]
        else:
            print("warning: no SOL price; network fees use $150/SOL")
    finally:
        await http.aclose()
    if not bars:
        print("no data to backtest", file=sys.stderr)
        return 1

    inp = BacktestInput(bars, liq, dex, sol_price, labels)
    strat = build_strategy(settings.strategy.name, dict(settings.strategy.params))
    start = min(df.index[0] for df in bars.values())
    end = max(df.index[-1] for df in bars.values())
    print(f"ghostfill backtest ({MODE} simulation): {strat} on {interval} bars, {start:%Y-%m-%d} -> {end:%Y-%m-%d}")
    for m, df in bars.items():
        print(f"  {labels.get(m) or '?':<6} {m}  bars={len(df):<5} liquidity=${liq[m]:,.0f} dex={dex.get(m, '?')}")
    res = run_backtest(inp, strat, settings)
    print()
    print(format_metrics("FULL PERIOD (configured parameters)", res.metrics))
    print(f"  Total slippage  ${res.total_slippage_usd:,.2f}")
    print(f"  Failed tx       {res.failed_tx} (simulated failures, network fee still paid)")
    print(f"  Slippage blocks {res.slippage_rejections} (orders over max_slippage_bps, not sent)")
    bh = {m: 100 * (df['close'].iloc[-1] / df['close'].iloc[strat.warmup] - 1) for m, df in bars.items()}
    print("  Buy-and-hold reference (no costs): " + ", ".join(f"{labels.get(m) or m[:6]} {v:+.1f}%" for m, v in bh.items()))
    print()
    print(format_walk_forward(walk_forward(inp, settings)))
    print()
    print(LIMITATIONS)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ghostfill", description="Paper trading for Solana tokens. Simulation only.")
    ap.add_argument("--config", default=os.environ.get("GHOSTFILL_CONFIG", "config/config.yaml"))
    ap.add_argument("--log-level", default="INFO")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-config", help="validate configuration and exit")
    sub.add_parser("backtest", help="run a backtest with walk-forward validation")
    run = sub.add_parser("run", help="run the paper engine and the read-only status API")
    run.add_argument("--host", default="0.0.0.0")
    run.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)

    settings = _load(args.config, require_rpc=args.cmd != "backtest")
    setup_logging([settings.rpc_url, settings.ws_url], args.log_level)

    if args.cmd == "check-config":
        print(f"config OK: {len(settings.tokens)} tokens, strategy {settings.strategy.name}, "
              f"RPC {redact_url(settings.rpc_url)}, journal {settings.db_path}")
        return 0
    if args.cmd == "backtest":
        return asyncio.run(_backtest(settings))
    if args.cmd == "run":
        from .service.app import serve

        serve(settings, args.host, args.port)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

# Ghostfill

Paper trading for Solana token strategies on live mainnet market data.
It runs continuously in Docker, records simulated fills to SQLite, and
exposes a read-only status API.

**This is a simulation.** It never signs or submits a transaction, holds no
keys, has no wallet code, and has no live trading mode. A test
(`tests/test_no_signing.py`) scans the whole source tree and fails if signing,
wallet loading or transaction submission code appears. The RPC client only
allows read methods (`getAccountInfo`, `getMultipleAccounts`,
`getTokenSupply`, `getTokenLargestAccounts`, `getSlot`, `getHealth`).

> **Warning: most strategies lose money after costs.** Fees, slippage, failed
> transactions and MEV eat small edges. The two example strategies here lose
> money in the backtest below. Nothing in this project predicts or guarantees
> profit, and a good paper result does not mean a strategy would make money
> with real funds.

## Project tree

```
ghostfill/
├── Dockerfile                  slim base, non-root user, no secrets baked in
├── docker-compose.yml          named volume for SQLite + Parquet, RPC_URL at runtime
├── pyproject.toml
├── config/
│   ├── config.yaml             MA crossover (default)
│   └── zscore.yaml             z-score mean reversion
├── scripts/setup.sh            one-shot local setup, tests and backtest
├── extension/                  Chrome extension (badge + popup) for the local API
├── src/ghostfill/
│   ├── config.py               YAML + env loading, validation at startup
│   ├── logutil.py              logging with RPC URL redaction
│   ├── mints.py                base58 mint validation
│   ├── data/
│   │   ├── http.py             httpx client: exponential backoff, HTTP 429, per-host pacing
│   │   ├── rpc.py              read-only Solana JSON-RPC (allowlisted methods)
│   │   ├── sources.py          DexScreener snapshots, GeckoTerminal OHLCV
│   │   ├── cache.py            Parquet OHLCV cache keyed by mint + interval
│   │   └── stream.py           websocket slotSubscribe (chain liveness)
│   ├── market.py               data facade used by engine and backtester
│   ├── screen.py               token screen
│   ├── strategies/             Strategy base class, ma_crossover, zscore_reversion
│   ├── costs.py                cost model (fees, priority fee, slippage, failures)
│   ├── risk.py                 risk limits
│   ├── portfolio.py            simulated portfolio shared by engine and backtester
│   ├── journal.py              SQLite journal
│   ├── backtest.py             backtester, metrics, walk-forward
│   ├── engine.py               live paper engine
│   ├── service/app.py          FastAPI read-only endpoints and dashboard
│   ├── service/static/         dashboard.html
│   └── cli.py                  `ghostfill check-config | backtest | run`
└── tests/                      pytest suite
```

## Data sources

| Data | Source | Notes |
|---|---|---|
| Price, liquidity, 24h volume, pool age | DexScreener `tokens/v1/solana` | deepest pool where the mint is the **base** token |
| OHLCV history | GeckoTerminal pool OHLCV | priced in USD for the configured mint, cached as Parquet |
| Mint/freeze authority, Token-2022 extensions, supply, top holders | your `RPC_URL` | read-only calls |
| Slot (liveness) | websocket `slotSubscribe` on `WS_URL` (derived from `RPC_URL` if unset) | optional |

Tokens are identified by mint address everywhere. Symbols from APIs are shown
for display only and never used to match a token.

## Setup

Requirements: Python 3.11+ (local runs) or Docker with the compose plugin.

You need a mainnet RPC endpoint. The public `https://api.mainnet-beta.solana.com`
works for snapshots and mint checks but rate-limits `getTokenLargestAccounts`,
so the holder check fails closed and **every token is rejected**. Use a
provider endpoint (Helius, Triton, QuickNode, etc.) for the screen to pass.

```bash
export RPC_URL='https://your-provider.example/?api-key=...'
./scripts/setup.sh          # venv, install, tests, config check, backtest
```

`RPC_URL` may contain an API key. It is never written to logs: the logger
redacts it everywhere (tracebacks included), and only the host is ever shown.

### Configuration

`config/config.yaml` holds token mints, strategy parameters and search grid,
fee and slippage assumptions, risk limits, screen thresholds and polling
intervals. Environment variables:

| Variable | Required | Default |
|---|---|---|
| `RPC_URL` | yes (for `run`, `check-config`) | |
| `WS_URL` | no | `RPC_URL` with `ws(s)://` |
| `JOURNAL_DB_PATH` | no | `./data/journal.sqlite3` (`/data/journal.sqlite3` in Docker) |
| `CACHE_DIR` | no | `./data/cache` (`/data/cache` in Docker) |
| `GHOSTFILL_CONFIG` | no | `config/config.yaml` |

The config is validated at startup. Unknown keys, invalid mint addresses,
out-of-range values (for example `failure_probability: 1.5`), duplicate mints,
or `max_open_positions × position_size_pct > 100` exit with code 2 and a list
of every problem:

```bash
.venv/bin/ghostfill check-config
```

## Run with Docker

```bash
export RPC_URL='https://your-provider.example/?api-key=...'
docker compose up -d --build
docker compose logs -f ghostfill
```

Every `docker compose` command (`ps`, `logs`, `down`, ...) needs `RPC_URL` set,
because the compose file requires it. Putting `RPC_URL=...` in a `.env` file
next to `docker-compose.yml` saves exporting it each time.

The image contains no RPC URL or keys. Compose passes `RPC_URL` from your shell
(or a local `.env` file, which is git- and docker-ignored). The SQLite journal
and Parquet cache live in the named volume `ghostfill-data`, so they survive
restarts and rebuilds; on restart the engine reloads cash, open positions,
daily-loss state and the last processed bar from SQLite. The API is bound to
`127.0.0.1:8000` only. The container runs as a non-root user with a read-only
root filesystem and all capabilities dropped.

To use the z-score strategy: `GHOSTFILL_CONFIG_FILE=zscore.yaml docker compose up -d`.

## Run a backtest

```bash
.venv/bin/ghostfill backtest                          # MA crossover
.venv/bin/ghostfill --config config/zscore.yaml backtest
docker compose run --rm backtest                     # inside Docker
```

The backtester uses the same `Portfolio`, `CostModel` and `RiskManager` as the
live engine. Signals computed on bar *t*'s close fill at bar *t+1*'s open; stops
and take-profits are checked against each bar's low/high (stop first if both
are touched); open positions are liquidated with costs at the end. It reports
CAGR, Sharpe, max drawdown, win rate, profit factor, trade count, total fees,
slippage, failed transactions and max-slippage blocks, then runs walk-forward
validation: the timeline is split into folds, parameters are chosen on each
fold's in-sample part, and evaluated on the following out-of-sample part. A
strategy is **FLAGGED** when mean OOS Sharpe falls below
`degradation_ratio × mean IS Sharpe`, when a positive IS return turns negative
OOS, or when no parameter set has a positive IS Sharpe. Every run ends with a
limitations section (survivorship bias, incomplete liquidity history, no MEV
modeling).

The backtest does not apply the token screen, because the screen needs current
on-chain facts that do not exist for past dates.

### Backtest results (run 2026-10-30, 1h bars, 2026-08-08 to 2026-10-30, JUP / BONK / WIF / RAY)

| | MA crossover (12/48) | Z-score reversion (48, 2.0, 0.0) |
|---|---|---|
| CAGR | −25.8% | −36.7% |
| Total return | −6.6% | −9.9% |
| Sharpe | −1.76 | −4.52 |
| Max drawdown | −15.6% | −10.7% |
| Win rate | 22.0% | 44.0% |
| Profit factor | 0.78 | 0.27 |
| Trades | 82 | 50 |
| Total fees | $639 | $371 |
| Walk-forward | FLAGGED (OOS Sharpe −0.29 vs IS 3.31) | FLAGGED (OOS Sharpe −2.77 vs IS 0.99) |

Over the same window buy-and-hold (no costs) was positive for all four tokens.
Both example strategies lost money after costs. Treat them as code examples,
not as strategies.

## Dashboard

Open <http://localhost:8000/> while the service runs. It is a read-only
terminal served by the same API, refreshing every 5 seconds:

* **Status bar:** PAPER MODE badge, uptime, engine cycles, latest mainnet slot, data age.
* **KPI tiles:** simulated balance, total P&L, today's P&L (or the daily-loss halt), win rate.
* **Balance history:** equity, one point a minute, against the starting balance.
* **Activity log:** every real engine event with a tag: `[SCAN]`/`[PASS]` rug checks,
  `[SIGNAL]` bar decisions, `[SKIP]`/`[RISK]` blocked entries, `[FILL]` simulated fills,
  `[FAIL]` simulated failures, `[EXIT]` closed trades with P&L. Filter by trades, scans or signals.
* **Watchlist:** per token, the last 96 bars with the strategy's own indicator lines and the
  periods it was long shaded, the screen strip (one cell per check; hover for the reason),
  the verdict and the current signal.
* Open positions, trades, risk limits and engine details.

Light and dark themes follow the system setting; the theme button overrides it.
The engine screens every configured token in the background, so verdicts show
even before any buy signal.

## Chrome extension

`extension/` is a Manifest V3 extension for the same local API.

* The toolbar badge shows today's paper P&L (refreshed every minute; `off` if the API is unreachable).
* The popup shows equity, P&L, each token's screen verdict and signal, and open positions, and opens the full dashboard.
* It only makes GET requests to `localhost` / `127.0.0.1`. The API address can be changed in the popup.

Install: open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and choose the
`extension` folder.

## Holder data fallback

The public Solana RPC rate-limits `getTokenLargestAccounts`. When the RPC lookup
fails, the screen falls back to RugCheck's public token report
(`screen.holder_fallback: rugcheck`; set `none` to disable). The report must be
for the same mint, and the source is recorded with each verdict (`holders via
rpc` or `holders via rugcheck`). After a rate limit the RPC lookup is skipped for
30 minutes. Missing data still fails closed, and screens with missing data are
retried after `retry_unavailable_seconds`.

## Reading /status

```bash
curl -s localhost:8000/health
curl -s localhost:8000/status | python3 -m json.tool
curl -s 'localhost:8000/journal?limit=20' | python3 -m json.tool
```

`GET /status` fields:

| Field | Meaning |
|---|---|
| `mode` | always `PAPER` |
| `cash_usd`, `equity_usd` | simulated cash, and cash plus open positions at last mark |
| `realized_pnl_usd` | closed trades net of all fees, plus network fees lost on failed transactions |
| `unrealized_pnl_usd` | open positions at last mark minus their entry cost (entry fees included; exit costs not yet paid) |
| `open_positions` | per position: mint, qty, entry price/time, mark price, unrealized PnL |
| `total_fees_usd`, `closed_trades` | totals from the journal |
| `entries_halted_today` | daily loss limit hit; no new entries until the next UTC day |
| `last_data_timestamp`, `data_age_seconds` | when the last market snapshot was fetched; a large age means data is stale |
| `last_slot` | latest mainnet slot seen on the websocket |
| `screens` | latest screen result per mint with reasons and metrics |

`GET /journal?limit=N` (1 to 500) returns recent simulated trades,
rejections, signal decisions and equity history. All routes are GET; the API opens SQLite read-only.

## Token screen

Applied before every simulated entry, cached for `screen.refresh_seconds`:

1. Reject an active mint authority or freeze authority, unless the mint is in `authority_allowlist`.
2. Reject risky Token-2022 extensions (`permanentDelegate`, `transferHook`, `nonTransferable`, `pausableConfig`), unless allowlisted.
3. Reject if the top `top_holder_count` token accounts hold more than `max_top_holder_pct` of supply. Accounts owned by the pool address and by `holder_exclude_owners` are excluded. Other AMM vaults (for example Raydium's authority-owned vaults) count as holders unless you add their owner to `holder_exclude_owners`.
4. Require minimum liquidity, 24h volume and pool age.
5. Missing data fails closed: if a fact cannot be fetched, the token is rejected.

Every rejection is logged and stored in the `rejections` table with its reason.

**These checks reduce but do not eliminate rug pull and honeypot risk.** A
token can pass every check and still be untradeable, be drained, or go to zero.

## Cost model and risk limits

Each simulated fill pays the pool fee (per DEX, configurable), a platform fee,
the base network fee plus a priority-fee assumption (lamports, converted at the
live SOL price), and slippage = base bps + constant-product impact against half
the pool's liquidity, so it scales with trade size relative to liquidity. A
configurable fraction of transactions fail at random; a failed transaction
still pays the network fee.

Risk limits, all enforced in code and tested (`tests/test_risk.py`):
position size as a percentage of simulated equity, per-trade stop loss and
take profit, a daily loss halt (latched until the next UTC day), max open
positions, and max slippage per fill (orders above it are not sent). After a
stop or take-profit exit, a mint is not re-entered until the strategy's target
goes flat again.

Fee values in `config.yaml` are assumptions, not measured fees for specific
pools. Check the pools you care about.

## Strategies

Subclass `ghostfill.strategies.Strategy` and implement `generate_signals(df)`,
returning a target position in [0, 1] per bar. A signal at bar *t* may use
only rows up to *t*. `tests/test_signals.py` changes future rows and fails if
any past signal changes, and also checks that a deliberately cheating strategy
is caught.

## Tests

```bash
.venv/bin/pytest
```

Covers the risk manager, cost model, token screen, signal look-ahead, the
no-signing source scan, config validation and redaction, HTTP backoff and 429
handling, the Parquet cache, the backtester and walk-forward flag, the engine's
restart recovery, and the read-only API.

## Known limits

* GeckoTerminal's free API is strict about rate limits; the client paces
  requests to it (one every 6s) and backs off on 429, so the first history
  fetch takes a minute or two. Later runs read the Parquet cache.
* One pool per token (the deepest by liquidity) is used for prices and bars.
* The public Solana websocket drops connections now and then; the slot stream reconnects with backoff.

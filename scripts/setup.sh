#!/usr/bin/env bash
# One-shot local setup for ghostfill (paper trading only).
#   export RPC_URL='https://your-provider.example/?api-key=...'
#   ./scripts/setup.sh            # install, test, check config, backtest
#   ./scripts/setup.sh --docker   # same, then build and start with docker compose
set -euo pipefail
cd "$(dirname "$0")/.."

PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || { echo "Python 3.11+ is required (brew install python@3.13)"; exit 1; }

if [ -z "${RPC_URL:-}" ]; then
  echo "RPC_URL is not set. Using the public endpoint; the holder check will fail closed."
  export RPC_URL="https://api.mainnet-beta.solana.com"
fi

echo "==> virtualenv and install ($PY)"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e '.[test]'

echo "==> tests"
.venv/bin/pytest -q -p no:warnings

echo "==> config check"
.venv/bin/ghostfill check-config

echo "==> backtest (first run fetches history; GeckoTerminal is rate limited, allow a few minutes)"
.venv/bin/ghostfill backtest

if [ "${1:-}" = "--docker" ]; then
  echo "==> docker compose up"
  docker compose up -d --build
  echo "waiting for /health..."
  for _ in $(seq 1 30); do
    curl -fs localhost:8000/health && break || sleep 2
  done
  echo
  curl -s localhost:8000/status | .venv/bin/python -m json.tool
fi

echo
echo "Done. Start the service locally with:  .venv/bin/ghostfill run --port 8000"
echo "Then:  curl -s localhost:8000/status | python3 -m json.tool"

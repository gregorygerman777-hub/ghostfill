import logging
import subprocess
import sys
from pathlib import Path

import pytest

from ghostfill.config import ConfigError, load_settings
from ghostfill.logutil import RedactingFormatter, redact_url

ROOT = Path(__file__).resolve().parents[1]
ENV = {"RPC_URL": "https://mainnet.helius-rpc.com/?api-key=SECRETKEY123"}


def write(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text)
    return p


GOOD = """
tokens: [{mint: JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN}]
strategy: {name: ma_crossover, params: {fast: 5, slow: 20}}
"""


def test_shipped_configs_are_valid():
    for name in ("config.yaml", "zscore.yaml"):
        s = load_settings(ROOT / "config" / name, env=ENV)
        assert s.tokens and s.rpc_url == ENV["RPC_URL"]


def test_minimal_config_loads_with_defaults(tmp_path):
    s = load_settings(write(tmp_path, GOOD), env=ENV)
    assert s.risk.max_open_positions == 3 and s.ws_url.startswith("wss://")


def test_missing_rpc_url(tmp_path):
    with pytest.raises(ConfigError, match="RPC_URL"):
        load_settings(write(tmp_path, GOOD), env={})


def test_rpc_url_not_allowed_in_yaml(tmp_path):
    with pytest.raises(ConfigError, match="environment"):
        load_settings(write(tmp_path, GOOD + "rpc_url: https://x\n"), env=ENV)


@pytest.mark.parametrize("snippet,match", [
    ("tokens: [{mint: not-a-mint}]", "mint"),
    ("tokens: []", "tokens"),
    ("risk: {position_size_pct: 0}", "position_size_pct"),
    ("risk: {stop_loss_pct: 150}", "stop_loss_pct"),
    ("fees: {failure_probability: 1.5}", "failure_probability"),
    ("data: {interval: 7m}", "interval"),
    ("screen: {max_top_holder_pct: 0}", "max_top_holder_pct"),
    ("risk: {position_size_pct: 40, max_open_positions: 3}", "exceeds 100%"),
    ("unknown_key: 1", "unknown_key"),
])
def test_out_of_range_values_rejected(tmp_path, snippet, match):
    strategy_only = "strategy: {name: ma_crossover, params: {fast: 5, slow: 20}}\n"
    text = strategy_only if snippet.startswith("tokens") else GOOD
    with pytest.raises(ConfigError, match=match):
        load_settings(write(tmp_path, text + snippet + "\n"), env=ENV)


def test_duplicate_mints_rejected(tmp_path):
    text = GOOD.replace("tokens: [{mint: JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN}]",
                        "tokens: [{mint: JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN}, "
                        "{mint: JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN}]")
    with pytest.raises(ConfigError, match="duplicate"):
        load_settings(write(tmp_path, text), env=ENV)


def test_cli_exits_with_clear_error(tmp_path):
    bad = write(tmp_path, GOOD + "risk: {daily_loss_limit_pct: -1}\n")
    r = subprocess.run([sys.executable, "-m", "ghostfill.cli", "--config", str(bad), "check-config"],
                       capture_output=True, text=True, env={"RPC_URL": ENV["RPC_URL"], "PATH": "/usr/bin"})
    assert r.returncode == 2
    assert "daily_loss_limit_pct" in r.stderr and "configuration error" in r.stderr
    assert "SECRETKEY123" not in r.stderr + r.stdout


def test_cli_rejects_bad_strategy_params(tmp_path):
    bad = write(tmp_path, GOOD.replace("fast: 5, slow: 20", "fast: 30, slow: 20"))
    r = subprocess.run([sys.executable, "-m", "ghostfill.cli", "--config", str(bad), "check-config"],
                       capture_output=True, text=True, env={"RPC_URL": ENV["RPC_URL"], "PATH": "/usr/bin"})
    assert r.returncode == 2 and "fast < slow" in r.stderr


def test_redact_url_hides_keys():
    assert redact_url("https://mainnet.helius-rpc.com/?api-key=abc") == "https://mainnet.helius-rpc.com/<redacted>"
    assert redact_url("https://x.solana-mainnet.quiknode.pro/0123abcd/") == "https://x.solana-mainnet.quiknode.pro/<redacted>"
    assert redact_url("https://api.mainnet-beta.solana.com") == "https://api.mainnet-beta.solana.com"


def test_log_formatter_redacts_rpc_url_including_tracebacks():
    url = ENV["RPC_URL"]
    fmt = RedactingFormatter([url], "%(message)s")
    try:
        raise RuntimeError(f"failed calling {url}")
    except RuntimeError:
        import sys as _s
        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "error with %s", (url,), _s.exc_info())
    out = fmt.format(rec)
    assert "SECRETKEY123" not in out and "<redacted>" in out

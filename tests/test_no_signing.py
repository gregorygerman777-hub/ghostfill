"""Scan the source tree: no signing, no wallet loading, no transaction submission.

Patterns are assembled from fragments so this file does not match itself.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".venv", ".git", "__pycache__", ".pytest_cache", "node_modules"}
SKIP_TOP = {"data"}  # runtime journal and cache at the repo root only, not src/solpaper/data
SUFFIXES = {".py", ".yaml", ".yml", ".toml", ".txt", ".cfg", ".ini", ".sh", ".json", ".md", ".js", ".html", ".css", ""}
EXTRA_FILES = {"Dockerfile", "docker-compose.yml"}


def j(*parts: str) -> str:
    return "".join(parts)


# Case-sensitive regexes. Word boundaries keep names like `SolanaRpc` from matching module imports.
FORBIDDEN = [
    # transaction submission (JSON-RPC methods and SDK helpers)
    j("send", "Transaction"), j("send", "Raw", "Transaction"), j("send_", "transaction"),
    j("send_raw_", "transaction"), j("simulate", "Transaction"), j("request", "Airdrop"),
    j("send_and_", "confirm"), j("sendAnd", "Confirm"),
    # signing
    j("sign", "Transaction"), j("sign_", "transaction"), j("sign", "_message"), j("sign", "Message"),
    j("partial", "_sign"), j("partial", "Sign"), j("Versioned", "Transaction"), j("nacl", r"\.signing"),
    j("Signing", "Key"), j("ed25519", "_sign"),
    # wallets and keys
    j("Key", "pair"), j("key", "pair"), j("secret", "_key"), j(r"\bsecret", "Key"), j("private", "_key"),
    j("private", "Key"), j("seed", "_phrase"), j("mnem", "onic"), j("from_", "seed"), j("from_", "secret"),
    j(r"\bid", r"\.json"), j("WALLET", "_"), j("wal", "let", "_path"),
    # Solana SDKs that can build or sign transactions
    j("sol", "ders"), j(r"\bimport ", r"solana\b"), j(r"\bfrom ", r"solana\b"), j("anchor", "py"),
    j("solana", "-py"), j("b58decode", "_check"),
]
PATTERN = re.compile("|".join(FORBIDDEN))


def _files():
    for p in ROOT.rglob("*"):
        parts = p.relative_to(ROOT).parts
        if parts[0] in SKIP_TOP or any(part in SKIP_DIRS or part.endswith(".egg-info") for part in parts):
            continue
        if not p.is_file():
            continue
        if p.suffix in SUFFIXES or p.name in EXTRA_FILES:
            yield p


def test_scan_covers_source_tree():
    names = {p.relative_to(ROOT).as_posix() for p in _files()}
    for must in ("src/solpaper/data/rpc.py", "src/solpaper/engine.py", "src/solpaper/portfolio.py",
                 "Dockerfile", "docker-compose.yml", "pyproject.toml", "config/config.yaml"):
        assert must in names, must


def test_no_signing_wallet_or_submission_code():
    hits = []
    for p in _files():
        text = p.read_text(errors="ignore")
        for n, line in enumerate(text.splitlines(), 1):
            m = PATTERN.search(line)
            if m:
                hits.append(f"{p.relative_to(ROOT)}:{n}: {m.group(0)!r} in {line.strip()[:100]}")
    assert not hits, "forbidden signing/wallet/submission code found:\n" + "\n".join(hits)


def test_rpc_client_allows_read_methods_only():
    from solpaper.data.rpc import READ_ONLY_METHODS

    assert all(m.startswith("get") for m in READ_ONLY_METHODS)


async def _call(method):
    from solpaper.data.rpc import SolanaRpc

    class Boom:
        async def request_json(self, *a, **k):
            raise AssertionError("request must not be made")

    return await SolanaRpc("https://rpc.invalid", Boom()).call(method)


def test_rpc_client_refuses_non_allowlisted_method():
    import asyncio

    import pytest

    with pytest.raises(PermissionError):
        asyncio.run(_call(j("send", "Transaction")))


def test_no_live_mode():
    from solpaper import MODE
    from solpaper.config import Settings

    assert MODE == "PAPER"
    assert "mode" not in Settings.model_fields and "live" not in Settings.model_fields

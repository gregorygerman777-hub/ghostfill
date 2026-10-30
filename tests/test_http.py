import asyncio

import httpx
import pytest

from solpaper.data.http import HttpClient, HttpError, retry_after_seconds
from solpaper.data.rpc import SolanaRpc


def client(handler, sleeps, **kw):
    async def fake_sleep(s):
        sleeps.append(s)

    return HttpClient(transport=httpx.MockTransport(handler), sleep=fake_sleep, min_interval={}, seed=1, **kw)


def run(coro):
    return asyncio.run(coro)


def test_retries_on_429_and_respects_retry_after():
    calls, sleeps = [], []

    def handler(req):
        calls.append(req)
        if len(calls) < 3:
            return httpx.Response(429, headers={"retry-after": "7"})
        return httpx.Response(200, json={"ok": True})

    async def go():
        c = client(handler, sleeps, backoff_base=0.1)
        try:
            return await c.get_json("https://api.example.com/x")
        finally:
            await c.aclose()

    assert run(go()) == {"ok": True}
    assert len(calls) == 3
    assert sleeps == [7.0, 7.0]


def test_exponential_backoff_on_server_errors_then_gives_up():
    sleeps = []

    async def go():
        c = client(lambda r: httpx.Response(503), sleeps, max_retries=4, backoff_base=1.0, backoff_max=100)
        try:
            await c.get_json("https://api.example.com/x")
        finally:
            await c.aclose()

    with pytest.raises(HttpError):
        run(go())
    assert len(sleeps) == 4
    for i, s in enumerate(sleeps):
        assert 0.5 * 2**i <= s <= 2**i  # jittered exponential
    assert sleeps == sorted(sleeps)


def test_429_without_retry_after_waits_minimum():
    sleeps, n = [], []

    def handler(req):
        n.append(1)
        return httpx.Response(429) if len(n) == 1 else httpx.Response(200, json=[])

    async def go():
        c = client(handler, sleeps, rate_limit_min_wait=12.0)
        try:
            return await c.get_json("https://api.example.com/x")
        finally:
            await c.aclose()

    run(go())
    assert sleeps == [12.0]


def test_client_errors_are_not_retried():
    sleeps = []

    async def go():
        c = client(lambda r: httpx.Response(404, text="nope"), sleeps)
        try:
            await c.get_json("https://api.example.com/x")
        finally:
            await c.aclose()

    with pytest.raises(HttpError, match="404"):
        run(go())
    assert sleeps == []


def test_jsonrpc_rate_limit_body_is_retried_and_url_redacted():
    sleeps, n = [], []
    url = "https://rpc.example.com/?api-key=SECRET"

    def handler(req):
        n.append(1)
        if len(n) == 1:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": 429, "message": "slow"}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": 123})

    async def go():
        c = client(handler, sleeps)
        try:
            return await SolanaRpc(url, c).get_slot()
        finally:
            await c.aclose()

    assert run(go()) == 123 and len(sleeps) == 1

    async def fail():
        c = client(lambda r: httpx.Response(500), [], max_retries=0)
        try:
            await SolanaRpc(url, c).get_slot()
        finally:
            await c.aclose()

    with pytest.raises(HttpError) as e:
        run(fail())
    assert "SECRET" not in str(e.value)


def test_retry_after_parsing():
    assert retry_after_seconds(httpx.Response(429, headers={"retry-after": "3"})) == 3.0
    assert retry_after_seconds(httpx.Response(429)) is None

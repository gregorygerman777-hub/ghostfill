"""Read-only Solana JSON-RPC client.

Only the methods in READ_ONLY_METHODS can be called. Anything else raises
before a request is made, so this client cannot be used to change chain state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .http import HttpClient, HttpError, RateLimited

READ_ONLY_METHODS = frozenset(
    {"getAccountInfo", "getMultipleAccounts", "getTokenSupply", "getTokenLargestAccounts", "getSlot", "getHealth"}
)

# JSON-RPC error codes that mean "slow down" on common providers.
_RATE_LIMIT_CODES = {429, -32005, -32429}


@dataclass(frozen=True)
class MintInfo:
    mint: str
    program: str  # "spl-token" or "spl-token-2022"
    decimals: int
    supply: int  # raw units
    mint_authority: str | None
    freeze_authority: str | None
    extensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class HolderAccount:
    token_account: str
    owner: str | None
    amount: int  # raw units


class RpcError(Exception):
    def __init__(self, method: str, code: int | None, message: str, data: Any = None):
        self.method, self.code, self.message, self.data = method, code, message, data
        super().__init__(f"RPC {method} failed ({code}): {message}")


def _check_rate_limit(body: Any) -> None:
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        if body["error"].get("code") in _RATE_LIMIT_CODES:
            raise RateLimited(str(body["error"].get("message", "rate limited")))


class SolanaRpc:
    def __init__(self, url: str, http: HttpClient):
        self._url = url
        self._http = http
        self._id = 0

    async def call(self, method: str, params: list[Any] | None = None) -> Any:
        if method not in READ_ONLY_METHODS:
            raise PermissionError(f"RPC method {method!r} is not in the read-only allowlist")
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or []}
        body = await self._http.request_json("POST", self._url, json=payload, check_body=_check_rate_limit)
        if "error" in body:
            err = body["error"] or {}
            raise RpcError(method, err.get("code"), str(err.get("message", "")), err.get("data"))
        return body.get("result")

    async def get_slot(self) -> int:
        return int(await self.call("getSlot", [{"commitment": "confirmed"}]))

    async def get_mint_info(self, mint: str) -> MintInfo:
        res = await self.call("getAccountInfo", [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        value = (res or {}).get("value")
        if not value:
            raise RpcError("getAccountInfo", None, f"mint account {mint} not found")
        data = value.get("data")
        if not isinstance(data, dict) or data.get("parsed", {}).get("type") != "mint":
            raise RpcError("getAccountInfo", None, f"account {mint} is not a token mint")
        info = data["parsed"]["info"]
        exts = tuple(sorted(e.get("extension", "") for e in info.get("extensions", []) if isinstance(e, dict)))
        return MintInfo(
            mint=mint,
            program=str(data.get("program", "")),
            decimals=int(info["decimals"]),
            supply=int(info["supply"]),
            mint_authority=info.get("mintAuthority"),
            freeze_authority=info.get("freezeAuthority"),
            extensions=exts,
        )

    async def get_top_holders(self, mint: str) -> list[HolderAccount]:
        """Up to 20 largest token accounts with their owners resolved."""
        res = await self.call("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
        rows = (res or {}).get("value") or []
        addresses = [r["address"] for r in rows]
        owners: dict[str, str | None] = {}
        if addresses:
            multi = await self.call("getMultipleAccounts", [addresses, {"encoding": "jsonParsed"}])
            for addr, acct in zip(addresses, (multi or {}).get("value") or []):
                owner = None
                if acct and isinstance(acct.get("data"), dict):
                    owner = acct["data"].get("parsed", {}).get("info", {}).get("owner")
                owners[addr] = owner
        return [HolderAccount(r["address"], owners.get(r["address"]), int(r["amount"])) for r in rows]


__all__ = ["SolanaRpc", "MintInfo", "HolderAccount", "RpcError", "HttpError", "READ_ONLY_METHODS"]

"""Read-only websocket slot subscription, used as a chain liveness signal for /status."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime

import websockets

from .http import backoff_delay
from .sources import utcnow

log = logging.getLogger(__name__)


class SlotStream:
    def __init__(self, ws_url: str, backoff_base: float = 1.0, backoff_max: float = 60.0):
        self._url = ws_url
        self._base = backoff_base
        self._max = backoff_max
        self._rng = random.Random()
        self.last_slot: int | None = None
        self.last_slot_at: datetime | None = None

    async def run(self) -> None:
        attempt = 0
        while True:
            try:
                async with websockets.connect(self._url, open_timeout=15, ping_interval=20) as ws:
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "slotSubscribe"}))
                    attempt = 0
                    async for raw in ws:
                        msg = json.loads(raw)
                        slot = (msg.get("params") or {}).get("result", {}).get("slot")
                        if slot is not None:
                            self.last_slot, self.last_slot_at = int(slot), utcnow()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # network errors are expected; keep retrying
                delay = backoff_delay(attempt, self._base, self._max, self._rng)
                # Public endpoints drop idle sockets routinely; only repeated failures are worth a warning.
                level = logging.WARNING if attempt >= 3 else logging.INFO
                log.log(level, "slot stream disconnected (%s); reconnecting in %.1fs", type(e).__name__, delay)
                attempt += 1
                await asyncio.sleep(delay)

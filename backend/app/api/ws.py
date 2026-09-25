"""WebSocket hub pushing live escalation events to counselor dashboards
(roadmap 3.1 — 'WebSocket push of flagged sessions').

Design:
  * /ws/counselor?token=<jwt> — authenticated via the same JWT as REST.
  * Hub keeps per-connection queues; broadcast() fans out JSON payloads.
  * Disconnects are pruned automatically; slow consumers are dropped rather
    than blocking the safety path (broadcast must never raise to callers).
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

logger = logging.getLogger("okoa.ws")


class EscalationHub:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue] = set()

    @property
    def client_count(self) -> int:
        return len(self._queues)

    def register(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._queues.add(q)
        return q

    def unregister(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    async def broadcast(self, payload: dict) -> None:
        """Non-blocking fan-out; drops messages for full queues."""
        dead = []
        for q in self._queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            logger.warning("dropping slow ws consumer")
            self._queues.discard(q)


hub = EscalationHub()
router = APIRouter(tags=["counselor-ws"])


@router.websocket("/ws/counselor")
async def counselor_ws(ws: WebSocket, token: str = Query(...)):
    # Lazy import to avoid circular dependency at module load.
    from app.auth.counselor_auth import decode_token

    try:
        claims = decode_token(token)
    except Exception:
        await ws.close(code=4401)
        return
    await ws.accept()
    q = hub.register()
    logger.info("counselor ws connected", extra={"event": "ws_connect",
                                                 "counselor": claims["username"]})
    try:
        # Send any queued events; also consume pings from the client.
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30)
                await ws.send_text(json.dumps(event))
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"type": "ping"}))
            # Client messages are ignored (server-push channel).
            try:
                while True:
                    await asyncio.wait_for(ws.receive_text(), timeout=0.01)
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister(q)
        logger.info("counselor ws disconnected", extra={"event": "ws_disconnect"})

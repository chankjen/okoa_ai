"""Health & observability endpoints (carried from Phase 0 skeleton)."""
from __future__ import annotations

import time

from fastapi import APIRouter, Request
from sqlalchemy import text

from app.db.session import get_engine

router = APIRouter(tags=["ops"])
_STARTED = time.monotonic()
REQUEST_COUNT = {"inbound_webhook": 0}


@router.get("/health")
async def health():
    return {"status": "ok", "uptime_s": round(time.monotonic() - _STARTED)}


@router.get("/ready")
async def ready():
    """Liveness against DB + Redis so orchestrators don't route to broken pods."""
    checks: dict[str, str] = {}
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"fail:{type(exc).__name__}"
    from app.services.session_store import get_redis

    r = await get_redis()
    checks["redis"] = "ok" if r is not None else "degraded"
    status = "ok" if checks["db"] == "ok" else "unhealthy"
    code = 200 if status == "ok" else 503
    from fastapi.responses import JSONResponse

    return JSONResponse({"status": status, **checks}, status_code=code)


@router.get("/metrics")
async def metrics(request: Request):
    """Minimal Prometheus-style text metrics (Phase 7 expands)."""
    pipeline = getattr(request.app.state, "pipeline", None)
    qsize = pipeline.queue.qsize() if pipeline else 0
    from fastapi.responses import PlainTextResponse

    body = "\n".join(
        [
            "# TYPE okoa_uptime_seconds gauge",
            f"okoa_uptime_seconds {round(time.monotonic() - _STARTED)}",
            "# TYPE okoa_pipeline_queue_size gauge",
            f"okoa_pipeline_queue_size {qsize}",
            "",
        ]
    )
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

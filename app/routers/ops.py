from __future__ import annotations

import asyncio

from fastapi import APIRouter

from .. import clients, config

router = APIRouter(prefix="/v1/ops", tags=["ops"])


@router.get("/services")
async def services():
    """
    Which of the five are live and which are still mocked.
    Point the clinician app's Settings screen at this instead of any hardcoded
    metric — it is the only honest source of 'what is actually running'.
    """
    results = await asyncio.gather(*(clients.service_health(k) for k in config.SERVICES))
    return {
        "c3_mode": config.C3_MODE,
        "fusion_weights_version": config.FUSION_WEIGHTS_VERSION,
        "fusion_weights": config.FUSION_WEIGHTS,
        "min_modalities": config.MIN_MODALITIES,
        "services": results,
        "all_live": all(r["mode"] == "live" and r["status"] == "ok" for r in results),
    }


@router.post("/warmup")
async def warmup():
    """
    Call this ~10 minutes before any demo. Free HF Spaces sleep after 48h and
    take 30s-several minutes to wake. Waking them on a schedule means your first
    live request is not the one your supervisor is watching.
    """
    results = await asyncio.gather(*(clients.service_health(k) for k in config.SERVICES))
    return {"woken": [r["service"] for r in results], "detail": results}

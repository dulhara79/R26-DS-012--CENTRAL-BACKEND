"""HTTP adapter for Uvindu's independently deployed Fusion Service.

The Central Backend owns orchestration and persistence, not fusion mathematics.
Authoritative fusion is requested only through the stable HTTP service boundary:
POST /v1/fuse/manual.

Tests may replace the fuse function with an explicit deterministic stub.
Production code must not import, vendor, or reimplement Fusion Service
scientific logic.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Dict, Optional

import httpx

# Kept as a compatibility field because /health currently reports fusion_mode.
# There is intentionally no in-process mode in this repository.
FUSION_MODE = "http"
FUSION_URL = os.getenv("FUSION_URL", "http://127.0.0.1:7861").rstrip("/")
FUSION_TOKEN = os.getenv("FUSION_API_TOKEN", "")
FUSION_TIMEOUT_S = float(os.getenv("FUSION_TIMEOUT_S", "20"))

BAND_FOR_TIER = {"Low": "GREEN", "Medium": "AMBER", "High": "RED", None: "GREY"}


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        v = value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
        return v.isoformat()
    return str(value)


def fuse(subject_id: str, readings: Dict[str, dict]) -> dict:
    """Send one subject's already-gated component readings to Fusion over HTTP."""
    components = {
        m: {
            "score": r.get("raw_score"),
            "available": True,
            "confidence": float(r.get("confidence", 0.5)),
            "coverage": float(r.get("coverage", 1.0)),
            "captured_at": _iso(r.get("captured_at")),
        }
        for m, r in readings.items()
    }

    result = _fuse_http(subject_id, components)
    # Compatibility with the pinned Central Backend response contract. This maps
    # an upstream tier label to its display band only; it does not tier a score.
    result.setdefault("band", BAND_FOR_TIER.get(result.get("tier"), "GREY"))
    result["subject_id"] = subject_id
    return result


def _fuse_http(subject_id: str, components: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    if FUSION_TOKEN:
        headers["Authorization"] = f"Bearer {FUSION_TOKEN}"
    r = httpx.post(
        f"{FUSION_URL}/v1/fuse/manual",
        headers=headers,
        json={
            "mrn": subject_id,
            "components": components,
            "already_harmonised": False,
        },
        timeout=FUSION_TIMEOUT_S,
    )
    r.raise_for_status()
    return r.json()

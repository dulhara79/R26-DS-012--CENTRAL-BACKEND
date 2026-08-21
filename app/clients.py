"""
Adapters for the five model services.

The orchestrator only ever calls `call_service("c1", payload)`. It has no idea
whether that hit a Hugging Face Space or a mock. That is the whole point: on
demo day you paste five URLs into .env, restart, and nothing above this file
changes.

Every response is passed through `coerce_envelope`, which is deliberately
paranoid. If a teammate ships `{"risk": 0.6}` instead of `{"score": 0.6}`, or
forgets `status`, or returns a string where a float belongs, the backend records
a usable reading instead of throwing a 500 the night before your viva.
"""
from __future__ import annotations

import asyncio
import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from . import config


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_dt(value: Any, fallback: datetime | None = None) -> datetime:
    fallback = fallback or utcnow()
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return fallback
    return fallback


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # reject NaN


# --------------------------------------------------------------------------
# Deterministic mocks — same subject always gets the same numbers
# --------------------------------------------------------------------------
def _rng(subject_id: str, salt: str) -> random.Random:
    seed = int(hashlib.sha256(f"{subject_id}|{salt}".encode()).hexdigest()[:12], 16)
    return random.Random(seed)


def _mock_c1(payload: dict) -> dict:
    sid = payload.get("subject_id", "?")
    r = _rng(sid, "c1")
    score = round(r.uniform(0.25, 0.80), 4)
    threshold = 2.80
    anomaly = round(threshold * (0.6 + score), 3)
    # Honour real ECG quality if the caller supplied it, so poor-signal
    # behaviour can be demonstrated deterministically instead of by luck.
    quality = _as_float((payload.get("features") or {}).get("ecg_quality"))
    if quality is None:
        quality = round(r.uniform(0.78, 0.98), 2)
    status = "poor_signal" if quality < 0.60 else "ok"
    return {
        "subject_id": sid, "modality": "c1_physiological",
        "score": None if status != "ok" else score, "status": status,
        "anomaly_score": anomaly, "threshold": threshold,
        "reconstruction_error": anomaly,
        "baseline_ready": True, "baseline_windows_seen": 1440,
        "baseline_windows_required": 1200,
        "horizon_minutes": 8, "signal_quality": quality,
        "captured_at": payload.get("window_end") or iso(utcnow()),
        "computed_at": iso(utcnow()),
        "model_version": "c1-lstmae-MOCK-v0",
    }


def _mock_c2(payload: dict) -> dict:
    sid = payload.get("subject_id", "?")
    days = len(payload.get("days") or [])
    r = _rng(sid, "c2")
    if days < config.C2_DAYS_REQUIRED:
        return {
            "subject_id": sid, "modality": "c2_behavioral",
            "score": None, "status": "insufficient_data",
            "days_of_history": days, "days_required": config.C2_DAYS_REQUIRED,
            "captured_at": iso(utcnow()), "computed_at": iso(utcnow()),
            "model_version": "c2-gatv2-MOCK-v0",
        }
    phen = r.choice([("A", "Social-Spatial Withdrawal"),
                     ("B", "Circadian Disruption"),
                     ("C", "Hypervigilant Mobility")])
    return {
        "subject_id": sid, "modality": "c2_behavioral",
        "score": round(r.uniform(0.20, 0.75), 4),
        # Honest by default: C2 is not clinically validated yet, so it is stored
        # and displayed but excluded from the composite. Set C2_STATUS=ok in .env
        # once Senu's validation lands — no other change is needed.
        "status": config.C2_MOCK_STATUS,
        "phenotype": phen[0], "phenotype_label": phen[1],
        "phenotype_confidence": round(r.uniform(0.55, 0.9), 2),
        "days_of_history": days, "days_required": config.C2_DAYS_REQUIRED,
        "nodes_present": days * 4, "nodes_expected": config.C2_DAYS_REQUIRED * 4,
        "captured_at": iso(utcnow()), "computed_at": iso(utcnow()),
        "model_version": "c2-gatv2-MOCK-v0",
    }


def _mock_c4(payload: dict) -> dict:
    sid = payload.get("subject_id", "?")
    note = payload.get("note_text") or ""
    support = payload.get("support_set") or []
    r = _rng(sid + note[:32], "c4")
    k = len(support)
    prob = round(r.uniform(0.18, 0.86), 4)
    threshold = 0.4036
    return {
        "subject_id": sid, "modality": "c4_clinical_nlp",
        "score": prob,
        "status": "ok" if k else "no_support_set",
        "prediction": "ANXIETY" if prob >= threshold else "CONTROL",
        "risk_score": round(min(1.0, prob * 1.05), 4),
        "calibrated_probability": prob,
        "confidence": round(r.uniform(0.6, 0.92), 2),
        "entropy": round(r.uniform(0.2, 0.7), 3),
        "threshold": threshold, "support_k": k, "ece": 0.061,
        "prototype_distance_anxiety": round(r.uniform(0.3, 0.7), 3),
        "prototype_distance_control": round(r.uniform(0.5, 1.0), 3),
        "attention_spans": [],          # mock returns NO weights, on purpose
        "support_contributions": [
            {"note_id": s.get("id", f"n-{i}"), "label": s.get("label", "anxiety"),
             "excerpt": (s.get("text") or "")[:60],
             "temporal_weight": round(r.uniform(0.4, 1.0), 2),
             "confidence_weight": round(r.uniform(0.5, 1.0), 2),
             "combined_weight": round(r.uniform(0.3, 0.95), 2),
             "note_date": s.get("note_date")}
            for i, s in enumerate(support[:5])
        ],
        "temporal_context": f"Visit {payload.get('visit_count', 1)}",
        "calibration_status": "calibrated",
        "evidence_note": ("Blinded evaluation: AUROC 0.8989 vs ProtoNet 0.9291 "
                          "with anxiety terms masked. Not a validated diagnostic."),
        "captured_at": payload.get("note_date") or iso(utcnow()),
        "computed_at": iso(utcnow()),
        "model_version": "TC-WPN-MOCK-v0",
    }


def _mock_fusion(payload: dict) -> dict:
    return local_fuse(payload, model_version="fusion-MOCK-v0")


def _mock_c3(payload: dict) -> dict:
    sid = payload.get("subject_id", "?")
    r = _rng(sid, "c3")
    composite = _as_float(payload.get("composite_score")) or 0.5
    tier = ("LOW" if composite < 0.25 else "MEDIUM" if composite < 0.5
            else "HIGH" if composite < 0.75 else "CRITICAL")
    neighbours = {"LOW": ["LOW", "MEDIUM"], "MEDIUM": ["LOW", "MEDIUM"],
                  "HIGH": ["MEDIUM", "HIGH"], "CRITICAL": ["HIGH", "CRITICAL"]}[tier]
    catalogue = [("iv-breath-478", "4-7-8 breathing"),
                 ("iv-ground-541", "5-4-3-2-1 grounding"),
                 ("iv-pmr-233", "Progressive muscle relaxation"),
                 ("iv-cbt-119", "Thought-record prompt")]
    r.shuffle(catalogue)
    return {
        "subject_id": sid, "tier": tier, "status": "ok",
        "tier_probability": round(r.uniform(0.55, 0.9), 2),
        "conformal_set": neighbours, "conformal_alpha": 0.10,
        "interventions": [
            {"id": cid, "name": name, "rank": i + 1,
             "similarity": round(r.uniform(0.6, 0.95), 2),
             "historical_success_rate": round(r.uniform(0.4, 0.8), 2)}
            for i, (cid, name) in enumerate(catalogue[:3])
        ],
        "xai": {"shap_top": [{"feature": "hrv_rmssd_delta", "value": -0.31},
                             {"feature": "screen_night_minutes", "value": 0.22}],
                "dice_counterfactuals": [], "similar_cases": []},
        "escalation": {"triggered": composite >= 0.85,
                       "rule": "composite >= 0.85 sustained >= 3 min"},
        "computed_at": iso(utcnow()),
        "model_version": "c3-gbdt-cbr-MOCK-v0",
    }


MOCKS = {"c1": _mock_c1, "c2": _mock_c2, "c4": _mock_c4,
         "fusion": _mock_fusion, "c3": _mock_c3}


# --------------------------------------------------------------------------
# Local fusion — used as mock AND as fallback when the real service is down
# --------------------------------------------------------------------------
def local_fuse(payload: dict, model_version: str = "fusion-LOCAL-v0") -> dict:
    components: dict = payload.get("components") or {}
    weights = config.FUSION_WEIGHTS
    usable, excluded, scores = {}, [], {}

    for modality, base_w in weights.items():
        comp = components.get(modality) or {}
        score = _as_float(comp.get("score"))
        scores[modality] = score
        if comp.get("available") and comp.get("status") == "ok" and score is not None:
            usable[modality] = (score, base_w)
        else:
            excluded.append({"modality": modality,
                             "reason": comp.get("status") or "missing"})

    if len(usable) < int(payload.get("min_modalities", config.MIN_MODALITIES)):
        return {"subject_id": payload.get("subject_id"),
                "composite_score": None, "alert_level": None,
                "reason": f"only {len(usable)} fresh modality/modalities "
                          f"(min {config.MIN_MODALITIES})",
                "scores": scores, "excluded": excluded,
                "modalities_available": len(usable),
                "computed_at": iso(utcnow()), "model_version": model_version}

    total = sum(w for _, w in usable.values())
    applied = {m: round(w / total, 4) for m, (_, w) in usable.items()}
    composite = round(sum(s * (w / total) for s, w in usable.values()), 4)

    return {"subject_id": payload.get("subject_id"),
            "composite_score": composite,
            "alert_level": config.band_for(composite),
            "confidence": round(min(1.0, 0.5 + 0.15 * len(usable)), 2),
            "renormalised": len(usable) < len(weights),
            "modalities_available": len(usable),
            "weights": applied, "scores": scores, "excluded": excluded,
            "computed_at": iso(utcnow()), "model_version": model_version}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
async def call_service(key: str, payload: dict) -> tuple[dict, str]:
    """
    Returns (response_dict, source) where source is 'mock' | 'live' | 'error'.
    Never raises. A dead service becomes a status='error' reading, not a 500.
    """
    cfg = config.SERVICES[key]
    if cfg.mock:
        await asyncio.sleep(0)  # keep it awaitable/async-shaped
        return MOCKS[key](payload), "mock"

    headers = {"Content-Type": "application/json"}
    if config.HF_TOKEN:
        headers["Authorization"] = f"Bearer {config.HF_TOKEN}"

    last_err = "unknown error"
    for attempt in range(config.HTTP_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT_S) as client:
                resp = await client.post(cfg.predict_url, json=payload, headers=headers)
            if resp.status_code >= 400:
                last_err = f"HTTP {resp.status_code}"
            else:
                return resp.json(), "live"
        except Exception as exc:                       # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
        if attempt < config.HTTP_RETRIES:
            await asyncio.sleep(2 ** attempt)          # HF cold start backoff

    return {"subject_id": payload.get("subject_id"), "status": "error",
            "score": None, "error": last_err,
            "computed_at": iso(utcnow()),
            "model_version": f"{key}-unavailable"}, "error"


async def service_health(key: str) -> dict:
    cfg = config.SERVICES[key]
    if cfg.mock:
        return {"service": key, "label": cfg.label, "mode": "mock",
                "status": "ok", "url": None}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(cfg.health_url)
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        return {"service": key, "label": cfg.label, "mode": "live",
                "status": "ok" if resp.status_code < 400 else "error",
                "http_status": resp.status_code, "url": cfg.health_url,
                "model_version": body.get("model_version")}
    except Exception as exc:                           # noqa: BLE001
        return {"service": key, "label": cfg.label, "mode": "live",
                "status": "unreachable", "url": cfg.health_url,
                "error": f"{type(exc).__name__}"}


# --------------------------------------------------------------------------
# Defensive envelope parsing
# --------------------------------------------------------------------------
SCORE_ALIASES = ("score", "risk_score", "calibrated_probability",
                 "vulnerability_score", "probability", "p_anxiety", "risk")

# For C4 we prefer the calibrated probability over the raw score
PREFERRED_SCORE_KEY = {"c4": "calibrated_probability"}


def coerce_envelope(key: str, modality: str, subject_id: str,
                    raw: dict, fallback_captured: datetime | None = None) -> dict:
    raw = raw if isinstance(raw, dict) else {}

    score = None
    preferred = PREFERRED_SCORE_KEY.get(key)
    if preferred:
        score = _as_float(raw.get(preferred))
    if score is None:
        for alias in SCORE_ALIASES:
            score = _as_float(raw.get(alias))
            if score is not None:
                break
    if score is not None:
        score = max(0.0, min(1.0, score))

    status = str(raw.get("status") or ("ok" if score is not None else "error")).lower()
    if score is None and status == "ok":
        status = "error"          # a service claiming ok with no number is not ok

    return {
        "subject_id": subject_id,
        "modality": modality,
        "score": score,
        "status": status,
        "captured_at": parse_dt(raw.get("captured_at"), fallback_captured),
        "computed_at": parse_dt(raw.get("computed_at")),
        "model_version": str(raw.get("model_version") or "unknown"),
        "payload": raw,
    }

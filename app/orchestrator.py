"""
The brain. Everything that decides *whether* and *when* a model runs lives here,
and nowhere else. Not in Flutter, not in a model Space.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clients, config, models
from .clients import iso, utcnow


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def audit(db: Session, subject_id: str | None, actor: str, action: str, detail: dict) -> None:
    db.add(models.AuditLog(subject_id=subject_id, actor=actor, action=action, detail=detail))


def store_reading(db: Session, env: dict, source: str) -> models.Reading:
    row = models.Reading(
        subject_id=env["subject_id"], modality=env["modality"],
        score=env["score"], status=env["status"],
        captured_at=env["captured_at"], computed_at=env["computed_at"],
        model_version=env["model_version"], source=source, payload=env["payload"],
    )
    db.add(row)
    db.flush()
    return row


# --------------------------------------------------------------------------
# Freshness / gating
# --------------------------------------------------------------------------
def latest_readings(db: Session, subject_id: str) -> dict[str, models.Reading | None]:
    out: dict[str, models.Reading | None] = {}
    for modality in config.FUSION_WEIGHTS:
        out[modality] = db.execute(
            select(models.Reading)
            .where(models.Reading.subject_id == subject_id,
                   models.Reading.modality == modality)
            .order_by(models.Reading.captured_at.desc())
            .limit(1)
        ).scalars().first()
    return out


def build_components(db: Session, subject_id: str) -> dict:
    now = utcnow()
    components: dict[str, dict] = {}

    for modality, reading in latest_readings(db, subject_id).items():
        if reading is None:
            components[modality] = {"score": None, "available": False,
                                    "status": "no_reading"}
            continue

        age = (now - _aware(reading.captured_at)).total_seconds()
        window = config.FRESHNESS_SECONDS.get(modality, 3600)
        stale = age > window
        usable = reading.status == "ok" and reading.score is not None and not stale

        components[modality] = {
            "score": reading.score if usable else None,
            "available": usable,
            "status": "stale" if (stale and reading.status == "ok") else reading.status,
            "captured_at": iso(_aware(reading.captured_at)),
            "freshness_seconds": int(age),
            "model_version": reading.model_version,
        }
    return components


# --------------------------------------------------------------------------
# The pipeline
# --------------------------------------------------------------------------
async def run_fusion(db: Session, subject_id: str, trigger: str) -> models.FusionResult:
    components = build_components(db, subject_id)
    payload = {
        "subject_id": subject_id,
        "requested_at": iso(utcnow()),
        "renormalise_on_missing": config.RENORMALISE_ON_MISSING,
        "min_modalities": config.MIN_MODALITIES,
        "components": components,
    }

    raw, source = await clients.call_service("fusion", payload)
    provisional = False

    # If the fusion service failed or answered nonsense, compute locally and
    # label the result provisional everywhere it appears.
    if source == "error" or clients._as_float(raw.get("composite_score")) is None:
        if raw.get("composite_score") is None and raw.get("reason") and source != "error":
            pass  # a legitimate "not enough modalities" answer, keep it
        else:
            raw = clients.local_fuse(payload)
            provisional = True

    composite = clients._as_float(raw.get("composite_score"))
    band = raw.get("alert_level") or (config.band_for(composite) if composite is not None else None)

    row = models.FusionResult(
        subject_id=subject_id,
        composite_score=composite,
        band=band,
        reason=raw.get("reason"),
        provisional=provisional,
        renormalised=bool(raw.get("renormalised")),
        modalities_available=int(raw.get("modalities_available") or 0),
        weights=raw.get("weights") or {},
        scores=raw.get("scores") or {},
        excluded=raw.get("excluded") or [],
        weights_version=config.FUSION_WEIGHTS_VERSION,
        model_version=str(raw.get("model_version") or "unknown"),
    )
    db.add(row)
    db.flush()

    audit(db, subject_id, "system", "fusion", {
        "trigger": trigger, "source": source, "provisional": provisional,
        "composite": composite, "band": band,
        "weights_version": config.FUSION_WEIGHTS_VERSION,
    })

    if composite is not None:
        await run_intervention(db, subject_id, row)

    return row


async def run_intervention(db: Session, subject_id: str,
                           fusion: models.FusionResult) -> models.InterventionResult:
    gad7 = db.execute(
        select(models.EmaResponse)
        .where(models.EmaResponse.subject_id == subject_id,
               models.EmaResponse.instrument == "gad7")
        .order_by(models.EmaResponse.captured_at.desc()).limit(1)
    ).scalars().first()

    payload = {
        "subject_id": subject_id,
        "composite_score": fusion.composite_score,
        "alert_level": fusion.band,
        "components": fusion.scores,
        "context": {"gad7": gad7.score if gad7 else None},
        "history": {"recent_interventions": []},
    }
    raw, source = await clients.call_service("c3", payload)

    row = models.InterventionResult(
        subject_id=subject_id, fusion_id=fusion.id,
        tier=raw.get("tier"),
        status=str(raw.get("status") or ("error" if source == "error" else "ok")),
        payload=raw,
    )
    db.add(row)
    db.flush()
    audit(db, subject_id, "system", "intervention",
          {"source": source, "tier": raw.get("tier")})
    return row


async def ingest_physiology(db: Session, subject_id: str, window_start: datetime,
                            window_end: datetime, features: dict) -> models.Reading:
    db.add(models.PhysioWindow(subject_id=subject_id, window_start=window_start,
                               window_end=window_end, features=features))
    payload = {"subject_id": subject_id, "window_start": iso(window_start),
               "window_end": iso(window_end), "sampling_hz": 50, "features": features}
    raw, source = await clients.call_service("c1", payload)
    env = clients.coerce_envelope("c1", "c1_physiological", subject_id, raw, window_end)
    reading = store_reading(db, env, source)
    audit(db, subject_id, "patient_app", "ingest_physiology",
          {"status": env["status"], "source": source})
    await run_fusion(db, subject_id, trigger="c1")
    return reading


async def ingest_behavior_day(db: Session, subject_id: str, day: str,
                              nodes: dict) -> models.Reading | None:
    existing = db.execute(
        select(models.BehaviorDay).where(models.BehaviorDay.subject_id == subject_id,
                                         models.BehaviorDay.day == day)
    ).scalars().first()
    if existing:
        existing.nodes = nodes
    else:
        db.add(models.BehaviorDay(subject_id=subject_id, day=day, nodes=nodes))
    db.flush()

    days = db.execute(
        select(models.BehaviorDay)
        .where(models.BehaviorDay.subject_id == subject_id)
        .order_by(models.BehaviorDay.day.desc())
        .limit(config.C2_DAYS_REQUIRED)
    ).scalars().all()

    payload = {"subject_id": subject_id, "window_end_date": day,
               "days": [{"date": d.day, "nodes": d.nodes} for d in reversed(days)]}
    raw, source = await clients.call_service("c2", payload)
    env = clients.coerce_envelope("c2", "c2_behavioral", subject_id, raw)
    reading = store_reading(db, env, source)
    audit(db, subject_id, "patient_app", "ingest_behavior",
          {"day": day, "days_held": len(days), "status": env["status"]})
    await run_fusion(db, subject_id, trigger="c2")
    return reading


async def analyse_note(db: Session, subject_id: str,
                       note: models.ClinicalNote) -> models.Reading:
    support = db.execute(
        select(models.SupportExample).where(
            (models.SupportExample.subject_id == subject_id)
            | (models.SupportExample.subject_id.is_(None))
        )
    ).scalars().all()

    payload = {
        "subject_id": subject_id,
        "note_text": note.note_text,
        "note_type": note.note_type,
        "note_date": iso(_aware(note.note_date)),
        "visit_count": note.visit_count,
        "support_set": [{"id": s.id, "text": s.text, "label": s.label,
                         "note_date": iso(_aware(s.note_date))} for s in support],
        "return_attention": True,
        "return_support_contributions": True,
    }
    raw, source = await clients.call_service("c4", payload)
    env = clients.coerce_envelope("c4", "c4_clinical_nlp", subject_id, raw,
                                  _aware(note.note_date))
    env["payload"] = {**env["payload"], "note_id": note.id}
    reading = store_reading(db, env, source)
    audit(db, subject_id, "clinician", "tcwpn_predict",
          {"note_id": note.id, "support_k": len(support), "status": env["status"]})
    await run_fusion(db, subject_id, trigger="c4")
    return reading


def latest_fusion(db: Session, subject_id: str) -> models.FusionResult | None:
    return db.execute(
        select(models.FusionResult)
        .where(models.FusionResult.subject_id == subject_id)
        .order_by(models.FusionResult.computed_at.desc()).limit(1)
    ).scalars().first()


def latest_intervention(db: Session, subject_id: str) -> models.InterventionResult | None:
    return db.execute(
        select(models.InterventionResult)
        .where(models.InterventionResult.subject_id == subject_id)
        .order_by(models.InterventionResult.computed_at.desc()).limit(1)
    ).scalars().first()

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import config, models, orchestrator, schemas, security
from ..db import get_db

router = APIRouter(prefix="/v1/subjects/me", tags=["patient"])


def _aware(dt): return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.post("/physiology")
async def post_physiology(body: schemas.PhysiologyIn,
                          p: dict = Depends(security.require_patient),
                          db: Session = Depends(get_db)):
    reading = await orchestrator.ingest_physiology(
        db, p["subject_id"], body.window_start, body.window_end, body.features)
    db.commit()
    return {"accepted": True, "status": reading.status, "modality": reading.modality}


@router.post("/behavior")
async def post_behavior(body: schemas.BehaviorDayIn,
                        p: dict = Depends(security.require_patient),
                        db: Session = Depends(get_db)):
    reading = await orchestrator.ingest_behavior_day(
        db, p["subject_id"], body.day, body.nodes)
    db.commit()
    return {"accepted": True, "status": reading.status if reading else "stored"}


@router.post("/ema")
async def post_ema(body: schemas.EmaIn,
                   p: dict = Depends(security.require_patient),
                   db: Session = Depends(get_db)):
    db.add(models.EmaResponse(
        subject_id=p["subject_id"], instrument=body.instrument, score=body.score,
        captured_at=body.captured_at or datetime.now(timezone.utc),
        payload=body.payload))
    orchestrator.audit(db, p["subject_id"], "patient_app", "ingest_ema",
                       {"instrument": body.instrument})
    db.commit()
    return {"accepted": True}


@router.get("/risk", response_model=schemas.PatientRiskOut)
def get_risk(p: dict = Depends(security.require_patient),
             db: Session = Depends(get_db)):
    """
    Deliberately thin. No per-modality breakdown, no model versions, no note
    text, no weights. The patient app gets a band, a number, and one suggested
    activity. Everything else is clinician-only.
    """
    subject_id = p["subject_id"]
    fusion = orchestrator.latest_fusion(db, subject_id)
    intervention = orchestrator.latest_intervention(db, subject_id)

    top = None
    if intervention and intervention.payload.get("interventions"):
        first = intervention.payload["interventions"][0]
        top = {"id": first.get("id"), "name": first.get("name")}

    band = None
    if fusion and fusion.band:
        band = config.PATIENT_BAND_LABEL.get(fusion.band, fusion.band)

    return schemas.PatientRiskOut(
        subject_id=subject_id,
        composite_score=fusion.composite_score if fusion else None,
        band=band,
        provisional=bool(fusion.provisional) if fusion else False,
        updated_at=_aware(fusion.computed_at) if fusion else None,
        intervention=top,
        reason=fusion.reason if fusion else "no data yet",
    )

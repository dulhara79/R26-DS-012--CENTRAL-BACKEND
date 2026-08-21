from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, models, orchestrator, schemas, security
from ..clients import iso
from ..db import get_db

router = APIRouter(prefix="/v1/clinician", tags=["clinician"])


def _own(db: Session, subject_id: str, principal: dict) -> models.Subject:
    subject = db.get(models.Subject, subject_id)
    if subject is None or subject.clinician_id != principal["sub"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subject not found")
    return subject


def _aware(dt): return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.post("/subjects", response_model=schemas.CreateSubjectOut)
def create_subject(body: schemas.CreateSubjectIn,
                   p: dict = Depends(security.require_clinician),
                   db: Session = Depends(get_db)):
    mrn_hash = security.hash_mrn(body.mrn)
    subject_id = security.new_subject_id(mrn_hash)

    if db.get(models.Subject, subject_id) is None:
        db.add(models.Subject(id=subject_id, mrn_hash=mrn_hash,
                              display_label=body.display_label or subject_id,
                              clinician_id=p["sub"]))
        db.add(models.SubjectAlias(subject_id=subject_id,
                                   alias_type="mrn_hash", alias_value=mrn_hash))

    code = security.new_pairing_code()
    db.add(models.PairingCode(code=code, subject_id=subject_id))
    orchestrator.audit(db, subject_id, p["sub"], "subject_enrol", {})
    db.commit()
    return schemas.CreateSubjectOut(subject_id=subject_id,
                                    display_label=body.display_label or subject_id,
                                    pairing_code=code)


@router.get("/subjects")
def list_subjects(p: dict = Depends(security.require_clinician),
                  db: Session = Depends(get_db)):
    subjects = db.execute(
        select(models.Subject).where(models.Subject.clinician_id == p["sub"])
        .order_by(models.Subject.display_label)
    ).scalars().all()

    out = []
    for s in subjects:
        fusion = orchestrator.latest_fusion(db, s.id)
        out.append({
            "subject_id": s.id, "display_label": s.display_label,
            "composite_score": fusion.composite_score if fusion else None,
            "band": fusion.band if fusion else None,
            "provisional": fusion.provisional if fusion else None,
            "updated_at": iso(_aware(fusion.computed_at)) if fusion else None,
        })
    return {"subjects": out}


@router.post("/subjects/{subject_id}/notes")
async def create_note(subject_id: str, body: schemas.NoteIn,
                      p: dict = Depends(security.require_clinician),
                      db: Session = Depends(get_db)):
    _own(db, subject_id, p)
    note = models.ClinicalNote(
        subject_id=subject_id, note_text=body.note_text, note_type=body.note_type,
        note_date=body.note_date or datetime.now(timezone.utc),
        visit_count=body.visit_count, author_id=p["sub"])
    db.add(note)
    db.flush()

    reading = await orchestrator.analyse_note(db, subject_id, note)
    fusion = orchestrator.latest_fusion(db, subject_id)
    db.commit()
    return {"note_id": note.id, "tcwpn": reading.payload,
            "composite_score": fusion.composite_score if fusion else None,
            "band": fusion.band if fusion else None}


@router.post("/subjects/{subject_id}/notes/{note_id}/verdict")
def set_verdict(subject_id: str, note_id: str, body: schemas.VerdictIn,
                p: dict = Depends(security.require_clinician),
                db: Session = Depends(get_db)):
    _own(db, subject_id, p)
    note = db.get(models.ClinicalNote, note_id)
    if note is None or note.subject_id != subject_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "note not found")
    note.verdict = body.verdict
    orchestrator.audit(db, subject_id, p["sub"], "hitl_verdict",
                       {"note_id": note_id, "verdict": body.verdict})
    db.commit()
    return {"ok": True}


@router.get("/subjects/{subject_id}/support-set")
def get_support_set(subject_id: str, p: dict = Depends(security.require_clinician),
                    db: Session = Depends(get_db)):
    _own(db, subject_id, p)
    rows = db.execute(
        select(models.SupportExample).where(
            (models.SupportExample.subject_id == subject_id)
            | (models.SupportExample.subject_id.is_(None)))
    ).scalars().all()
    anxiety = sum(1 for r in rows if r.label == "anxiety")
    return {"k": len(rows), "class_balance": {"anxiety": anxiety,
                                              "control": len(rows) - anxiety},
            "examples": [{"id": r.id, "label": r.label,
                          "note_date": iso(_aware(r.note_date)),
                          "scope": "patient" if r.subject_id else "site",
                          "excerpt": r.text[:120]} for r in rows]}


@router.post("/subjects/{subject_id}/support-set")
def add_support_example(subject_id: str, body: schemas.SupportExampleIn,
                        p: dict = Depends(security.require_clinician),
                        db: Session = Depends(get_db)):
    _own(db, subject_id, p)
    row = models.SupportExample(
        subject_id=subject_id if body.subject_scoped else None,
        text=body.text, label=body.label, note_date=body.note_date)
    db.add(row)
    orchestrator.audit(db, subject_id, p["sub"], "support_add",
                       {"label": body.label, "scope": "patient" if body.subject_scoped else "site"})
    db.commit()
    return {"id": row.id}


@router.get("/subjects/{subject_id}/timeline")
def timeline(subject_id: str, p: dict = Depends(security.require_clinician),
             db: Session = Depends(get_db)):
    """Everything the clinician app needs for one chart, in one call."""
    subject = _own(db, subject_id, p)
    fusion = orchestrator.latest_fusion(db, subject_id)
    intervention = orchestrator.latest_intervention(db, subject_id)
    components = orchestrator.build_components(db, subject_id)

    readings = db.execute(
        select(models.Reading).where(models.Reading.subject_id == subject_id)
        .order_by(models.Reading.captured_at.desc()).limit(50)
    ).scalars().all()

    notes = db.execute(
        select(models.ClinicalNote).where(models.ClinicalNote.subject_id == subject_id)
        .order_by(models.ClinicalNote.note_date.desc()).limit(20)
    ).scalars().all()

    return {
        "subject_id": subject.id,
        "display_label": subject.display_label,
        "fusion": None if fusion is None else {
            "composite_score": fusion.composite_score, "band": fusion.band,
            "reason": fusion.reason, "provisional": fusion.provisional,
            "renormalised": fusion.renormalised,
            "modalities_available": fusion.modalities_available,
            "weights": fusion.weights, "scores": fusion.scores,
            "excluded": fusion.excluded,
            "weights_version": fusion.weights_version,
            "model_version": fusion.model_version,
            "computed_at": iso(_aware(fusion.computed_at)),
        },
        "components": components,
        "intervention": None if intervention is None else {
            "tier": intervention.tier, "status": intervention.status,
            **{k: intervention.payload.get(k) for k in
               ("conformal_set", "interventions", "xai", "escalation", "model_version")},
            "computed_at": iso(_aware(intervention.computed_at)),
        },
        "readings": [{"modality": r.modality, "score": r.score, "status": r.status,
                      "captured_at": iso(_aware(r.captured_at)),
                      "model_version": r.model_version, "source": r.source,
                      "detail": r.payload} for r in readings],
        "notes": [{"id": n.id, "note_type": n.note_type,
                   "note_date": iso(_aware(n.note_date)),
                   "verdict": n.verdict, "text": n.note_text} for n in notes],
    }


@router.get("/subjects/{subject_id}/audit")
def subject_audit(subject_id: str, p: dict = Depends(security.require_clinician),
                  db: Session = Depends(get_db)):
    _own(db, subject_id, p)
    rows = db.execute(
        select(models.AuditLog).where(models.AuditLog.subject_id == subject_id)
        .order_by(models.AuditLog.at.desc()).limit(200)
    ).scalars().all()
    return {"entries": [{"at": iso(_aware(r.at)), "actor": r.actor,
                         "action": r.action, "detail": r.detail} for r in rows]}

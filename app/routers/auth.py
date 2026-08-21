from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..db import get_db
from ..orchestrator import audit

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/clinician/login", response_model=schemas.TokenOut)
def clinician_login(body: schemas.LoginIn, db: Session = Depends(get_db)):
    clinician = db.execute(
        select(models.Clinician).where(models.Clinician.email == body.email.lower())
    ).scalars().first()
    if not clinician or not security.verify_password(body.password, clinician.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad credentials")
    audit(db, None, clinician.email, "clinician_login", {})
    db.commit()
    return schemas.TokenOut(
        access_token=security.make_token(clinician.id, "clinician"),
        role="clinician",
    )


@router.post("/patient/pair", response_model=schemas.TokenOut)
def patient_pair(body: schemas.PairIn, db: Session = Depends(get_db)):
    """
    The patient app never types an MRN. The clinician reads out a pairing code,
    the patient enters it once, and the device is bound to a subject_id forever.
    This is what makes patient separation real rather than aspirational.
    """
    code = db.get(models.PairingCode, body.pairing_code.strip().upper())
    if not code or code.used:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or already-used pairing code")

    code.used = True
    alias = db.execute(
        select(models.SubjectAlias).where(
            models.SubjectAlias.alias_type == "app_user",
            models.SubjectAlias.alias_value == body.device_id)
    ).scalars().first()
    if alias is None:
        db.add(models.SubjectAlias(subject_id=code.subject_id,
                                   alias_type="app_user", alias_value=body.device_id))
    audit(db, code.subject_id, body.device_id, "patient_pair", {})
    db.commit()
    return schemas.TokenOut(
        access_token=security.make_token(body.device_id, "patient", code.subject_id),
        role="patient", subject_id=code.subject_id,
    )

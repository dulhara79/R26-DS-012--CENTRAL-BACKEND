"""Database-backed authorization for Handbook Phase 1."""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import VerifiedPrincipal
from db_models import AuditLog, Clinician, ClinicianSubjectAssignment, Subject, utcnow
from schemas.phase0_v1 import PrincipalType


def _audit_access(
    db: Session,
    *,
    subject_id: Optional[str],
    actor: str,
    allowed: bool,
    reason: str,
) -> None:
    db.add(AuditLog(
        subject_id=subject_id,
        event="authorization.allowed" if allowed else "authorization.denied",
        actor=actor,
        detail={"reason": reason},
    ))


def require_active_clinician(db: Session, principal: VerifiedPrincipal) -> Clinician:
    if principal.principal_type != PrincipalType.CLINICIAN or not principal.clinician_id:
        raise HTTPException(status_code=403, detail="clinician principal required")

    clinician = db.get(Clinician, principal.clinician_id)
    if clinician is None:
        _audit_access(
            db, subject_id=None, actor=principal.actor_id,
            allowed=False, reason="clinician_not_registered",
        )
        db.commit()
        raise HTTPException(status_code=403, detail="clinician is not registered")

    if clinician.status != "active":
        _audit_access(
            db, subject_id=None, actor=principal.actor_id,
            allowed=False, reason=f"clinician_status={clinician.status}",
        )
        db.commit()
        raise HTTPException(status_code=403, detail="clinician is not active")

    if clinician.auth_subject and clinician.auth_subject != principal.principal_id:
        _audit_access(
            db, subject_id=None, actor=principal.actor_id,
            allowed=False, reason="auth_subject_mismatch",
        )
        db.commit()
        raise HTTPException(status_code=403, detail="clinician identity binding mismatch")

    if clinician.role != principal.role:
        _audit_access(
            db, subject_id=None, actor=principal.actor_id,
            allowed=False, reason="role_mismatch",
        )
        db.commit()
        raise HTTPException(status_code=403, detail="clinician role mismatch")

    return clinician


def require_clinician_assignment(
    db: Session,
    principal: VerifiedPrincipal,
    subject_id: str,
) -> ClinicianSubjectAssignment:
    clinician = require_active_clinician(db, principal)

    subject = db.get(Subject, subject_id)
    if subject is None or subject.status != "active":
        _audit_access(
            db, subject_id=subject_id, actor=clinician.clinician_id,
            allowed=False, reason="subject_not_active_or_not_found",
        )
        db.commit()
        # Phase 0 left 403-vs-404 as a disclosure-policy choice. Phase 1 uses
        # 403 consistently so authenticated clients can distinguish permission
        # failure from invalid/expired identity without revealing extra details.
        raise HTTPException(status_code=403, detail="not authorized for this subject")

    assignment = db.scalar(select(ClinicianSubjectAssignment).where(
        ClinicianSubjectAssignment.clinician_id == clinician.clinician_id,
        ClinicianSubjectAssignment.subject_id == subject_id,
        ClinicianSubjectAssignment.active.is_(True),
    ))
    if assignment is None:
        _audit_access(
            db, subject_id=subject_id, actor=clinician.clinician_id,
            allowed=False, reason="no_active_assignment",
        )
        db.commit()
        raise HTTPException(status_code=403, detail="not authorized for this subject")

    _audit_access(
        db, subject_id=subject_id, actor=clinician.clinician_id,
        allowed=True, reason="active_assignment",
    )
    return assignment


def require_patient_subject(
    db: Session,
    principal: VerifiedPrincipal,
    subject_id: str,
) -> Subject:
    if principal.principal_type != PrincipalType.PATIENT or not principal.subject_id:
        raise HTTPException(status_code=403, detail="patient principal required")
    if principal.subject_id != subject_id:
        _audit_access(
            db, subject_id=subject_id, actor=principal.actor_id,
            allowed=False, reason="patient_subject_mismatch",
        )
        db.commit()
        raise HTTPException(status_code=403, detail="not authorized for this subject")

    subject = db.get(Subject, subject_id)
    if subject is None or subject.status != "active":
        _audit_access(
            db, subject_id=subject_id, actor=principal.actor_id,
            allowed=False, reason="subject_not_active_or_not_found",
        )
        db.commit()
        raise HTTPException(status_code=403, detail="not authorized for this subject")

    _audit_access(
        db, subject_id=subject_id, actor=principal.actor_id,
        allowed=True, reason="patient_subject_binding",
    )
    return subject


def assignment_is_active(
    db: Session,
    clinician_id: str,
    subject_id: str,
) -> bool:
    return db.scalar(select(ClinicianSubjectAssignment.id).where(
        ClinicianSubjectAssignment.clinician_id == clinician_id,
        ClinicianSubjectAssignment.subject_id == subject_id,
        ClinicianSubjectAssignment.active.is_(True),
    )) is not None


def end_assignment(assignment: ClinicianSubjectAssignment) -> None:
    assignment.active = False
    assignment.ended_at = utcnow()

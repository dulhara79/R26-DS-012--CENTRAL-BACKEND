from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Clinician(Base):
    __tablename__ = "clinicians"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Subject(Base):
    """The canonical patient. Never an MRN, never a device id."""
    __tablename__ = "subjects"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)      # S-xxxxxxxx
    mrn_hash: Mapped[str] = mapped_column(String(64), index=True)       # sha256(mrn + pepper)
    display_label: Mapped[str] = mapped_column(String(64), default="")  # "P001" for the demo
    clinician_id: Mapped[str] = mapped_column(String(32), ForeignKey("clinicians.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SubjectAlias(Base):
    """Maps device / app identities onto one canonical subject_id."""
    __tablename__ = "subject_aliases"
    __table_args__ = (UniqueConstraint("alias_type", "alias_value"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(String(40), ForeignKey("subjects.id"), index=True)
    alias_type: Mapped[str] = mapped_column(String(32))   # app_user | mrn_hash
    alias_value: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PairingCode(Base):
    __tablename__ = "pairing_codes"
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(40), ForeignKey("subjects.id"))
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Reading(Base):
    """
    APPEND ONLY. One row per model output, for every modality, including
    failures and warm-ups. Never updated, never deleted.
    """
    __tablename__ = "modality_readings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(String(40), index=True)
    modality: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="ok")
    captured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    model_version: Mapped[str] = mapped_column(String(64), default="unknown")
    source: Mapped[str] = mapped_column(String(16), default="mock")  # mock | live
    payload: Mapped[dict] = mapped_column(JSON, default=dict)        # full service response


class PhysioWindow(Base):
    __tablename__ = "physio_windows"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(String(40), index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime)
    window_end: Mapped[datetime] = mapped_column(DateTime, index=True)
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BehaviorDay(Base):
    """One row per patient per day. The backend assembles the 42-day graph."""
    __tablename__ = "behavior_days"
    __table_args__ = (UniqueConstraint("subject_id", "day"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(String(40), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)   # YYYY-MM-DD
    nodes: Mapped[dict] = mapped_column(JSON, default=dict)    # morning/afternoon/evening/night
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EmaResponse(Base):
    __tablename__ = "ema_responses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(String(40), index=True)
    instrument: Mapped[str] = mapped_column(String(32))   # gad7 | pss10 | mood
    score: Mapped[float] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class ClinicalNote(Base):
    """Raw note text lives here and is served ONLY to clinician endpoints."""
    __tablename__ = "clinical_notes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(String(40), index=True)
    note_text: Mapped[str] = mapped_column(Text)
    note_type: Mapped[str] = mapped_column(String(64), default="Psychiatry note")
    note_date: Mapped[datetime] = mapped_column(DateTime)
    visit_count: Mapped[int] = mapped_column(Integer, default=1)
    author_id: Mapped[str] = mapped_column(String(32))
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)  # agree|disagree
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SupportExample(Base):
    """Few-shot support set. subject_id NULL => site-level example."""
    __tablename__ = "support_examples"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    subject_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    text: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(16))   # anxiety | control
    note_date: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FusionResult(Base):
    __tablename__ = "fusion_results"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(String(40), index=True)
    composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    band: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provisional: Mapped[bool] = mapped_column(Boolean, default=False)
    renormalised: Mapped[bool] = mapped_column(Boolean, default=False)
    modalities_available: Mapped[int] = mapped_column(Integer, default=0)
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    scores: Mapped[dict] = mapped_column(JSON, default=dict)
    excluded: Mapped[list] = mapped_column(JSON, default=list)
    weights_version: Mapped[str] = mapped_column(String(64), default="")
    model_version: Mapped[str] = mapped_column(String(64), default="")
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class InterventionResult(Base):
    __tablename__ = "intervention_results"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(String(40), index=True)
    fusion_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="ok")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    subject_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

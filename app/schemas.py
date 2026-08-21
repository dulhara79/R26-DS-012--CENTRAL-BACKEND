from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    subject_id: str | None = None


class PairIn(BaseModel):
    pairing_code: str
    device_id: str


class CreateSubjectIn(BaseModel):
    mrn: str
    display_label: str = ""


class CreateSubjectOut(BaseModel):
    subject_id: str
    display_label: str
    pairing_code: str


class PhysiologyIn(BaseModel):
    window_start: datetime
    window_end: datetime
    features: dict = Field(default_factory=dict)


class BehaviorDayIn(BaseModel):
    day: str                       # YYYY-MM-DD
    nodes: dict = Field(default_factory=dict)


class EmaIn(BaseModel):
    instrument: str                # gad7 | pss10 | mood
    score: float
    captured_at: datetime | None = None
    payload: dict = Field(default_factory=dict)


class NoteIn(BaseModel):
    note_text: str
    note_type: str = "Psychiatry note"
    note_date: datetime | None = None
    visit_count: int = 1


class SupportExampleIn(BaseModel):
    text: str
    label: str                     # anxiety | control
    note_date: datetime
    subject_scoped: bool = False   # False => site-level example


class VerdictIn(BaseModel):
    verdict: str                   # agree | disagree


class PatientRiskOut(BaseModel):
    subject_id: str
    composite_score: float | None
    band: str | None
    provisional: bool
    updated_at: datetime | None
    intervention: dict | None
    reason: str | None = None

"""R26-DS-012 Phase 0 contract lock.

These models encode TARGET contracts from the System Integration Handbook and
ClinAnx technical specification. Importing this module does not claim the
corresponding TARGET endpoints/persistence are implemented.

CURRENT runtime behavior remains in main.py/db_models.py until later phases add:
- Phase 1: JWT verification + assignment authorization
- Phase 2: canonical latest-assessment endpoints
- Phase 3: ForecastResult persistence
- Phase 4: AttentionEvent persistence/engine/actions
"""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


PHASE0_CONTRACT_VERSION = "r26ds012-phase0-v1"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModalityId(str, Enum):
    C1_PHYSIOLOGICAL = "c1_physiological"
    C2_BEHAVIORAL = "c2_behavioral"
    C3_CLINICAL_NLP = "c3_clinical_nlp"
    C4_DEMOGRAPHIC = "c4_demographic"


class ComponentStatus(str, Enum):
    OK = "ok"
    WARMING_UP = "warming_up"
    INSUFFICIENT_DATA = "insufficient_data"
    POOR_SIGNAL = "poor_signal"
    NO_SUPPORT_SET = "no_support_set"
    NOT_VALIDATED = "not_validated"
    ERROR = "error"


class AssessmentStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class Tier(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class Band(str, Enum):
    GREEN = "GREEN"
    AMBER = "AMBER"
    RED = "RED"
    GREY = "GREY"


class ForecastScope(str, Enum):
    PHYSIOLOGICAL = "physiological"
    MULTIMODAL = "multimodal"


class AttentionEventStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class PrincipalType(str, Enum):
    CLINICIAN = "clinician"
    PATIENT = "patient"
    ADMIN = "admin"
    RESEARCHER = "researcher"
    SERVICE = "service"


class CurrentAssessment(ContractModel):
    score: Optional[float] = None
    tier: Optional[Tier] = None
    band: Band


class ForecastProjection(ContractModel):
    forecast_result_id: str = Field(min_length=1)
    scope: ForecastScope
    horizon_minutes: int = Field(gt=0)
    score: Optional[float] = None
    tier: Optional[Tier] = None
    escalation_probability: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    escalation_predicted: bool
    generated_at: dt.datetime
    valid_until: dt.datetime

    @model_validator(mode="after")
    def validity_window_is_forward(self):
        if self.valid_until <= self.generated_at:
            raise ValueError("valid_until must be later than generated_at")
        return self


class ModalitySummary(ContractModel):
    component_id: ModalityId
    score: Optional[float] = None
    available: bool
    included_in_fusion: bool
    status: ComponentStatus
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    coverage: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    captured_at: Optional[dt.datetime] = None
    contribution: Optional[float] = None


class AssessmentSummary(ContractModel):
    """Frozen TARGET latest-assessment contract.

    Both patient and clinician projections must refer to the same
    fusion_result_id. Audience-specific endpoints may omit sensitive detail, but
    they must not recompute a different authoritative current score.
    """

    subject_id: UUID
    fusion_result_id: int = Field(gt=0)
    current_assessment: CurrentAssessment
    forecast: Optional[ForecastProjection] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    assessment_status: AssessmentStatus
    modalities: list[ModalitySummary]
    computed_at: dt.datetime
    model_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def missing_is_not_low(self):
        if self.assessment_status == AssessmentStatus.UNAVAILABLE:
            if self.current_assessment.score is not None or self.current_assessment.tier is not None:
                raise ValueError("unavailable assessment must not expose a current score/tier")
            if self.current_assessment.band != Band.GREY:
                raise ValueError("unavailable assessment must use GREY, never a reassuring band")
        elif self.current_assessment.score is None or self.current_assessment.tier is None:
            raise ValueError("complete/partial assessment requires current score and tier")
        return self


class ForecastSource(ContractModel):
    component: ModalityId
    reading_id: int = Field(gt=0)


class ForecastResultContract(ContractModel):
    """Frozen TARGET ForecastResult wire/domain contract.

    scope='physiological' is the currently defensible scope for C1-led future
    prediction. 'multimodal' is reserved for a separately specified and
    validated future forecasting method.
    """

    forecast_result_id: str = Field(min_length=1)
    subject_id: UUID
    fusion_result_id: int = Field(gt=0)
    scope: ForecastScope
    horizon_minutes: int = Field(gt=0)
    score: Optional[float] = None
    tier: Optional[Tier] = None
    escalation_probability: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    escalation_predicted: bool
    generated_at: dt.datetime
    valid_until: dt.datetime
    model_version: str = Field(min_length=1)
    source: ForecastSource

    @model_validator(mode="after")
    def validity_window_is_forward(self):
        if self.valid_until <= self.generated_at:
            raise ValueError("valid_until must be later than generated_at")
        return self


class AttentionEvent(ContractModel):
    id: str = Field(min_length=1)
    subject_id: UUID
    fusion_result_id: int = Field(gt=0)
    forecast_result_id: Optional[str] = None
    event_type: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    forecast_horizon: int = Field(gt=0)
    status: AttentionEventStatus
    created_at: dt.datetime
    acknowledged_at: Optional[dt.datetime] = None
    acknowledged_by: Optional[str] = None
    resolved_at: Optional[dt.datetime] = None
    resolved_by: Optional[str] = None
    policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def lifecycle_snapshot_is_consistent(self):
        ack = self.acknowledged_at is not None and bool(self.acknowledged_by)
        resolved = self.resolved_at is not None and bool(self.resolved_by)

        if self.status == AttentionEventStatus.OPEN:
            if any((self.acknowledged_at, self.acknowledged_by,
                    self.resolved_at, self.resolved_by)):
                raise ValueError("OPEN event cannot already carry ACK/RESOLVE actor or timestamp")

        elif self.status == AttentionEventStatus.ACKNOWLEDGED:
            if not ack:
                raise ValueError("ACKNOWLEDGED event requires acknowledged_at and acknowledged_by")
            if self.resolved_at is not None or self.resolved_by is not None:
                raise ValueError("ACKNOWLEDGED event cannot already carry RESOLVED fields")

        elif self.status == AttentionEventStatus.RESOLVED:
            if not ack:
                raise ValueError("RESOLVED event must preserve prior acknowledgement actor/time")
            if not resolved:
                raise ValueError("RESOLVED event requires resolved_at and resolved_by")

        if self.acknowledged_at and self.acknowledged_at < self.created_at:
            raise ValueError("acknowledged_at cannot precede created_at")
        if self.resolved_at and self.resolved_at < self.created_at:
            raise ValueError("resolved_at cannot precede created_at")
        if self.resolved_at and self.acknowledged_at and self.resolved_at < self.acknowledged_at:
            raise ValueError("resolved_at cannot precede acknowledged_at")
        return self


class AttentionEventListResponse(ContractModel):
    events: list[AttentionEvent]


class AttentionEventResponse(ContractModel):
    event: AttentionEvent


class AcknowledgeAttentionEventRequest(ContractModel):
    """Frozen body is {}. Actor and timestamp come from authenticated server principal."""


class ResolveAttentionEventRequest(ContractModel):
    """Frozen body is {}. Actor and timestamp come from authenticated server principal."""


class ClinicianSubjectAssignmentContract(ContractModel):
    clinician_id: str = Field(min_length=1)
    subject_id: UUID
    active: bool
    assigned_at: dt.datetime
    ended_at: Optional[dt.datetime] = None


class AuthPrincipal(ContractModel):
    """Normalized server principal AFTER credential verification.

    This does not define a JWT algorithm, issuer value, audience value, key set,
    or authentication provider. Phase 1 must configure those from the approved
    auth deployment rather than inventing them.
    """

    principal_type: PrincipalType
    principal_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    clinician_id: Optional[str] = None
    subject_id: Optional[UUID] = None

    @model_validator(mode="after")
    def binding_matches_principal_type(self):
        if self.principal_type == PrincipalType.CLINICIAN and not self.clinician_id:
            raise ValueError("clinician principal requires clinician_id binding")
        if self.principal_type == PrincipalType.PATIENT and self.subject_id is None:
            raise ValueError("patient principal requires subject_id binding")
        return self


class ErrorCode(str, Enum):
    ASSESSMENT_UNAVAILABLE = "ASSESSMENT_UNAVAILABLE"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    CONFLICT = "CONFLICT"


class ErrorDetail(ContractModel):
    code: ErrorCode
    message: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    retryable: bool


class ErrorEnvelope(ContractModel):
    error: ErrorDetail


RESEARCH_SAFE_TERMINOLOGY = {
    "current_assessment": "Current multimodal assessment",
    "c3_signal": "Clinical NLP / TC-WPN signal",
    "forecast": "Potential escalation predicted within the 10-minute forecast horizon",
    "insufficient": "Assessment unavailable - insufficient current data",
    "c2": "Experimental - not included in fusion",
    "rag_unavailable": "Supporting evidence unavailable / RAG abstained",
    "offline": "Last updated ... / Offline",
}

PROHIBITED_RESEARCH_CLAIMS = (
    "Patient will have an anxiety attack in exactly 10 minutes",
    "Patient risk = TC-WPN",
    "Behavioural risk = 0",
)

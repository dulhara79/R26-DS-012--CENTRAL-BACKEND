"""R26-DS-012 Phase 0 frozen integration contracts (v1).

Status
------
TARGET contract definitions only. This file intentionally contains no FastAPI
routes, persistence logic, authentication implementation, fusion calculation,
or attention-policy thresholds. Those belong to later handbook phases.

The contracts are aligned with:
* the System Integration Implementation Handbook v1.0;
* the ClinAnx frozen client contracts/fixtures on 2026-09-18.

Research-use boundary
---------------------
This is a research prototype contract. A physiological forecast is not called
a multimodal forecast unless a separately specified and validated method
supports that claim.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Probability = Annotated[float, Field(ge=0.0, le=1.0)]
PositiveId = Annotated[int, Field(gt=0)]
PositiveMinutes = Annotated[int, Field(gt=0)]


class FrozenModel(BaseModel):
    """Reject silent contract drift at the backend boundary."""

    model_config = ConfigDict(extra="forbid")


class RiskTier(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class AssessmentStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class ForecastScope(str, Enum):
    PHYSIOLOGICAL = "physiological"
    MULTIMODAL = "multimodal"


class ModalityState(str, Enum):
    """Client-facing AssessmentSummary status vocabulary.

    Internal ComponentResult adapters may retain richer service-specific states
    (for example warming_up/insufficient_data/poor_signal) in their audit
    detail. This enum freezes the v1 assessment projection understood by the
    current ClinAnx client.
    """

    OK = "ok"
    STALE = "stale"
    NOT_VALIDATED = "not_validated"
    UNAVAILABLE = "unavailable"
    NO_SUPPORT_SET = "no_support_set"
    ERROR = "error"


class AttentionEventStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class AttentionSeverity(str, Enum):
    HIGH = "high"


class CurrentAssessment(FrozenModel):
    score: Probability | None = None
    tier: RiskTier | None = None
    band: str | None = None

    @model_validator(mode="after")
    def score_and_tier_move_together(self) -> "CurrentAssessment":
        if self.score is not None and self.tier is None:
            raise ValueError("tier is required when current assessment score is present")
        return self


class ForecastSource(FrozenModel):
    component: Literal["c1_physiological"]
    reading_id: str | int | None = None


class ForecastResult(FrozenModel):
    """Forecast wire object.

    `subject_id`, `fusion_result_id`, `model_version`, and `source` are optional
    in the nested AssessmentSummary projection because the frozen ClinAnx
    fixture does not require them. Phase 3 persistence must retain equivalent
    provenance in the durable ForecastResult record.
    """

    forecast_result_id: str = Field(min_length=1)
    scope: ForecastScope
    horizon_minutes: PositiveMinutes
    score: Probability | None = None
    tier: RiskTier | None = None
    escalation_probability: Probability | None = None
    escalation_predicted: bool
    generated_at: datetime
    valid_until: datetime

    subject_id: str | None = None
    fusion_result_id: PositiveId | None = None
    model_version: str | None = None
    source: ForecastSource | None = None

    @model_validator(mode="after")
    def validate_forecast(self) -> "ForecastResult":
        if self.score is not None and self.tier is None:
            raise ValueError("tier is required when forecast score is present")
        if self.valid_until <= self.generated_at:
            raise ValueError("valid_until must be after generated_at")
        return self


class ModalityStatus(FrozenModel):
    component_id: Literal[
        "c1_physiological",
        "c2_behavioral",
        "c3_clinical_nlp",
        "c4_demographic",
    ]
    score: Probability | None = None
    available: bool
    included_in_fusion: bool
    status: ModalityState
    confidence: Probability | None = None
    coverage: Probability | None = None
    captured_at: datetime | None = None
    contribution: float | None = None

    @model_validator(mode="after")
    def validate_inclusion_semantics(self) -> "ModalityStatus":
        if self.included_in_fusion:
            if not self.available:
                raise ValueError("included modality must be available")
            if self.score is None:
                raise ValueError("included modality must have a score")
            if self.status != ModalityState.OK:
                raise ValueError("included modality must have status=ok")

        if self.status == ModalityState.STALE and self.included_in_fusion:
            raise ValueError("stale modality cannot be included in fusion")

        if self.status == ModalityState.NOT_VALIDATED:
            if self.included_in_fusion:
                raise ValueError("not_validated modality cannot be included")
            if self.contribution is not None:
                raise ValueError("not_validated modality cannot have a contribution")

        # Phase 0 freezes the registered exclusion rule for C2.
        if self.component_id == "c2_behavioral" and self.included_in_fusion:
            raise ValueError("c2_behavioral is excluded from v1 active fusion")

        return self


class AssessmentSummary(FrozenModel):
    subject_id: str = Field(min_length=1)
    fusion_result_id: PositiveId | None = None
    current_assessment: CurrentAssessment
    forecast: ForecastResult | None = None
    confidence: Probability | None = None
    uncertainty: Probability | None = None
    assessment_status: AssessmentStatus
    modalities: list[ModalityStatus]
    computed_at: datetime
    model_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_assessment_state(self) -> "AssessmentSummary":
        if self.assessment_status == AssessmentStatus.COMPLETE:
            if self.fusion_result_id is None:
                raise ValueError("complete assessment requires fusion_result_id")
            if self.current_assessment.score is None:
                raise ValueError("complete assessment requires current score")

        if self.assessment_status == AssessmentStatus.UNAVAILABLE:
            if self.current_assessment.score is not None:
                raise ValueError("unavailable assessment cannot expose a current score")
            if self.current_assessment.tier is not None:
                raise ValueError("unavailable assessment cannot expose a current tier")

        return self


class PatientSummary(FrozenModel):
    subject_id: str = Field(min_length=1)
    display_id: str | None = None
    fusion_result_id: PositiveId | None = None
    current: CurrentAssessment | None = None
    forecast: ForecastResult | None = None
    assessment_status: AssessmentStatus
    last_updated: datetime | None = None
    open_event_count: Annotated[int, Field(ge=0)] | None = None


class AttentionEvent(FrozenModel):
    id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    fusion_result_id: PositiveId
    forecast_result_id: str = Field(min_length=1)
    event_type: Literal["acute_escalation_forecast"]
    severity: AttentionSeverity
    reason: str = Field(min_length=1)
    forecast_horizon: PositiveMinutes
    status: AttentionEventStatus
    created_at: datetime
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "AttentionEvent":
        if self.status == AttentionEventStatus.OPEN:
            if any(
                value is not None
                for value in (
                    self.acknowledged_at,
                    self.acknowledged_by,
                    self.resolved_at,
                    self.resolved_by,
                )
            ):
                raise ValueError("OPEN event cannot contain acknowledge/resolve state")

        if self.status == AttentionEventStatus.ACKNOWLEDGED:
            if self.acknowledged_at is None or not self.acknowledged_by:
                raise ValueError("ACKNOWLEDGED event requires actor and timestamp")
            if self.resolved_at is not None or self.resolved_by is not None:
                raise ValueError("ACKNOWLEDGED event cannot contain resolved state")

        if self.status == AttentionEventStatus.RESOLVED:
            if self.acknowledged_at is None or not self.acknowledged_by:
                raise ValueError("RESOLVED event must preserve acknowledgement state")
            if self.resolved_at is None or not self.resolved_by:
                raise ValueError("RESOLVED event requires actor and timestamp")

        return self


JwtAudience = str | list[str]


class ClinicianPrincipalClaims(FrozenModel):
    """Required target claims after cryptographic JWT verification.

    Exact issuer/audience values and signing-key configuration are deployment
    settings to be frozen in Phase 1. Phase 0 freezes the required semantics,
    not fabricated deployment values.
    """

    sub: str = Field(min_length=1)
    role: Literal["clinician"]
    exp: int
    iss: str = Field(min_length=1)
    aud: JwtAudience

    @property
    def clinician_id(self) -> str:
        return self.sub


class PatientPrincipalClaims(FrozenModel):
    sub: str = Field(min_length=1)
    role: Literal["patient"]
    subject_id: str = Field(min_length=1)
    exp: int
    iss: str = Field(min_length=1)
    aud: JwtAudience

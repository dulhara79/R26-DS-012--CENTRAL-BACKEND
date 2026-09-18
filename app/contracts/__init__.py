"""Frozen target integration contracts for the R26-DS-012 central backend.

These modules describe TARGET wire contracts. Importing them does not make
the corresponding HTTP routes CURRENT or implemented.
"""

from .v1 import (
    AssessmentStatus,
    AssessmentSummary,
    AttentionEvent,
    AttentionEventStatus,
    AttentionSeverity,
    ClinicianPrincipalClaims,
    CurrentAssessment,
    ForecastResult,
    ForecastScope,
    ModalityState,
    ModalityStatus,
    PatientPrincipalClaims,
    PatientSummary,
    RiskTier,
)

__all__ = [
    "AssessmentStatus",
    "AssessmentSummary",
    "AttentionEvent",
    "AttentionEventStatus",
    "AttentionSeverity",
    "ClinicianPrincipalClaims",
    "CurrentAssessment",
    "ForecastResult",
    "ForecastScope",
    "ModalityState",
    "ModalityStatus",
    "PatientPrincipalClaims",
    "PatientSummary",
    "RiskTier",
]

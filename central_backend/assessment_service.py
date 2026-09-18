"""Canonical persisted-assessment projection for Handbook Phase 2.

This module is deliberately read-only. It does not call C1-C4, Fusion, or RAG
and it does not construct a ForecastResult. The authoritative current score,
tier, band, confidence, model version and assessment identity come from one
persisted FusionResult row.

Legacy CURRENT completeness vocabulary is adapted explicitly:
    complete    -> complete
    provisional -> partial
    insufficient -> unavailable

The frozen Phase-0 TARGET vocabulary is:
    complete | partial | unavailable
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable, Literal, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

import gate
import forecast_service
from db_models import FusionResult, ModalityReading
from schemas.phase0_v1 import (
    AssessmentStatus,
    AssessmentSummary,
    Band,
    ComponentStatus,
    CurrentAssessment,
    ModalityId,
    ModalitySummary,
    Tier,
)

ALL_MODALITIES = (
    ModalityId.C1_PHYSIOLOGICAL,
    ModalityId.C2_BEHAVIORAL,
    ModalityId.C3_CLINICAL_NLP,
    ModalityId.C4_DEMOGRAPHIC,
)
REQUIRED_FUSION_MODALITIES = {
    ModalityId.C1_PHYSIOLOGICAL.value,
    ModalityId.C3_CLINICAL_NLP.value,
    ModalityId.C4_DEMOGRAPHIC.value,
}

_LEGACY_ASSESSMENT_STATUS = {
    "complete": AssessmentStatus.COMPLETE,
    "provisional": AssessmentStatus.PARTIAL,
    "insufficient": AssessmentStatus.UNAVAILABLE,
    # Accept already-target-shaped persisted metadata without changing it.
    "partial": AssessmentStatus.PARTIAL,
    "unavailable": AssessmentStatus.UNAVAILABLE,
}


class AssessmentProjectionError(RuntimeError):
    """Persisted state cannot be represented safely by the frozen contract."""


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


def latest_fusion_row(db: Session, subject_id: str) -> Optional[FusionResult]:
    """Return the newest persisted authoritative FusionResult; never recompute."""
    return db.scalar(
        select(FusionResult)
        .where(FusionResult.subject_id == subject_id)
        .order_by(FusionResult.computed_at.desc(), FusionResult.id.desc())
        .limit(1)
    )


def fusion_history_rows(db: Session, subject_id: str, *, limit: int = 100) -> list[FusionResult]:
    """Return immutable persisted FusionResult history, newest first."""
    return list(
        db.scalars(
            select(FusionResult)
            .where(FusionResult.subject_id == subject_id)
            .order_by(FusionResult.computed_at.desc(), FusionResult.id.desc())
            .limit(limit)
        ).all()
    )


def _stored_gate_usable(row: FusionResult) -> set[str]:
    harmonisation = row.harmonisation or {}
    gate_summary = harmonisation.get("gate")
    if isinstance(gate_summary, dict):
        usable = gate_summary.get("usable_modalities")
        if isinstance(usable, (list, tuple, set)):
            return {str(x) for x in usable}

    # Older persisted rows may predate the gate summary. Positive persisted
    # Fusion weights are the next-best provenance signal; this reads stored
    # output only and never recalculates scientific weights.
    weights = row.weights or {}
    return {
        str(component_id)
        for component_id, value in weights.items()
        if isinstance(value, (int, float))
        and float(value) > 0.0
        and str(component_id) not in gate.EXCLUDED_MODALITIES
    }


def assessment_status_for_row(row: FusionResult) -> AssessmentStatus:
    """Adapt CURRENT persisted/gating terminology to the frozen TARGET enum."""
    # A row without an authoritative composite/tier is unavailable regardless
    # of older metadata. This prevents GREY/blocked results becoming Low/0.
    if row.composite is None or row.tier is None or row.band == Band.GREY.value:
        return AssessmentStatus.UNAVAILABLE

    stored = (row.harmonisation or {}).get("assessment")
    raw_status = stored.get("status") if isinstance(stored, dict) else None
    mapped = _LEGACY_ASSESSMENT_STATUS.get(str(raw_status).lower()) if raw_status else None
    if mapped is not None:
        # A valid persisted composite cannot satisfy the frozen unavailable
        # invariant; conservatively expose it as partial rather than pretending
        # completeness if legacy metadata is contradictory.
        if mapped == AssessmentStatus.UNAVAILABLE:
            return AssessmentStatus.PARTIAL
        return mapped

    usable = _stored_gate_usable(row)
    if REQUIRED_FUSION_MODALITIES.issubset(usable):
        return AssessmentStatus.COMPLETE

    # Unknown/missing legacy completeness metadata with a valid authoritative
    # composite fails conservative: it is partial, never silently complete.
    return AssessmentStatus.PARTIAL


def _reading_as_of(
    db: Session,
    *,
    subject_id: str,
    modality: str,
    fusion_row: FusionResult,
) -> Optional[ModalityReading]:
    """Find the newest append-only reading that existed when this row was made.

    created_at is used for historical provenance rather than asking external
    services or taking today's latest reading. This preserves history across
    later ingests. The current schema does not persist exact reading IDs on each
    FusionResult, so this is the strongest reconstruction available without a
    schema/scientific-table change.
    """
    return db.scalar(
        select(ModalityReading)
        .where(
            ModalityReading.subject_id == subject_id,
            ModalityReading.modality == modality,
            ModalityReading.created_at <= fusion_row.computed_at,
        )
        .order_by(ModalityReading.created_at.desc(), ModalityReading.id.desc())
        .limit(1)
    )


def _is_fresh(reading: ModalityReading, modality: str, *, as_of: dt.datetime) -> bool:
    max_age = gate.MAX_AGE_MINUTES.get(modality)
    if max_age is None:
        return True
    captured_at = _aware(reading.captured_at)
    age_minutes = max((_aware(as_of) - captured_at).total_seconds() / 60.0, 0.0)
    return age_minutes <= max_age


def _component_status(
    reading: Optional[ModalityReading],
    modality: str,
    *,
    as_of: dt.datetime,
) -> ComponentStatus:
    if modality == ModalityId.C2_BEHAVIORAL.value:
        # Registered project rule: C2 stays experimental/not_validated and
        # excluded even if a service response was persisted.
        return ComponentStatus.NOT_VALIDATED

    if reading is None:
        return ComponentStatus.INSUFFICIENT_DATA

    raw = str(reading.status or "").strip().lower()
    try:
        status = ComponentStatus(raw)
    except ValueError:
        # Unknown component states fail closed instead of being treated as ok.
        return ComponentStatus.ERROR

    if status == ComponentStatus.OK and not _is_fresh(reading, modality, as_of=as_of):
        # The frozen Phase-0 vocabulary has no literal "stale"; poor_signal is
        # the safe non-ok status used for stale evidence at this boundary.
        return ComponentStatus.POOR_SIGNAL
    return status


def _persisted_signal_score(
    row: FusionResult,
    reading: Optional[ModalityReading],
    modality: str,
    status: ComponentStatus,
) -> Optional[float]:
    """Expose only a persisted, scale-safe component score; never calculate one."""
    if reading is None or status != ComponentStatus.OK:
        return None
    if modality == ModalityId.C2_BEHAVIORAL.value:
        return None

    audit = (row.harmonisation or {}).get(modality)
    if isinstance(audit, dict):
        harmonised = audit.get("harmonised")
        if isinstance(harmonised, (int, float)):
            return float(harmonised)

    # C3/C4 CURRENT adapters already persist probability-like values on 0..1.
    # C1's CURRENT native score is 0..100, so do not silently normalize it if
    # the persisted Fusion harmonisation audit is absent.
    if modality in {
        ModalityId.C3_CLINICAL_NLP.value,
        ModalityId.C4_DEMOGRAPHIC.value,
    } and isinstance(reading.raw_score, (int, float)):
        value = float(reading.raw_score)
        if 0.0 <= value <= 1.0:
            return value
    return None


def _modality_summaries(db: Session, row: FusionResult) -> list[ModalitySummary]:
    usable = _stored_gate_usable(row)
    contributions = row.contributions or {}
    out: list[ModalitySummary] = []

    for component in ALL_MODALITIES:
        modality = component.value
        reading = _reading_as_of(
            db,
            subject_id=row.subject_id,
            modality=modality,
            fusion_row=row,
        )
        status = _component_status(reading, modality, as_of=row.computed_at)
        available = status == ComponentStatus.OK and modality not in gate.EXCLUDED_MODALITIES
        included = (
            row.composite is not None
            and available
            and modality in usable
            and modality not in gate.EXCLUDED_MODALITIES
        )

        contribution = contributions.get(modality) if included else None
        if not isinstance(contribution, (int, float)):
            contribution = None

        out.append(
            ModalitySummary(
                component_id=component,
                score=_persisted_signal_score(row, reading, modality, status),
                available=available,
                included_in_fusion=included,
                status=status,
                confidence=(float(reading.confidence) if reading is not None else None),
                coverage=(float(reading.coverage) if reading is not None else None),
                captured_at=(_aware(reading.captured_at) if reading is not None else None),
                contribution=(float(contribution) if contribution is not None else None),
            )
        )

    return out


def build_assessment(
    db: Session,
    row: FusionResult,
    *,
    audience: Literal["clinician", "patient"] = "clinician",
    forecast_mode: Literal["current", "historical"] = "current",
) -> AssessmentSummary:
    """Project one persisted FusionResult into the frozen AssessmentSummary."""
    status = assessment_status_for_row(row)

    if status == AssessmentStatus.UNAVAILABLE:
        current = CurrentAssessment(score=None, tier=None, band=Band.GREY)
        confidence = None
    else:
        try:
            tier = Tier(str(row.tier))
            band = Band(str(row.band))
        except ValueError as exc:
            raise AssessmentProjectionError(
                "persisted FusionResult has an unsupported tier/band"
            ) from exc
        if row.composite is None:
            raise AssessmentProjectionError(
                "available/partial FusionResult is missing composite"
            )
        current = CurrentAssessment(score=float(row.composite), tier=tier, band=band)
        confidence = float(row.confidence) if row.confidence is not None else None

    model_version = str(row.model_version or "").strip()
    if not model_version:
        raise AssessmentProjectionError(
            "persisted FusionResult is missing model_version provenance"
        )

    modalities = _modality_summaries(db, row)
    if audience == "patient":
        # The CURRENT patient projection intentionally withholds per-modality
        # clinical detail. Phase 2 keeps that privacy boundary; an ethics/UX
        # approved patient modality view can be introduced later without
        # changing the authoritative assessment identity.
        modalities = []

    if forecast_mode == "historical":
        forecast_row = forecast_service.latest_forecast_for_fusion(db, row.id)
    else:
        forecast_row = forecast_service.latest_forecast_for_fusion(
            db,
            row.id,
            valid_at=dt.datetime.now(dt.timezone.utc),
        )
    forecast = (
        forecast_service.to_projection(forecast_row)
        if forecast_row is not None
        else None
    )

    return AssessmentSummary(
        subject_id=row.subject_id,
        fusion_result_id=row.id,
        current_assessment=current,
        forecast=forecast,
        confidence=confidence,
        assessment_status=status,
        modalities=modalities,
        computed_at=_aware(row.computed_at),
        model_version=model_version,
    )


def build_history(
    db: Session,
    rows: Iterable[FusionResult],
) -> list[AssessmentSummary]:
    """Build clinician history only from persisted state; no external calls."""
    return [
        build_assessment(
            db,
            row,
            audience="clinician",
            forecast_mode="historical",
        )
        for row in rows
    ]

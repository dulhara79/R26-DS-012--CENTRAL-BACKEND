"""Phase 3 near-term forecast persistence and projection.

The handbook requires a ForecastResult that is separate from current
FusionResult. CURRENT C1 returns a physiological risk_forecast, so Phase 3
persists only that signal with scope="physiological".

This module does not create an attention/escalation policy. Phase 4 owns
confirmation, hysteresis, thresholds, deduplication and AttentionEvent creation.
"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from db_models import ForecastResult, FusionResult, ModalityReading
from schemas.phase0_v1 import ForecastProjection, ForecastResultContract, ForecastSource


C1_COMPONENT = "c1_physiological"
TARGET_HORIZON_MINUTES = 10


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


def _parse_time(value) -> Optional[dt.datetime]:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return _aware(value)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _aware(parsed)


def _finite_number(value) -> Optional[float]:
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _ten_minute_score(detail: dict) -> Optional[float]:
    """Extract the documented +10 minute C1 point without rescaling it.

    Current C1 uses its native 0..100 risk-index scale. Phase 3 preserves that
    scale rather than inventing a normalization merely because the handbook's
    illustrative JSON uses 0.84.
    """
    raw = detail.get("risk_forecast")
    if not isinstance(raw, list):
        return None

    horizons = detail.get("forecast_horizons_minutes")
    if isinstance(horizons, list) and len(horizons) == len(raw):
        for horizon, score in zip(horizons, raw):
            if _finite_number(horizon) == float(TARGET_HORIZON_MINUTES):
                return _finite_number(score)
        return None

    # Handbook CURRENT compatibility: legacy C1 may return >=10 sequential
    # minute points instead of an explicit horizons array.
    if len(raw) >= TARGET_HORIZON_MINUTES:
        return _finite_number(raw[TARGET_HORIZON_MINUTES - 1])
    return None


def _generated_at(detail: dict, reading: ModalityReading) -> dt.datetime:
    for key in ("forecast_generated_at", "generated_at", "computed_at"):
        parsed = _parse_time(detail.get(key))
        if parsed is not None:
            return parsed

    # The inspected C1 contract does not guarantee a forecast-generation
    # timestamp. In that case record the Central Backend observation/persist
    # time, not the sensor capture time, because captured_at describes the
    # source reading rather than when the future projection was produced.
    return _aware(reading.created_at)


def _model_version(detail: dict, reading: ModalityReading) -> str:
    for key in ("forecast_model_version", "model_version"):
        value = detail.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    if reading.model_version and str(reading.model_version).strip():
        return str(reading.model_version).strip()
    return "unreported:c1"


def latest_forecast_for_fusion(
    db: Session,
    fusion_result_id: int,
    *,
    valid_at: Optional[dt.datetime] = None,
) -> Optional[ForecastResult]:
    stmt = (
        select(ForecastResult)
        .where(ForecastResult.fusion_result_id == fusion_result_id)
        .order_by(ForecastResult.generated_at.desc(), ForecastResult.forecast_result_id.desc())
    )
    rows = list(db.scalars(stmt).all())
    if valid_at is None:
        return rows[0] if rows else None

    point = _aware(valid_at)
    for row in rows:
        if _aware(row.generated_at) <= point < _aware(row.valid_until):
            return row
    return None


def persist_c1_forecast(
    db: Session,
    *,
    subject_id: str,
    reading: ModalityReading,
    fusion_row: Optional[FusionResult],
) -> Optional[ForecastResult]:
    """Persist one handbook ForecastResult from the already-stored C1 response.

    Returns None when there is no defensible +10 minute forecast or when no
    FusionResult exists to satisfy the frozen foreign-key/identity contract.
    """
    if reading.modality != C1_COMPONENT or reading.status != "ok":
        return None
    if fusion_row is None or fusion_row.subject_id != subject_id:
        return None

    stored = reading.detail or {}
    response = stored.get("response")
    if not isinstance(response, dict):
        return None

    score = _ten_minute_score(response)
    if score is None:
        return None

    existing = db.scalar(
        select(ForecastResult).where(
            ForecastResult.source_component == C1_COMPONENT,
            ForecastResult.source_reading_id == reading.id,
            ForecastResult.horizon_minutes == TARGET_HORIZON_MINUTES,
        )
    )
    if existing is not None:
        return existing

    generated_at = _generated_at(response, reading)
    row = ForecastResult(
        forecast_result_id=f"fcst_{uuid.uuid4().hex}",
        subject_id=subject_id,
        fusion_result_id=fusion_row.id,
        scope="physiological",
        horizon_minutes=TARGET_HORIZON_MINUTES,
        score=score,
        # Phase 3 must not invent a threshold-derived forecast tier.
        tier=None,
        escalation_probability=None,
        # Phase 4 owns the project-approved escalation decision. False here
        # explicitly means no server escalation policy has declared an event;
        # it is not a reassuring classification of the physiological score.
        escalation_predicted=False,
        generated_at=generated_at,
        valid_until=generated_at + dt.timedelta(minutes=TARGET_HORIZON_MINUTES),
        model_version=_model_version(response, reading),
        source_component=C1_COMPONENT,
        source_reading_id=reading.id,
    )
    db.add(row)
    db.flush()
    return row


def to_contract(row: ForecastResult) -> ForecastResultContract:
    return ForecastResultContract(
        forecast_result_id=row.forecast_result_id,
        subject_id=row.subject_id,
        fusion_result_id=row.fusion_result_id,
        scope=row.scope,
        horizon_minutes=row.horizon_minutes,
        score=row.score,
        tier=row.tier,
        escalation_probability=row.escalation_probability,
        escalation_predicted=bool(row.escalation_predicted),
        generated_at=_aware(row.generated_at),
        valid_until=_aware(row.valid_until),
        model_version=row.model_version,
        source=ForecastSource(
            component=row.source_component,
            reading_id=row.source_reading_id,
        ),
    )


def to_projection(row: ForecastResult) -> ForecastProjection:
    contract = to_contract(row)
    return ForecastProjection(
        forecast_result_id=contract.forecast_result_id,
        scope=contract.scope,
        horizon_minutes=contract.horizon_minutes,
        score=contract.score,
        tier=contract.tier,
        escalation_probability=contract.escalation_probability,
        escalation_predicted=contract.escalation_predicted,
        generated_at=contract.generated_at,
        valid_until=contract.valid_until,
    )

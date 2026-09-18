"""Handbook Phase 4 server-owned attention engine.

This module migrates the documented CURRENT C1 predictive-escalation gate into
one durable Central Backend policy and implements the frozen AttentionEvent
lifecycle. It does not introduce a new model or claim clinical validation.

Policy source: Integration Handbook section 11.1 documents:
- elevated threshold 45
- high threshold 70
- minimum elevated increase 20
- minimum high increase 10
- recovery threshold 40
- two confirmations
- minimum confirmation spacing 20 seconds
- maximum confirmation gap 2 minutes

The target architecture requires that policy evaluation, episode suppression,
persistent events, acknowledgement and resolution are server-owned.
"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db_models import (
    AttentionEpisodeState,
    AttentionEventRecord,
    ForecastResult,
    ModalityReading,
    utcnow,
)
from schemas.phase0_v1 import AttentionEvent


POLICY_VERSION = "c1-physio-escalation-v1"
EVENT_TYPE = "acute_escalation_forecast"

ELEVATED_THRESHOLD = 45.0
HIGH_THRESHOLD = 70.0
MIN_ELEVATED_INCREASE = 20.0
MIN_HIGH_INCREASE = 10.0
RECOVERY_THRESHOLD = 40.0
REQUIRED_CONFIRMATIONS = 2
MIN_CONFIRMATION_SPACING = dt.timedelta(seconds=20)
MAX_CONFIRMATION_GAP = dt.timedelta(minutes=2)


@dataclass(frozen=True)
class PolicyEvaluation:
    action: str
    event: Optional[AttentionEventRecord] = None
    qualifying_level: Optional[str] = None


class InvalidEventTransition(RuntimeError):
    pass


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


def _finite(value) -> Optional[float]:
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _get_or_create_state(db: Session, subject_id: str) -> AttentionEpisodeState:
    state = db.scalar(
        select(AttentionEpisodeState)
        .where(
            AttentionEpisodeState.subject_id == subject_id,
            AttentionEpisodeState.policy_version == POLICY_VERSION,
        )
        .with_for_update()
    )
    if state is not None:
        return state

    state = AttentionEpisodeState(
        subject_id=subject_id,
        policy_version=POLICY_VERSION,
        confirmation_count=0,
        event_emitted=False,
        episode_number=1,
    )
    db.add(state)
    try:
        db.flush()
        return state
    except IntegrityError:
        # Concurrent first evaluations can race to create the one state row.
        # The caller commits ForecastResult before entering this engine, so a
        # rollback here cannot discard the scientific forecast snapshot.
        db.rollback()
        state = db.scalar(
            select(AttentionEpisodeState)
            .where(
                AttentionEpisodeState.subject_id == subject_id,
                AttentionEpisodeState.policy_version == POLICY_VERSION,
            )
            .with_for_update()
        )
        if state is None:
            raise
        return state


def _qualifying_level(current_score: float, forecast_score: float) -> Optional[str]:
    increase = forecast_score - current_score
    if forecast_score >= HIGH_THRESHOLD and increase >= MIN_HIGH_INCREASE:
        return "high"
    if forecast_score >= ELEVATED_THRESHOLD and increase >= MIN_ELEVATED_INCREASE:
        return "elevated"
    return None


def _reason(level: str, current_score: float, forecast_score: float) -> str:
    increase = forecast_score - current_score
    if level == "high":
        return (
            f"Confirmed physiological forecast crossing: forecast >= {HIGH_THRESHOLD:g} "
            f"with increase >= {MIN_HIGH_INCREASE:g} "
            f"(current={current_score:.1f}, forecast={forecast_score:.1f}, "
            f"increase={increase:.1f})"
        )
    return (
        f"Confirmed physiological forecast crossing: forecast >= {ELEVATED_THRESHOLD:g} "
        f"with increase >= {MIN_ELEVATED_INCREASE:g} "
        f"(current={current_score:.1f}, forecast={forecast_score:.1f}, "
        f"increase={increase:.1f})"
    )


def evaluate_forecast(
    db: Session,
    forecast: ForecastResult,
    *,
    evaluated_at: Optional[dt.datetime] = None,
) -> PolicyEvaluation:
    """Evaluate one persisted C1 ForecastResult and update durable episode state.

    Event creation requires two qualifying ForecastResults separated by at least
    20 seconds and no more than 2 minutes. Once an event is emitted, further
    qualifying updates are suppressed until both current and forecast scores are
    below the documented recovery threshold.
    """
    now = _aware(evaluated_at or utcnow())

    if (
        forecast.scope != "physiological"
        or forecast.source_component != "c1_physiological"
        or forecast.horizon_minutes != 10
    ):
        return PolicyEvaluation("unsupported_forecast")

    generated_at = _aware(forecast.generated_at)
    valid_until = _aware(forecast.valid_until)
    if generated_at > now or now >= valid_until:
        return PolicyEvaluation("forecast_not_current")

    reading = db.get(ModalityReading, forecast.source_reading_id)
    if (
        reading is None
        or reading.subject_id != forecast.subject_id
        or reading.modality != "c1_physiological"
        or reading.status != "ok"
    ):
        return PolicyEvaluation("source_reading_unusable")

    current_score = _finite(reading.raw_score)
    forecast_score = _finite(forecast.score)
    if current_score is None or forecast_score is None:
        return PolicyEvaluation("score_unavailable")

    state = _get_or_create_state(db, forecast.subject_id)

    # Retrying the same ingest/forecast must never count as a second clinical
    # confirmation.
    if state.last_forecast_result_id == forecast.forecast_result_id:
        event = db.get(AttentionEventRecord, state.current_event_id) if state.current_event_id else None
        return PolicyEvaluation("duplicate_forecast", event=event)

    state.last_forecast_result_id = forecast.forecast_result_id
    state.updated_at = now

    recovered = (
        current_score < RECOVERY_THRESHOLD
        and forecast_score < RECOVERY_THRESHOLD
    )
    if recovered:
        was_active_episode = bool(
            state.event_emitted
            or state.confirmation_count
            or state.last_confirmation_at
        )
        if was_active_episode:
            if state.event_emitted:
                state.episode_number += 1
            state.confirmation_count = 0
            state.last_confirmation_at = None
            state.event_emitted = False
            state.current_event_id = None
            state.recovered_at = now
            return PolicyEvaluation("recovered")
        return PolicyEvaluation("stable_below_recovery")

    level = _qualifying_level(current_score, forecast_score)
    if level is None:
        # Same behavior as the documented CURRENT gate: a non-qualifying update
        # clears an unconfirmed candidate but does not re-arm an already emitted
        # episode until recovery criteria are met.
        state.confirmation_count = 0
        state.last_confirmation_at = None
        return PolicyEvaluation("not_qualifying")

    if state.event_emitted:
        event = db.get(AttentionEventRecord, state.current_event_id) if state.current_event_id else None
        return PolicyEvaluation("suppressed_same_episode", event=event, qualifying_level=level)

    if state.last_confirmation_at is not None:
        last = _aware(state.last_confirmation_at)
        gap = generated_at - last
        if gap.total_seconds() < 0 or gap > MAX_CONFIRMATION_GAP:
            state.confirmation_count = 0
            state.last_confirmation_at = None
        elif gap < MIN_CONFIRMATION_SPACING:
            return PolicyEvaluation("awaiting_confirmation_spacing", qualifying_level=level)

    state.last_confirmation_at = generated_at
    state.confirmation_count += 1
    state.recovered_at = None

    if state.confirmation_count < REQUIRED_CONFIRMATIONS:
        return PolicyEvaluation("candidate_confirmation", qualifying_level=level)

    episode_key = f"{forecast.subject_id}:{POLICY_VERSION}:{state.episode_number}"
    existing = db.scalar(
        select(AttentionEventRecord).where(
            AttentionEventRecord.episode_key == episode_key
        )
    )
    if existing is not None:
        state.event_emitted = True
        state.current_event_id = existing.id
        return PolicyEvaluation("existing_episode_event", event=existing, qualifying_level=level)

    event = AttentionEventRecord(
        id=f"evt_{uuid.uuid4().hex}",
        subject_id=forecast.subject_id,
        fusion_result_id=forecast.fusion_result_id,
        forecast_result_id=forecast.forecast_result_id,
        event_type=EVENT_TYPE,
        severity=level,
        reason=_reason(level, current_score, forecast_score),
        forecast_horizon=forecast.horizon_minutes,
        status="OPEN",
        created_at=now,
        policy_version=POLICY_VERSION,
        episode_key=episode_key,
    )
    db.add(event)
    db.flush()

    state.event_emitted = True
    state.current_event_id = event.id

    # This field is the frozen ForecastResult policy annotation. It is set only
    # after the server confirmation rule succeeds; a single noisy point remains
    # false and cannot manufacture urgency.
    forecast.escalation_predicted = True

    return PolicyEvaluation("event_created", event=event, qualifying_level=level)


def to_contract(row: AttentionEventRecord) -> AttentionEvent:
    return AttentionEvent(
        id=row.id,
        subject_id=row.subject_id,
        fusion_result_id=row.fusion_result_id,
        forecast_result_id=row.forecast_result_id,
        event_type=row.event_type,
        severity=row.severity,
        reason=row.reason,
        forecast_horizon=row.forecast_horizon,
        status=row.status,
        created_at=_aware(row.created_at),
        acknowledged_at=_aware(row.acknowledged_at) if row.acknowledged_at else None,
        acknowledged_by=row.acknowledged_by,
        resolved_at=_aware(row.resolved_at) if row.resolved_at else None,
        resolved_by=row.resolved_by,
        policy_version=row.policy_version,
    )


def acknowledge(
    db: Session,
    event_id: str,
    *,
    actor: str,
    at: Optional[dt.datetime] = None,
) -> Optional[AttentionEventRecord]:
    """Atomically OPEN -> ACKNOWLEDGED; repeats return canonical server state."""
    when = _aware(at or utcnow())
    result = db.execute(
        update(AttentionEventRecord)
        .where(
            AttentionEventRecord.id == event_id,
            AttentionEventRecord.status == "OPEN",
        )
        .values(
            status="ACKNOWLEDGED",
            acknowledged_at=when,
            acknowledged_by=actor,
        )
        .execution_options(synchronize_session=False)
    )
    db.flush()
    row = db.get(AttentionEventRecord, event_id)
    if row is None:
        return None
    if result.rowcount == 1 or row.status in {"ACKNOWLEDGED", "RESOLVED"}:
        return row
    raise InvalidEventTransition(f"cannot acknowledge event from {row.status}")


def resolve(
    db: Session,
    event_id: str,
    *,
    actor: str,
    at: Optional[dt.datetime] = None,
) -> Optional[AttentionEventRecord]:
    """Atomically ACKNOWLEDGED -> RESOLVED; repeats return canonical state."""
    when = _aware(at or utcnow())
    result = db.execute(
        update(AttentionEventRecord)
        .where(
            AttentionEventRecord.id == event_id,
            AttentionEventRecord.status == "ACKNOWLEDGED",
        )
        .values(
            status="RESOLVED",
            resolved_at=when,
            resolved_by=actor,
        )
        .execution_options(synchronize_session=False)
    )
    db.flush()
    row = db.get(AttentionEventRecord, event_id)
    if row is None:
        return None
    if result.rowcount == 1 or row.status == "RESOLVED":
        return row
    if row.status == "OPEN":
        raise InvalidEventTransition("event must be acknowledged before resolve")
    raise InvalidEventTransition(f"cannot resolve event from {row.status}")

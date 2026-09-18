"""Phase 4 server-owned attention-engine integration tests."""

from __future__ import annotations

import datetime as dt
import os
import tempfile
import uuid

_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdb.name}"
os.environ["MRN_PEPPER"] = "phase4-test-pepper"
os.environ["AUTH_JWT_ISSUER"] = "https://phase4-auth.test"
os.environ["AUTH_JWT_AUDIENCE"] = "r26ds012-central-backend"
os.environ["AUTH_JWT_ALGORITHMS"] = "HS256"
os.environ["AUTH_JWT_SECRET"] = "phase4-test-key"
os.environ["FUSION_URL"] = "https://fusion.invalid"
os.environ["FUSION_API_TOKEN"] = "phase4-service-key"

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

import attention_engine  # noqa: E402
import fusion_client  # noqa: E402
import main  # noqa: E402
import modality_clients as mc  # noqa: E402
from db_models import (  # noqa: E402
    AttentionEpisodeState,
    AttentionEventRecord,
    AuditLog,
    Clinician,
    ClinicianSubjectAssignment,
    ForecastResult,
    ModalityReading,
    SessionLocal,
    Subject,
)
from migrate_phase4 import apply as apply_phase4_migration  # noqa: E402

client = TestClient(main.app)
passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}   {detail}")


def make_token(
    *,
    sub: str,
    role: str,
    principal_type: str,
    clinician_id: str | None = None,
    subject_id: str | None = None,
) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    claims = {
        "sub": sub,
        "role": role,
        "principal_type": principal_type,
        "iss": os.environ["AUTH_JWT_ISSUER"],
        "aud": os.environ["AUTH_JWT_AUDIENCE"],
        "exp": now + dt.timedelta(hours=1),
    }
    if clinician_id:
        claims["clinician_id"] = clinician_id
    if subject_id:
        claims["subject_id"] = subject_id
    return jwt.encode(claims, os.environ["AUTH_JWT_SECRET"], algorithm="HS256")


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


subject_id = str(uuid.uuid4())
other_subject_id = str(uuid.uuid4())
base_time = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) - dt.timedelta(minutes=2)

with SessionLocal() as db:
    db.add_all([
        Subject(subject_id=subject_id, status="active"),
        Subject(subject_id=other_subject_id, status="active"),
        Clinician(
            clinician_id="DR_X",
            auth_subject="phase4-clinician-x",
            display_name="Dr X",
            role="clinician",
            status="active",
        ),
        Clinician(
            clinician_id="DR_Y",
            auth_subject="phase4-clinician-y",
            display_name="Dr Y",
            role="clinician",
            status="active",
        ),
        Clinician(
            clinician_id="DR_Z",
            auth_subject="phase4-clinician-z",
            display_name="Dr Z",
            role="clinician",
            status="active",
        ),
    ])
    db.flush()
    db.add_all([
        ClinicianSubjectAssignment(
            clinician_id="DR_X",
            subject_id=subject_id,
            active=True,
            assigned_at=base_time - dt.timedelta(days=1),
        ),
        ClinicianSubjectAssignment(
            clinician_id="DR_Y",
            subject_id=subject_id,
            active=True,
            assigned_at=base_time - dt.timedelta(days=1),
        ),
        ClinicianSubjectAssignment(
            clinician_id="DR_Z",
            subject_id=other_subject_id,
            active=True,
            assigned_at=base_time - dt.timedelta(days=1),
        ),
        # Static C4 gives the first C1 ingest enough streams for current Fusion.
        ModalityReading(
            subject_id=subject_id,
            modality="c4_demographic",
            raw_score=0.43,
            status="ok",
            confidence=0.7,
            coverage=1.0,
            captured_at=base_time - dt.timedelta(days=1),
            model_version="dcar-v1",
            detail={"response": {"score": 0.43}},
            created_at=base_time - dt.timedelta(days=1),
        ),
    ])
    db.commit()


token_x = make_token(
    sub="phase4-clinician-x",
    role="clinician",
    principal_type="clinician",
    clinician_id="DR_X",
)
token_y = make_token(
    sub="phase4-clinician-y",
    role="clinician",
    principal_type="clinician",
    clinician_id="DR_Y",
)
token_z = make_token(
    sub="phase4-clinician-z",
    role="clinician",
    principal_type="clinician",
    clinician_id="DR_Z",
)

# Two qualifying observations -> one event; further qualifying update stays in
# the same episode; recovery rearms; two later confirmations -> second event.
sequence = [
    # current, +10 forecast, generated_at
    (40.0, 65.0, base_time),
    (42.0, 72.0, base_time + dt.timedelta(seconds=30)),
    (43.0, 75.0, base_time + dt.timedelta(seconds=60)),
    (30.0, 35.0, base_time + dt.timedelta(seconds=90)),
    (40.0, 65.0, base_time + dt.timedelta(seconds=120)),
    (42.0, 72.0, base_time + dt.timedelta(seconds=150)),
]
call_index = 0


def stub_c1(user_id, window=None, client=None):
    global call_index
    current, forecast, generated = sequence[call_index]
    call_index += 1
    return mc.ComponentResult(
        raw_score=current,
        status="ok",
        confidence=0.5,
        coverage=0.5,
        model_version="c1-phase4-test-v1",
        captured_at=generated,
        detail={
            "status": "success",
            "current_risk_index": current,
            "risk_forecast": [max(current, forecast - 5.0), forecast],
            "forecast_horizons_minutes": [5, 10],
            "latest_reading_at": generated.isoformat(),
            "generated_at": generated.isoformat(),
            "model_version": "c1-phase4-test-v1",
        },
        note="phase4 test",
    )


def stub_fusion(subject_id_arg, readings):
    assert subject_id_arg == subject_id
    return {
        "composite_score": 0.50,
        "tier": "Medium",
        "band": "AMBER",
        "confidence": 0.60,
        "modalities_available": 2,
        "renormalised": True,
        "weights": {
            "c1_physiological": 0.65,
            "c4_demographic": 0.35,
            "c2_behavioral": 0.0,
        },
        "contributions": {
            "c1_physiological": 0.33,
            "c4_demographic": 0.17,
        },
        "harmonisation": {
            "c1_physiological": {"harmonised": 0.50},
            "c4_demographic": {"harmonised": 0.43},
        },
        "reason": None,
        "model_version": "fusion-phase4-test-v1",
    }


main.mc.call_c1 = stub_c1
mc.call_c1 = stub_c1
main.fusion_client.fuse = stub_fusion
fusion_client.fuse = stub_fusion

print("=" * 74)
print("Phase 4 · Attention Engine")
print("=" * 74)

responses = []
for i in range(3):
    r = client.post(
        "/v1/ingest/physiological",
        headers=headers(token_x),
        json={"subject_id": subject_id, "device_user_id": "phase4-device"},
    )
    responses.append(r)
    check(f"qualifying ingest {i + 1} returns 200", r.status_code == 200, r.text)

first = responses[0].json()
second = responses[1].json()
third = responses[2].json()

check(
    "first qualifying forecast is only a candidate confirmation",
    first.get("attention_event_id") is None
    and first.get("attention_policy_action") == "candidate_confirmation",
    str(first),
)
check(
    "second spaced confirmation creates one server event",
    bool(second.get("attention_event_id"))
    and second.get("attention_policy_action") == "event_created",
    str(second),
)
first_event_id = second.get("attention_event_id")
check(
    "later qualifying update is suppressed in the same episode",
    third.get("attention_event_id") == first_event_id
    and third.get("attention_policy_action") == "suppressed_same_episode",
    str(third),
)

with SessionLocal() as db:
    events = list(db.scalars(
        select(AttentionEventRecord).where(
            AttentionEventRecord.subject_id == subject_id
        )
    ).all())
    check("only one persistent event exists before recovery", len(events) == 1, str(len(events)))
    event = events[0]
    check(
        "event links exact persisted fusion and forecast results",
        db.get(ForecastResult, event.forecast_result_id) is not None
        and event.fusion_result_id == db.get(ForecastResult, event.forecast_result_id).fusion_result_id,
    )
    check(
        "event policy version is reproducible",
        event.policy_version == attention_engine.POLICY_VERSION,
        event.policy_version,
    )
    check(
        "event begins OPEN with no lifecycle actors",
        event.status == "OPEN"
        and event.acknowledged_at is None
        and event.acknowledged_by is None
        and event.resolved_at is None
        and event.resolved_by is None,
    )
    linked_forecast = db.get(ForecastResult, event.forecast_result_id)
    check(
        "confirmed crossing annotates only the confirmed forecast as predicted",
        linked_forecast is not None and linked_forecast.escalation_predicted is True,
    )
    state = db.scalar(select(AttentionEpisodeState).where(
        AttentionEpisodeState.subject_id == subject_id
    ))
    check(
        "episode state persists emitted-event suppression",
        state is not None
        and state.event_emitted is True
        and state.current_event_id == first_event_id,
    )

# Both assigned clinicians can retrieve the same canonical server event.
for label, token in [("Dr X", token_x), ("Dr Y", token_y)]:
    r = client.get(
        "/v1/attention-events?status=OPEN",
        headers=headers(token),
    )
    body = r.json() if r.status_code == 200 else {}
    ids = [e["id"] for e in body.get("events", [])]
    check(f"{label} sees assigned OPEN event", r.status_code == 200 and first_event_id in ids, r.text)

r = client.get(
    f"/v1/attention-events/{first_event_id}",
    headers=headers(token_z),
)
check("unassigned clinician cannot retrieve guessed event id", r.status_code == 403, r.text)

r = client.get(
    "/v1/attention-events",
    headers=headers(token_z),
)
check(
    "unassigned clinician global event list does not leak another patient",
    r.status_code == 200 and r.json().get("events") == [],
    r.text,
)

# Resolve-before-ack is an illegal lifecycle transition.
r = client.post(
    f"/v1/attention-events/{first_event_id}/resolve",
    headers=headers(token_x),
    json={},
)
check("OPEN event cannot resolve before acknowledgement", r.status_code == 409, r.text)

# First clinician acknowledges; second clinician sees/reconciles that state.
r = client.post(
    f"/v1/attention-events/{first_event_id}/acknowledge",
    headers=headers(token_x),
    json={},
)
ack = r.json().get("event", {}) if r.status_code == 200 else {}
check(
    "acknowledge records authenticated clinician and server timestamp",
    r.status_code == 200
    and ack.get("status") == "ACKNOWLEDGED"
    and ack.get("acknowledged_by") == "DR_X"
    and ack.get("acknowledged_at") is not None,
    r.text,
)

r = client.post(
    f"/v1/attention-events/{first_event_id}/acknowledge",
    headers=headers(token_y),
    json={},
)
ack_again = r.json().get("event", {}) if r.status_code == 200 else {}
check(
    "second clinician reconcile returns existing canonical ACK state",
    r.status_code == 200
    and ack_again.get("status") == "ACKNOWLEDGED"
    and ack_again.get("acknowledged_by") == "DR_X",
    r.text,
)

r = client.post(
    f"/v1/attention-events/{first_event_id}/resolve",
    headers=headers(token_y),
    json={},
)
resolved = r.json().get("event", {}) if r.status_code == 200 else {}
check(
    "resolve preserves ACK actor/time and records resolving clinician",
    r.status_code == 200
    and resolved.get("status") == "RESOLVED"
    and resolved.get("acknowledged_by") == "DR_X"
    and resolved.get("resolved_by") == "DR_Y"
    and resolved.get("resolved_at") is not None,
    r.text,
)

r = client.post(
    f"/v1/attention-events/{first_event_id}/resolve",
    headers=headers(token_x),
    json={},
)
resolved_again = r.json().get("event", {}) if r.status_code == 200 else {}
check(
    "repeat resolve returns canonical server state instead of overwriting actor",
    r.status_code == 200
    and resolved_again.get("resolved_by") == "DR_Y",
    r.text,
)

# The frozen mutation body is {}, so client-supplied actors/notes are rejected.
r = client.post(
    f"/v1/attention-events/{first_event_id}/acknowledge",
    headers=headers(token_x),
    json={"acknowledged_by": "spoofed"},
)
check("client cannot supply lifecycle actor fields", r.status_code == 422, r.text)

# Recovery update re-arms the durable episode state.
r = client.post(
    "/v1/ingest/physiological",
    headers=headers(token_x),
    json={"subject_id": subject_id, "device_user_id": "phase4-device"},
)
recovery = r.json() if r.status_code == 200 else {}
check(
    "documented recovery criterion re-arms episode state",
    r.status_code == 200
    and recovery.get("attention_policy_action") == "recovered",
    r.text,
)

with SessionLocal() as db:
    state = db.scalar(select(AttentionEpisodeState).where(
        AttentionEpisodeState.subject_id == subject_id
    ))
    check(
        "recovery clears suppression and advances episode number",
        state is not None
        and state.event_emitted is False
        and state.current_event_id is None
        and state.episode_number == 2,
        str(state.episode_number if state else None),
    )

# A distinct post-recovery episode can create exactly one new event.
r4 = client.post(
    "/v1/ingest/physiological",
    headers=headers(token_x),
    json={"subject_id": subject_id, "device_user_id": "phase4-device"},
)
r5 = client.post(
    "/v1/ingest/physiological",
    headers=headers(token_x),
    json={"subject_id": subject_id, "device_user_id": "phase4-device"},
)
check(
    "new episode again requires first candidate then confirmation",
    r4.status_code == 200
    and r4.json().get("attention_policy_action") == "candidate_confirmation"
    and r5.status_code == 200
    and r5.json().get("attention_policy_action") == "event_created",
    f"{r4.text} | {r5.text}",
)
second_event_id = r5.json().get("attention_event_id")
check("second episode receives a different event id", second_event_id != first_event_id)

with SessionLocal() as db:
    events = list(db.scalars(
        select(AttentionEventRecord)
        .where(AttentionEventRecord.subject_id == subject_id)
        .order_by(AttentionEventRecord.created_at)
    ).all())
    check("exactly two events exist for two distinct episodes", len(events) == 2, str(len(events)))

    audit_names = set(db.scalars(
        select(AuditLog.event).where(AuditLog.subject_id == subject_id)
    ).all())
    check(
        "create/ack/resolve/recovery lifecycle is auditable",
        {
            "attention.created",
            "attention.acknowledged",
            "attention.resolved",
            "attention.recovered",
        }.issubset(audit_names),
        str(sorted(audit_names)),
    )

# Same ForecastResult cannot count twice toward confirmation.
with SessionLocal() as db:
    latest = db.scalar(
        select(ForecastResult)
        .where(ForecastResult.subject_id == subject_id)
        .order_by(ForecastResult.generated_at.desc())
        .limit(1)
    )
    duplicate = attention_engine.evaluate_forecast(
        db,
        latest,
        evaluated_at=dt.datetime.now(dt.timezone.utc),
    )
    db.commit()
    check(
        "re-evaluating identical forecast cannot create another event",
        duplicate.action == "duplicate_forecast",
        duplicate.action,
    )

# API filters stay assignment-scoped and preserve persistent history.
r = client.get(
    f"/v1/attention-events?subject_id={subject_id}&limit=10",
    headers=headers(token_y),
)
check(
    "assigned clinician can retrieve persistent event history",
    r.status_code == 200 and len(r.json().get("events", [])) == 2,
    r.text,
)

r = client.get(
    f"/v1/attention-events?subject_id={subject_id}",
    headers=headers(token_z),
)
check("unassigned subject filter is denied", r.status_code == 403, r.text)

# Migration records schema state exactly once.
first_migration = apply_phase4_migration()
second_migration = apply_phase4_migration()
check(
    "Phase 4 migration is idempotent",
    first_migration is True and second_migration is False,
    f"first={first_migration}, second={second_migration}",
)

# Health exposes policy version, not secret threshold/config material.
r = client.get("/health")
health_attention = r.json().get("attention", {}) if r.status_code == 200 else {}
check(
    "health exposes attention policy version and lifecycle",
    r.status_code == 200
    and health_attention.get("policy_version") == attention_engine.POLICY_VERSION
    and health_attention.get("persistent_events") is True,
    r.text,
)

print()
print("=" * 74)
print(f"  {passed} passed, {failed} failed")
print("=" * 74)
raise SystemExit(1 if failed else 0)

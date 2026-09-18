"""Phase 3 ForecastResult persistence/integration tests."""

from __future__ import annotations

import datetime as dt
import os
import tempfile
import uuid

_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdb.name}"
os.environ["MRN_PEPPER"] = "phase3-test-pepper"
os.environ["AUTH_JWT_ISSUER"] = "https://phase3-auth.test"
os.environ["AUTH_JWT_AUDIENCE"] = "r26ds012-central-backend"
os.environ["AUTH_JWT_ALGORITHMS"] = "HS256"
os.environ["AUTH_JWT_SECRET"] = "phase3-test-key"
os.environ["FUSION_URL"] = "https://fusion.invalid"
os.environ["FUSION_API_TOKEN"] = "phase3-service-key"

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

import forecast_service  # noqa: E402
import fusion_client  # noqa: E402
import main  # noqa: E402
import modality_clients as mc  # noqa: E402
from db_models import (  # noqa: E402
    Clinician,
    ClinicianSubjectAssignment,
    ForecastResult,
    FusionResult,
    ModalityReading,
    SessionLocal,
    Subject,
)
from migrate_phase3 import apply as apply_phase3_migration  # noqa: E402

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
now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

with SessionLocal() as db:
    db.add(Subject(subject_id=subject_id, status="active"))
    db.add(Clinician(
        clinician_id="DR_PHASE3",
        auth_subject="phase3-clinician-sub",
        display_name="Dr Phase 3",
        role="clinician",
        status="active",
    ))
    db.flush()
    db.add(ClinicianSubjectAssignment(
        clinician_id="DR_PHASE3",
        subject_id=subject_id,
        active=True,
        assigned_at=now - dt.timedelta(days=1),
    ))
    # Existing static C4 prior lets the current-fusion gate pass when C1 arrives.
    db.add(ModalityReading(
        subject_id=subject_id,
        modality="c4_demographic",
        raw_score=0.43,
        status="ok",
        confidence=0.7,
        coverage=1.0,
        captured_at=now - dt.timedelta(days=1),
        model_version="dcar-v1",
        detail={"response": {"score": 0.43}},
        created_at=now - dt.timedelta(days=1),
    ))
    db.commit()


clinician = make_token(
    sub="phase3-clinician-sub",
    role="clinician",
    principal_type="clinician",
    clinician_id="DR_PHASE3",
)
patient = make_token(
    sub="phase3-patient-sub",
    role="patient",
    principal_type="patient",
    subject_id=subject_id,
)


def stub_c1(user_id, window=None, client=None):
    return mc.ComponentResult(
        raw_score=48.0,
        status="ok",
        confidence=0.5,
        coverage=0.5,
        model_version=None,
        captured_at=now,
        detail={
            "status": "success",
            "current_risk_index": 48.0,
            "risk_forecast": [61.0, 82.0],
            "forecast_horizons_minutes": [5, 10],
            "latest_reading_at": now.isoformat(),
            "generated_at": now.isoformat(),
            "model_version": "c1-forecast-test-v1",
        },
        note="phase3 fixture",
    )


def stub_fusion(subject_id_arg, readings):
    assert subject_id_arg == subject_id
    assert set(readings) == {"c1_physiological", "c4_demographic"}
    return {
        "composite_score": 0.44,
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
            "c1_physiological": 0.29,
            "c4_demographic": 0.15,
        },
        "harmonisation": {
            "c1_physiological": {"harmonised": 0.45},
            "c4_demographic": {"harmonised": 0.43},
        },
        "reason": None,
        "model_version": "fusion-test-v1",
    }


main.mc.call_c1 = stub_c1
mc.call_c1 = stub_c1
main.fusion_client.fuse = stub_fusion
fusion_client.fuse = stub_fusion

print("=" * 74)
print("Phase 3 · Forecast Contract")
print("=" * 74)

r = client.post(
    "/v1/ingest/physiological",
    headers=headers(clinician),
    json={"subject_id": subject_id, "device_user_id": "phase3-device"},
)
check("physiological ingest returns 200", r.status_code == 200, r.text)
payload = r.json() if r.status_code == 200 else {}
forecast_result_id = payload.get("forecast_result_id")
check("ingest persists a ForecastResult id", bool(forecast_result_id), str(payload))

with SessionLocal() as db:
    forecasts = list(db.scalars(
        select(ForecastResult).where(ForecastResult.subject_id == subject_id)
    ).all())
    check("exactly one forecast persisted", len(forecasts) == 1, str(len(forecasts)))
    forecast = forecasts[0] if forecasts else None

    check(
        "forecast is explicitly physiological, never mislabeled multimodal",
        forecast is not None and forecast.scope == "physiological",
        str(getattr(forecast, "scope", None)),
    )
    check(
        "forecast preserves C1 native +10 minute score without invented rescaling",
        forecast is not None
        and forecast.horizon_minutes == 10
        and forecast.score == 82.0,
        str(getattr(forecast, "score", None)),
    )
    check(
        "forecast has exact source-reading provenance",
        forecast is not None
        and forecast.source_component == "c1_physiological"
        and db.get(ModalityReading, forecast.source_reading_id) is not None,
    )
    check(
        "forecast links to a persisted authoritative FusionResult",
        forecast is not None
        and db.get(FusionResult, forecast.fusion_result_id) is not None,
    )
    check(
        "forecast preserves reported C1 model version",
        forecast is not None and forecast.model_version == "c1-forecast-test-v1",
        str(getattr(forecast, "model_version", None)),
    )
    check(
        "validity window is explicit 10 minutes",
        forecast is not None
        and (
            forecast_service._aware(forecast.valid_until)
            - forecast_service._aware(forecast.generated_at)
        ) == dt.timedelta(minutes=10),
    )
    check(
        "Phase 3 does not invent forecast tier or escalation probability",
        forecast is not None
        and forecast.tier is None
        and forecast.escalation_probability is None,
    )
    check(
        "Phase 3 does not run Phase-4 escalation policy",
        forecast is not None and forecast.escalation_predicted is False,
    )

# Assessment now includes the persisted forecast but keeps current state separate.
r = client.get(
    f"/v1/patients/{subject_id}/assessment/latest",
    headers=headers(patient),
)
check("patient latest assessment returns 200", r.status_code == 200, r.text)
assessment = r.json() if r.status_code == 200 else {}
forecast_json = assessment.get("forecast") or {}
check(
    "current assessment and forecast remain separate fields",
    assessment.get("current_assessment", {}).get("score") == 0.44
    and forecast_json.get("score") == 82.0,
    str(assessment),
)
check(
    "assessment forecast references persisted ForecastResult",
    forecast_json.get("forecast_result_id") == forecast_result_id
    and forecast_json.get("scope") == "physiological"
    and forecast_json.get("horizon_minutes") == 10,
    str(forecast_json),
)

# Read-path must not call C1/Fusion again.
external_calls: list[str] = []


def forbidden_external(*args, **kwargs):
    external_calls.append("called")
    raise AssertionError("assessment read attempted external service call")


main.mc.call_c1 = forbidden_external
mc.call_c1 = forbidden_external
main.fusion_client.fuse = forbidden_external
fusion_client.fuse = forbidden_external

r = client.get(
    f"/v1/patients/{subject_id}/assessment/latest",
    headers=headers(clinician),
)
check("clinician assessment read remains available", r.status_code == 200, r.text)
check("assessment read does not re-call C1/Fusion", external_calls == [], str(external_calls))

# History must include persisted forecast even after validity expires.
with SessionLocal() as db:
    forecast = db.get(ForecastResult, forecast_result_id)
    forecast.generated_at = now - dt.timedelta(hours=2)
    forecast.valid_until = forecast.generated_at + dt.timedelta(minutes=10)
    db.commit()

r = client.get(
    f"/v1/patients/{subject_id}/assessments",
    headers=headers(clinician),
)
history = r.json() if r.status_code == 200 else []
check("assessment history returns 200", r.status_code == 200, r.text)
check(
    "historical assessment preserves expired forecast provenance",
    bool(history)
    and history[0].get("forecast", {}).get("forecast_result_id") == forecast_result_id,
    str(history[:1]),
)

r = client.get(
    f"/v1/patients/{subject_id}/assessment/latest",
    headers=headers(patient),
)
check(
    "expired forecast is not presented as current",
    r.status_code == 200 and r.json().get("forecast") is None,
    r.text,
)

# Direct parser compatibility for legacy >=10 point C1 arrays.
legacy_reading = ModalityReading(
    id=9999,
    subject_id=subject_id,
    modality="c1_physiological",
    raw_score=45.0,
    status="ok",
    confidence=0.5,
    coverage=0.5,
    captured_at=now,
    model_version=None,
    detail={
        "response": {
            "status": "success",
            "risk_forecast": [float(i) for i in range(1, 11)],
        }
    },
    created_at=now,
)
check(
    "legacy 10-step C1 forecast selects the +10 minute point",
    forecast_service._ten_minute_score(legacy_reading.detail["response"]) == 10.0,
)

first = apply_phase3_migration()
second = apply_phase3_migration()
check(
    "Phase 3 migration is idempotent",
    first is True and second is False,
    f"first={first}, second={second}",
)

print()
print("=" * 74)
print(f"  {passed} passed, {failed} failed")
print("=" * 74)
raise SystemExit(1 if failed else 0)

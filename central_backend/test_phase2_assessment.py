"""Phase 2 canonical assessment integration tests."""

from __future__ import annotations

import datetime as dt
import os
import tempfile
import uuid

_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdb.name}"
os.environ["MRN_PEPPER"] = "phase2-test-pepper"
os.environ["AUTH_JWT_ISSUER"] = "https://phase2-auth.test"
os.environ["AUTH_JWT_AUDIENCE"] = "r26ds012-central-backend"
os.environ["AUTH_JWT_ALGORITHMS"] = "HS256"
os.environ["AUTH_JWT_SECRET"] = "phase2-test-key"
os.environ["FUSION_URL"] = "https://fusion.invalid"
os.environ["FUSION_API_TOKEN"] = "phase2-service-key"

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import fusion_client  # noqa: E402
import main  # noqa: E402
import modality_clients as mc  # noqa: E402
from db_models import (  # noqa: E402
    Clinician,
    ClinicianSubjectAssignment,
    FusionResult,
    ModalityReading,
    SessionLocal,
    Subject,
)

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
    expires_in_minutes: int = 30,
) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    claims = {
        "sub": sub,
        "role": role,
        "principal_type": principal_type,
        "iss": os.environ["AUTH_JWT_ISSUER"],
        "aud": os.environ["AUTH_JWT_AUDIENCE"],
        "exp": now + dt.timedelta(minutes=expires_in_minutes),
    }
    if clinician_id:
        claims["clinician_id"] = clinician_id
    if subject_id:
        claims["subject_id"] = subject_id
    return jwt.encode(
        claims,
        os.environ["AUTH_JWT_SECRET"],
        algorithm="HS256",
    )


def headers(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


subject_id = str(uuid.uuid4())
other_subject_id = str(uuid.uuid4())
now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

with SessionLocal() as db:
    db.add_all([
        Subject(subject_id=subject_id, status="active"),
        Subject(subject_id=other_subject_id, status="active"),
        Clinician(
            clinician_id="DR_PHASE2",
            auth_subject="phase2-clinician-sub",
            display_name="Dr Phase 2",
            role="clinician",
            status="active",
        ),
        Clinician(
            clinician_id="DR_OTHER",
            auth_subject="phase2-other-sub",
            display_name="Dr Other",
            role="clinician",
            status="active",
        ),
    ])
    db.flush()
    db.add(ClinicianSubjectAssignment(
        clinician_id="DR_PHASE2",
        subject_id=subject_id,
        active=True,
        assigned_at=now - dt.timedelta(days=1),
    ))

    complete_time = now - dt.timedelta(hours=2)
    for modality, score, status, captured in [
        ("c1_physiological", 62.0, "ok", complete_time - dt.timedelta(minutes=1)),
        ("c2_behavioral", None, "not_validated", complete_time - dt.timedelta(days=1)),
        ("c3_clinical_nlp", 0.67, "ok", complete_time - dt.timedelta(minutes=20)),
        ("c4_demographic", 0.43, "ok", complete_time - dt.timedelta(days=20)),
    ]:
        db.add(ModalityReading(
            subject_id=subject_id,
            modality=modality,
            raw_score=score,
            status=status,
            confidence=0.6,
            coverage=1.0,
            captured_at=captured,
            model_version=f"{modality}-v1",
            detail={"response": {"internal_marker": "not-for-patient"}},
            created_at=complete_time - dt.timedelta(seconds=30),
        ))
    complete = FusionResult(
        subject_id=subject_id,
        composite=0.58,
        tier="Medium",
        band="AMBER",
        confidence=0.71,
        modalities_used=3,
        renormalised=True,
        weights={
            "c1_physiological": 0.30,
            "c2_behavioral": 0.0,
            "c3_clinical_nlp": 0.45,
            "c4_demographic": 0.25,
        },
        contributions={
            "c1_physiological": 0.18,
            "c3_clinical_nlp": 0.28,
            "c4_demographic": 0.12,
        },
        harmonisation={
            "c1_physiological": {"harmonised": 0.62},
            "c3_clinical_nlp": {"harmonised": 0.67},
            "c4_demographic": {"harmonised": 0.43},
            "gate": {
                "passed": True,
                "usable_modalities": [
                    "c1_physiological",
                    "c3_clinical_nlp",
                    "c4_demographic",
                ],
            },
            "assessment": {"status": "complete", "missing_modalities": []},
        },
        model_version="ragf-v0.4",
        computed_at=complete_time,
    )
    db.add(complete)
    db.flush()
    complete_id = complete.id

    unavailable_time = now - dt.timedelta(hours=1)
    unavailable = FusionResult(
        subject_id=subject_id,
        composite=None,
        tier=None,
        band="GREY",
        confidence=0.0,
        modalities_used=1,
        renormalised=True,
        weights={},
        contributions={},
        harmonisation={
            "gate": {
                "passed": False,
                "usable_modalities": ["c4_demographic"],
                "rejected": {
                    "c1_physiological": "stale",
                    "c3_clinical_nlp": "no_support_set",
                },
            },
            "assessment": {
                "status": "insufficient",
                "missing_modalities": [
                    "c1_physiological",
                    "c3_clinical_nlp",
                ],
            },
        },
        reason="insufficient evidence",
        model_version="gate-blocked",
        computed_at=unavailable_time,
    )
    db.add(unavailable)
    db.flush()
    unavailable_id = unavailable.id

    partial_time = now
    db.add_all([
        ModalityReading(
            subject_id=subject_id,
            modality="c1_physiological",
            raw_score=88.0,
            status="ok",
            confidence=0.5,
            coverage=0.5,
            captured_at=partial_time - dt.timedelta(minutes=30),
            model_version="c1-v1",
            detail={"response": {"internal_marker": "not-for-patient"}},
            created_at=partial_time - dt.timedelta(seconds=30),
        ),
        ModalityReading(
            subject_id=subject_id,
            modality="c3_clinical_nlp",
            raw_score=0.61,
            status="ok",
            confidence=0.55,
            coverage=1.0,
            captured_at=partial_time - dt.timedelta(days=1),
            model_version="tc-wpn-v1",
            detail={"response": {"internal_marker": "not-for-patient"}},
            created_at=partial_time - dt.timedelta(seconds=30),
        ),
        ModalityReading(
            subject_id=subject_id,
            modality="c4_demographic",
            raw_score=0.44,
            status="ok",
            confidence=0.7,
            coverage=1.0,
            captured_at=partial_time - dt.timedelta(days=20),
            model_version="dcar-v1",
            detail={"response": {"internal_marker": "not-for-patient"}},
            created_at=partial_time - dt.timedelta(seconds=30),
        ),
    ])
    partial = FusionResult(
        subject_id=subject_id,
        composite=0.51,
        tier="Medium",
        band="AMBER",
        confidence=0.62,
        modalities_used=2,
        renormalised=True,
        weights={
            "c1_physiological": 0.0,
            "c2_behavioral": 0.0,
            "c3_clinical_nlp": 0.60,
            "c4_demographic": 0.40,
        },
        contributions={
            "c3_clinical_nlp": 0.36,
            "c4_demographic": 0.15,
        },
        harmonisation={
            "c3_clinical_nlp": {"harmonised": 0.61},
            "c4_demographic": {"harmonised": 0.44},
            "gate": {
                "passed": True,
                "usable_modalities": ["c3_clinical_nlp", "c4_demographic"],
                "rejected": {"c1_physiological": "stale"},
            },
            "assessment": {
                "status": "provisional",
                "missing_modalities": ["c1_physiological"],
            },
        },
        model_version="ragf-v0.4",
        computed_at=partial_time,
    )
    db.add(partial)
    db.flush()
    partial_id = partial.id
    db.commit()


clinician = make_token(
    sub="phase2-clinician-sub",
    role="clinician",
    principal_type="clinician",
    clinician_id="DR_PHASE2",
)
other_clinician = make_token(
    sub="phase2-other-sub",
    role="clinician",
    principal_type="clinician",
    clinician_id="DR_OTHER",
)
patient = make_token(
    sub="phase2-patient-sub",
    role="patient",
    principal_type="patient",
    subject_id=subject_id,
)
wrong_patient = make_token(
    sub="phase2-wrong-patient",
    role="patient",
    principal_type="patient",
    subject_id=other_subject_id,
)
expired_patient = make_token(
    sub="phase2-expired-patient",
    role="patient",
    principal_type="patient",
    subject_id=subject_id,
    expires_in_minutes=-1,
)

external_calls: list[str] = []


def forbidden_external(*args, **kwargs):
    external_calls.append("called")
    raise AssertionError("assessment read attempted external service call")


fusion_client.fuse = forbidden_external
main.fusion_client.fuse = forbidden_external
for name in ("call_c1", "call_c2", "call_c3", "call_c4"):
    setattr(mc, name, forbidden_external)
    setattr(main.mc, name, forbidden_external)

print("=" * 74)
print("Phase 2 · Canonical Assessment Contract")
print("=" * 74)

rp = client.get(
    f"/v1/patients/{subject_id}/assessment/latest",
    headers=headers(patient),
)
rc = client.get(
    f"/v1/patients/{subject_id}/assessment/latest",
    headers=headers(clinician),
)
check("patient latest returns 200", rp.status_code == 200, rp.text)
check("clinician latest returns 200", rc.status_code == 200, rc.text)

patient_json = rp.json() if rp.status_code == 200 else {}
clinician_json = rc.json() if rc.status_code == 200 else {}

check(
    "same fusion_result_id for patient and clinician",
    patient_json.get("fusion_result_id") == partial_id
    and clinician_json.get("fusion_result_id") == partial_id,
)
check(
    "same authoritative current composite for both audiences",
    patient_json.get("current_assessment", {}).get("score") == 0.51
    and clinician_json.get("current_assessment", {}).get("score") == 0.51,
)
check("assessment read makes no external call", external_calls == [], str(external_calls))
check("provisional maps to partial", clinician_json.get("assessment_status") == "partial")

mods = {m.get("component_id"): m for m in clinician_json.get("modalities", [])}
check(
    "stale C1 is non-ok, null-scored and excluded",
    mods.get("c1_physiological", {}).get("status") == "poor_signal"
    and mods.get("c1_physiological", {}).get("score") is None
    and mods.get("c1_physiological", {}).get("included_in_fusion") is False,
    str(mods.get("c1_physiological")),
)
check(
    "C2 remains not_validated and excluded",
    mods.get("c2_behavioral", {}).get("status") == "not_validated"
    and mods.get("c2_behavioral", {}).get("included_in_fusion") is False
    and mods.get("c2_behavioral", {}).get("score") is None,
)
check(
    "C3 remains one component signal",
    mods.get("c3_clinical_nlp", {}).get("score") == 0.61
    and clinician_json.get("current_assessment", {}).get("score") == 0.51,
)
check(
    "Phase 2 forecast remains null",
    patient_json.get("forecast") is None and clinician_json.get("forecast") is None,
)
check(
    "patient projection omits clinician-only modality internals",
    patient_json.get("modalities") == []
    and "weights" not in patient_json
    and "contributions" not in patient_json
    and "harmonisation" not in patient_json
    and "internal_marker" not in rp.text,
    rp.text,
)

rh = client.get(
    f"/v1/patients/{subject_id}/assessments?limit=10",
    headers=headers(clinician),
)
check("assigned clinician history returns 200", rh.status_code == 200, rh.text)
history = rh.json() if rh.status_code == 200 else []
ids = [item.get("fusion_result_id") for item in history]
check(
    "history preserves persisted fusion_result_id order",
    ids[:3] == [partial_id, unavailable_id, complete_id],
    str(ids),
)
by_id = {item.get("fusion_result_id"): item for item in history}
unavailable_json = by_id.get(unavailable_id, {})
check(
    "insufficient maps to unavailable without false low",
    unavailable_json.get("assessment_status") == "unavailable"
    and unavailable_json.get("current_assessment", {}).get("score") is None
    and unavailable_json.get("current_assessment", {}).get("tier") is None
    and unavailable_json.get("current_assessment", {}).get("band") == "GREY",
    str(unavailable_json),
)
check(
    "history preserves timestamp and model version",
    by_id.get(complete_id, {}).get("model_version") == "ragf-v0.4"
    and by_id.get(complete_id, {}).get("computed_at", "").startswith(
        complete_time.isoformat()[:19]
    ),
)
check("history read makes no external call", external_calls == [], str(external_calls))

r = client.get(f"/v1/patients/{subject_id}/assessment/latest")
check("missing JWT -> 401", r.status_code == 401, r.text)

r = client.get(
    f"/v1/patients/{subject_id}/assessment/latest",
    headers=headers(expired_patient),
)
check("expired JWT -> 401", r.status_code == 401, r.text)

r = client.get(
    f"/v1/patients/{subject_id}/assessment/latest",
    headers=headers(other_clinician),
)
check("unassigned clinician -> 403", r.status_code == 403, r.text)

r = client.get(
    f"/v1/patients/{subject_id}/assessment/latest",
    headers=headers(wrong_patient),
)
check("wrong patient subject -> 403", r.status_code == 403, r.text)

r = client.get(
    f"/v1/patients/{subject_id}/assessments",
    headers=headers(patient),
)
check("patient history access -> 403", r.status_code == 403, r.text)

print()
print("=" * 74)
print(f"  {passed} passed, {failed} failed")
print("=" * 74)
raise SystemExit(1 if failed else 0)

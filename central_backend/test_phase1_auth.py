"""Phase 1 authentication + assignment authorization tests."""

from __future__ import annotations

import datetime as dt
import os
import tempfile
import uuid

_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdb.name}"
os.environ["MRN_PEPPER"] = "phase1-test-pepper"
os.environ["AUTH_JWT_ISSUER"] = "https://auth.phase1.test"
os.environ["AUTH_JWT_AUDIENCE"] = "r26ds012-central-backend"
os.environ["AUTH_JWT_ALGORITHMS"] = "HS256"
os.environ["AUTH_JWT_SECRET"] = "phase1-test-secret-not-for-production"
os.environ["FUSION_URL"] = "https://fusion.test.invalid"
os.environ["FUSION_API_TOKEN"] = "test-fusion-token"

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402
from migrate_phase1 import apply as apply_phase1_migration  # noqa: E402

client = TestClient(app)
passed = 0
failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def token(
    *,
    sub: str,
    role: str,
    principal_type: str | None = None,
    clinician_id: str | None = None,
    subject_id: str | None = None,
    issuer: str | None = None,
    audience: str | None = None,
    expires_in_minutes: int = 60,
) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    claims = {
        "sub": sub,
        "role": role,
        "principal_type": principal_type or role,
        "iss": issuer or os.environ["AUTH_JWT_ISSUER"],
        "aud": audience or os.environ["AUTH_JWT_AUDIENCE"],
        "exp": now + dt.timedelta(minutes=expires_in_minutes),
    }
    if clinician_id is not None:
        claims["clinician_id"] = clinician_id
    if subject_id is not None:
        claims["subject_id"] = subject_id
    return jwt.encode(claims, os.environ["AUTH_JWT_SECRET"], algorithm="HS256")


def headers(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


admin = token(sub="admin-auth-sub", role="admin")
dr_a = token(
    sub="auth-dr-a", role="clinician",
    principal_type="clinician", clinician_id="DR_A",
)
dr_b = token(
    sub="auth-dr-b", role="clinician",
    principal_type="clinician", clinician_id="DR_B",
)

print("=" * 74)
print("Phase 1 · Authentication + assignment authorization")
print("=" * 74)

r = client.get("/v1/me")
check("missing bearer is 401", r.status_code == 401, r.text)

expired = token(
    sub="auth-dr-a", role="clinician",
    principal_type="clinician", clinician_id="DR_A",
    expires_in_minutes=-1,
)
r = client.get("/v1/me", headers=headers(expired))
check("expired JWT is 401", r.status_code == 401, r.text)

wrong_aud = token(
    sub="auth-dr-a", role="clinician",
    principal_type="clinician", clinician_id="DR_A",
    audience="wrong-audience",
)
r = client.get("/v1/me", headers=headers(wrong_aud))
check("wrong JWT audience is 401", r.status_code == 401, r.text)

wrong_iss = token(
    sub="auth-dr-a", role="clinician",
    principal_type="clinician", clinician_id="DR_A",
    issuer="https://wrong-issuer.test",
)
r = client.get("/v1/me", headers=headers(wrong_iss))
check("wrong JWT issuer is 401", r.status_code == 401, r.text)

# Valid clinician JWT is not enough until the clinician is provisioned.
r = client.get("/v1/me", headers=headers(dr_a))
check("unregistered clinician is forbidden", r.status_code == 403, r.text)

for clinician_id, auth_subject, display_name in [
    ("DR_A", "auth-dr-a", "Dr A"),
    ("DR_B", "auth-dr-b", "Dr B"),
]:
    r = client.post(
        "/v1/admin/clinicians",
        headers=headers(admin),
        json={
            "clinician_id": clinician_id,
            "auth_subject": auth_subject,
            "display_name": display_name,
            "role": "clinician",
            "status": "active",
        },
    )
    check(f"admin provisions {clinician_id}", r.status_code == 200, r.text)

r = client.get("/v1/me", headers=headers(dr_a))
check("/v1/me returns verified clinician identity",
      r.status_code == 200
      and r.json().get("clinician_id") == "DR_A"
      and r.json().get("display_name") == "Dr A",
      r.text)

# A clinician cannot provision assignments.
r = client.post(
    "/v1/admin/assignments",
    headers=headers(dr_a),
    json={
        "clinician_id": "DR_A",
        "subject_id": str(uuid.uuid4()),
        "active": True,
    },
)
check("clinician cannot use privileged assignment route", r.status_code == 403, r.text)

# New clinician enrolment creates one canonical subject and assignment to actor.
r = client.post(
    "/v1/subjects",
    headers=headers(dr_a),
    json={"mrn": "PHASE1-SUBJECT-A", "enrolled_by": "SPOOFED_CLIENT_ACTOR"},
)
check("clinician enrolment succeeds", r.status_code == 200, r.text)
subject_a = r.json().get("subject_id")
check("enrolment returns canonical UUID", bool(subject_a and len(subject_a) == 36))

r = client.get("/v1/clinicians/me/patients", headers=headers(dr_a))
patients_a = r.json().get("patients", []) if r.status_code == 200 else []
check("enrolling clinician roster contains subject",
      r.status_code == 200 and any(x.get("subject_id") == subject_a for x in patients_a),
      r.text)

r = client.get("/v1/clinicians/me/patients", headers=headers(dr_b))
patients_b = r.json().get("patients", []) if r.status_code == 200 else []
check("other clinician roster excludes unassigned subject",
      r.status_code == 200 and all(x.get("subject_id") != subject_a for x in patients_b),
      r.text)

# Clinician A may access; Clinician B must be denied by assignment.
r = client.get(f"/v1/doctor/patients/{subject_a}/timeline", headers=headers(dr_a))
check("assigned clinician can read patient", r.status_code == 200, r.text)

r = client.get(f"/v1/doctor/patients/{subject_a}/timeline", headers=headers(dr_b))
check("unassigned clinician receives 403", r.status_code == 403, r.text)

# Patient subject binding is enforced on participant-safe projection.
patient_a = token(
    sub="patient-auth-a", role="patient",
    principal_type="patient", subject_id=subject_a,
)
r = client.get(f"/v1/patients/{subject_a}/risk", headers=headers(patient_a))
check("patient may read own participant-safe projection", r.status_code == 200, r.text)

other_subject = str(uuid.uuid4())
patient_other = token(
    sub="patient-auth-other", role="patient",
    principal_type="patient", subject_id=other_subject,
)
r = client.get(f"/v1/patients/{subject_a}/risk", headers=headers(patient_other))
check("patient cannot read another subject", r.status_code == 403, r.text)

r = client.get(f"/v1/doctor/patients/{subject_a}/timeline", headers=headers(patient_a))
check("patient principal cannot use clinician timeline", r.status_code == 403, r.text)

# Admin can grant and revoke assignment; the server state is authoritative.
r = client.post(
    "/v1/admin/assignments",
    headers=headers(admin),
    json={"clinician_id": "DR_B", "subject_id": subject_a, "active": True},
)
check("admin can activate assignment", r.status_code == 200 and r.json().get("active") is True, r.text)

r = client.get(f"/v1/doctor/patients/{subject_a}/timeline", headers=headers(dr_b))
check("newly assigned clinician can read patient", r.status_code == 200, r.text)

r = client.post(
    "/v1/admin/assignments",
    headers=headers(admin),
    json={"clinician_id": "DR_B", "subject_id": subject_a, "active": False},
)
check("admin can end assignment", r.status_code == 200 and r.json().get("active") is False, r.text)

r = client.get(f"/v1/doctor/patients/{subject_a}/timeline", headers=headers(dr_b))
check("ended assignment immediately blocks access", r.status_code == 403, r.text)

# JWT sub binding prevents someone from naming another clinician_id in claims.
spoofed = token(
    sub="attacker-auth-sub", role="clinician",
    principal_type="clinician", clinician_id="DR_A",
)
r = client.get("/v1/me", headers=headers(spoofed))
check("clinician auth-subject mismatch is forbidden", r.status_code == 403, r.text)

# Legacy/shared tokens are not accepted as clinician identities.
r = client.get("/v1/me", headers={"Authorization": "Bearer r26ds012-backend-changeme"})
check("legacy shared backend token is not an end-user credential",
      r.status_code == 401, r.text)

# Phase-1 migration is idempotent and records state without touching science tables.
first = apply_phase1_migration()
second = apply_phase1_migration()
check("Phase 1 migration runs/idempotently records schema state",
      first is True and second is False, f"first={first}, second={second}")

h = client.get("/health").json()
check("health reports JWT verification configured",
      h.get("auth", {}).get("end_user_jwt_configured") is True, str(h.get("auth")))

print()
print("=" * 74)
print(f"  {passed} passed, {failed} failed")
print("=" * 74)
raise SystemExit(1 if failed else 0)

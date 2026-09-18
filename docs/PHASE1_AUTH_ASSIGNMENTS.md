# Phase 1 — Backend Authentication + Assignments

Status: implementation branch
Handbook phase: **1. Backend auth + assignments**

This phase implements the Phase-0 principal/assignment contracts without
inventing an identity provider or JWT deployment values.

## CURRENT implementation added in this phase

### JWT verification

Protected end-user routes accept a signed JWT and validate:

- signature;
- expiry (`exp`);
- issuer (`iss`);
- audience (`aud`);
- subject (`sub`);
- role.

Configuration is fail-closed. The repository does not contain verified
production values for algorithm, issuer, audience or signing keys, therefore
they must be supplied by the approved auth deployment:

- `AUTH_JWT_ISSUER`
- `AUTH_JWT_AUDIENCE`
- `AUTH_JWT_ALGORITHMS`
- exactly one of:
  - `AUTH_JWT_JWKS_URL`
  - `AUTH_JWT_PUBLIC_KEY`
  - `AUTH_JWT_SECRET`

No algorithm/issuer/audience value is guessed in source control.

### Principal bindings

Clinician JWT:

- `principal_type=clinician` (or role resolving to clinician);
- `sub`;
- `clinician_id` (falls back to `sub` if the approved issuer uses the same
  identifier);
- `role`;
- `exp`, `iss`, `aud`.

The verified `sub` must match the stored clinician `auth_subject`.

Patient JWT:

- `principal_type=patient`;
- `sub`;
- `subject_id`;
- `role`;
- `exp`, `iss`, `aud`.

Patient authorization is bound to exactly one canonical subject.

### Persistence

New tables:

- `clinicians`
- `clinician_subject_assignments`

The assignment edge has:

- clinician_id;
- subject_id;
- active;
- assigned_at;
- ended_at.

A versioned idempotent migration exists at:

`central_backend/migrate_phase1.py`

### Authorization

Clinician patient access requires all of:

1. valid JWT;
2. registered clinician;
3. active clinician;
4. JWT `sub` == stored `auth_subject`;
5. role match;
6. active subject;
7. active clinician-subject assignment.

Failure semantics:

- invalid/missing/expired JWT -> **401**;
- valid identity without required role/assignment -> **403**.

### Implemented routes

- `GET /v1/me`
- `GET /v1/clinicians/me/patients`
- `POST /v1/admin/clinicians`
- `POST /v1/admin/assignments`

The admin routes require a verified `admin` or `researcher` principal.

Existing clinician patient routes are now assignment-scoped:

- enrolment / MRN resolution;
- external-ID registration;
- C3 note submission;
- manual fusion;
- clinician verdict;
- clinician timeline;
- evidence;
- CARE-X explanation.

Participant-safe risk access is now authenticated and accepts only:

- the patient bound to that subject; or
- an assigned clinician.

Patient-originated C1/C2/C4 ingestion is subject-scoped using the same principal
rules.

### Enrolment rule

When a clinician creates a **new** subject through `POST /v1/subjects`, the
backend creates an active assignment to that authenticated clinician.

For an **existing** subject, a clinician must already have an active assignment.
Knowing an MRN-like value does not grant access.

The client-supplied `enrolled_by` field is retained for wire compatibility but
is not authoritative. The server stores/audits the authenticated clinician ID.

## Important deployment dependency

The ClinAnx repository currently consumes an `access_token` from its separate
auth service, but the inspected project sources do not define the production JWT
algorithm, issuer, audience, JWKS URL/public key or secret.

Therefore Phase 1 code is complete and testable, but a real deployment remains
**not ready** until the auth-service owner supplies those exact verified values.
Do not work around this by embedding a shared privileged backend token in the
mobile app.

## Explicit Phase 1 boundary

Implemented here:

- JWT/session verification boundary;
- clinician identity persistence;
- clinician-to-subject assignments;
- patient subject binding;
- `/v1/me`;
- assignment-scoped patient roster;
- assignment enforcement on existing clinical routes;
- 401 vs 403 semantics;
- migration + tests.

Not implemented here:

- canonical latest AssessmentSummary endpoint (Phase 2);
- ForecastResult persistence (Phase 3);
- AttentionEvent persistence/policy/ACK/RESOLVE (Phase 4);
- device-token registry / push delivery (Phase 7);
- any Fusion/C4/RAG scientific logic changes.

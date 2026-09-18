# Phase 0 — Contract Lock

Status: **TARGET contract frozen for implementation**
Contract version: `r26ds012-phase0-v1`
Baseline: Central-Backend-only `main` after PR #3.

This document freezes the integration semantics required before Phase 1–4
implementation. It does **not** claim those later-phase features already exist.

## 1. Source-of-truth rule

- **CURRENT** = verified running/repository behavior.
- **TARGET** = contract required by the System Integration Handbook / ClinAnx
  specification but not necessarily implemented.
- **PROPOSED** = recommendation, not an implemented or validated fact.

Later code must not silently convert TARGET/PROPOSED behavior into a claim about
CURRENT behavior.

## 2. Canonical identity

One participant has exactly one opaque Central Backend `subject_id`.

Frozen rules:

- `subject_id` is the canonical participant key.
- raw MRN is never a persistent identifier; the backend stores only its keyed
  hash alias.
- `app_user_id`, `c1_device_id`, `c2_subject_id`, and `c3_patient_id`
  are aliases that resolve to the same `subject_id`.
- alias collision across different subjects is rejected.
- pairing code is short-lived and single-use.
- the canonical identity flow is the existing
  `/v1/subjects`, `/v1/subjects/pair`, `/v1/subjects/self`,
  `/v1/subjects/resolve`, and external-ID registration flow.
- **No new `POST /v1/subjects/attach` contract is introduced.**

This preserves the handbook principle:

> One patient -> one canonical backend identity -> one authoritative fusion
> result -> audience-specific views.

## 3. Authentication principal semantics

Phase 0 freezes the normalized principal semantics, not a specific identity
provider.

### Clinician principal

Required verified token semantics:

- signed short-lived end-user credential;
- verified clinician identity (`sub` and/or approved clinician binding);
- role;
- expiry;
- issuer;
- audience.

Central Backend authorization derives actor identity from the **verified
principal**, never from arbitrary request JSON.

### Patient principal

Required binding:

- authenticated patient-scoped token/session;
- exactly one canonical `subject_id`;
- may access only participant-safe resources for that subject.

### Service principal

Central Backend -> C1/C2/C3/C4/Fusion/RAG credentials are service credentials,
not forwarded end-user tokens unless an approved service contract explicitly
requires it.

### Not frozen because the sources do not define them

Phase 0 deliberately does **not** invent:

- JWT signing algorithm;
- issuer value;
- audience value;
- JWKS URL/public key;
- authentication provider;
- refresh-token implementation.

Those become deployment configuration in Phase 1 after the approved auth
service contract is confirmed.

## 4. Assignment semantics

Target table/domain contract:

`clinician_subject_assignments`

Minimum frozen semantics:

- `clinician_id`
- `subject_id`
- `active`
- `assigned_at`
- `ended_at`

Every clinician roster/patient/attention-event read or mutation is
assignment-scoped server-side. Flutter visibility is not authorization.

A valid clinician without an active assignment must be denied. Whether the
deployment returns 403 or privacy-preserving 404 is a study disclosure decision
and is not invented in Phase 0.

## 5. Canonical latest assessment

Target route:

`GET /v1/patients/{id}/assessment/latest`

Frozen model: `AssessmentSummary` in
`central_backend/schemas/phase0_v1.py`.

Required invariant:

- patient and clinician projections of the same latest assessment reference the
  **same `fusion_result_id` and same authoritative current composite**;
- audience views may differ in detail;
- neither mobile app recomputes authoritative fusion;
- `assessment_status` = `complete | partial | unavailable`;
- unavailable assessment has no current score/tier and uses GREY semantics;
- stale/missing evidence is never represented as a low score.

## 6. ForecastResult

Forecast is a separate persisted concept from current `FusionResult`.

Frozen fields:

- `forecast_result_id`
- `subject_id`
- `fusion_result_id`
- `scope`
- `horizon_minutes`
- `score`
- `tier`
- optional `escalation_probability`
- `escalation_predicted`
- `generated_at`
- `valid_until`
- `model_version`
- source component + reading ID

Scientific boundary:

- the currently defensible near-term future signal is C1-led and therefore uses
  `scope="physiological"`;
- `scope="multimodal"` is reserved for a separately specified and validated
  forecasting method;
- Phase 0 does not invent forecast thresholds or escalation policy.

## 7. AttentionEvent

Frozen target list/detail/mutation API:

- `GET /v1/attention-events?status=OPEN`
- `GET /v1/attention-events`
- `GET /v1/attention-events?subject_id=...`
- `GET /v1/attention-events/{id}`
- `POST /v1/attention-events/{id}/acknowledge`
- `POST /v1/attention-events/{id}/resolve`

Response envelopes:

- list: `{"events":[...]}`
- detail/mutation: `{"event":{...}}`

ACK/RESOLVE request body is frozen as `{}`.

`acknowledged_by`, `resolved_by`, and server timestamps come from the
authenticated server principal/state transition. They are never accepted from
request JSON.

### Lifecycle

Only legal forward lifecycle:

`OPEN -> ACKNOWLEDGED -> RESOLVED`

The event preserves:

- exact `fusion_result_id`;
- optional exact `forecast_result_id`;
- event type;
- severity;
- reason;
- forecast horizon;
- actor/timestamps;
- `policy_version`.

Phase 0 freezes the lifecycle and schema only. Confirmation, hysteresis,
cooldown, recovery thresholds and event-generation policy belong to Phase 4 and
must come from the approved research design rather than being invented here.

## 8. Modality semantics

Frozen backend identifiers:

- `c1_physiological`
- `c2_behavioral`
- `c3_clinical_nlp`
- `c4_demographic`

C2 remains `not_validated` / experimental and excluded from authoritative
fusion unless research governance explicitly changes that rule.

C3 is **Clinical NLP / TC-WPN signal**, not overall patient risk.

Missing/stale/error component evidence is absent evidence, never score zero.

## 9. Research-safe terminology

Use:

- **Current multimodal assessment**
- **Clinical NLP / TC-WPN signal**
- **Potential escalation predicted within the 10-minute forecast horizon**
- **Assessment unavailable - insufficient current data**
- **Experimental - not included in fusion**
- **Supporting evidence unavailable / RAG abstained**
- **Last updated ... / Offline**

Avoid claims such as:

- “Patient will have an anxiety attack in exactly 10 minutes”
- “Patient risk = TC-WPN”
- “Behavioural risk = 0” for unavailable/excluded C2.

## 10. Error semantics

Frozen target envelope:

```json
{
  "error": {
    "code": "ASSESSMENT_UNAVAILABLE | UNAUTHORIZED | FORBIDDEN | VALIDATION_ERROR | SERVICE_UNAVAILABLE | CONFLICT",
    "message": "Human-readable safe message",
    "request_id": "req_...",
    "retryable": false
  }
}
```

Client-facing errors must not expose stack traces, raw clinical-note content,
secrets, or external model credentials.

## 11. Phase boundary

### Completed by Phase 0

- canonical ID semantics frozen;
- principal semantics frozen without fabricated JWT deployment values;
- assignment semantics frozen;
- AssessmentSummary schema frozen;
- ForecastResult schema frozen;
- AttentionEvent schema/lifecycle/API envelopes frozen;
- modality identifiers/status semantics frozen;
- terminology/error semantics frozen;
- machine-readable fixtures and validation tests committed.

### Explicitly NOT implemented in Phase 0

- JWT verification;
- clinician table and assignment persistence;
- assignment authorization;
- latest-assessment route implementation;
- ForecastResult persistence;
- AttentionEvent persistence/engine;
- ACK/RESOLVE database concurrency;
- push/device-token registry;
- any new scientific threshold/model/fusion/RAG logic.

Those remain Phase 1 onward.

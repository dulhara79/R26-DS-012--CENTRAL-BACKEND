# Phase 0 Contract Lock — Central Backend Integration v1

**Repository:** `dulhara79/R26-DS-012--CENTRAL-BACKEND`  
**Status:** FROZEN TARGET contract for Phase 0  
**Date:** 2026-09-18  
**Research boundary:** Research prototype — not a diagnostic device.

## 1. Purpose

Phase 0 does not implement the target backend. It freezes the contract that later backend phases must implement without forcing ClinAnx or the Patient App to guess API semantics.

The governing engineering invariant is:

> One patient -> one canonical backend identity -> one authoritative fusion result -> audience-specific views.

This contract lock is based on the R26-DS-012 System Integration Implementation Handbook v1.0, the 14-Day Implementation Sprint Plan, and the currently committed ClinAnx typed contracts and JSON fixtures.

This phase deliberately does **not** depend on `UVINDUSEN/component4final`. The working implementation repository for the backend work is this repository. Fusion remains a service boundary and later integration must use a documented service contract rather than inventing or silently replacing fusion mathematics.

## 2. CURRENT versus TARGET

The source documents distinguish CURRENT, TARGET and PROPOSED behavior. That distinction is preserved here.

- `app/contracts/v1.py` describes **TARGET wire semantics** only.
- Existing routes, models, mock services and orchestration in this repository remain **legacy CURRENT behavior** until replaced in later phases.
- A Pydantic model existing in this branch does not mean the corresponding HTTP endpoint exists.
- Runtime changes for authentication, assignments, assessment aggregation, forecast persistence and AttentionEvent persistence are intentionally deferred to the handbook phases that own them.

## 3. Frozen integration invariants

| Invariant | Frozen rule |
|---|---|
| Canonical patient | One opaque server-owned `subject_id` identifies a participant across integrations. |
| Fusion authority | The Central Backend persists and projects one authoritative current `FusionResult`; clients never calculate an authoritative composite. |
| Shared result | Patient and clinician projections of the same assessment must carry the same `fusion_result_id` and current fused score. |
| Current vs forecast | Current multimodal assessment and future-horizon forecast are separate objects, identifiers and timestamps. |
| Unavailable != low | Missing, stale or insufficient evidence must never be converted to `0`, `Low` or `Green`. |
| C2 | `c2_behavioral` remains experimental / not included in active fusion under the v1 contract. |
| C3 | `c3_clinical_nlp` / TC-WPN is a contributing signal, not the patient's overall risk authority. |
| Forecast scope | A C1-led future result is `physiological`; it must not be labelled `multimodal` without a separately specified and validated method. |
| Assignment | Clinician access is authorized by server-side clinician-to-subject assignment, not Flutter visibility. |
| Event authority | Central Backend owns AttentionEvent creation, persistence, dedupe, actor identity, timestamps and lifecycle. |
| Human actor | ACK/RESOLVE actor identity comes from the authenticated principal, never request JSON. |
| Auditability | Fusion/forecast/event identifiers remain linkable through the lifecycle. |

## 4. Identifier lock

### 4.1 `subject_id`

- Opaque, server-owned string.
- Clients must not infer authorization, MRN, component identity or clinical meaning from its format.
- External/device/model identifiers map to the canonical subject through aliases/pairing.
- Raw MRN is not a canonical identifier and must not be persisted in clear text.

### 4.2 `fusion_result_id`

- **v1 external contract type: positive integer.**
- The current ClinAnx frozen parser and fixtures require an integer, and the handbook target examples use an integer.
- The legacy backend currently stores `FusionResult.id` as a string UUID. This is a known contract conflict, not something Phase 0 silently coerces. Phase 2 must migrate or introduce a stable compatible public identifier before the latest-assessment API is made live.

### 4.3 `forecast_result_id`

- Opaque, non-empty server-generated string.
- No delimiter/prefix format is semantically guaranteed (`fcst-001` and `fcst_...` are examples only).
- It identifies one persisted forecast snapshot/result.

### 4.4 `AttentionEvent.id`

- Opaque, non-empty server-generated string.
- The event identifier is the deduplication/deep-link identity shared by clients.
- Prefix spelling is not part of authorization or lifecycle semantics.

### 4.5 `clinician_id` and display identifiers

- `clinician_id` is derived from the verified clinician principal (`sub`).
- `display_id`/display labels are presentation values and must not be used as authorization keys.

## 5. Authentication principal model

Phase 0 freezes required claim semantics; Phase 1 implements verification.

### Clinician principal

Required claims:

```text
sub        -> canonical clinician_id
role       -> "clinician"
exp        -> expiry
iss        -> issuer
aud        -> central-backend audience
```

The Central Backend must validate signature, expiry, issuer and audience before accepting the principal. Assignment authorization is separate from authentication.

### Patient principal

Required binding:

```text
sub
role       -> "patient"
subject_id -> exactly one canonical participant
exp
iss
aud
```

Patient-scoped sessions may access only participant-safe resources for that `subject_id`.

### Values intentionally not invented in Phase 0

The source documents do not specify the production issuer string, audience string, signing algorithm/key distribution endpoint, refresh-token policy or production identity provider. Those values/configuration are Phase 1 work and must be supplied by the chosen authentication service.

The current ClinAnx LOCAL mode creates `local-session-*` demo tokens. Those are development/demo credentials, not target JWTs, and cannot be treated as valid research/study authentication.

## 6. Target endpoint lock

The following are TARGET integration endpoints/semantics to implement in later phases:

```text
POST /auth/login
GET  /v1/me

GET  /v1/clinicians/me/dashboard
GET  /v1/clinicians/me/patients

GET  /v1/patients/{id}/assessment/latest
GET  /v1/patients/{id}/assessments
GET  /v1/patients/{id}/data-quality
GET/POST /v1/patients/{id}/clinical-notes

GET  /v1/attention-events
GET  /v1/attention-events/{id}
POST /v1/attention-events/{id}/acknowledge
POST /v1/attention-events/{id}/resolve

GET/POST /v1/device-tokens
```

`POST /auth/login` may be owned by a separate authentication service. The non-negotiable boundary is that the Central Backend validates the resulting principal on protected routes.

### Frozen AttentionEvent response wrappers

ClinAnx already implements this exact client-first contract:

```text
GET  /v1/attention-events?status=OPEN      -> {"events": [...]}
GET  /v1/attention-events                  -> {"events": [...]}
GET  /v1/attention-events?subject_id=...   -> {"events": [...]}
GET  /v1/attention-events/{id}             -> {"event": {...}}
POST /v1/attention-events/{id}/acknowledge -> {"event": {...}}
POST /v1/attention-events/{id}/resolve     -> {"event": {...}}
```

ACK and RESOLVE requests currently use `{}`; actor and UTC timestamps are server-generated.

Expected authorization/lifecycle semantics:

- `401`: missing, invalid or expired clinician session.
- `403`: authenticated but not authorized for the subject/event.
- `404`: canonical resource does not exist.
- `409`: lifecycle mutation conflicts with current canonical server state.
- `400`/`422`: invalid request.
- `5xx`: backend/service failure.

## 7. Frozen `AssessmentSummary` projection

```text
subject_id
fusion_result_id
current_assessment {
  score?
  tier?
  band?
}
forecast? {
  forecast_result_id
  scope
  horizon_minutes
  score?
  tier?
  escalation_probability?
  escalation_predicted
  generated_at
  valid_until
}
confidence?
uncertainty?
assessment_status
modalities[] {
  component_id
  score?
  available
  included_in_fusion
  status
  confidence?
  coverage?
  captured_at?
  contribution?
}
computed_at
model_version
```

`assessment_status` v1 vocabulary:

```text
complete | partial | unavailable
```

`RiskTier` v1 vocabulary:

```text
Low | Medium | High
```

Client compatibility with unknown future vocabulary remains a client safety feature. The backend v1 contract itself rejects undeclared values rather than emitting accidental schema drift.

### Modality identifiers

```text
c1_physiological
c2_behavioral
c3_clinical_nlp
c4_demographic
```

The public assessment projection currently recognizes these modality states:

```text
ok
stale
not_validated
unavailable
no_support_set
error
```

Internal component adapters may retain richer service-specific status detail for audit/provenance. Adding a new public wire status requires a coordinated contract version/change because the mobile parser must remain conservative.

## 8. Frozen `ForecastResult`

Persisted forecast semantics require:

```text
forecast_result_id
subject_id
fusion_result_id or source-result linkage
scope
horizon_minutes
score?
tier?
escalation_probability?
escalation_predicted
generated_at
valid_until
model_version
source component / reading identity
```

The nested `AssessmentSummary.forecast` projection may omit backend-only provenance fields that are already implied by the parent assessment, but the durable Phase 3 record must retain equivalent source/version linkage.

v1 scope vocabulary:

```text
physiological | multimodal
```

The existence of `multimodal` in the enum does not claim the current system has a validated multimodal forecasting method.

## 9. Frozen `AttentionEvent`

Minimum v1 fields:

```text
id
subject_id
fusion_result_id
forecast_result_id
event_type
severity
reason
forecast_horizon
status
created_at
acknowledged_at?
acknowledged_by?
resolved_at?
resolved_by?
policy_version
```

Frozen event type:

```text
acute_escalation_forecast
```

Frozen lifecycle:

```text
OPEN -> ACKNOWLEDGED -> RESOLVED
```

Phase 0 does **not** invent an escalation threshold. Policy thresholds, confirmation/hysteresis, recovery and deduplication execution belong to Phase 4 and must use the frozen/versioned policy approved for the study.

## 10. Research-safe terminology lock

Use:

- **Current multimodal assessment**
- **Near-term physiological forecast** when the forecast source is C1-only
- **Potential escalation predicted within the near-term forecast horizon**
- **Assessment unavailable — insufficient current data**
- **Experimental — not included in fusion** for C2 while excluded
- **Clinical NLP unavailable** when C3 is missing/unusable
- **Research prototype — not a diagnostic device**

Do not use:

- language guaranteeing an anxiety attack at an exact future time;
- `0`, `Low` or `Green` as a replacement for missing/stale/unavailable data;
- “multimodal forecast” for a C1-only forecast;
- TC-WPN/C3 as the patient's overall multimodal risk;
- local mobile notification state as proof that an event was acknowledged/resolved.

## 11. Frozen fixture set

The backend mirrors the already committed ClinAnx contract fixtures under:

```text
tests/fixtures/contracts/
```

The v1 fixture set is:

```text
assessment_complete.json
assessment_partial_stale_c1.json
assessment_unavailable_c3.json
assessment_unknown_enum.json
patient_summary.json
attention_event_open.json
attention_event_acknowledged.json
attention_event_resolved.json
```

These fixtures are contract artifacts. They are not claims that the current backend already produces these responses.

## 12. Verified legacy-repository conflicts to resolve later

The current backend predates the September handbook. Phase 0 records these conflicts instead of hiding them:

| Legacy CURRENT in this repository | Frozen TARGET direction | Owning phase |
|---|---|---|
| `Subject.clinician_id` directly assigns one clinician | `clinician_subject_assignments` authorization relation | Phase 1 |
| Locally issued HS256 tokens contain no `iss`/`aud` | Verify clinician/patient principal claims including issuer/audience | Phase 1 |
| ClinAnx LOCAL session is not a target JWT | Use remote/project-approved auth for protected research data | Phase 1 |
| `FusionResult.id` is string UUID | External v1 `fusion_result_id` is positive integer | Phase 2 |
| No canonical latest `AssessmentSummary` route | Add latest assessment + audience projections | Phase 2 |
| No durable `ForecastResult` | Persist source-scoped forecast with validity/provenance | Phase 3 |
| No server-owned `AttentionEvent` | Persistent server lifecycle + concurrency | Phase 4 |
| Old component naming treats TC-WPN as C4 and intervention as C3 | Handbook naming is C3 Clinical NLP / C4 contextual-demographic | Later integration refactor before live target routes |
| Legacy local fusion fallback/weights are embedded in this repo | Fusion method remains an authoritative versioned service/backend boundary; do not invent a competing research result | Later fusion integration |
| Legacy route family differs from target ClinAnx contracts | Preserve legacy runtime until replacement routes are implemented and tested | Phases 1-5 |

No conflict in this table is declared fixed merely because Phase 0 documents it.

## 13. Phase 0 acceptance gate

Phase 0 is ready for review when:

1. Target contract models are committed without wiring fake target routes.
2. ClinAnx's frozen JSON fixtures are mirrored exactly.
3. Contract tests cover complete/partial/unavailable assessment states, C2 exclusion, stale C1, event lifecycle, shared result identity and principal binding.
4. Unknown/undeclared backend v1 vocabulary is rejected instead of silently changing semantics.
5. Existing legacy smoke tests remain in CI so this contract-only phase does not quietly break the running backend.
6. README clearly distinguishes legacy runtime behavior from the frozen target contract.
7. No fusion formula, model behavior, escalation threshold or production auth values are invented in Phase 0.

After this PR is reviewed, **Phase 1** begins with backend JWT verification, clinician identity mapping, `clinician_subject_assignments`, assignment authorization and patient-session binding.

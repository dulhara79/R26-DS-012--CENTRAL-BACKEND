# Phase 2 — Canonical Assessment Contract

Status: implementation branch  
Handbook phase: **2. Assessment contract**

Phase 2 turns the frozen Phase-0 `AssessmentSummary` contract into a read-only
runtime projection over the Central Backend's persisted authoritative
`FusionResult` state. It does not introduce a second risk engine and it does
not change Uvindu-owned Fusion, C4/DCAR, or CARE-AnxRAG logic.

## CURRENT before Phase 2

The merged Phase-1 baseline already had:

- append-only `FusionResult` persistence;
- append-only `ModalityReading` persistence;
- external HTTP Fusion only through `POST /v1/fuse/manual`;
- legacy patient `GET /v1/patients/{subject_id}/risk`;
- legacy clinician `GET /v1/doctor/patients/{subject_id}/timeline`;
- JWT verification, patient subject binding, clinician persistence and
  assignment-scoped authorization.

The two legacy egress routes both read Central Backend state, but they expose
different response shapes and do not implement the frozen Phase-0
`AssessmentSummary`.

The CURRENT baseline completeness vocabulary stored alongside fusion was:

- `complete`
- `provisional`
- `insufficient`

## TARGET implemented by Phase 2

Phase 2 adds:

- `GET /v1/patients/{subject_id}/assessment/latest`
- `GET /v1/patients/{subject_id}/assessments` for assignment-scoped clinician
  history
- one internal canonical assessment builder in
  `central_backend/assessment_service.py`
- patient and clinician projections derived from the exact same persisted
  `FusionResult`
- the exact persisted `fusion_result_id`, `computed_at`, current composite,
  tier/band, confidence and `model_version`
- explicit modality availability/inclusion metadata for the clinician view
- a reduced patient view that withholds per-modality clinical internals
- explicit CURRENT-to-TARGET assessment-status adaptation
- Phase-2 invariant tests and CI coverage.

No new database table or migration is required. Phase 2 can be implemented from
the existing append-only `FusionResult` and `ModalityReading` persistence.
Adding a parallel assessment table would duplicate authoritative current state
and create an unnecessary consistency problem.

## Status mapping

The mapping is explicit and centralized:

| CURRENT persisted/gating state | TARGET AssessmentSummary |
| --- | --- |
| `complete` | `complete` |
| `provisional` | `partial` |
| `insufficient` | `unavailable` |

Safety overrides:

- a persisted fusion row with no composite/tier or GREY band is always projected
  as `unavailable`;
- `unavailable` exposes no current score or tier and uses GREY;
- if old metadata is contradictory and says `insufficient` while the same row
  contains a valid authoritative composite, the read projection fails
  conservative to `partial` rather than discarding the persisted authoritative
  composite or calling it complete.

The legacy persistence terminology is not silently renamed in historical DB
rows. Phase 2 adapts it at the API boundary to the frozen TARGET vocabulary.

## Authoritative assessment identity

The read path is:

```text
persisted FusionResult
        +
persisted ModalityReading provenance
        |
        v
canonical AssessmentSummary builder
        |
        +-- patient projection
        |
        +-- clinician projection
        |
        +-- clinician assessment history
```

The builder is read-only. It does **not** call:

- C1
- C2
- C3
- C4
- Fusion
- CARE-AnxRAG

and it does not calculate a new fusion score.

For latest reads, both authorized audiences resolve the same persisted
`FusionResult.id` and current composite.

## Modality/data-quality projection

C2 remains `not_validated`, experimental and excluded.

For C1/C3/C4:

- unknown component status fails closed as `error`;
- missing persisted evidence is `insufficient_data`;
- an `ok` reading that is beyond the existing gate freshness limit is exposed
  as the frozen non-ok `poor_signal` status at this API boundary;
- stale/missing/error evidence never becomes score zero and is never included in
  fusion by the projection.

The Phase-0 status enum does not contain a literal `stale` value, so Phase 2
does not invent a new wire enum. Freshness failure is conservatively represented
as `poor_signal`.

A clinician modality score is exposed only when it can be taken safely from
persisted state. The projection prefers the persisted Fusion harmonisation
audit. It never locally normalizes C1's native 0-100 score merely to fill an API
field.

## Patient vs clinician projection

Both audiences share:

- `subject_id`
- `fusion_result_id`
- authoritative `current_assessment`
- `forecast`
- `confidence`
- `assessment_status`
- `computed_at`
- `model_version`

The clinician projection includes modality summaries.

The patient projection returns an empty `modalities` array in Phase 2. This
preserves the existing patient privacy boundary because the handbook says
patient per-modality display is a separate ethics/clinical-UX approval decision.
Raw note text, component response blobs, weights, harmonisation internals and
other clinician-only detail are never included.

## Assessment history

`GET /v1/patients/{subject_id}/assessments` is clinician-only and requires an
active clinician-subject assignment.

History is generated exclusively from persisted `FusionResult` rows, newest
first. It preserves each row's:

- `fusion_result_id`
- authoritative current composite/tier/band
- `computed_at`
- `model_version`
- persisted confidence/provenance

No historical read reruns Fusion or any modality service and no old fusion row
is mutated.

Existing `FusionResult` does not persist the exact source reading IDs for all
modalities. For historical modality metadata, Phase 2 therefore uses the latest
append-only reading that had already been created at or before that
`FusionResult.computed_at`. This is documented reconstruction, not a claim that
an exact reading-ID link exists. Phase 4 event/source linkage must use explicit
persisted IDs where its frozen contract requires them.

## Forecast boundary

Phase 3 owns persistent `ForecastResult`.

Therefore Phase 2:

- keeps the frozen `forecast` field;
- returns `forecast: null` because no valid persisted `ForecastResult` exists;
- does not manufacture a forecast from current `FusionResult`;
- does not call C1 during assessment rendering;
- does not label C1's current standalone trajectory as a persisted multimodal
  forecast.

## Authorization

Latest assessment accepts only Phase-1-authorized subject access:

- patient: valid patient JWT bound to the same canonical `subject_id`;
- clinician: valid registered active clinician plus active
  `clinician_subject_assignment`.

History is clinician-only and assignment-scoped.

The Phase-1 distinction remains:

- invalid/missing/expired identity -> 401;
- valid identity without required subject/assignment authority -> 403.

## No-assessment case

The frozen `AssessmentSummary` requires a positive `fusion_result_id`.
Therefore a subject with no persisted `FusionResult` cannot honestly be
represented by fabricating an assessment ID. The latest endpoint returns 404
with an assessment-unavailable message until an authoritative persisted fusion
row exists.

## DEFERRED to Phase 3+

Phase 2 deliberately does not implement:

- `ForecastResult` table/persistence or forecast computation — Phase 3;
- attention policy, `AttentionEvent`, deduplication, hysteresis,
  ACK/RESOLVE/concurrency — Phase 4;
- ClinAnx live wiring/UI changes — Phase 5;
- Patient App live wiring/UI changes — Phase 6;
- device tokens/push/realtime — Phase 7;
- any Fusion/C4/RAG scientific method or threshold change.

## Deployment dependency

The Phase-1 deployment dependency remains unchanged: production JWT algorithm,
issuer, audience and approved JWKS/public-key/secret source must be supplied by
the real authentication service. Phase 2 does not invent those values or fall
back to a shared privileged mobile token.

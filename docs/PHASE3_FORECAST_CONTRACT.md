# Phase 3 — Forecast Contract

Status: implementation branch  
Handbook phase: **3. Forecast contract**

Phase 3 implements the handbook requirement to persist a near-term
`ForecastResult` separately from the current `FusionResult`. The implementation
is deliberately limited to the forecast evidence that CURRENT C1 actually
provides. It does not introduce a new multimodal forecasting algorithm and it
does not implement the Phase-4 attention/escalation policy.

## Source-derived requirements

The handbook states that CURRENT C1 exposes `risk_forecast` while the Central
Backend previously had no separate forecast persistence. The TARGET requires a
`forecast_results` persistence model with a forecast identity, subject,
`fusion_result_id`, scope, horizon, score/tier, timestamps, validity and model
version.

The handbook also explicitly states that a C1 future trajectory is **not**
automatically a multimodal future forecast. Until a separately specified and
validated method exists, the forecast must use `scope="physiological"`.

The frozen Phase-0 `ForecastResultContract` additionally requires exact source
provenance through:

- source component;
- source `ModalityReading.id`.

## CURRENT before Phase 3

After merged Phase 2:

- current multimodal state was persisted in append-only `FusionResult`;
- C1's complete external response, including its forecast fields, was retained
  inside the append-only `ModalityReading.detail`;
- `AssessmentSummary.forecast` was always null;
- no `forecast_results` table existed;
- the Patient App still consumed C1's forecast directly;
- no server-side attention policy existed.

## TARGET implemented by Phase 3

Phase 3 adds:

- append-only `forecast_results` persistence;
- explicit `forecast_result_id`;
- exact `subject_id`;
- exact linked `fusion_result_id`;
- `scope="physiological"`;
- a 10-minute forecast horizon;
- generated/valid-until timestamps;
- model-version provenance;
- exact C1 source component + source reading ID;
- projection of a currently valid persisted forecast into the canonical
  `AssessmentSummary`;
- historical projection of the forecast originally linked to each
  `FusionResult`;
- an idempotent Phase-3 database migration;
- Phase-3 integration/contract tests and CI coverage.

## C1 forecast extraction

The inspected CURRENT contract supports two representations:

1. explicit `risk_forecast` plus `forecast_horizons_minutes`, including +5 and
   +10 minute points;
2. the documented legacy >=10 sequential-point form.

Phase 3 selects the **+10 minute point** only.

For an explicit horizons array, it requires a literal 10-minute entry.

For the legacy form, the tenth sequential point is selected.

Malformed, missing or non-finite forecast values do not create a
`ForecastResult`.

## Score scale

The CURRENT C1 risk index/forecast is consumed by the patient implementation on
its native 0..100 scale. The handbook's ForecastResult JSON example uses a value
such as `0.84`, but it does not define a required scale-conversion formula.

Therefore Phase 3 **does not silently divide by 100 or otherwise normalize the
C1 forecast**. The persisted forecast score remains the C1 native value.

This avoids adding unapproved scientific transformation logic merely to match an
illustrative JSON number.

## Forecast tier and escalation semantics

Phase 3 does **not** invent a forecast tier threshold.

The persisted fields are therefore:

- `tier = null`;
- `escalation_probability = null`.

The handbook places the versioned escalation policy, confirmation, hysteresis,
deduplication and event generation in the next attention-engine phase. Phase 3
does not move that work forward.

The frozen Phase-0 contract requires the boolean `escalation_predicted`. Until
the Phase-4 server policy exists, Phase 3 persists it as `false` to represent
that **no authoritative server escalation decision has been made**. It must not
be interpreted as a reassuring or low-risk classification of the C1 forecast
score.

Phase 4 must update the authoritative escalation/event semantics rather than
treating this Phase-3 placeholder as its policy decision.

## Generated time and validity

When the C1 payload publishes one of:

- `forecast_generated_at`;
- `generated_at`;
- `computed_at`;

that value is preserved.

The inspected C1 contract does not guarantee a forecast-generation timestamp.
When it is absent, the backend records the Central Backend persistence/observation
time for that C1 reading. It deliberately does **not** substitute the sensor's
`captured_at`, because that timestamp describes the source observation rather
than when the future projection was generated.

`valid_until` is stored as:

```text
generated_at + 10 minutes
```

matching the explicit 10-minute forecast horizon represented by this
ForecastResult.

The latest assessment includes a forecast only while that persisted validity
window is active. Historical assessment reads retain the linked forecast even
after it has expired.

## Model version

If C1 publishes `forecast_model_version` or `model_version`, that value is
stored.

If the adapter or source reading already has a model version, it is used.

The inspected CURRENT C1 contract does not guarantee a model version. In that
case the persisted value is the explicit provenance marker:

`unreported:c1`

This is not presented as a real scientific version.

## Current assessment remains separate

Phase 3 does not alter Fusion mathematics or overwrite `FusionResult`.

Example:

```text
current_assessment.score = authoritative persisted FusionResult composite
forecast.score           = C1 +10-minute physiological forecast
```

They can use different scales and are not collapsed into a generic
`risk_score`.

The forecast is linked to the latest authoritative FusionResult available when
the C1 reading is persisted. Physiological fusion may remain on the existing
debounced cadence; the C1 forecast does not need to be discarded merely because
a new full FusionResult was not written that minute.

## Assessment projection

`GET /v1/patients/{subject_id}/assessment/latest`

now returns the latest persisted forecast linked to that authoritative
`FusionResult` **only when its validity window is current**.

`GET /v1/patients/{subject_id}/assessments`

continues to be clinician/assignment scoped and includes historical linked
forecast projections even after their validity windows have expired.

Assessment reads still do not call C1, Fusion or another model service.

## Persistence and migration

New table:

`forecast_results`

Key properties:

- primary key: `forecast_result_id`;
- foreign key to canonical `subject_id`;
- foreign key to exact `fusion_result_id`;
- foreign key to exact source `ModalityReading.id`;
- append-only forecast snapshots;
- uniqueness over source component + source reading + horizon, preventing the
  same source forecast from being persisted twice.

Migration:

`central_backend/migrate_phase3.py`

Migration ID:

`phase3_forecast_result_v1`

The migration is idempotent and does not alter `FusionResult`, C4/DCAR, Fusion
service tables, or RAG state.

## Ownership boundary preserved

Phase 3 does not:

- implement or modify Uvindu's Fusion mathematics;
- implement or modify C4/DCAR;
- implement or modify CARE-AnxRAG;
- create a local multimodal forecasting model;
- invent forecast weights;
- invent clinical thresholds;
- implement AttentionEvent;
- implement server confirmation/hysteresis/deduplication.

## DEFERRED — Phase 4+

Phase 4 remains responsible for:

- project-approved/versioned escalation policy;
- confirmation;
- hysteresis;
- episode state;
- deduplication;
- cooldown/recovery;
- persistent AttentionEvent;
- exact FusionResult/ForecastResult event linkage;
- OPEN -> ACKNOWLEDGED -> RESOLVED lifecycle;
- concurrency-safe ACK/RESOLVE;
- server-derived lifecycle actors/timestamps.

Mobile application migration away from direct C1 forecast consumption remains
in the later application-integration phases.

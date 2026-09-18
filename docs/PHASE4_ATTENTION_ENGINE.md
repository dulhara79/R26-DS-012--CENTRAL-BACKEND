# Phase 4 — Attention Engine

Status: implementation branch  
Handbook phase: **4. Attention engine**

Phase 4 moves urgent escalation handling into the Central Backend, using the
frozen `AttentionEvent` contract and the escalation-gate parameters documented
in the implementation handbook. The server now owns policy evaluation, durable
episode state, duplicate suppression, event persistence, assignment-scoped
event access, acknowledgement, resolution and lifecycle audit.

This is a research-prototype implementation. It does not claim that the policy
is clinically validated or that an anxiety event will occur at an exact future
time.

## Handbook requirements implemented

The System Integration Implementation Handbook requires:

- a versioned server escalation policy;
- confirmation/hysteresis before event creation;
- one persistent event for one escalation episode;
- recovery criteria before a future episode may emit another event;
- exact links from an AttentionEvent to its FusionResult and ForecastResult;
- OPEN -> ACKNOWLEDGED -> RESOLVED lifecycle;
- actor/timestamp derived on the server;
- assignment-scoped clinician retrieval/mutation;
- atomic acknowledgement/resolution;
- audit records for creation, acknowledgement and resolution.

The roadmap defines Phase 4 as:

`Server policy, episode state, dedupe, events, ACK/RESOLVE, audit.`

## Policy source and version

No new scientific threshold was invented.

The handbook documents the CURRENT patient-side predictive escalation gate with:

- elevated forecast threshold: **45**
- high forecast threshold: **70**
- minimum increase for elevated crossing: **20**
- minimum increase for high crossing: **10**
- recovery threshold: **40**
- required confirmations: **2**
- minimum confirmation spacing: **20 seconds**
- maximum confirmation gap: **2 minutes**

These values are represented as one immutable code policy version:

`c1-physio-escalation-v1`

Changing any threshold/confirmation/recovery rule requires a new policy version
rather than silently changing the meaning of historical events.

The policy operates only on Phase-3 `scope="physiological"` C1 ForecastResults.
It does not describe the forecast as multimodal.

## Evaluation semantics

For every valid persisted C1 +10-minute ForecastResult:

1. load the exact source C1 ModalityReading;
2. preserve current C1 and forecast C1 on their native 0..100 scale;
3. evaluate the documented forecast threshold + increase rule;
4. persist candidate confirmation state on the server;
5. require a second qualifying forecast between 20 seconds and 2 minutes after
   the prior confirmation;
6. create exactly one AttentionEvent after confirmed crossing;
7. suppress later qualifying updates while the same episode remains active;
8. re-arm only after both current and forecast scores are below 40.

A duplicate re-evaluation of the exact same `forecast_result_id` cannot count as
a second confirmation.

A non-qualifying forecast clears an unconfirmed candidate. It does not re-arm an
already emitted episode. Only the documented recovery condition does that.

## Persistent episode state

New table:

`attention_episode_state`

It persists:

- subject;
- policy version;
- confirmation count;
- last confirmation time;
- last evaluated forecast_result_id;
- whether an event has already been emitted;
- episode number;
- current event ID;
- recovery timestamp;
- update timestamp.

This means duplicate suppression and recovery survive process/app restarts.

## Persistent AttentionEvent

New table:

`attention_events`

Each event stores the frozen contract fields:

- `id`
- `subject_id`
- `fusion_result_id`
- `forecast_result_id`
- `event_type`
- `severity`
- `reason`
- `forecast_horizon`
- `status`
- `created_at`
- `acknowledged_at`
- `acknowledged_by`
- `resolved_at`
- `resolved_by`
- `policy_version`

An internal unique `episode_key` provides a database-level second line of
defence against duplicate event creation for one subject/policy episode.

The event links to the exact persisted FusionResult and exact ForecastResult
that caused the confirmed server decision.

## Forecast annotation

Phase 3 deliberately left `escalation_predicted=false` because no authoritative
server policy existed yet.

Phase 4 sets that field to `true` only on the exact forecast that completes the
server confirmation rule and creates the AttentionEvent. A single qualifying
point remains false and cannot manufacture urgency.

No forecast tier or escalation probability is invented. They remain null unless
a scientifically defined source supplies them in a future contract.

## Event API

Implemented frozen routes:

- `GET /v1/attention-events`
- `GET /v1/attention-events?status=OPEN`
- `GET /v1/attention-events?subject_id=...`
- `GET /v1/attention-events/{id}`
- `POST /v1/attention-events/{id}/acknowledge`
- `POST /v1/attention-events/{id}/resolve`

List supports status, severity, subject and bounded limit filters.

All clinician reads/mutations are constrained by active
`clinician_subject_assignments`.

An unassigned clinician cannot retrieve an event by guessing its ID. A global
list returns only events for active assignments.

## Frozen mutation body

The Phase-0 contract explicitly froze ACK and RESOLVE request bodies as:

`{}`

Therefore Phase 4 does not accept client-supplied:

- acknowledged_by;
- acknowledged_at;
- resolved_by;
- resolved_at;
- free-text lifecycle actor identity.

Actor identity comes from the verified clinician JWT and timestamps are created
on the server.

The full handbook mentions an optional resolve note/reason as a possible target
semantic, but the later frozen Phase-0 implementation contract chose `{}`.
Adding a resolve note therefore requires a versioned contract change rather than
silently widening this endpoint.

## Atomic lifecycle and concurrency

Legal forward lifecycle:

`OPEN -> ACKNOWLEDGED -> RESOLVED`

The database mutation uses conditional UPDATE statements:

- acknowledge updates only rows currently `OPEN`;
- resolve updates only rows currently `ACKNOWLEDGED`.

If two assigned clinicians act on the same event:

- the first successful database transition wins;
- the second request fetches and returns the canonical server state;
- the original actor/timestamp are not overwritten.

Repeated acknowledge/resolve calls are reconciliation-safe/idempotent once the
event has already progressed.

Resolving an OPEN event is rejected with HTTP 409 because it would skip the
frozen lifecycle.

## Audit

Phase 4 records:

- `attention.created`
- `attention.recovered`
- `attention.read`
- `attention.list`
- `attention.acknowledged`
- `attention.resolved`

Creation uses the server attention-engine actor. Clinician mutations use the
verified clinician identity.

## Physiological ingestion integration

After a C1 ingest:

1. C1 reading persists;
2. current fusion follows the existing controlled/debounced cadence;
3. ForecastResult persists;
4. forecast persistence commits;
5. the lightweight attention policy evaluates the forecast immediately;
6. candidate/recovery/event episode state persists;
7. one server event is created only after confirmation.

This follows the handbook rule that avoiding a full FusionResult every minute
must not prevent lightweight urgent forecast-state evaluation at each valid C1
update.

The physiological ingest response includes operational identifiers:

- `forecast_result_id`
- `attention_event_id` when an event exists for the evaluation;
- `attention_policy_action`

The Central Backend event remains authoritative; these fields are not a mobile
authorization mechanism.

## Migration

Migration:

`central_backend/migrate_phase4.py`

Migration ID:

`phase4_attention_engine_v1`

The migration creates:

- `attention_events`
- `attention_episode_state`

and records its migration marker idempotently.

It does not change Fusion mathematics, C1 model inference, C4/DCAR, C3/TC-WPN,
C2 eligibility, or CARE-AnxRAG.

## Health

`GET /health` now exposes non-secret attention-engine readiness metadata:

- policy version;
- persistent event capability;
- lifecycle string.

Threshold details remain documented/versioned in code/docs rather than being
secret deployment configuration.

## Ownership boundaries preserved

Phase 4 does not implement:

- mobile UI;
- client-side alert authority;
- device-token registry;
- FCM/APNs;
- WebSocket delivery;
- new multimodal forecast science;
- a new C1 model;
- new fusion weights;
- new C2 eligibility;
- RAG risk calculation.

## Deferred

Phase 5 owns ClinAnx presentation/integration.

Phase 6 owns Patient App shared-event consumption.

Phase 7 owns push/realtime/device-token delivery with polling fallback.

Phase 8 owns broader hardening/failure injection and release review.

The server AttentionEvent remains the source of truth regardless of later
notification transport.

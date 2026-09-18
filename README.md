# R26-DS-012 Central Backend

This repository is the integration-owned Central Backend workspace for R26-DS-012.

## CURRENT baseline

The Central Backend baseline was synchronized from:

- source repository: UVINDUSEN/component4final
- pinned commit: a1ceef8daba268dac24d81aedd76c89e7c9ccc6a
- pinned central_backend tree: 87585bb3c823b9de449c1a0be1efd9d65f815fd2

The original synchronized copy was merged by PR #2. The scientific Fusion Service
was also copied in that PR, but that ownership was subsequently corrected. This
repository now contains the Central Backend only.

## Ownership boundary

The Central Backend owns integration concerns: canonical subject identity,
aliases/pairing, modality HTTP orchestration, persistence, API projections,
authorization/assignments as they are implemented in later handbook phases,
auditability, forecasts/events, notifications, and failure handling.

These scientific services remain independently owned and deployed:

- C4/DCAR: Uvindu's contextual/demographic service
- Multimodal Fusion Service: Uvindu's harmonisation/fusion implementation
- CARE-AnxRAG: Uvindu's evidence retrieval/generation service

The Central Backend communicates with them through HTTP adapters only. It does
not vendor or independently maintain fusion.py, harmonisation/reference data,
DCAR model internals, or CARE-AnxRAG internals.

## Repository layout

    central_backend/   Central Backend source
    docs/              integration-owned traceability / contract documentation

The pre-handbook implementation remains preserved on:

    archive/legacy-pre-handbook-2026-09-18

## Explicit integration-owned deviation from the pinned source

The pinned component4final Central Backend supported both in-process and HTTP
Fusion modes. This repository intentionally removes the in-process path so the
ownership boundary is enforceable:

    Central Backend -> POST {FUSION_URL}/v1/fuse/manual -> external Fusion Service

No replacement fusion algorithm is implemented here. Automated Central Backend
tests use an explicit deterministic Fusion stub to exercise orchestration,
persistence, identity separation, and API contracts without testing Uvindu's
mathematics.

C4 and CARE-AnxRAG remain external HTTP dependencies as well.

## Local setup

    python -m venv .venv
    # Windows: .venv\Scripts\activate
    # Linux/macOS: source .venv/bin/activate
    pip install -r central_backend/requirements.txt

    cd central_backend
    # create .env from env.example.txt and set MRN_PEPPER and service URLs
    python test_backend.py
    uvicorn main:app --reload --port 8000

The automated baseline test script stubs C1-C4 and Fusion. Separate live/service
contract checks are required before a research demo or release.

## Handbook roadmap

The Central-Backend-only prerequisite was merged in PR #3.

Phase 0 contract-lock implementation is merged and documented in
`docs/PHASE0_CONTRACT_LOCK.md`.

Phase 1 authentication/assignment implementation is documented in
`docs/PHASE1_AUTH_ASSIGNMENTS.md`. It uses configurable fail-closed JWT
verification, server-owned clinician identity/assignments, patient subject
binding and assignment-scoped clinician access. Production JWT issuer/audience/
algorithm/key values must come from the approved auth service; they are not
invented in this repository.

Roadmap:

1. Phase 0 - contract lock
2. Phase 1 - backend authentication + assignments
3. Phase 2 - canonical latest assessment
4. Phase 3 - ForecastResult
5. Phase 4 - persistent AttentionEvent engine
6. later app integration, delivery, hardening and release phases

Keep CURRENT, TARGET and PROPOSED behavior explicit. Do not silently turn a
target contract into a claim about the running system.

Research prototype - not a diagnostic device.

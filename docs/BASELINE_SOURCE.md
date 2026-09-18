# Baseline Source Record

Date synchronized: 2026-09-18
Ownership correction: 2026-09-18

## Authoritative CURRENT source used

Source repository:

    https://github.com/UVINDUSEN/component4final

Pinned source commit:

    a1ceef8daba268dac24d81aedd76c89e7c9ccc6a

Pinned source tree used for the Central Backend:

    central_backend/: 87585bb3c823b9de449c1a0be1efd9d65f815fd2

For provenance only, the source repository's Fusion Service tree at that commit
was:

    fusion_service/: 1184427955ddd2f7be40eb00f915290356177a14

That Fusion tree is NOT owned or vendored by this repository after the ownership
correction.

## What PR #2 did

PR #2 synchronized both central_backend/ and fusion_service/ from the pinned
component4final snapshot. It was merged before the later ownership clarification.

The previous independent Central Backend implementation remains preserved on:

    archive/legacy-pre-handbook-2026-09-18

## Corrected ownership boundary

This repository owns the Central Backend integration layer only.

Uvindu independently owns:

- C4/DCAR scientific model and deployment
- Multimodal Fusion Service, including fusion mathematics, harmonisation,
  weights, reference distributions, registered exclusion logic and model version
- CARE-AnxRAG retrieval/generation internals and scientific validation

The Central Backend calls those services over HTTP. Their implementations are
not copied here.

## Baseline identity and documented deviations

Before the ownership correction, every file under central_backend/ was verified
byte-for-byte against the pinned central_backend tree above.

The correction intentionally changes only integration-owned files required to
enforce the external service boundary. In particular:

- central_backend/fusion_client.py is HTTP-only and calls POST /v1/fuse/manual;
- central_backend/env.example.txt no longer configures an in-process Fusion path;
- central_backend/test_backend.py uses a deterministic test stub instead of
  importing Fusion scientific code;
- root Docker/CI/readme files package and verify the Central Backend only.

All other central_backend baseline files remain pinned-source copies unless a
later handbook phase explicitly changes them through a reviewed PR.

## Source-of-truth rule

- CURRENT: verified behavior in the checked-out implementation.
- TARGET: handbook/specification requirement not necessarily implemented yet.
- PROPOSED: engineering recommendation not yet implemented/validated.

Do not silently reconcile differences between these categories.

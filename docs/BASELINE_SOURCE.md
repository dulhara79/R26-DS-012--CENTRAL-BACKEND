# Baseline Source Record

Date synchronized: 2026-09-18

## Authoritative CURRENT source used

Repository:

`https://github.com/UVINDUSEN/component4final`

Pinned commit:

`a1ceef8daba268dac24d81aedd76c89e7c9ccc6a`

Pinned trees:

- `central_backend/`: `87585bb3c823b9de449c1a0be1efd9d65f815fd2`
- `fusion_service/`: `1184427955ddd2f7be40eb00f915290356177a14`

## Synchronization rule

Files under `central_backend/` and `fusion_service/` in the baseline commit are byte-for-byte copies of the pinned source snapshot. The root-level Docker/CI/readme files are integration-repository wrappers and are not claimed to originate from component4final.

The previous independent Central Backend implementation is preserved on:

`archive/legacy-pre-handbook-2026-09-18`

## Ownership boundary

The Central Backend integration work may add handbook TARGET capabilities around the synchronized baseline.

The fusion snapshot exists for reproducibility and the existing in-process test/local mode. Scientific fusion formulae, harmonisation references, registered exclusions, weights, and model-version semantics must not be independently changed as part of Central Backend integration work. Changes to fusion research logic require the fusion/component owner and research-method approval.

## Handbook source-of-truth rule

- CURRENT: synchronized implementation above.
- TARGET: requirements explicitly defined in the system-integration handbook / approved app contracts.
- PROPOSED: recommendations that must not be presented as implemented or validated behavior.

Do not silently reconcile CURRENT/TARGET differences.

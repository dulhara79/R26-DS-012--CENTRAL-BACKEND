# R26-DS-012 Central Backend

This repository is the integration-owned Central Backend workspace for R26-DS-012.

## Baseline

The executable baseline on this branch is synchronized from:

- Source repository: `UVINDUSEN/component4final`
- Source commit: `a1ceef8daba268dac24d81aedd76c89e7c9ccc6a`
- Source Central Backend tree: `87585bb3c823b9de449c1a0be1efd9d65f815fd2`
- Source Fusion Service tree: `1184427955ddd2f7be40eb00f915290356177a14`

This is the CURRENT implementation used as the starting point by the R26-DS-012 integration handbook. Future implementation in this repository must preserve the handbook distinction between CURRENT, TARGET and PROPOSED behavior.

## Repository layout

```text
central_backend/   # synchronized CURRENT central backend
fusion_service/    # synchronized fusion implementation needed for exact local/test mode
docs/              # integration-owned traceability and contract documentation
```

The Fusion Service snapshot is included so the synchronized backend's existing `FUSION_MODE=inprocess` test/local path remains reproducible. Do not independently change the scientific fusion mathematics here. Production/research separation should use the documented HTTP fusion-service boundary when configured.

## Run the synchronized baseline

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r central_backend/requirements.txt

cd central_backend
# create .env from env.example.txt and set MRN_PEPPER
python test_backend.py
uvicorn main:app --reload --port 8000
```

The test suite stubs external component services. It does not require live C1-C4 services.

## Integration rule

Do not revive the pre-handbook backend architecture from this repository's archived branch. The legacy version is preserved at:

`archive/legacy-pre-handbook-2026-09-18`

Phase work must now start from this synchronized baseline and follow the handbook roadmap:

1. Phase 0 — contract lock
2. Phase 1 — backend authentication + assignments
3. Phase 2 — canonical assessment contract
4. Phase 3 — forecast contract
5. Phase 4 — AttentionEvent engine
6. later application/delivery/hardening phases

Research prototype — not a diagnostic device.

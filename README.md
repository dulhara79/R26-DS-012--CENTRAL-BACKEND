---
title: R26-DS-012 Central Backend
emoji: 🧠
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8000
pinned: false
short_description: Orchestrator for the R26-DS-012 multimodal anxiety framework
---

# R26-DS-012 — Central Backend

The missing middle layer. Both Flutter apps talk to this and nothing else; this
talks to the five model services.

It runs **today**, with zero model services deployed. Every service it can't
reach is mocked, deterministically, per patient. When a Hugging Face Space goes
live you paste one URL into `.env`, restart, and that service is real. No code
changes anywhere — not here, not in either app.

```
Patient App ─┐                            ┌─ C1 physiological
             ├─► CENTRAL BACKEND ────────►├─ C2 behavioural
Clinician App┘   auth · identity ·        ├─ C4 TC-WPN
                 freshness · gating ·     ├─ Fusion
                 orchestration · audit    └─ C3 intervention
                        │
                        └─► PostgreSQL / SQLite
```

---

## Quickstart (2 minutes)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python seed.py
uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000/docs>. Login: `doctor@nhsl.demo` / `demo1234`.

Then, in a second terminal:

```bash
python smoke_test.py
```

That script is your proof of integration. It checks enrolment, pairing, both
ingestion paths, both view surfaces, the audit trail, and — the one that
matters — that one patient's token cannot reach another patient's data.

---

## Going live: the only thing you change

`.env`. That's it.

```bash
C4_BASE_URL=https://dulharakaushalya-tc-wpn-demo.hf.space
C1_BASE_URL=https://dewdu-c1-physiological.hf.space
# leave the rest blank -> they stay mocked
```

Restart. Check which are real:

```bash
curl localhost:8000/v1/ops/services
```

```
c1      live  ok           c1-lstmae-v1.2.0
c2      mock  ok
c4      live  ok           TC-WPN-v1.0
fusion  mock  ok
c3      mock  ok
```

**Mixed mock and live works.** You can go live one service at a time as your
teammates finish, and the system keeps producing composites the whole way. You
never have a day where nothing runs because someone's Space isn't ready.

If a teammate insists on a different endpoint path, override it (`C1_PREDICT_PATH`)
rather than editing code.

---

## Why nothing breaks when a teammate ships a slightly wrong response

Every model response goes through `coerce_envelope` in `app/clients.py`, which
is deliberately paranoid:

- accepts `score`, `risk_score`, `calibrated_probability`, `vulnerability_score`,
  `probability`, `p_anxiety` or `risk` as the score field
- for C4 it *prefers* `calibrated_probability` over `risk_score`
- clamps to `[0,1]`, rejects NaN, coerces strings
- a service claiming `status: ok` with no usable number is recorded as `error`
- a dead or sleeping Space becomes a stored `status: error` reading, never a 500

So a contract mismatch on demo day costs you one excluded modality and a visible
gap in the timeline, not a crashed backend.

---

## What the orchestrator decides (and Flutter never does)

| Decision | Where |
|---|---|
| Is this reading fresh enough? | `FRESHNESS_SECONDS` — C1 15 min, C2 7 days, C4 90 days |
| Are there enough modalities? | `MIN_MODALITIES` (default 2) |
| What weights apply? | `FUSION_WEIGHTS`, stamped with `FUSION_WEIGHTS_VERSION` on every result |
| Renormalise or impute? | Always renormalise. Never impute. |
| Should fusion run? | On every new modality reading |
| Should C3 run? | After fusion, only when a composite exists |

If the fusion service is unreachable, the backend computes the same weighted
renormalisation locally and marks the result `provisional: true`. The clinician
app already has a `FusionResult.local` concept — this feeds it.

### C3 position

`C3_MODE=downstream` (default) — fusion combines C1/C2/C4; C3 consumes the
composite and returns tier, conformal set and the ranked plan.

This is deliberate. Your main repo defines C3's input as a **tri-modal risk
vector** built from physiological, behavioural and textual features. C3 is
already a fusion model. Feeding its output back into fusion as a fourth peer
counts C1/C2/C4 twice.

If your team decides otherwise, set `C3_MODE=modality` and the four-way weights
apply instead. Decide before you write the paper, not after.

---

## API surface

The apps only ever see these. No `/c1`, `/c2`, `/c4`, `/fusion` is exposed.

**Auth**
```
POST /v1/auth/clinician/login          email + password  -> JWT
POST /v1/auth/patient/pair             pairing_code + device_id -> JWT (scoped to 1 subject)
```

**Patient** (bearer = patient token; `me` resolves from the token, never from a path)
```
POST /v1/subjects/me/physiology        60s window -> C1 -> fusion -> C3
POST /v1/subjects/me/behavior          one day    -> C2 -> fusion -> C3
POST /v1/subjects/me/ema               GAD-7 / PSS-10 / mood
GET  /v1/subjects/me/risk              composite + band + one activity. Nothing else.
```

**Clinician**
```
POST /v1/clinician/subjects                        enrol -> subject_id + pairing code
GET  /v1/clinician/subjects                        caseload
POST /v1/clinician/subjects/{id}/notes             note -> TC-WPN -> fusion -> C3
POST /v1/clinician/subjects/{id}/notes/{nid}/verdict   HITL agree/disagree
GET  /v1/clinician/subjects/{id}/support-set
POST /v1/clinician/subjects/{id}/support-set
GET  /v1/clinician/subjects/{id}/timeline          everything for one chart, one call
GET  /v1/clinician/subjects/{id}/audit
```

**Ops**
```
GET  /health
GET  /v1/ops/services      which are live, which are mocked, what versions
POST /v1/ops/warmup        wake sleeping HF Spaces before a demo
```

---

## Patient separation

This is the thing to demonstrate, and it is enforced in three places:

1. **Identity.** MRN is never stored in the clear. `subject_id = "S-" +
   sha256(MRN + pepper)[:10]`. Device IDs and MRN hashes are *aliases* onto one
   canonical subject.
2. **Token scope.** A patient token carries exactly one `subject_id`. The patient
   endpoints are `/subjects/me/...` — there is no path parameter to tamper with.
3. **Ownership check.** Every clinician route calls `_own()`, which 404s if the
   subject doesn't belong to the calling clinician.

Fusion requests are built by `build_components()` from a single-subject query.
Two patients' scores physically cannot meet.

---

## Demo scenarios (from `seed.py`)

| | Setup | Result |
|---|---|---|
| **P001** | everything reporting | composite; C2 excluded as `not_validated` |
| **P002** | C2 has 18 of 42 days | composite; C2 excluded with the day count |
| **P003** | C1 ECG quality 0.41 | C1 excluded `poor_signal`; **no composite**, reason stated |
| **P004** | C1 captured 3 h ago | C1 excluded `stale`; **no composite**, reason stated |

Show P003 and P004. A system that refuses to produce a number when it shouldn't
is far more convincing than a green dial, and it's the direct software
expression of the honesty your evaluation demands.

---

## Deployment

`Dockerfile` and `render.yaml` are included.

- **Backend:** Render. The free tier spins down after ~15 min with a 30–60 s cold
  start — fine while building, a real risk during a live demo. The Starter plan
  removes it.
- **Database:** set `DATABASE_URL` to a managed Postgres (Neon / Supabase /
  Render). SQLite is the default and is fine for development.
- **Model services:** Hugging Face. They sleep after 48 h. `HTTP_TIMEOUT_S=90`
  and `HTTP_RETRIES=2` handle the cold start; call `POST /v1/ops/warmup` ten
  minutes before you present.

Secrets live here, in the server environment. `HF_TOKEN` must never appear in
either APK — the apps get a backend URL and a user JWT, nothing more.

---

## App-side changes

**Clinician app.** You already have `TcwpnGateway`, `C3Gateway`, `ModalityGateway`,
`FusionGateway` over one `ApiClient`. Keep all of it. Change only what the
gateways point at:

```bash
# before
--dart-define=TCWPN_BASE=... --dart-define=FUSION_BASE=... --dart-define=HF_TOKEN=hf_xxx

# after
--dart-define=API_BASE=https://your-backend.onrender.com
```

`TcwpnGateway.predict` → `POST /v1/clinician/subjects/{id}/notes`.
`FusionGateway.fuse` → gone; the composite arrives inside `/timeline`.

**Patient app.** Point the existing offline queue at
`POST /v1/subjects/me/physiology`. Keep the queue and retry logic — it's good and
it's exactly right for a backend that talks to sleeping Spaces. Keep the Apps
Script writer as a research logger if you want; just take it off the inference
path. And remove or clearly relabel the client-side 0–100 "Anxiety Risk Score",
or you'll have two disagreeing numbers on screen.

---

## Four-day plan

**Day 1 — stand it up.** Clone, `seed.py`, `smoke_test.py` green. Deploy to
Render with Postgres. Send your three teammates the response contract for their
service (`/docs` is generated from it) and the one sentence that matters: *match
these field names and your Space drops straight in.*

**Day 2 — clinician app.** Collapse five `*_BASE` defines to one `API_BASE`.
Repoint the four gateways. Delete `HF_TOKEN` from the build. You should be able
to enrol P001, write a note, and see a composite with a real weights breakdown.

**Day 3 — patient app.** Repoint the offline queue. Implement the pairing-code
screen. Relabel the local risk number. Verify P001 and P002 on two devices show
different data.

**Day 4 — swap in whatever is ready, and rehearse.** Paste live URLs one at a
time, checking `/v1/ops/services` after each. Anything not ready stays mocked and
the demo still runs. Rehearse the P003/P004 gap cases — they're your strongest
material. Warm up ten minutes before.

The critical property: **you are never blocked on a teammate.** If nobody's Space
is ready on day 4, you still have a complete, working, defensible system.

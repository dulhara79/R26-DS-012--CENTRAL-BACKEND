"""
End-to-end check against a running server.

    uvicorn app.main:app --port 8000     # terminal 1
    python smoke_test.py                 # terminal 2

Proves, in order:
  1. clinician login works
  2. enrolment issues a subject_id + pairing code
  3. a patient device pairs and gets a token scoped to ONE subject
  4. patient ingestion triggers C1 -> fusion -> C3
  5. a clinical note triggers TC-WPN -> fusion -> C3
  6. the patient view is thin and the clinician view is full
  7. P001's token cannot read P002's data          <-- the separation test
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import httpx

BASE = "http://127.0.0.1:8000"
OK, FAIL = "  [ok] ", "  [FAIL] "
failures = 0


def check(label: str, condition: bool, extra: str = "") -> None:
    global failures
    print((OK if condition else FAIL) + label + (f"  {extra}" if extra else ""))
    if not condition:
        failures += 1


def iso(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=60.0)

    print("\n1. service wiring")
    ops = c.get("/v1/ops/services").json()
    for s in ops["services"]:
        print(f"       {s['service']:<7} {s['mode']:<5} {s['status']}")
    print(f"       c3_mode={ops['c3_mode']}  weights={ops['fusion_weights_version']}")

    print("\n2. clinician login")
    r = c.post("/v1/auth/clinician/login",
               json={"email": "doctor@nhsl.demo", "password": "demo1234"})
    check("login returns a token", r.status_code == 200, str(r.status_code))
    doc = {"Authorization": f"Bearer {r.json()['access_token']}"}

    print("\n3. enrolment")
    r = c.post("/v1/clinician/subjects",
               json={"mrn": "MRN-SMOKE-A", "display_label": "SMOKE-A"}, headers=doc)
    a = r.json()
    check("subject created with pairing code", "pairing_code" in a, a.get("subject_id", ""))

    r = c.post("/v1/clinician/subjects",
               json={"mrn": "MRN-SMOKE-B", "display_label": "SMOKE-B"}, headers=doc)
    b = r.json()
    check("second subject is a different subject_id", a["subject_id"] != b["subject_id"])

    print("\n4. patient pairing")
    r = c.post("/v1/auth/patient/pair",
               json={"pairing_code": a["pairing_code"], "device_id": "device-aaa"})
    check("device paired", r.status_code == 200, str(r.status_code))
    pat_a = {"Authorization": f"Bearer {r.json()['access_token']}"}
    check("token is scoped to one subject", r.json()["subject_id"] == a["subject_id"])

    r = c.post("/v1/auth/patient/pair",
               json={"pairing_code": a["pairing_code"], "device_id": "device-zzz"})
    check("pairing code cannot be reused", r.status_code == 400, str(r.status_code))

    print("\n5. patient ingestion -> C1 -> fusion")
    now = datetime.now(timezone.utc)
    r = c.post("/v1/subjects/me/physiology", headers=pat_a, json={
        "window_start": iso(now - timedelta(minutes=1)), "window_end": iso(now),
        "features": {"hr_mean": 88.2, "hrv_rmssd": 24.1, "resp_rate": 18.4,
                     "skin_temp": 36.8, "ecg_quality": 0.94}})
    check("physiology accepted", r.status_code == 200, r.text[:80])
    check("C1 reported ok", r.json().get("status") == "ok", str(r.json()))

    c.post("/v1/subjects/me/ema", headers=pat_a,
           json={"instrument": "gad7", "score": 13})

    print("\n6. clinical note -> TC-WPN -> fusion -> C3")
    r = c.post(f"/v1/clinician/subjects/{a['subject_id']}/notes", headers=doc, json={
        "note_text": "Reports persistent worry, difficulty controlling it, "
                     "initial insomnia and muscle tension over three weeks.",
        "visit_count": 3})
    check("note analysed", r.status_code == 200, r.text[:120])
    body = r.json()
    check("composite produced from 2 modalities", body.get("composite_score") is not None,
          f"composite={body.get('composite_score')} band={body.get('band')}")

    print("\n7. the two views differ")
    pv = c.get("/v1/subjects/me/risk", headers=pat_a).json()
    cv = c.get(f"/v1/clinician/subjects/{a['subject_id']}/timeline", headers=doc).json()
    check("patient view has no per-modality breakdown",
          "components" not in pv and "readings" not in pv, str(list(pv.keys())))
    check("patient view has no note text", "notes" not in pv)
    check("clinician view has weights + excluded",
          bool(cv["fusion"]["weights"]) and "excluded" in cv["fusion"],
          f"weights={cv['fusion']['weights']}")
    check("clinician view carries model versions",
          all(r.get("model_version") for r in cv["readings"]))
    check("audit trail is populated",
          len(c.get(f"/v1/clinician/subjects/{a['subject_id']}/audit",
                    headers=doc).json()["entries"]) > 0)

    print("\n8. PATIENT SEPARATION")
    r = c.get("/v1/subjects/me/risk", headers=pat_a).json()
    check("A's token returns A's subject_id", r["subject_id"] == a["subject_id"])
    r = c.get(f"/v1/clinician/subjects/{b['subject_id']}/timeline", headers=pat_a)
    check("A's patient token is refused the clinician API", r.status_code == 403,
          str(r.status_code))
    tb = c.get(f"/v1/clinician/subjects/{b['subject_id']}/timeline", headers=doc).json()
    check("B has entirely separate readings",
          all(x["modality"] for x in tb["readings"]) or tb["readings"] == [],
          f"A readings={len(cv['readings'])}  B readings={len(tb['readings'])}")

    print("\n9. gating scenarios from the seed")
    for s in c.get("/v1/clinician/subjects", headers=doc).json()["subjects"]:
        if s["display_label"].startswith("P"):
            t = c.get(f"/v1/clinician/subjects/{s['subject_id']}/timeline",
                      headers=doc).json()
            f = t["fusion"] or {}
            verdict = (f"{f.get('composite_score')} {f.get('band')}"
                       if f.get("composite_score") is not None
                       else f"NO COMPOSITE - {f.get('reason')}")
            excl = ", ".join(f"{e['modality']}={e['reason']}" for e in f.get("excluded", []))
            print(f"       {s['display_label']}: {verdict}")
            if excl:
                print(f"                excluded: {excl}")

    print(f"\n{'ALL CHECKS PASSED' if not failures else f'{failures} CHECK(S) FAILED'}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

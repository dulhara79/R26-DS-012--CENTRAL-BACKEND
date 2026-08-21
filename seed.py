"""
Demo data.

    python seed.py

Creates one clinician and four subjects, each demonstrating a different
behaviour of the gating logic. The last three are the interesting ones — any
system can show a green number when everything works.

    P001  everything reporting              -> composite, C2 excluded as not_validated
    P002  C2 has only 18 of 42 days         -> composite, C2 excluded with a day count
    P003  C1 ECG quality 0.41                -> C1 excluded, NO composite, reason stated
    P004  C1 captured 3 hours ago            -> C1 stale (15 min window), NO composite

Every one of these is a *correct* outcome. The system refusing to produce a
number is the thing worth demonstrating, not the number.

Login:  doctor@nhsl.demo  /  demo1234
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app import models, orchestrator, security
from app.clients import utcnow
from app.db import SessionLocal, init_db

CLINICIAN_EMAIL = "doctor@nhsl.demo"
CLINICIAN_PASSWORD = "demo1234"

NOTES = {
    "P001": ("Patient reports persistent worry most days over the past three weeks, "
             "with difficulty controlling the worry, initial insomnia and muscle "
             "tension. Denies panic attacks. GAD-7 administered today."),
    "P002": ("Follow-up. Sleep improved on current regimen. Reports restlessness "
             "before examinations but functioning maintained. No avoidance behaviour."),
    "P003": ("Attended with parent. Describes episodes of palpitations and chest "
             "tightness lasting several minutes, with fear of losing control. "
             "Considering panic disorder as differential."),
    "P004": ("Initial assessment. Presents with low mood and reduced concentration. "
             "Anxiety symptoms not prominent at this visit."),
}

SUPPORT = [
    ("Reports excessive worry on more days than not for six months, with fatigue "
     "and irritability.", "anxiety", 40),
    ("Recurrent unexpected panic attacks with persistent concern about further "
     "attacks.", "anxiety", 180),
    ("Routine review. Mood euthymic, sleep adequate, no worry reported.", "control", 90),
    ("Post-operative follow-up, no psychiatric symptoms elicited.", "control", 300),
]


def behaviour_nodes(rng: random.Random) -> dict:
    slot = lambda: {f"f{i}": round(rng.uniform(0, 1), 3) for i in range(10)}
    return {"morning": slot(), "afternoon": slot(),
            "evening": slot(), "night": slot()}


def physio_features(rng: random.Random) -> dict:
    return {"hr_mean": round(rng.uniform(62, 96), 1),
            "hrv_rmssd": round(rng.uniform(18, 62), 1),
            "hrv_sdnn": round(rng.uniform(25, 80), 1),
            "resp_rate": round(rng.uniform(12, 21), 1),
            "resp_variability": round(rng.uniform(0.1, 0.9), 3),
            "skin_temp": round(rng.uniform(35.4, 37.1), 2),
            "accel_magnitude": round(rng.uniform(0.02, 0.6), 3),
            "accel_entropy": round(rng.uniform(0.2, 1.4), 3),
            "ecg_quality": round(rng.uniform(0.7, 0.99), 2),
            "rr_interval_mean": round(rng.uniform(620, 980), 1),
            "rr_interval_std": round(rng.uniform(20, 90), 1)}


async def main() -> None:
    init_db()
    db = SessionLocal()
    rng = random.Random(20260821)

    # ---- wipe (demo DB only) --------------------------------------------
    for table in (models.AuditLog, models.InterventionResult, models.FusionResult,
                  models.Reading, models.PhysioWindow, models.BehaviorDay,
                  models.EmaResponse, models.ClinicalNote, models.SupportExample,
                  models.PairingCode, models.SubjectAlias, models.Subject,
                  models.Clinician):
        db.execute(delete(table))
    db.commit()

    # ---- clinician -------------------------------------------------------
    clinician = models.Clinician(
        email=CLINICIAN_EMAIL, name="Dr Demo",
        password_hash=security.hash_password(CLINICIAN_PASSWORD))
    db.add(clinician)
    db.flush()

    # ---- site-level support set (shared across patients) -----------------
    now = utcnow()
    for text, label, days_ago in SUPPORT:
        db.add(models.SupportExample(subject_id=None, text=text, label=label,
                                     note_date=now - timedelta(days=days_ago)))
    db.commit()

    scenarios = {
        # ecg_quality < 0.60 makes C1 report poor_signal.
        # physio_age_min > FRESH_C1_SECONDS (15 min) makes C1 stale.
        "P001": {"behaviour_days": 42, "ecg_quality": 0.93, "physio_age_min": 1, "note": True},
        "P002": {"behaviour_days": 18, "ecg_quality": 0.88, "physio_age_min": 1, "note": True},
        "P003": {"behaviour_days": 42, "ecg_quality": 0.41, "physio_age_min": 1, "note": True},
        "P004": {"behaviour_days": 42, "ecg_quality": 0.91, "physio_age_min": 180, "note": True},
    }

    for label, plan in scenarios.items():
        mrn_hash = security.hash_mrn(f"MRN-{label}")
        sid = security.new_subject_id(mrn_hash)
        db.add(models.Subject(id=sid, mrn_hash=mrn_hash, display_label=label,
                              clinician_id=clinician.id))
        db.add(models.SubjectAlias(subject_id=sid, alias_type="mrn_hash",
                                   alias_value=mrn_hash))
        code = security.new_pairing_code()
        db.add(models.PairingCode(code=code, subject_id=sid))
        db.add(models.EmaResponse(subject_id=sid, instrument="gad7",
                                  score=rng.randint(3, 18), captured_at=now,
                                  payload={"source": "seed"}))
        db.commit()

        # behaviour history, inserted in bulk, then one C2 call over the window
        if plan["behaviour_days"]:
            for i in range(plan["behaviour_days"]):
                day = (now - timedelta(days=plan["behaviour_days"] - i)).date().isoformat()
                db.add(models.BehaviorDay(subject_id=sid, day=day,
                                          nodes=behaviour_nodes(rng)))
            db.commit()
            last = (now - timedelta(days=1)).date().isoformat()
            await orchestrator.ingest_behavior_day(db, sid, last, behaviour_nodes(rng))
            db.commit()

        # physiology — goes through the real ingestion path in every case
        window_end = now - timedelta(minutes=plan["physio_age_min"])
        features = physio_features(rng)
        features["ecg_quality"] = plan["ecg_quality"]
        await orchestrator.ingest_physiology(
            db, sid, window_end - timedelta(minutes=1), window_end, features)
        db.commit()

        # clinical note (this triggers TC-WPN, then fusion, then C3)
        if plan["note"]:
            note = models.ClinicalNote(
                subject_id=sid, note_text=NOTES[label],
                note_date=now - timedelta(hours=2), visit_count=rng.randint(1, 5),
                author_id=clinician.id)
            db.add(note)
            db.flush()
            await orchestrator.analyse_note(db, sid, note)
            db.commit()

        fusion = orchestrator.latest_fusion(db, sid)
        verdict = (f"composite {fusion.composite_score} ({fusion.band})"
                   if fusion and fusion.composite_score is not None
                   else f"no composite - {fusion.reason if fusion else 'no fusion run'}")
        print(f"  {label}  {sid}  pairing={code}  ->  {verdict}")

    db.close()
    print(f"\nClinician login: {CLINICIAN_EMAIL} / {CLINICIAN_PASSWORD}")


if __name__ == "__main__":
    print("Seeding demo data...")
    asyncio.run(main())

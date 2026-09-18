"""Executable contract checks for Handbook Phase 0.

This suite validates the frozen TARGET contract only. It does not pretend later
Phase 1-4 runtime behavior is already implemented.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from schemas.phase0_v1 import (
    AcknowledgeAttentionEventRequest,
    AssessmentStatus,
    AssessmentSummary,
    AttentionEvent,
    AttentionEventListResponse,
    AttentionEventResponse,
    AttentionEventStatus,
    AuthPrincipal,
    Band,
    ClinicianSubjectAssignmentContract,
    ForecastResultContract,
    ModalityId,
    PrincipalType,
    PROHIBITED_RESEARCH_CLAIMS,
    RESEARCH_SAFE_TERMINOLOGY,
    ResolveAttentionEventRequest,
)

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures" / "phase0"

passed = 0
failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def rejects(model, payload: dict) -> bool:
    try:
        model.model_validate(payload)
    except ValidationError:
        return True
    return False


print("=" * 74)
print("Phase 0 · Frozen contract validation")
print("=" * 74)

complete = AssessmentSummary.model_validate(load("assessment_complete.json"))
unavailable = AssessmentSummary.model_validate(load("assessment_unavailable.json"))
forecast = ForecastResultContract.model_validate(load("forecast_physiological.json"))
evt_open = AttentionEvent.model_validate(load("attention_event_open.json"))
evt_ack = AttentionEvent.model_validate(load("attention_event_acknowledged.json"))
evt_resolved = AttentionEvent.model_validate(load("attention_event_resolved.json"))

check("complete AssessmentSummary fixture validates",
      complete.assessment_status == AssessmentStatus.COMPLETE)
check("complete assessment references canonical fusion_result_id",
      complete.fusion_result_id == 123)
check("forecast is separate from current assessment",
      complete.forecast is not None and
      complete.forecast.forecast_result_id == forecast.forecast_result_id)
check("frozen forecast fixture is physiological, not mislabeled multimodal",
      forecast.scope.value == "physiological")
check("forecast validity window is forward",
      forecast.valid_until > forecast.generated_at)

check("unavailable AssessmentSummary fixture validates",
      unavailable.assessment_status == AssessmentStatus.UNAVAILABLE)
check("unavailable assessment exposes no score",
      unavailable.current_assessment.score is None)
check("unavailable assessment exposes no tier",
      unavailable.current_assessment.tier is None)
check("unavailable assessment uses GREY",
      unavailable.current_assessment.band == Band.GREY)

bad_unavailable = load("assessment_unavailable.json")
bad_unavailable["current_assessment"] = {
    "score": 0.0, "tier": "Low", "band": "GREEN"
}
check("missing/unavailable cannot silently become Low/Green",
      rejects(AssessmentSummary, bad_unavailable))

bad_partial = load("assessment_complete.json")
bad_partial["assessment_status"] = "partial"
bad_partial["current_assessment"]["score"] = None
check("partial assessment cannot omit authoritative current score",
      rejects(AssessmentSummary, bad_partial))

check("canonical modality ids are frozen exactly",
      {x.value for x in ModalityId} == {
          "c1_physiological", "c2_behavioral",
          "c3_clinical_nlp", "c4_demographic",
      })
c2 = next(m for m in complete.modalities
          if m.component_id == ModalityId.C2_BEHAVIORAL)
check("C2 fixture is experimental/not_validated",
      c2.status.value == "not_validated")
check("C2 fixture is excluded from authoritative fusion",
      c2.included_in_fusion is False and c2.score is None)

check("OPEN AttentionEvent fixture validates",
      evt_open.status == AttentionEventStatus.OPEN)
check("ACKNOWLEDGED AttentionEvent fixture validates",
      evt_ack.status == AttentionEventStatus.ACKNOWLEDGED)
check("RESOLVED AttentionEvent fixture validates",
      evt_resolved.status == AttentionEventStatus.RESOLVED)
check("event source linkage remains stable across lifecycle",
      evt_open.fusion_result_id == evt_ack.fusion_result_id == evt_resolved.fusion_result_id
      and evt_open.forecast_result_id == evt_ack.forecast_result_id == evt_resolved.forecast_result_id)
check("resolved event preserves acknowledgement before resolution",
      evt_resolved.created_at <= evt_resolved.acknowledged_at <= evt_resolved.resolved_at)

bad_open = load("attention_event_open.json")
bad_open["acknowledged_by"] = "DR_BAD"
bad_open["acknowledged_at"] = "2026-09-18T10:02:00Z"
check("OPEN event cannot carry acknowledgement actor/time",
      rejects(AttentionEvent, bad_open))

bad_ack = load("attention_event_acknowledged.json")
bad_ack["acknowledged_by"] = None
check("ACKNOWLEDGED requires server actor + timestamp",
      rejects(AttentionEvent, bad_ack))

bad_resolved = load("attention_event_resolved.json")
bad_resolved["resolved_at"] = "2026-09-18T10:02:00Z"
check("RESOLVED cannot precede acknowledgement",
      rejects(AttentionEvent, bad_resolved))

check("attention list envelope is frozen as {events:[...]}",
      len(AttentionEventListResponse(events=[evt_open]).events) == 1)
check("attention detail envelope is frozen as {event:{...}}",
      AttentionEventResponse(event=evt_open).event.id == evt_open.id)

check("ACK request body accepts empty object",
      AcknowledgeAttentionEventRequest.model_validate({}) is not None)
check("RESOLVE request body accepts empty object",
      ResolveAttentionEventRequest.model_validate({}) is not None)
check("ACK request rejects client-supplied actor",
      rejects(AcknowledgeAttentionEventRequest, {"acknowledged_by": "client"}))
check("RESOLVE request rejects client-supplied actor",
      rejects(ResolveAttentionEventRequest, {"resolved_by": "client"}))

clinician = AuthPrincipal.model_validate({
    "principal_type": "clinician",
    "principal_id": "auth-sub-dr-x",
    "role": "clinician",
    "clinician_id": "DR_X",
})
patient = AuthPrincipal.model_validate({
    "principal_type": "patient",
    "principal_id": "auth-sub-patient-a",
    "role": "patient",
    "subject_id": "11111111-1111-4111-8111-111111111111",
})
check("clinician principal requires explicit clinician binding",
      clinician.principal_type == PrincipalType.CLINICIAN
      and clinician.clinician_id == "DR_X")
check("patient principal requires canonical subject binding",
      patient.principal_type == PrincipalType.PATIENT
      and patient.subject_id is not None)
check("clinician principal without clinician_id is rejected",
      rejects(AuthPrincipal, {
          "principal_type": "clinician",
          "principal_id": "x",
          "role": "clinician",
      }))
check("patient principal without subject_id is rejected",
      rejects(AuthPrincipal, {
          "principal_type": "patient",
          "principal_id": "x",
          "role": "patient",
      }))

assignment = ClinicianSubjectAssignmentContract.model_validate({
    "clinician_id": "DR_X",
    "subject_id": "11111111-1111-4111-8111-111111111111",
    "active": True,
    "assigned_at": "2026-09-18T09:00:00Z",
    "ended_at": None,
})
check("assignment contract binds clinician to canonical subject",
      assignment.clinician_id == "DR_X" and assignment.active is True)

check("research-safe current assessment wording is frozen",
      RESEARCH_SAFE_TERMINOLOGY["current_assessment"] == "Current multimodal assessment")
check("TC-WPN wording remains a signal, not overall risk",
      RESEARCH_SAFE_TERMINOLOGY["c3_signal"] == "Clinical NLP / TC-WPN signal")
check("forecast wording is probabilistic/research-safe",
      "Potential escalation predicted" in RESEARCH_SAFE_TERMINOLOGY["forecast"])
check("prohibited exact-time claim remains explicitly prohibited",
      any("exactly 10 minutes" in x for x in PROHIBITED_RESEARCH_CLAIMS))

print()
print("=" * 74)
print(f"  {passed} passed, {failed} failed")
print("=" * 74)

raise SystemExit(1 if failed else 0)

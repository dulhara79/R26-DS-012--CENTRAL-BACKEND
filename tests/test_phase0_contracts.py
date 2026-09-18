from __future__ import annotations

import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.contracts.v1 import (
    AssessmentStatus,
    AssessmentSummary,
    AttentionEvent,
    AttentionEventStatus,
    ClinicianPrincipalClaims,
    ForecastScope,
    PatientPrincipalClaims,
    PatientSummary,
)

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"

def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

class Phase0ContractTests(unittest.TestCase):
    def test_complete_assessment_matches_frozen_clinanx_fixture(self) -> None:
        assessment = AssessmentSummary.model_validate(load_fixture("assessment_complete.json"))
        self.assertEqual(assessment.subject_id, "subject-001")
        self.assertEqual(assessment.fusion_result_id, 123)
        self.assertEqual(assessment.current_assessment.score, 0.58)
        self.assertEqual(assessment.assessment_status, AssessmentStatus.COMPLETE)
        self.assertIsNotNone(assessment.forecast)
        assert assessment.forecast is not None
        self.assertEqual(assessment.forecast.scope, ForecastScope.PHYSIOLOGICAL)
        self.assertEqual(assessment.forecast.score, 0.84)

    def test_partial_assessment_keeps_stale_c1_out_of_fusion(self) -> None:
        assessment = AssessmentSummary.model_validate(load_fixture("assessment_partial_stale_c1.json"))
        self.assertEqual(assessment.assessment_status, AssessmentStatus.PARTIAL)
        c1 = next(m for m in assessment.modalities if m.component_id == "c1_physiological")
        self.assertTrue(c1.available)
        self.assertFalse(c1.included_in_fusion)
        self.assertEqual(c1.status.value, "stale")
        self.assertIsNone(c1.contribution)

    def test_c2_remains_experimental_and_excluded(self) -> None:
        assessment = AssessmentSummary.model_validate(load_fixture("assessment_complete.json"))
        c2 = next(m for m in assessment.modalities if m.component_id == "c2_behavioral")
        self.assertFalse(c2.available)
        self.assertFalse(c2.included_in_fusion)
        self.assertEqual(c2.status.value, "not_validated")
        self.assertIsNone(c2.contribution)

    def test_unavailable_assessment_does_not_fabricate_low_or_zero(self) -> None:
        assessment = AssessmentSummary.model_validate(load_fixture("assessment_unavailable_c3.json"))
        self.assertEqual(assessment.assessment_status, AssessmentStatus.UNAVAILABLE)
        self.assertIsNone(assessment.current_assessment.score)
        self.assertIsNone(assessment.current_assessment.tier)
        c3 = next(m for m in assessment.modalities if m.component_id == "c3_clinical_nlp")
        self.assertIsNone(c3.score)
        self.assertFalse(c3.included_in_fusion)

    def test_unknown_vocabulary_is_rejected_by_backend_v1_contract(self) -> None:
        with self.assertRaises(ValidationError):
            AssessmentSummary.model_validate(load_fixture("assessment_unknown_enum.json"))

    def test_attention_event_lifecycle_fixtures_are_exact(self) -> None:
        open_event = AttentionEvent.model_validate(load_fixture("attention_event_open.json"))
        acknowledged = AttentionEvent.model_validate(load_fixture("attention_event_acknowledged.json"))
        resolved = AttentionEvent.model_validate(load_fixture("attention_event_resolved.json"))
        self.assertEqual(open_event.status, AttentionEventStatus.OPEN)
        self.assertEqual(acknowledged.status, AttentionEventStatus.ACKNOWLEDGED)
        self.assertEqual(resolved.status, AttentionEventStatus.RESOLVED)
        self.assertEqual(open_event.subject_id, "subject-001")
        self.assertEqual(open_event.fusion_result_id, 123)
        self.assertEqual(open_event.forecast_result_id, "fcst-001")

    def test_patient_summary_points_to_same_canonical_fusion_row(self) -> None:
        assessment = AssessmentSummary.model_validate(load_fixture("assessment_complete.json"))
        patient = PatientSummary.model_validate(load_fixture("patient_summary.json"))
        self.assertEqual(patient.subject_id, assessment.subject_id)
        self.assertEqual(patient.fusion_result_id, assessment.fusion_result_id)
        assert patient.current is not None
        self.assertEqual(patient.current.score, assessment.current_assessment.score)

    def test_clinician_principal_identity_is_derived_from_sub(self) -> None:
        principal = ClinicianPrincipalClaims.model_validate({
            "sub": "DR001", "role": "clinician", "exp": 1800000000,
            "iss": "test-issuer", "aud": "central-backend",
        })
        self.assertEqual(principal.clinician_id, "DR001")

    def test_patient_principal_is_bound_to_exactly_one_subject(self) -> None:
        principal = PatientPrincipalClaims.model_validate({
            "sub": "patient-session-001", "role": "patient", "subject_id": "subject-001",
            "exp": 1800000000, "iss": "test-issuer", "aud": "central-backend",
        })
        self.assertEqual(principal.subject_id, "subject-001")

if __name__ == "__main__":
    unittest.main()

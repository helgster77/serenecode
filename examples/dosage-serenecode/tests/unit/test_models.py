"""Tests for domain models and their invariants.

Verifies: REQ-001, REQ-002, REQ-003, REQ-004, REQ-005, REQ-025
"""

import icontract
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dosage.core.models import (
    ContraindicationResult,
    DoseResult,
    Drug,
    Patient,
    SafetyResult,
)


# -- Patient (REQ-001, REQ-025) --


def test_patient_valid_construction() -> None:
    """Create a patient with valid attributes.

    Verifies: REQ-001
    """
    p = Patient(
        weight_kg=70.0,
        age_years=30.0,
        creatinine_clearance=100.0,
        current_medications=["aspirin"],
    )
    assert p.weight_kg == 70.0
    assert p.age_years == 30.0
    assert p.creatinine_clearance == 100.0
    assert p.current_medications == ["aspirin"]


def test_patient_zero_weight_rejected() -> None:
    """Weight of zero is rejected.

    Verifies: REQ-025
    """
    with pytest.raises(icontract.ViolationError):
        Patient(
            weight_kg=0.0,
            age_years=30.0,
            creatinine_clearance=100.0,
            current_medications=[],
        )


def test_patient_negative_weight_rejected() -> None:
    """Negative weight is rejected.

    Verifies: REQ-025
    """
    with pytest.raises(icontract.ViolationError):
        Patient(
            weight_kg=-1.0,
            age_years=30.0,
            creatinine_clearance=100.0,
            current_medications=[],
        )


def test_patient_weight_above_max_rejected() -> None:
    """Weight above 300 kg is rejected.

    Verifies: REQ-001
    """
    with pytest.raises(icontract.ViolationError):
        Patient(
            weight_kg=301.0,
            age_years=30.0,
            creatinine_clearance=100.0,
            current_medications=[],
        )


def test_patient_negative_age_rejected() -> None:
    """Negative age is rejected.

    Verifies: REQ-025
    """
    with pytest.raises(icontract.ViolationError):
        Patient(
            weight_kg=70.0,
            age_years=-1.0,
            creatinine_clearance=100.0,
            current_medications=[],
        )


def test_patient_zero_age_accepted() -> None:
    """Zero age (neonate) is valid.

    Verifies: REQ-001
    """
    p = Patient(
        weight_kg=3.5,
        age_years=0.0,
        creatinine_clearance=50.0,
        current_medications=[],
    )
    assert p.age_years == 0.0


def test_patient_age_above_max_rejected() -> None:
    """Age above 150 is rejected.

    Verifies: REQ-001
    """
    with pytest.raises(icontract.ViolationError):
        Patient(
            weight_kg=70.0,
            age_years=151.0,
            creatinine_clearance=100.0,
            current_medications=[],
        )


def test_patient_zero_creatinine_rejected() -> None:
    """Zero creatinine clearance is rejected.

    Verifies: REQ-025
    """
    with pytest.raises(icontract.ViolationError):
        Patient(
            weight_kg=70.0,
            age_years=30.0,
            creatinine_clearance=0.0,
            current_medications=[],
        )


def test_patient_creatinine_above_max_rejected() -> None:
    """Creatinine clearance above 200 is rejected.

    Verifies: REQ-001
    """
    with pytest.raises(icontract.ViolationError):
        Patient(
            weight_kg=70.0,
            age_years=30.0,
            creatinine_clearance=201.0,
            current_medications=[],
        )


def test_patient_boundary_values() -> None:
    """Boundary values at limits are accepted.

    Verifies: REQ-001
    """
    p = Patient(
        weight_kg=300.0,
        age_years=150.0,
        creatinine_clearance=200.0,
        current_medications=[],
    )
    assert p.weight_kg == 300.0
    assert p.age_years == 150.0
    assert p.creatinine_clearance == 200.0


@given(
    weight=st.floats(min_value=0.01, max_value=300.0, allow_nan=False),
    age=st.floats(min_value=0.0, max_value=150.0, allow_nan=False),
    crcl=st.floats(min_value=0.01, max_value=200.0, allow_nan=False),
)
@settings(max_examples=200, deadline=None)
def test_patient_valid_ranges_satisfy_invariants(
    weight: float, age: float, crcl: float
) -> None:
    """Valid ranges always produce a valid Patient.

    Verifies: REQ-001
    """
    p = Patient(
        weight_kg=weight,
        age_years=age,
        creatinine_clearance=crcl,
        current_medications=[],
    )
    assert p.weight_kg == weight


# -- Drug (REQ-002, REQ-025) --


def _make_drug(**overrides: object) -> Drug:
    """Build a Drug with sensible defaults, overriding specified fields."""
    defaults: dict[str, object] = dict(
        drug_id="ibuprofen",
        dose_per_kg=10.0,
        concentration_mg_per_ml=20.0,
        max_single_dose_mg=400.0,
        max_daily_dose_mg=1200.0,
        doses_per_day=3,
        contraindicated_with=set(),
    )
    defaults.update(overrides)
    return Drug(**defaults)  # type: ignore[arg-type]


def test_drug_valid_construction() -> None:
    """Create a drug with valid attributes.

    Verifies: REQ-002
    """
    d = _make_drug()
    assert d.drug_id == "ibuprofen"
    assert d.doses_per_day == 3


def test_drug_empty_id_rejected() -> None:
    """Empty drug_id is rejected.

    Verifies: REQ-025
    """
    with pytest.raises(icontract.ViolationError):
        _make_drug(drug_id="")


def test_drug_zero_dose_per_kg_rejected() -> None:
    """Zero dose_per_kg is rejected.

    Verifies: REQ-025
    """
    with pytest.raises(icontract.ViolationError):
        _make_drug(dose_per_kg=0.0)


def test_drug_zero_concentration_rejected() -> None:
    """Zero concentration is rejected.

    Verifies: REQ-025
    """
    with pytest.raises(icontract.ViolationError):
        _make_drug(concentration_mg_per_ml=0.0)


def test_drug_zero_max_single_dose_rejected() -> None:
    """Zero max_single_dose_mg is rejected.

    Verifies: REQ-025
    """
    with pytest.raises(icontract.ViolationError):
        _make_drug(max_single_dose_mg=0.0, max_daily_dose_mg=0.0)


def test_drug_daily_less_than_single_rejected() -> None:
    """max_daily_dose_mg < max_single_dose_mg is rejected.

    Verifies: REQ-025
    """
    with pytest.raises(icontract.ViolationError):
        _make_drug(max_single_dose_mg=500.0, max_daily_dose_mg=400.0)


def test_drug_zero_doses_per_day_rejected() -> None:
    """Zero doses_per_day is rejected.

    Verifies: REQ-025
    """
    with pytest.raises(icontract.ViolationError):
        _make_drug(doses_per_day=0)


def test_drug_daily_equals_single_accepted() -> None:
    """max_daily_dose_mg == max_single_dose_mg is valid (once-daily drug).

    Verifies: REQ-002
    """
    d = _make_drug(
        max_single_dose_mg=400.0, max_daily_dose_mg=400.0, doses_per_day=1
    )
    assert d.max_daily_dose_mg == d.max_single_dose_mg


# -- DoseResult (REQ-003) --


def test_dose_result_valid() -> None:
    """Create a valid DoseResult.

    Verifies: REQ-003
    """
    r = DoseResult(dose_mg=200.0, volume_ml=10.0, was_capped=False)
    assert r.dose_mg == 200.0
    assert r.volume_ml == 10.0
    assert r.was_capped is False


def test_dose_result_zero_dose_rejected() -> None:
    """Zero dose_mg is rejected.

    Verifies: REQ-003
    """
    with pytest.raises(icontract.ViolationError):
        DoseResult(dose_mg=0.0, volume_ml=10.0, was_capped=False)


def test_dose_result_zero_volume_rejected() -> None:
    """Zero volume_ml is rejected.

    Verifies: REQ-003
    """
    with pytest.raises(icontract.ViolationError):
        DoseResult(dose_mg=100.0, volume_ml=0.0, was_capped=False)


# -- SafetyResult (REQ-004) --


def test_safety_result_valid_safe() -> None:
    """Create a valid safe SafetyResult.

    Verifies: REQ-004
    """
    r = SafetyResult(
        daily_total_mg=600.0,
        max_daily_mg=1200.0,
        is_safe=True,
        utilization_pct=50.0,
    )
    assert r.is_safe is True
    assert r.utilization_pct == 50.0


def test_safety_result_valid_unsafe() -> None:
    """Create a valid unsafe SafetyResult.

    Verifies: REQ-004
    """
    r = SafetyResult(
        daily_total_mg=1500.0,
        max_daily_mg=1200.0,
        is_safe=False,
        utilization_pct=125.0,
    )
    assert r.is_safe is False


def test_safety_result_negative_utilization_rejected() -> None:
    """Negative utilization_pct is rejected.

    Verifies: REQ-004
    """
    with pytest.raises(icontract.ViolationError):
        SafetyResult(
            daily_total_mg=100.0,
            max_daily_mg=1200.0,
            is_safe=True,
            utilization_pct=-1.0,
        )


# -- ContraindicationResult (REQ-005) --


def test_contraindication_result_safe() -> None:
    """Create a safe ContraindicationResult with no conflicts.

    Verifies: REQ-005
    """
    r = ContraindicationResult(is_safe=True, conflicts=[])
    assert r.is_safe is True
    assert r.conflicts == []


def test_contraindication_result_unsafe() -> None:
    """Create an unsafe ContraindicationResult with conflicts.

    Verifies: REQ-005
    """
    r = ContraindicationResult(is_safe=False, conflicts=["warfarin"])
    assert r.is_safe is False
    assert r.conflicts == ["warfarin"]


def test_contraindication_result_inconsistent_safe_rejected() -> None:
    """is_safe=True with non-empty conflicts is rejected.

    Verifies: REQ-005
    """
    with pytest.raises(icontract.ViolationError):
        ContraindicationResult(is_safe=True, conflicts=["warfarin"])


def test_contraindication_result_inconsistent_unsafe_rejected() -> None:
    """is_safe=False with empty conflicts is rejected.

    Verifies: REQ-005
    """
    with pytest.raises(icontract.ViolationError):
        ContraindicationResult(is_safe=False, conflicts=[])

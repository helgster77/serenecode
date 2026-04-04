"""Tests for dose calculation and renal adjustment functions.

Verifies: REQ-006, REQ-007, REQ-008, REQ-009, REQ-010,
          REQ-011, REQ-012, REQ-013, REQ-014, REQ-015, REQ-016
"""

import icontract
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dosage.core.dosage import adjust_for_renal_function, calculate_dose
from dosage.core.models import Drug, Patient


def _make_patient(**overrides: object) -> Patient:
    """Build a Patient with sensible defaults."""
    defaults: dict[str, object] = dict(
        weight_kg=70.0,
        age_years=30.0,
        creatinine_clearance=100.0,
        current_medications=[],
    )
    defaults.update(overrides)
    return Patient(**defaults)  # type: ignore[arg-type]


def _make_drug(**overrides: object) -> Drug:
    """Build a Drug with sensible defaults."""
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


# -- calculate_dose (REQ-006, REQ-007, REQ-008, REQ-009, REQ-010) --


def test_calculate_dose_basic() -> None:
    """Basic dose calculation without capping.

    Verifies: REQ-006, REQ-008
    """
    patient = _make_patient(weight_kg=20.0)
    drug = _make_drug(dose_per_kg=10.0, concentration_mg_per_ml=20.0)
    result = calculate_dose(patient, drug)
    assert result.dose_mg == 200.0
    assert result.volume_ml == 10.0
    assert result.was_capped is False


def test_calculate_dose_capped() -> None:
    """Dose is capped at max_single_dose_mg when raw dose exceeds it.

    Verifies: REQ-007, REQ-010
    """
    patient = _make_patient(weight_kg=100.0)
    drug = _make_drug(dose_per_kg=10.0, max_single_dose_mg=400.0)
    result = calculate_dose(patient, drug)
    assert result.dose_mg == 400.0
    assert result.was_capped is True


def test_calculate_dose_exactly_at_max_not_capped() -> None:
    """When raw dose equals max, was_capped is False.

    Verifies: REQ-010
    """
    patient = _make_patient(weight_kg=40.0)
    drug = _make_drug(dose_per_kg=10.0, max_single_dose_mg=400.0)
    result = calculate_dose(patient, drug)
    assert result.dose_mg == 400.0
    assert result.was_capped is False


def test_calculate_dose_just_above_max_capped() -> None:
    """When raw dose barely exceeds max, was_capped is True.

    Verifies: REQ-007, REQ-010
    """
    patient = _make_patient(weight_kg=40.1)
    drug = _make_drug(dose_per_kg=10.0, max_single_dose_mg=400.0)
    result = calculate_dose(patient, drug)
    assert result.dose_mg == 400.0
    assert result.was_capped is True


def test_calculate_dose_volume_positive() -> None:
    """Volume is always positive.

    Verifies: REQ-008, REQ-009
    """
    patient = _make_patient(weight_kg=0.5)
    drug = _make_drug(dose_per_kg=1.0, concentration_mg_per_ml=100.0)
    result = calculate_dose(patient, drug)
    assert result.volume_ml > 0


def test_calculate_dose_small_patient() -> None:
    """Dose calculation for a very small patient (neonate).

    Verifies: REQ-006, REQ-009
    """
    patient = _make_patient(weight_kg=0.5, age_years=0.0, creatinine_clearance=10.0)
    drug = _make_drug(dose_per_kg=5.0, max_single_dose_mg=400.0)
    result = calculate_dose(patient, drug)
    assert result.dose_mg == 2.5
    assert result.was_capped is False


@given(
    weight=st.floats(min_value=0.01, max_value=300.0, allow_nan=False),
    dose_per_kg=st.floats(min_value=0.01, max_value=100.0, allow_nan=False),
    max_single=st.floats(min_value=0.01, max_value=10000.0, allow_nan=False),
    concentration=st.floats(min_value=0.01, max_value=1000.0, allow_nan=False),
)
@settings(max_examples=200, deadline=None)
def test_calculate_dose_always_positive_and_bounded(
    weight: float, dose_per_kg: float, max_single: float, concentration: float
) -> None:
    """Dose is always positive and never exceeds max single dose.

    Verifies: REQ-009
    """
    patient = _make_patient(weight_kg=min(weight, 300.0))
    drug = _make_drug(
        dose_per_kg=dose_per_kg,
        max_single_dose_mg=max_single,
        max_daily_dose_mg=max(max_single, max_single) * 4,
        concentration_mg_per_ml=concentration,
    )
    result = calculate_dose(patient, drug)
    assert result.dose_mg > 0
    assert result.dose_mg <= drug.max_single_dose_mg
    assert result.volume_ml > 0


# -- adjust_for_renal_function (REQ-011 to REQ-016) --


def test_renal_normal() -> None:
    """Normal kidney function (CrCl >= 60) preserves dose.

    Verifies: REQ-011
    """
    assert adjust_for_renal_function(100.0, 90.0) == 100.0


def test_renal_exactly_60_is_normal() -> None:
    """CrCl of exactly 60 is Normal tier.

    Verifies: REQ-015
    """
    assert adjust_for_renal_function(100.0, 60.0) == 100.0


def test_renal_moderate() -> None:
    """Moderate impairment (30 <= CrCl < 60) gives 75%.

    Verifies: REQ-012
    """
    assert adjust_for_renal_function(100.0, 45.0) == 75.0


def test_renal_exactly_30_is_moderate() -> None:
    """CrCl of exactly 30 is Moderate tier.

    Verifies: REQ-015
    """
    assert adjust_for_renal_function(100.0, 30.0) == 75.0


def test_renal_severe() -> None:
    """Severe impairment (15 <= CrCl < 30) gives 50%.

    Verifies: REQ-013
    """
    assert adjust_for_renal_function(100.0, 20.0) == 50.0


def test_renal_exactly_15_is_severe() -> None:
    """CrCl of exactly 15 is Severe tier.

    Verifies: REQ-015
    """
    assert adjust_for_renal_function(100.0, 15.0) == 50.0


def test_renal_critical() -> None:
    """Critical impairment (CrCl < 15) gives 25%.

    Verifies: REQ-014
    """
    assert adjust_for_renal_function(100.0, 10.0) == 25.0


def test_renal_very_low_crcl() -> None:
    """Very low CrCl still gives 25%.

    Verifies: REQ-014
    """
    assert adjust_for_renal_function(100.0, 1.0) == 25.0


def test_renal_zero_dose_rejected() -> None:
    """Zero dose is rejected.

    Verifies: REQ-016
    """
    with pytest.raises(icontract.ViolationError):
        adjust_for_renal_function(0.0, 60.0)


def test_renal_zero_crcl_rejected() -> None:
    """Zero creatinine clearance is rejected.

    Verifies: REQ-016
    """
    with pytest.raises(icontract.ViolationError):
        adjust_for_renal_function(100.0, 0.0)


def test_renal_negative_dose_rejected() -> None:
    """Negative dose is rejected.

    Verifies: REQ-016
    """
    with pytest.raises(icontract.ViolationError):
        adjust_for_renal_function(-10.0, 60.0)


@given(
    dose=st.floats(min_value=0.01, max_value=10000.0, allow_nan=False),
    crcl=st.floats(min_value=0.01, max_value=200.0, allow_nan=False),
)
@settings(max_examples=200, deadline=None)
def test_renal_adjustment_never_increases_dose(dose: float, crcl: float) -> None:
    """Adjusted dose is always positive and never exceeds input.

    Verifies: REQ-016
    """
    result = adjust_for_renal_function(dose, crcl)
    assert result > 0
    assert result <= dose


@given(
    dose=st.floats(min_value=0.01, max_value=10000.0, allow_nan=False),
    crcl=st.floats(min_value=60.0, max_value=200.0, allow_nan=False),
)
@settings(max_examples=200, deadline=None)
def test_renal_normal_range_preserves_dose(dose: float, crcl: float) -> None:
    """Normal CrCl always returns the original dose unchanged.

    Verifies: REQ-011
    """
    assert adjust_for_renal_function(dose, crcl) == dose

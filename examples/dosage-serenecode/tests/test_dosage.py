"""Tests for dosage calculation functions."""

from decimal import Decimal

import icontract
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from src.core.dosage import (
    adjust_for_renal_function,
    calculate_dose,
    check_contraindications,
    check_daily_safety,
)
from src.core.models import DosageError, Drug, Patient
from tests.conftest import drugs, patients


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_drug(
    dose_per_kg: float = 10.0,
    concentration: float = 50.0,
    max_single: float = 500.0,
    max_daily: float = 1500.0,
    doses_per_day: int = 3,
    contras: set[str] | None = None,
) -> Drug:
    return Drug(
        drug_id="test",
        dose_per_kg=dose_per_kg,
        concentration_mg_per_ml=concentration,
        max_single_dose_mg=max_single,
        max_daily_dose_mg=max_daily,
        doses_per_day=doses_per_day,
        contraindicated_with=contras or set(),
    )


def _make_patient(
    weight_kg: float = 70.0,
    age_years: float = 30.0,
    creatinine_clearance: float = 90.0,
    meds: tuple[str, ...] | None = None,
) -> Patient:
    return Patient(
        weight_kg=weight_kg,
        age_years=age_years,
        creatinine_clearance=creatinine_clearance,
        current_medications=meds or (),
    )


# ---------------------------------------------------------------------------
# calculate_dose
# ---------------------------------------------------------------------------


class TestCalculateDose:
    """Unit tests for calculate_dose."""

    def test_uncapped_dose(self) -> None:
        patient = _make_patient(weight_kg=10.0)
        drug = _make_drug(dose_per_kg=5.0, max_single=500.0, concentration=10.0)
        result = calculate_dose(patient, drug)
        assert result.dose_mg == 50.0
        assert result.volume_ml == 5.0
        assert result.was_capped is False

    def test_capped_dose(self) -> None:
        patient = _make_patient(weight_kg=200.0)
        drug = _make_drug(dose_per_kg=10.0, max_single=500.0, concentration=50.0)
        result = calculate_dose(patient, drug)
        assert result.dose_mg == 500.0
        assert result.volume_ml == 10.0
        assert result.was_capped is True

    def test_exact_boundary_not_capped(self) -> None:
        patient = _make_patient(weight_kg=50.0)
        drug = _make_drug(dose_per_kg=10.0, max_single=500.0, concentration=25.0)
        result = calculate_dose(patient, drug)
        assert result.dose_mg == 500.0
        assert result.was_capped is False

    def test_result_is_frozen(self) -> None:
        result = calculate_dose(_make_patient(), _make_drug())
        with pytest.raises(DosageError, match="frozen"):
            result.dose_mg = 0.0


# ---------------------------------------------------------------------------
# adjust_for_renal_function
# ---------------------------------------------------------------------------


class TestAdjustForRenalFunction:
    """Unit tests for the renal adjustment tiers."""

    @pytest.mark.parametrize(
        "crcl, expected_factor",
        [
            (90.0, 1.0),
            (60.0, 1.0),
            (59.9, 0.75),
            (30.0, 0.75),
            (29.9, 0.5),
            (15.0, 0.5),
            (14.9, 0.25),
            (1.0, 0.25),
        ],
    )
    def test_tier_factors(self, crcl: float, expected_factor: float) -> None:
        dose = 100.0
        assert adjust_for_renal_function(dose, crcl) == pytest.approx(
            dose * expected_factor
        )

    def test_high_crcl_no_change(self) -> None:
        assert adjust_for_renal_function(200.0, 120.0) == 200.0


# ---------------------------------------------------------------------------
# check_daily_safety
# ---------------------------------------------------------------------------


class TestCheckDailySafety:
    """Unit tests for check_daily_safety."""

    def test_safe_dose(self) -> None:
        drug = _make_drug(max_daily=1500.0, doses_per_day=3)
        result = check_daily_safety(100.0, drug)
        assert result.daily_total_mg == 300.0
        assert result.is_safe is True
        assert result.utilization_pct == pytest.approx(20.0)

    def test_unsafe_dose(self) -> None:
        drug = _make_drug(max_daily=1500.0, doses_per_day=3)
        result = check_daily_safety(600.0, drug)
        assert result.daily_total_mg == 1800.0
        assert result.is_safe is False

    def test_exact_boundary_safe(self) -> None:
        drug = _make_drug(max_daily=1500.0, doses_per_day=3)
        result = check_daily_safety(500.0, drug)
        assert result.daily_total_mg == 1500.0
        assert result.is_safe is True
        assert result.utilization_pct == pytest.approx(100.0)

    def test_decimal_precision(self) -> None:
        """Verify Decimal-based computation avoids float drift."""
        drug = _make_drug(max_daily=1000.0, doses_per_day=3)
        result = check_daily_safety(0.1, drug)
        expected = float(Decimal("0.1") * Decimal("3"))
        assert result.daily_total_mg == expected

    def test_result_is_frozen(self) -> None:
        result = check_daily_safety(100.0, _make_drug())
        with pytest.raises(DosageError, match="frozen"):
            result.is_safe = True


# ---------------------------------------------------------------------------
# check_contraindications
# ---------------------------------------------------------------------------


class TestCheckContraindications:
    """Unit tests for check_contraindications."""

    def test_no_conflicts(self) -> None:
        drug = _make_drug(contras={"penicillin"})
        result = check_contraindications(drug, ("aspirin",))
        assert result.is_safe is True
        assert result.conflicts == ()

    def test_with_conflicts(self) -> None:
        drug = _make_drug(contras={"aspirin", "ibuprofen"})
        result = check_contraindications(drug, ("aspirin", "paracetamol"))
        assert result.is_safe is False
        assert result.conflicts == ("aspirin",)

    def test_multiple_conflicts(self) -> None:
        drug = _make_drug(contras={"aspirin", "ibuprofen"})
        result = check_contraindications(drug, ("aspirin", "ibuprofen"))
        assert set(result.conflicts) == {"aspirin", "ibuprofen"}

    def test_empty_medications(self) -> None:
        drug = _make_drug(contras={"aspirin"})
        result = check_contraindications(drug, ())
        assert result.is_safe is True

    def test_empty_contraindications(self) -> None:
        drug = _make_drug(contras=set())
        result = check_contraindications(drug, ("aspirin",))
        assert result.is_safe is True

    def test_result_is_frozen(self) -> None:
        result = check_contraindications(_make_drug(), ())
        with pytest.raises(DosageError, match="frozen"):
            result.is_safe = False


# ---------------------------------------------------------------------------
# Hypothesis property-based tests
# ---------------------------------------------------------------------------


class TestCalculateDoseHypothesis:
    """Property-based tests for calculate_dose."""

    @given(patient=patients(), drug=drugs())
    def test_dose_never_exceeds_max(self, patient: Patient, drug: Drug) -> None:
        result = calculate_dose(patient, drug)
        assert result.dose_mg <= drug.max_single_dose_mg

    @given(patient=patients(), drug=drugs())
    def test_dose_is_positive(self, patient: Patient, drug: Drug) -> None:
        result = calculate_dose(patient, drug)
        assert result.dose_mg > 0
        assert result.volume_ml > 0

    @given(patient=patients(), drug=drugs())
    def test_was_capped_consistent(self, patient: Patient, drug: Drug) -> None:
        raw = patient.weight_kg * drug.dose_per_kg
        result = calculate_dose(patient, drug)
        assert result.was_capped == (raw > drug.max_single_dose_mg)

    @given(patient=patients(), drug=drugs())
    def test_dose_value_correct(self, patient: Patient, drug: Drug) -> None:
        raw = patient.weight_kg * drug.dose_per_kg
        result = calculate_dose(patient, drug)
        if result.was_capped:
            assert result.dose_mg == drug.max_single_dose_mg
        else:
            assert result.dose_mg == raw


class TestAdjustRenalHypothesis:
    """Property-based tests for adjust_for_renal_function."""

    @given(
        dose=st.floats(min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False),
        crcl=st.floats(min_value=0.01, max_value=200.0, allow_nan=False, allow_infinity=False),
    )
    def test_adjusted_never_exceeds_original(self, dose: float, crcl: float) -> None:
        adjusted = adjust_for_renal_function(dose, crcl)
        assert adjusted <= dose

    @given(
        dose=st.floats(min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False),
        crcl=st.floats(min_value=0.01, max_value=200.0, allow_nan=False, allow_infinity=False),
    )
    def test_adjusted_is_positive(self, dose: float, crcl: float) -> None:
        assert adjust_for_renal_function(dose, crcl) > 0

    @given(
        dose=st.floats(min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False),
        crcl=st.floats(min_value=60.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    )
    def test_no_reduction_above_60(self, dose: float, crcl: float) -> None:
        assert adjust_for_renal_function(dose, crcl) == dose

    @given(
        dose=st.floats(min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False),
        crcl=st.floats(min_value=0.01, max_value=59.99, allow_nan=False, allow_infinity=False),
    )
    def test_reduced_below_60(self, dose: float, crcl: float) -> None:
        assert adjust_for_renal_function(dose, crcl) < dose

    @given(
        dose=st.floats(min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False),
        low=st.floats(min_value=0.01, max_value=59.99, allow_nan=False, allow_infinity=False),
        high=st.floats(min_value=0.01, max_value=59.99, allow_nan=False, allow_infinity=False),
    )
    def test_monotonicity(self, dose: float, low: float, high: float) -> None:
        """Higher CrCl should give equal or higher adjusted dose."""
        assume(low <= high)
        assert adjust_for_renal_function(dose, low) <= adjust_for_renal_function(dose, high)


class TestCheckDailySafetyHypothesis:
    """Property-based tests for check_daily_safety."""

    @given(
        dose=st.floats(min_value=0.01, max_value=5000.0, allow_nan=False, allow_infinity=False),
        drug=drugs(),
    )
    def test_daily_total_uses_decimal(self, dose: float, drug: Drug) -> None:
        result = check_daily_safety(dose, drug)
        expected = float(Decimal(str(dose)) * Decimal(str(drug.doses_per_day)))
        assert result.daily_total_mg == expected

    @given(
        dose=st.floats(min_value=0.01, max_value=5000.0, allow_nan=False, allow_infinity=False),
        drug=drugs(),
    )
    def test_is_safe_consistent(self, dose: float, drug: Drug) -> None:
        result = check_daily_safety(dose, drug)
        assert result.is_safe == (result.daily_total_mg <= result.max_daily_mg)

    @given(
        dose=st.floats(min_value=0.01, max_value=5000.0, allow_nan=False, allow_infinity=False),
        drug=drugs(),
    )
    def test_utilization_non_negative(self, dose: float, drug: Drug) -> None:
        result = check_daily_safety(dose, drug)
        assert result.utilization_pct >= 0


class TestCheckContraindicationsHypothesis:
    """Property-based tests for check_contraindications."""

    @given(drug=drugs())
    def test_empty_meds_always_safe(self, drug: Drug) -> None:
        result = check_contraindications(drug, ())
        assert result.is_safe is True
        assert result.conflicts == ()

    @given(drug=drugs(), patient=patients())
    def test_conflicts_subset_of_both(self, drug: Drug, patient: Patient) -> None:
        result = check_contraindications(drug, patient.current_medications)
        for c in result.conflicts:
            assert c in drug.contraindicated_with
            assert c in patient.current_medications

    @given(drug=drugs(), patient=patients())
    def test_is_safe_consistent_with_conflicts(
        self, drug: Drug, patient: Patient
    ) -> None:
        result = check_contraindications(drug, patient.current_medications)
        assert result.is_safe == (len(result.conflicts) == 0)

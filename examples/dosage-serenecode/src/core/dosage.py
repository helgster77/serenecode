"""Medical dosage calculation functions."""

from decimal import Decimal

import icontract

from core.models import (
    ContraindicationResult,
    DoseResult,
    Drug,
    Patient,
    SafetyResult,
)


@icontract.require(
    lambda patient: patient.weight_kg > 0,
    "Patient weight must be positive",
)
@icontract.require(
    lambda drug: drug.dose_per_kg > 0,
    "Drug dose_per_kg must be positive",
)
@icontract.require(
    lambda drug: drug.concentration_mg_per_ml > 0,
    "Drug concentration must be positive",
)
@icontract.require(
    lambda drug: drug.max_single_dose_mg > 0,
    "Drug max single dose must be positive",
)
@icontract.ensure(
    lambda result: result.dose_mg > 0,
    "Resulting dose must be positive",
)
@icontract.ensure(
    lambda result, drug: result.dose_mg <= drug.max_single_dose_mg,
    "Resulting dose must not exceed max single dose",
)
@icontract.ensure(
    lambda result: result.volume_ml > 0,
    "Resulting volume must be positive",
)
@icontract.ensure(
    lambda result, patient, drug: (
        patient.weight_kg * drug.dose_per_kg <= drug.max_single_dose_mg
    )
    == (not result.was_capped),
    "was_capped must reflect whether dose was actually capped",
)
@icontract.ensure(
    lambda result, patient, drug: (
        result.dose_mg == patient.weight_kg * drug.dose_per_kg
        if not result.was_capped
        else result.dose_mg == drug.max_single_dose_mg
    ),
    "dose_mg must be raw dose if uncapped, or max_single_dose_mg if capped",
)
def calculate_dose(patient: Patient, drug: Drug) -> DoseResult:
    """Compute the dose for a given patient and drug.

    Raw dose is weight_kg * dose_per_kg, capped at max_single_dose_mg.
    Volume is capped_dose / concentration_mg_per_ml.
    """
    raw_dose: float = patient.weight_kg * drug.dose_per_kg
    was_capped: bool = raw_dose > drug.max_single_dose_mg
    dose_mg: float = drug.max_single_dose_mg if was_capped else raw_dose
    volume_ml: float = dose_mg / drug.concentration_mg_per_ml
    return DoseResult(dose_mg=dose_mg, volume_ml=volume_ml, was_capped=was_capped)


@icontract.require(
    lambda dose_mg: dose_mg > 0,
    "Dose must be positive",
)
@icontract.require(
    lambda creatinine_clearance: creatinine_clearance > 0,
    "Creatinine clearance must be positive",
)
@icontract.ensure(
    lambda result: result > 0,
    "Adjusted dose must be positive",
)
@icontract.ensure(
    lambda result, dose_mg: result <= dose_mg,
    "Adjusted dose must not exceed original dose",
)
@icontract.ensure(
    lambda result, dose_mg, creatinine_clearance: (
        result == dose_mg if creatinine_clearance >= 60 else True
    ),
    "Dose must be unchanged when CrCl >= 60",
)
@icontract.ensure(
    lambda result, dose_mg, creatinine_clearance: (
        result < dose_mg if creatinine_clearance < 60 and dose_mg > 0 else True
    ),
    "Dose must be reduced when CrCl < 60",
)
def adjust_for_renal_function(dose_mg: float, creatinine_clearance: float) -> float:
    """Adjust a dose based on the patient's kidney function.

    Tiered adjustment:
      CrCl >= 60: 100% (no change)
      CrCl >= 30 and < 60: 75%
      CrCl >= 15 and < 30: 50%
      CrCl < 15: 25%
    """
    if creatinine_clearance >= 60:
        factor: float = 1.0
    elif creatinine_clearance >= 30:
        factor = 0.75
    elif creatinine_clearance >= 15:
        factor = 0.5
    else:
        factor = 0.25

    return dose_mg * factor


@icontract.require(
    lambda dose_mg: dose_mg > 0,
    "Dose must be positive",
)
@icontract.require(
    lambda drug: drug.doses_per_day > 0,
    "Doses per day must be positive",
)
@icontract.require(
    lambda drug: drug.max_daily_dose_mg > 0,
    "Max daily dose must be positive",
)
@icontract.ensure(
    lambda result, dose_mg, drug: result.daily_total_mg
    == float(Decimal(str(dose_mg)) * Decimal(str(drug.doses_per_day))),
    "Daily total must equal dose_mg * doses_per_day exactly",
)
@icontract.ensure(
    lambda result: result.is_safe == (result.daily_total_mg <= result.max_daily_mg),
    "is_safe must be True iff daily total is within max",
)
@icontract.ensure(
    lambda result: result.utilization_pct >= 0,
    "Utilization must be non-negative",
)
@icontract.ensure(
    lambda result: not result.is_safe or result.utilization_pct <= 100.0,
    "If safe, utilization must be at most 100%",
)
def check_daily_safety(dose_mg: float, drug: Drug) -> SafetyResult:
    """Verify the prescribed dose does not exceed the max daily limit.

    daily_total = dose_mg * doses_per_day
    is_safe = daily_total <= max_daily_dose_mg
    """
    # Use Decimal throughout to avoid floating-point drift
    daily_total_dec: Decimal = Decimal(str(dose_mg)) * Decimal(str(drug.doses_per_day))
    max_daily_dec: Decimal = Decimal(str(drug.max_daily_dose_mg))
    daily_total_mg: float = float(daily_total_dec)
    max_daily_mg: float = drug.max_daily_dose_mg
    is_safe: bool = daily_total_mg <= max_daily_mg
    utilization_pct: float = float(
        (daily_total_dec / max_daily_dec) * Decimal("100")
    )

    return SafetyResult(
        daily_total_mg=daily_total_mg,
        max_daily_mg=max_daily_mg,
        is_safe=is_safe,
        utilization_pct=utilization_pct,
    )


@icontract.require(
    lambda drug: drug.drug_id,
    "Drug must have a non-empty ID",
)
@icontract.ensure(
    lambda result: result.is_safe == (len(result.conflicts) == 0),
    "is_safe must be True iff conflicts is empty",
)
@icontract.ensure(
    lambda result, drug, current_medications: all(
        c in drug.contraindicated_with and c in current_medications
        for c in result.conflicts
    ),
    "Every conflict must be in both contraindicated_with and current_medications",
)
@icontract.ensure(
    lambda result, current_medications: (
        result.is_safe if len(current_medications) == 0 else True
    ),
    "If current_medications is empty, result must be safe",
)
def check_contraindications(
    drug: Drug, current_medications: list[str]
) -> ContraindicationResult:
    """Check whether the drug is safe alongside the patient's current medications.

    For each medication in current_medications, check if it appears in
    drug.contraindicated_with. Collect all conflicts.
    """
    conflicts: list[str] = []

    # Loop invariant: conflicts contains all drugs from
    # current_medications[:i] that are in drug.contraindicated_with
    for i, med in enumerate(current_medications):
        assert all(
            c in drug.contraindicated_with for c in conflicts
        ), f"Loop invariant violated at iteration {i}: conflict not in contraindicated_with"

        if med in drug.contraindicated_with:
            conflicts.append(med)

    is_safe: bool = len(conflicts) == 0
    return ContraindicationResult(is_safe=is_safe, conflicts=conflicts)

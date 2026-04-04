"""Dose calculation and renal adjustment functions.

This is a core module — no I/O operations are permitted.
"""

import icontract

from dosage.core.models import DoseResult, Drug, Patient


@icontract.require(lambda patient: patient is not None, "patient must not be None")
@icontract.require(lambda drug: drug is not None, "drug must not be None")
@icontract.require(
    lambda drug: drug.concentration_mg_per_ml > 0,
    "drug concentration must be positive",
)
@icontract.ensure(
    lambda result, drug: result.dose_mg <= drug.max_single_dose_mg,
    "dose must not exceed max single dose",
)
@icontract.ensure(lambda result: result.dose_mg > 0, "dose must be positive")
@icontract.ensure(lambda result: result.volume_ml > 0, "volume must be positive")
@icontract.ensure(
    lambda patient, drug, result: (
        patient.weight_kg * drug.dose_per_kg <= drug.max_single_dose_mg
    )
    == (not result.was_capped),
    "capping flag must reflect whether raw dose exceeded max",
)
def calculate_dose(patient: Patient, drug: Drug) -> DoseResult:
    """Compute the dose for a given patient and drug.

    Calculates raw dose from weight, caps at the maximum single dose,
    and computes the volume to administer.

    Implements: REQ-006, REQ-007, REQ-008, REQ-009, REQ-010
    """
    raw_dose = patient.weight_kg * drug.dose_per_kg
    was_capped = raw_dose > drug.max_single_dose_mg
    dose_mg = drug.max_single_dose_mg if was_capped else raw_dose
    volume_ml = dose_mg / drug.concentration_mg_per_ml
    return DoseResult(dose_mg=dose_mg, volume_ml=volume_ml, was_capped=was_capped)


@icontract.require(
    lambda dose_mg: dose_mg > 0 and dose_mg * 0.25 > 0,
    "dose must be positive and large enough to survive renal adjustment",
)
@icontract.require(
    lambda creatinine_clearance: creatinine_clearance > 0,
    "creatinine clearance must be positive",
)
@icontract.ensure(lambda result: result > 0, "adjusted dose must be positive")
@icontract.ensure(
    lambda dose_mg, result: result <= dose_mg,
    "adjusted dose must not exceed original dose",
)
@icontract.ensure(
    lambda creatinine_clearance, dose_mg, result: (
        result == dose_mg if creatinine_clearance >= 60 else True
    ),
    "dose unchanged when kidney function is normal",
)
def adjust_for_renal_function(dose_mg: float, creatinine_clearance: float) -> float:
    """Adjust a dose based on the patient's kidney function.

    Impaired kidneys cannot clear drugs effectively, requiring dose reduction.
    Uses tiered adjustment based on creatinine clearance levels.

    Implements: REQ-011, REQ-012, REQ-013, REQ-014, REQ-015, REQ-016
    """
    # Invariant: factor is the renal adjustment multiplier for the given CrCl tier
    if creatinine_clearance >= 60:
        factor = 1.0
    elif creatinine_clearance >= 30:
        factor = 0.75
    elif creatinine_clearance >= 15:
        factor = 0.5
    else:
        factor = 0.25
    return dose_mg * factor

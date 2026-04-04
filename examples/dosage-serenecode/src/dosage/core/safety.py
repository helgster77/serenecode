"""Daily safety and contraindication check functions.

This is a core module — no I/O operations are permitted.
"""

import icontract

from dosage.core.models import ContraindicationResult, Drug, SafetyResult


@icontract.require(lambda dose_mg: dose_mg > 0, "dose must be positive")
@icontract.require(lambda drug: drug is not None, "drug must not be None")
@icontract.require(
    lambda drug: drug.max_daily_dose_mg > 0,
    "max daily dose must be positive",
)
@icontract.ensure(
    lambda dose_mg, drug, result: result.daily_total_mg == dose_mg * drug.doses_per_day,
    "daily total must equal dose times doses per day",
)
@icontract.ensure(
    lambda result: result.is_safe == (result.daily_total_mg <= result.max_daily_mg),
    "is_safe must reflect whether daily total is within limit",
)
@icontract.ensure(
    lambda result: result.utilization_pct >= 0,
    "utilization must be non-negative",
)
@icontract.ensure(
    lambda result: not result.is_safe or result.utilization_pct <= 100.0,
    "utilization must be <= 100% when safe",
)
def check_daily_safety(dose_mg: float, drug: Drug) -> SafetyResult:
    """Verify that a prescribed dose does not exceed the maximum daily limit.

    Computes total daily dose and compares against the drug's max daily dose.

    Implements: REQ-017, REQ-018, REQ-019
    """
    daily_total_mg = dose_mg * drug.doses_per_day
    max_daily_mg = drug.max_daily_dose_mg
    is_safe = daily_total_mg <= max_daily_mg
    utilization_pct = daily_total_mg / max_daily_mg * 100
    return SafetyResult(
        daily_total_mg=daily_total_mg,
        max_daily_mg=max_daily_mg,
        is_safe=is_safe,
        utilization_pct=utilization_pct,
    )


@icontract.require(lambda drug: drug is not None, "drug must not be None")
@icontract.require(
    lambda current_medications: current_medications is not None,
    "current_medications must not be None",
)
@icontract.ensure(
    lambda result: result.is_safe == (len(result.conflicts) == 0),
    "is_safe must be True iff no conflicts",
)
@icontract.ensure(
    lambda drug, current_medications, result: all(
        c in drug.contraindicated_with and c in current_medications
        for c in result.conflicts
    ),
    "every conflict must be in both contraindicated_with and current_medications",
)
def check_contraindications(
    drug: Drug, current_medications: list[str]
) -> ContraindicationResult:
    """Check whether a drug is safe to prescribe alongside current medications.

    Checks one direction only: whether the prescribed drug lists any of the
    current medications as contraindicated.

    Implements: REQ-020, REQ-021, REQ-022, REQ-023, REQ-024
    """
    conflicts: list[str] = []
    # Loop invariant: conflicts contains all medications checked so far that
    # appear in drug.contraindicated_with
    for med in current_medications:
        if med in drug.contraindicated_with:
            conflicts.append(med)
    is_safe = len(conflicts) == 0
    return ContraindicationResult(is_safe=is_safe, conflicts=conflicts)

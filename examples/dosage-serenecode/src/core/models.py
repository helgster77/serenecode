"""Domain models for the medical dosage calculator."""

import icontract


class _DosageErrorBase(Exception):
    """Base for DosageError that provides __setstate__ for icontract compatibility."""

    def __setstate__(self, state: dict[str, object] | None) -> None:
        """Restore state from pickling."""
        if state is not None:
            self.__dict__.update(state)


@icontract.invariant(
    lambda self: len(self.args) > 0,
    "DosageError must have a non-empty message",
)
class DosageError(_DosageErrorBase):
    """Domain-specific exception for dosage calculation errors."""

    @icontract.require(
        lambda message: len(message) > 0,
        "Error message must be non-empty",
    )
    @icontract.ensure(
        lambda self: len(self.args) > 0,
        "Exception must have at least one arg",
    )
    def __init__(self, message: str) -> None:
        """Initialize DosageError with a non-empty message."""
        super().__init__(message)


class _Frozen:
    """Mixin that prevents attribute modification after ``__init__`` completes.

    Subclasses must set ``self._frozen = True`` as the last line of ``__init__``.
    The invariant on ``_Frozen`` is omitted to avoid icontract ``__new__``
    wrapping conflicts with subclass invariants; immutability is enforced
    structurally by ``__setattr__``.
    """

    def __setattr__(self, name: str, value: object) -> None:
        """Set attribute only if the instance is not yet frozen."""
        if getattr(self, "_frozen", False):
            raise DosageError(
                f"Cannot modify attribute '{name}' on frozen {type(self).__name__}"
            )
        object.__setattr__(self, name, value)


@icontract.invariant(
    lambda self: self.dose_mg > 0,
    "Dose must be positive",
)
@icontract.invariant(
    lambda self: self.volume_ml > 0,
    "Volume must be positive",
)
class DoseResult(_Frozen):
    """Result of a dose calculation."""

    @icontract.require(
        lambda dose_mg: dose_mg > 0,
        "dose_mg must be positive",
    )
    @icontract.require(
        lambda volume_ml: volume_ml > 0,
        "volume_ml must be positive",
    )
    @icontract.ensure(
        lambda self: self.dose_mg > 0 and self.volume_ml > 0,
        "Fields must be set to valid positive values",
    )
    def __init__(self, dose_mg: float, volume_ml: float, was_capped: bool) -> None:
        """Initialize a DoseResult with dose, volume, and capping status."""
        self.dose_mg = dose_mg
        self.volume_ml = volume_ml
        self.was_capped = was_capped
        self._frozen = True


@icontract.invariant(
    lambda self: self.daily_total_mg > 0,
    "Daily total must be positive",
)
@icontract.invariant(
    lambda self: self.max_daily_mg > 0,
    "Max daily dose must be positive",
)
@icontract.invariant(
    lambda self: self.utilization_pct >= 0,
    "Utilization percentage must be non-negative",
)
@icontract.invariant(
    lambda self: not self.is_safe or self.utilization_pct <= 100.0,
    "If safe, utilization must be at most 100%",
)
@icontract.invariant(
    lambda self: self.is_safe == (self.daily_total_mg <= self.max_daily_mg),
    "is_safe must be True iff daily total is within max",
)
class SafetyResult(_Frozen):
    """Result of a daily safety check."""

    @icontract.require(
        lambda daily_total_mg: daily_total_mg > 0,
        "daily_total_mg must be positive",
    )
    @icontract.require(
        lambda max_daily_mg: max_daily_mg > 0,
        "max_daily_mg must be positive",
    )
    @icontract.require(
        lambda utilization_pct: utilization_pct >= 0,
        "utilization_pct must be non-negative",
    )
    @icontract.ensure(
        lambda self: self.daily_total_mg > 0 and self.max_daily_mg > 0,
        "Fields must be set to valid values",
    )
    def __init__(
        self,
        daily_total_mg: float,
        max_daily_mg: float,
        is_safe: bool,
        utilization_pct: float,
    ) -> None:
        """Initialize a SafetyResult with daily totals, limits, and safety status."""
        self.daily_total_mg = daily_total_mg
        self.max_daily_mg = max_daily_mg
        self.is_safe = is_safe
        self.utilization_pct = utilization_pct
        self._frozen = True


@icontract.invariant(
    lambda self: self.is_safe == (len(self.conflicts) == 0),
    "is_safe must be True iff conflicts is empty",
)
class ContraindicationResult(_Frozen):
    """Result of a contraindication check."""

    @icontract.require(
        lambda is_safe, conflicts: is_safe == (len(conflicts) == 0),
        "is_safe must be consistent with conflicts",
    )
    @icontract.ensure(
        lambda self: self.is_safe == (len(self.conflicts) == 0),
        "is_safe must be True iff conflicts is empty",
    )
    def __init__(self, is_safe: bool, conflicts: list[str]) -> None:
        """Initialize a ContraindicationResult with safety status and conflict list."""
        self.is_safe = is_safe
        self.conflicts = list(conflicts)
        self._frozen = True


@icontract.invariant(
    lambda self: self.weight_kg > 0,
    "Weight must be positive",
)
@icontract.invariant(
    lambda self: self.weight_kg <= 300,
    "Weight must be at most 300 kg",
)
@icontract.invariant(
    lambda self: self.age_years >= 0,
    "Age must be non-negative",
)
@icontract.invariant(
    lambda self: self.age_years <= 150,
    "Age must be at most 150",
)
@icontract.invariant(
    lambda self: self.creatinine_clearance > 0,
    "Creatinine clearance must be positive",
)
@icontract.invariant(
    lambda self: self.creatinine_clearance <= 200,
    "Creatinine clearance must be at most 200 mL/min",
)
class Patient(_Frozen):
    """A patient with weight, age, kidney function, and current medications."""

    @icontract.require(
        lambda weight_kg: 0 < weight_kg <= 300,
        "Weight must be between 0 (exclusive) and 300 kg",
    )
    @icontract.require(
        lambda age_years: 0 <= age_years <= 150,
        "Age must be between 0 and 150 years",
    )
    @icontract.require(
        lambda creatinine_clearance: 0 < creatinine_clearance <= 200,
        "Creatinine clearance must be between 0 (exclusive) and 200 mL/min",
    )
    @icontract.ensure(
        lambda self: self.weight_kg > 0 and self.creatinine_clearance > 0,
        "Patient fields must be set to valid values",
    )
    def __init__(
        self,
        weight_kg: float,
        age_years: float,
        creatinine_clearance: float,
        current_medications: list[str],
    ) -> None:
        """Initialize a Patient with weight, age, kidney function, and medications."""
        self.weight_kg = weight_kg
        self.age_years = age_years
        self.creatinine_clearance = creatinine_clearance
        self.current_medications = list(current_medications)
        self._frozen = True


@icontract.invariant(
    lambda self: len(self.drug_id) > 0,
    "Drug ID must be non-empty",
)
@icontract.invariant(
    lambda self: self.dose_per_kg > 0,
    "Dose per kg must be positive",
)
@icontract.invariant(
    lambda self: self.concentration_mg_per_ml > 0,
    "Concentration must be positive",
)
@icontract.invariant(
    lambda self: self.max_single_dose_mg > 0,
    "Max single dose must be positive",
)
@icontract.invariant(
    lambda self: self.max_daily_dose_mg > 0,
    "Max daily dose must be positive",
)
@icontract.invariant(
    lambda self: self.max_daily_dose_mg >= self.max_single_dose_mg,
    "Max daily dose must be >= max single dose",
)
@icontract.invariant(
    lambda self: self.doses_per_day > 0,
    "Doses per day must be positive",
)
@icontract.invariant(
    lambda self: isinstance(self.doses_per_day, int),
    "Doses per day must be an integer",
)
class Drug(_Frozen):
    """A drug with dosing parameters and contraindication information."""

    @icontract.require(
        lambda drug_id: len(drug_id) > 0,
        "Drug ID must be non-empty",
    )
    @icontract.require(
        lambda dose_per_kg: dose_per_kg > 0,
        "dose_per_kg must be positive",
    )
    @icontract.require(
        lambda concentration_mg_per_ml: concentration_mg_per_ml > 0,
        "concentration must be positive",
    )
    @icontract.require(
        lambda max_single_dose_mg: max_single_dose_mg > 0,
        "max_single_dose_mg must be positive",
    )
    @icontract.require(
        lambda max_daily_dose_mg: max_daily_dose_mg > 0,
        "max_daily_dose_mg must be positive",
    )
    @icontract.require(
        lambda max_daily_dose_mg, max_single_dose_mg: max_daily_dose_mg >= max_single_dose_mg,
        "max_daily_dose_mg must be >= max_single_dose_mg",
    )
    @icontract.require(
        lambda doses_per_day: isinstance(doses_per_day, int) and doses_per_day > 0,
        "doses_per_day must be a positive integer",
    )
    @icontract.ensure(
        lambda self: self.dose_per_kg > 0 and self.max_single_dose_mg > 0,
        "Drug fields must be set to valid values",
    )
    def __init__(
        self,
        drug_id: str,
        dose_per_kg: float,
        concentration_mg_per_ml: float,
        max_single_dose_mg: float,
        max_daily_dose_mg: float,
        doses_per_day: int,
        contraindicated_with: set[str],
    ) -> None:
        """Initialize a Drug with dosing parameters and contraindications."""
        self.drug_id = drug_id
        self.dose_per_kg = dose_per_kg
        self.concentration_mg_per_ml = concentration_mg_per_ml
        self.max_single_dose_mg = max_single_dose_mg
        self.max_daily_dose_mg = max_daily_dose_mg
        self.doses_per_day = doses_per_day
        self.contraindicated_with = frozenset(contraindicated_with)
        self._frozen = True

"""Shared Hypothesis strategies for dosage calculator tests."""

import hypothesis.strategies as st
from hypothesis import settings

from src.core.models import Drug, Patient

settings.register_profile("ci", max_examples=200)
settings.register_profile("default", max_examples=50)


@st.composite
def patients(draw: st.DrawFn) -> Patient:
    """Generate a valid Patient with randomised but in-range fields."""
    weight_kg = draw(
        st.floats(min_value=0.1, max_value=300.0, allow_nan=False, allow_infinity=False)
    )
    age_years = draw(
        st.floats(min_value=0.0, max_value=150.0, allow_nan=False, allow_infinity=False)
    )
    creatinine_clearance = draw(
        st.floats(min_value=0.1, max_value=200.0, allow_nan=False, allow_infinity=False)
    )
    meds = draw(
        st.lists(
            st.text(
                min_size=1,
                max_size=20,
                alphabet=st.characters(whitelist_categories=("L",)),
            ),
            max_size=5,
        )
    )
    return Patient(
        weight_kg=weight_kg,
        age_years=age_years,
        creatinine_clearance=creatinine_clearance,
        current_medications=tuple(meds),
    )


@st.composite
def drugs(draw: st.DrawFn) -> Drug:
    """Generate a valid Drug with randomised but in-range fields."""
    dose_per_kg = draw(
        st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False)
    )
    concentration = draw(
        st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False)
    )
    max_single = draw(
        st.floats(min_value=0.01, max_value=5000.0, allow_nan=False, allow_infinity=False)
    )
    max_daily_extra = draw(
        st.floats(min_value=0.0, max_value=45000.0, allow_nan=False, allow_infinity=False)
    )
    max_daily = max_single + max_daily_extra
    doses_per_day = draw(st.integers(min_value=1, max_value=10))
    drug_id = draw(
        st.text(
            min_size=1,
            max_size=20,
            alphabet=st.characters(whitelist_categories=("L",)),
        )
    )
    contras = draw(
        st.frozensets(
            st.text(
                min_size=1,
                max_size=20,
                alphabet=st.characters(whitelist_categories=("L",)),
            ),
            max_size=5,
        )
    )
    return Drug(
        drug_id=drug_id,
        dose_per_kg=dose_per_kg,
        concentration_mg_per_ml=concentration,
        max_single_dose_mg=max_single,
        max_daily_dose_mg=max_daily,
        doses_per_day=doses_per_day,
        contraindicated_with=set(contras),
    )

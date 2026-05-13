"""
SI units + dimensional analysis layer.

This is the foundation for v2.0's "rooted in physical principles" thesis: every
numeric column gets an inferred SI unit and dimension, every fitted physics
equation is checked for dimensional consistency, and any prior whose dimensions
don't balance is rejected with an explicit reason.

Public API
----------

    from fantasyai.aurora.units import (
        Dimension,           # frozen dataclass over SI base dimensions
        UnitGuess,           # result of inferring a unit from a column name
        infer_column_unit,   # parse one column → UnitGuess
        infer_columns,       # bulk parse with a confidence-aware report
        dimensions_balance,  # check that an equation's dim equation balances
    )

Design choice: hand-rolled, no external deps. Pint is the obvious next step
when compound-unit conversion gets real, but the heuristic system here covers
90% of real-world column naming conventions.
"""
from .dimensions import (
    Dimension, DIMENSIONLESS, MASS, LENGTH, TIME, TEMPERATURE, CURRENT,
    AMOUNT, LUMINOUS, dimension_from_unit_string,
)
from .inference import (
    UnitGuess, infer_column_unit, infer_columns, KNOWN_UNIT_PATTERNS,
)
from .matcher import dimensions_balance, dimensional_consistency_check

__all__ = [
    "Dimension", "DIMENSIONLESS", "MASS", "LENGTH", "TIME", "TEMPERATURE",
    "CURRENT", "AMOUNT", "LUMINOUS",
    "UnitGuess", "infer_column_unit", "infer_columns", "KNOWN_UNIT_PATTERNS",
    "dimensions_balance", "dimensional_consistency_check",
    "dimension_from_unit_string",
]

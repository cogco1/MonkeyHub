"""Compatibility facade for precedent-adoption contracts.

The canonical implementation lives in ``archive.archflow.research.adoption``.  This
module intentionally owns no implementation so existing imports preserve
class and function identity.
"""

from archive.archflow.research.adoption import (
    PrecedentAdoption,
    PrecedentError,
    PrecedentFact,
    compile_precedent_constraints,
)

__all__ = [
    "PrecedentAdoption",
    "PrecedentError",
    "PrecedentFact",
    "compile_precedent_constraints",
]

"""Compatibility facade for canonical material intent and assignment ledgers.

New code imports :mod:`archive.archflow.materials.ledger`.  This module preserves the
legacy path without defining parallel contracts or behavior.
"""

from archive.archflow.materials.ledger import (
    MaterialError,
    MaterialIntent,
    MaterialLedger,
    ledger_coverage,
    material_display_color,
)

__all__ = [
    "MaterialError",
    "MaterialIntent",
    "MaterialLedger",
    "ledger_coverage",
    "material_display_color",
]

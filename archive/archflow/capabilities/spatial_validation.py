"""Compatibility facade for the canonical spatial validation owner.

New code imports :mod:`archive.archflow.validation.spatial`.  This module preserves
the P084 import path without defining parallel classes, schemas, or behavior.
"""

from archive.archflow.validation.spatial import (
    AABB,
    HostRegion,
    OpeningClearRegion,
    SpatialCheckComparator,
    SpatialCheckKind,
    SpatialCheckStatus,
    SpatialElement,
    SpatialElementKind,
    SpatialValidationCheck,
    SpatialValidationError,
    SpatialValidationInput,
    SpatialValidationReceipt,
    SpatialValidationStatus,
    normalize_spatial_validation_input,
    spatial_validation_input_digest,
    validate_spatial_layout,
)

__all__ = [
    "AABB",
    "HostRegion",
    "OpeningClearRegion",
    "SpatialCheckComparator",
    "SpatialCheckKind",
    "SpatialCheckStatus",
    "SpatialElement",
    "SpatialElementKind",
    "SpatialValidationCheck",
    "SpatialValidationError",
    "SpatialValidationInput",
    "SpatialValidationReceipt",
    "SpatialValidationStatus",
    "normalize_spatial_validation_input",
    "spatial_validation_input_digest",
    "validate_spatial_layout",
]

"""Small, dependency-light contracts shared across ArchFlow domains."""

from archflow.contracts.branch import (
    branch_ref_from_dict,
    branch_ref_to_dict,
    require_exact_branch,
    require_same_branch,
)
from archflow.contracts.canonical import (
    CanonicalValueError,
    canonical_digest,
    canonical_json,
    canonical_json_bytes,
    require_sha256,
)

__all__ = [
    "CanonicalValueError",
    "branch_ref_from_dict",
    "branch_ref_to_dict",
    "canonical_digest",
    "canonical_json",
    "canonical_json_bytes",
    "require_exact_branch",
    "require_same_branch",
    "require_sha256",
]

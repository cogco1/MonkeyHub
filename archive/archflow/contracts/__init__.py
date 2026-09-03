"""Small, dependency-light contracts shared across ArchFlow domains."""

from archflow.contracts.authority import (
    DEFAULT_AUTHORITY_FIELDS,
    AuthorityContractError,
    no_authority,
)
from archive.archflow.contracts.branch import (
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
from archive.archflow.contracts.reading import read_key_diagnostics

__all__ = [
    "AuthorityContractError",
    "CanonicalValueError",
    "DEFAULT_AUTHORITY_FIELDS",
    "branch_ref_from_dict",
    "branch_ref_to_dict",
    "canonical_digest",
    "canonical_json",
    "canonical_json_bytes",
    "no_authority",
    "read_key_diagnostics",
    "require_exact_branch",
    "require_same_branch",
    "require_sha256",
]

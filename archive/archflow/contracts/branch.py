"""Frozen-lane compatibility surface for branch identity serialization.

The canonical implementation moved onto ``BranchRef`` in
``archflow.project.refs``; nothing on the spine imports this module any more.
It survives only because twenty-five frozen modules under ``archive/`` still
import this path, and the archive lane is not edited.
"""

from __future__ import annotations

from archflow.project.refs import (
    BranchRef,
    require_exact_branch,
    require_same_branch,
)


def branch_ref_to_dict(branch: BranchRef) -> dict[str, object]:
    return require_exact_branch(branch).to_dict()


def branch_ref_from_dict(value: object) -> BranchRef:
    return BranchRef.from_dict(value)


__all__ = [
    "branch_ref_from_dict",
    "branch_ref_to_dict",
    "require_exact_branch",
    "require_same_branch",
]

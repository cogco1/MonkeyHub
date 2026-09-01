"""Exact, persistence-neutral serialization for branch identity."""

from __future__ import annotations

from collections.abc import Mapping

from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef


def require_exact_branch(branch: object, field: str = "branch") -> BranchRef:
    """Require a branch whose run is bound to digested canonical base state."""

    if not isinstance(branch, BranchRef):
        raise TypeError(f"{field} must be a BranchRef")
    branch.run.base.require_digest()
    return branch


def branch_ref_to_dict(branch: BranchRef) -> dict[str, object]:
    branch = require_exact_branch(branch)
    base = branch.run.base
    return {
        "project_id": branch.run.project_id,
        "run_id": branch.run.run_id,
        "base": {
            "project_id": base.project_id,
            "version": base.version,
            "state_sha256": base.require_digest(),
        },
        "branch_id": branch.branch_id,
        "epoch": branch.epoch,
    }


def branch_ref_from_dict(value: object) -> BranchRef:
    if not isinstance(value, Mapping) or set(value) != {
        "project_id",
        "run_id",
        "base",
        "branch_id",
        "epoch",
    }:
        raise ValueError("branch schema drifted")
    base = value["base"]
    if not isinstance(base, Mapping) or set(base) != {
        "project_id",
        "version",
        "state_sha256",
    }:
        raise ValueError("branch base schema drifted")
    project_id = value["project_id"]
    if base["project_id"] != project_id:
        raise ValueError("branch and base belong to different projects")
    result = BranchRef(
        run=RunRef(
            project_id=project_id,
            run_id=value["run_id"],
            base=ProjectVersionRef(
                project_id=base["project_id"],
                version=base["version"],
                state_sha256=base["state_sha256"],
            ),
        ),
        branch_id=value["branch_id"],
        epoch=value["epoch"],
    )
    return require_exact_branch(result)


def require_same_branch(
    expected: BranchRef,
    actual: BranchRef,
    *,
    field: str,
) -> None:
    expected = require_exact_branch(expected, "expected branch")
    actual = require_exact_branch(actual, field)
    if actual != expected:
        raise ValueError(f"{field} crossed its exact branch")


__all__ = [
    "branch_ref_from_dict",
    "branch_ref_to_dict",
    "require_exact_branch",
    "require_same_branch",
]

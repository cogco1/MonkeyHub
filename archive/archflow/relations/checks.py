"""Stable identifiers shared by relation coverage control adapters."""

from __future__ import annotations

from archive.archflow.relations.coverage import SemanticKindRelationPolicy


RELATION_COVERAGE_CHECKER_ID = "architectural-relation-coverage-checker"


def relation_coverage_check_id(policy: SemanticKindRelationPolicy) -> str:
    if not isinstance(policy, SemanticKindRelationPolicy):
        raise TypeError("policy must be a SemanticKindRelationPolicy")
    return f"relation-coverage-{policy.policy_id}"


__all__ = [
    "RELATION_COVERAGE_CHECKER_ID",
    "relation_coverage_check_id",
]

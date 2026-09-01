"""Compatibility facade for canonical realized-relation discovery evidence.

New code imports :mod:`archflow.evidence.relation_discovery`.  This module
preserves the legacy path without defining requirements, closure, or a second
implementation.
"""

from archflow.evidence.relation_discovery import (
    CandidateRelation,
    RelationCoverageError,
    detect_program_edges,
    enumerate_candidate_relations,
    relation_coverage_ledger,
)

__all__ = [
    "CandidateRelation",
    "RelationCoverageError",
    "detect_program_edges",
    "enumerate_candidate_relations",
    "relation_coverage_ledger",
]

"""Compatibility facade for canonical and historical basis index APIs.

New branch-bound production code must import :mod:`archive.archflow.research.index`.
Historical unscoped readers live in
:mod:`archive.archflow.research.compat.unscoped_v1`.
"""

from archive.archflow.research.compat.unscoped_v1 import (
    BasisIndex,
    build_basis_index,
    decision_slug,
)
from archive.archflow.research.index import (
    BasisIndexError,
    BranchBasisIndex,
    BranchDecisionContext,
    BranchRAGProgress,
    BranchRAGProgressStatus,
    build_branch_basis_index,
    compile_branch_decision_context,
    compile_branch_rag_progress,
    compile_next_branch_queries,
    require_branch_frontier_matches,
)

__all__ = [
    "BasisIndex",
    "BasisIndexError",
    "BranchBasisIndex",
    "BranchDecisionContext",
    "BranchRAGProgress",
    "BranchRAGProgressStatus",
    "build_basis_index",
    "build_branch_basis_index",
    "compile_branch_decision_context",
    "compile_branch_rag_progress",
    "compile_next_branch_queries",
    "decision_slug",
    "require_branch_frontier_matches",
]

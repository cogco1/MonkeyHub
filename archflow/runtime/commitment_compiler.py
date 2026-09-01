"""Compatibility facade for canonical commitment compilation.

New code imports :mod:`archflow.compilers.commitments`.  This module preserves
the legacy runtime path without defining parallel contracts or behavior.
"""

from archflow.compilers.commitments import (
    CommitmentProposal,
    CommitmentReplacementRequiresRevision,
    CommitmentRevision,
    IntentCompilation,
    IntentCompilationStatus,
    IntentObservation,
    IntentOperator,
    IntentTerm,
    LockedCommitment,
    compile_intent,
    confirm_proposal,
    revise_locked_commitment,
)

__all__ = [
    "IntentOperator",
    "IntentCompilationStatus",
    "CommitmentReplacementRequiresRevision",
    "IntentTerm",
    "IntentObservation",
    "CommitmentProposal",
    "LockedCommitment",
    "CommitmentRevision",
    "IntentCompilation",
    "compile_intent",
    "confirm_proposal",
    "revise_locked_commitment",
]

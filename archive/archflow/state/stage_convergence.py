"""Compatibility facade for the control-owned convergence contracts.

The canonical implementation lives in :mod:`archive.archflow.control.convergence`.
This module intentionally contains no logic so existing imports retain object
identity while framework production code migrates to the control owner.
"""

from archive.archflow.control.convergence import (
    StageConvergenceError,
    StageConvergenceEvidence,
    StageConvergenceOutcome,
    StageConvergencePolicy,
    StageConvergencePotential,
    StageConvergenceReceipt,
    StageTransitionKind,
    StageTransitionRequest,
    evaluate_stage_convergence,
)

__all__ = [
    "StageConvergenceError",
    "StageConvergenceEvidence",
    "StageConvergenceOutcome",
    "StageConvergencePolicy",
    "StageConvergencePotential",
    "StageConvergenceReceipt",
    "StageTransitionKind",
    "StageTransitionRequest",
    "evaluate_stage_convergence",
]

"""One bounded Architect attempt from canonical state to a named outcome."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from archive.archflow.adapters.fake_voxel import FakeVoxelAdapter
from archive.archflow.commit.model import CommitReceipt
from archive.archflow.commit.committer import Committer
from archive.archflow.commit.store import InMemoryStateStore
from archive.archflow.evaluation.engine import ClaimCoverageEvaluator, Evaluator, evaluate_submission
from archive.archflow.evaluation.model import EvaluationObservation
from archive.archflow.runtime.fake_architect import FakeArchitect
from archflow.state.model import CanonicalState, GoalContract, Obligation, StateRef
from archflow.validation.engine import ArtifactPresentValidator, ObligationDischargeValidator, RequiredClaimsValidator, Validator, validate_submission
from archflow.validation.model import ValidationReceipt
from archive.archflow.workspace.manager import WorkspaceManager


class RunStatus(StrEnum):
    COMMITTED = "committed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RunOutcome:
    status: RunStatus
    before: StateRef
    after: StateRef
    validation: ValidationReceipt
    evaluations: tuple[EvaluationObservation, ...]
    commit: CommitReceipt | None
    workspace_id: str

    @property
    def complete(self) -> bool:
        return self.status is RunStatus.COMMITTED and self.commit is not None


WALKING_SKELETON_COMPATIBILITY_MUST = (
    "artifact.loadable",
    "use.requested",
    "size.within_target",
)


def initial_state(
    prompt: str,
    *,
    run_id: str | None = None,
    must: tuple[str, ...] = WALKING_SKELETON_COMPATIBILITY_MUST,
) -> CanonicalState:
    """Create compatibility state for fake boundary tests, never production."""

    goal = GoalContract(prompt=prompt, must=must)
    return CanonicalState(
        ref=StateRef(
            project_id=run_id or f"compat-project-{uuid4().hex[:12]}",
            version=0,
        ),
        goal=goal,
        open_obligations=tuple(
            Obligation(
                obligation_id=clause,
                statement=clause,
                source_ref="compatibility-goal-contract",
            )
            for clause in must
        ),
    )


def run_once(
    store: InMemoryStateStore,
    workspace_manager: WorkspaceManager,
    architect: FakeArchitect,
    *,
    validators: tuple[Validator, ...] | None = None,
    evaluators: tuple[Evaluator, ...] | None = None,
) -> RunOutcome:
    baseline = store.read()
    workspace = workspace_manager.fork(baseline)
    submission = architect.propose(baseline, workspace)
    chosen_validators = validators or (
        ArtifactPresentValidator(),
        RequiredClaimsValidator(),
        ObligationDischargeValidator(),
    )
    validation = validate_submission(baseline, submission, chosen_validators)
    if not validation.passed:
        return RunOutcome(
            status=RunStatus.REJECTED,
            before=baseline.ref,
            after=store.read().ref,
            validation=validation,
            evaluations=(),
            commit=None,
            workspace_id=workspace.workspace_id,
        )
    observations = evaluate_submission(
        baseline,
        submission,
        evaluators or (ClaimCoverageEvaluator(),),
    )
    receipt = Committer(store).commit(submission, validation, observations)
    return RunOutcome(
        status=RunStatus.COMMITTED,
        before=baseline.ref,
        after=store.read().ref,
        validation=validation,
        evaluations=observations,
        commit=receipt,
        workspace_id=workspace.workspace_id,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    parser.add_argument(
        "--workspace-root",
        type=Path,
        required=True,
        help=(
            "Explicit disposable or probe-owned workspace path; "
            "ArchFlow has no repository-level project-output default."
        ),
    )
    parser.add_argument("--reject", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state = initial_state(args.prompt)
    store = InMemoryStateStore(state)
    architect = FakeArchitect(
        adapter=FakeVoxelAdapter(),
        omit_claims=frozenset({"use.requested"}) if args.reject else frozenset(),
    )
    outcome = run_once(
        store,
        WorkspaceManager(args.workspace_root),
        architect,
    )
    print(
        json.dumps(
            {
                "status": outcome.status.value,
                "before": outcome.before.version,
                "after": outcome.after.version,
                "validation": outcome.validation.receipt_id,
                "findings": [
                    {"code": item.code, "message": item.message}
                    for item in outcome.validation.findings
                ],
                "commit": outcome.commit.receipt_id if outcome.commit else None,
                "complete": outcome.complete and not store.read().open_obligations,
                "workspace_id": outcome.workspace_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if outcome.status is RunStatus.COMMITTED else 2


if __name__ == "__main__":
    raise SystemExit(main())

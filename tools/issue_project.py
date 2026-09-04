"""Issue one run as the project's published design (ADR-007).

An issue (出图) is the act that moves a run from *shared* to *published*: the
run's stage must have closed SATISFIED, its seats must all have executed, and
it must have been developed against the version that is published now. This
command does nothing but check those and call the repository; every refusal
prints one line saying which of them failed.

Example::

    py -3.12 tools/issue_project.py \
      --project D:\\...\\villa-rotonda-reconstruction \
      --run runner-003 --decided-by kaiwen --note "stage 1 sign-off"

``decided_by`` and ``note`` are printed here and are not retained: the
``PromotionDecision@1`` the repository demands has a frozen key set, and adding
to it would be refused as schema drift. See ``archflow/project/issue.py``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from archflow.project.issue import IssueError, IssueReceipt, issue_run  # noqa: E402
from archflow.project.repository import (  # noqa: E402
    FilesystemProjectRepository,
    ProjectRepositoryError,
)


def issue_project(
    *,
    project_root: Path,
    run_id: str,
    decided_by: str,
    note: str,
) -> IssueReceipt:
    """Open the project and issue one run as its published design."""

    repository = FilesystemProjectRepository.open(project_root)
    return issue_run(
        repository,
        run_id=run_id,
        decided_by=decided_by,
        note=note,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Issue a run as the project's published design: one "
            "compare-and-swap from a satisfied stage closure."
        )
    )
    parser.add_argument(
        "--project",
        required=True,
        type=Path,
        help="the P036 project root to issue from",
    )
    parser.add_argument(
        "--run",
        required=True,
        help="the shared run whose closed stage this issue cites",
    )
    parser.add_argument(
        "--decided-by",
        required=True,
        help="who issued this design; printed, not retained",
    )
    parser.add_argument(
        "--note",
        default="",
        help="one line about this issue; printed, not retained",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = issue_project(
            project_root=args.project.resolve(),
            run_id=args.run,
            decided_by=args.decided_by,
            note=args.note,
        )
    except (IssueError, ProjectRepositoryError, OSError, ValueError) as exc:
        # The typed reason, not a traceback: whoever ran this needs to know
        # which of the three conditions of an issue was not met.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"published: issue {receipt.issue} (was issue {receipt.previous})")
    print(f"  project state  {receipt.state_sha256}")
    print(f"  run            {receipt.run_id}")
    print(f"  stage          {receipt.stage_id}")
    print(f"  closure        {receipt.closure_ref}")
    print(f"  decision       {receipt.decision_ref}")
    print(f"  decided by     {args.decided_by}")
    if args.note:
        print(f"  note           {args.note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

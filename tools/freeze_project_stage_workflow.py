"""Retain an authority-free project stage workflow without accepting a stage.

The command is intentionally small: it validates ``ProjectStageWorkflow@1``,
stores the exact payload through the P036 repository, and writes a freeze
receipt proving that nothing was issued.  It does not open Stage 0,
convert legacy runs into accepted evidence, or create geometry.

Example::

    python tools/freeze_project_stage_workflow.py \
      --project D:\\...\\villa-rotonda-reconstruction \
      --run workflow-001 --create-run \
      --workflow D:\\...\\inputs\\villa-rotonda-stage-workflow-v1.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from archflow.contracts.authority import (  # noqa: E402
    DEFAULT_AUTHORITY_FIELDS,
    no_authority,
)
from archflow.project.record_kinds import (  # noqa: E402
    PROJECT_STAGE_WORKFLOW,
    PROJECT_STAGE_WORKFLOW_FREEZE_RECEIPT,
)
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.state.stage_workflow import ProjectStageWorkflow  # noqa: E402


_AUTHORITY = DEFAULT_AUTHORITY_FIELDS


def freeze_workflow(
    *,
    project_root: Path,
    run_id: str,
    workflow_path: Path,
    create_run: bool,
) -> dict[str, object]:
    repository = FilesystemProjectRepository.open(project_root)
    workflow = ProjectStageWorkflow.from_dict(
        json.loads(workflow_path.read_text(encoding="utf-8"))
    )
    if workflow.project_id != repository.load_manifest().project_id:
        raise ValueError("workflow belongs to another project")
    before = repository.read_head()
    run_manifest = repository.layout.run(run_id).manifest
    if not run_manifest.exists():
        if not create_run:
            raise ValueError(
                "workflow run does not exist; pass --create-run explicitly"
            )
        run = repository.create_run(run_id, base=before)
    else:
        # Preserve typed repository-integrity failures.  A corrupt manifest is
        # not equivalent to an absent run and must never be silently replaced.
        run = repository.load_run(run_id)
    if run.base != before:
        raise ValueError("workflow run is not based on the published version")

    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    workflow_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=PROJECT_STAGE_WORKFLOW,
        payload=workflow.to_dict(),
    )
    receipt = {
        "schema": "ProjectStageWorkflowFreezeReceipt@1",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "base": {
            "version": run.base.version,
            "state_sha256": run.base.require_digest(),
        },
        "workflow_ref": workflow_ref.uri,
        "workflow_digest": workflow.workflow_digest,
        "stage_count": len(workflow.stages),
        "stage_status": "NOT_STARTED",
        "next_required_stage": workflow.stages[0].stage_id,
        "legacy_basis_is_acceptance": False,
        # Retained freeze receipts were written with this key and their
        # digests bind it (ADR-004), so it keeps its name; in words it says
        # that freezing issued nothing.
        "canonical_head_changed": False,
        **no_authority(_AUTHORITY),
    }
    receipt_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=PROJECT_STAGE_WORKFLOW_FREEZE_RECEIPT,
        payload=receipt,
    )
    repository.verify()
    after = repository.read_head()
    if after != before:
        raise RuntimeError("freezing a workflow issued a new published design")
    return {
        **receipt,
        "freeze_receipt_ref": receipt_ref.uri,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--run", required=True)
    parser.add_argument("--workflow", required=True, type=Path)
    parser.add_argument("--create-run", action="store_true")
    args = parser.parse_args()
    result = freeze_workflow(
        project_root=args.project.resolve(),
        run_id=args.run,
        workflow_path=args.workflow.resolve(),
        create_run=args.create_run,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

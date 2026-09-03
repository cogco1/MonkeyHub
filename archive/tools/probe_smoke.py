"""Run the current framework boundary against a data-only probe.

This is an explicit synthetic integration smoke, not a production design
runner. It contains no building-type routing or project answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archive.archflow.adapters.fake_voxel import FakeVoxelAdapter
from archive.archflow.commit.committer import Committer
from archive.archflow.commit.store import InMemoryStateStore
from archive.archflow.evaluation.engine import ClaimCoverageEvaluator, evaluate_submission
from archive.archflow.runtime.fake_architect import FakeArchitect
from archive.archflow.runtime.walking_skeleton import initial_state
from archflow.state.model import CanonicalState, StateRef
from archflow.submission.model import CandidateSubmission
from archflow.validation.engine import ArtifactPresentValidator, ObligationDischargeValidator, RequiredClaimsValidator, validate_submission
from archflow.validation.model import ValidationReceipt
from archive.archflow.workspace.manager import WorkspaceManager

PROBES_ROOT = (ROOT / "probes").resolve()
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_FORBIDDEN_CASE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".js",
    ".ps1",
    ".py",
    ".sh",
    ".ts",
}
_EXPECTED_INPUT_FIELDS = {
    "schema",
    "project_id",
    "prompt",
    "synthetic",
    "generation_authority",
    "supplied_constraints",
    "evidence_refs",
}


class ProbeSmokeError(ValueError):
    """The probe cannot be used by the bounded synthetic smoke."""


def run_probe_smoke(probe_root: Path, run_id: str) -> Path:
    root = probe_root.resolve()
    try:
        root.relative_to(PROBES_ROOT)
    except ValueError as exc:
        raise ProbeSmokeError("probe must live under the repository probes/") from exc
    if not root.is_dir():
        raise ProbeSmokeError("probe directory does not exist")
    if not _RUN_ID.fullmatch(run_id):
        raise ProbeSmokeError("run_id contains unsupported characters")
    _reject_case_executables(root)

    input_path = root / "input" / "request.json"
    encoded = input_path.read_bytes()
    request = _load_request(encoded)
    if request["project_id"] != root.name:
        raise ProbeSmokeError("project_id must match the probe directory")

    run_root = (root / "runs" / run_id).resolve()
    try:
        run_root.relative_to((root / "runs").resolve())
    except ValueError as exc:
        raise ProbeSmokeError("run path escapes the probe") from exc
    if run_root.exists():
        raise ProbeSmokeError("run already exists; records are append-only")
    run_root.mkdir(parents=True)

    state = initial_state(
        request["prompt"],
        run_id=str(request["project_id"]),
        must=("artifact.loadable", "use.requested"),
    )
    store = InMemoryStateStore(state)
    workspace = WorkspaceManager(run_root / "workspaces").fork(state)
    submission = FakeArchitect(FakeVoxelAdapter()).propose(state, workspace)
    validation = validate_submission(
        state,
        submission,
        (
            ArtifactPresentValidator(),
            RequiredClaimsValidator(),
            ObligationDischargeValidator(),
        ),
    )
    evaluations = ()
    commit = None
    status = "rejected"
    if validation.passed:
        evaluations = evaluate_submission(
            state,
            submission,
            (ClaimCoverageEvaluator(),),
        )
        commit = Committer(store).commit(
            submission,
            validation,
            evaluations,
        )
        status = "committed"
    final_state = store.read()
    artifacts = []
    artifact_uris: dict[str, str] = {}
    for artifact in final_state.artifacts:
        artifact_path = _file_uri_path(artifact.uri)
        try:
            artifact_path.relative_to(run_root)
        except ValueError as exc:
            raise ProbeSmokeError(
                "framework artifact escaped the probe run directory"
            ) from exc
        portable_uri = _project_artifact_uri(
            artifact_path,
            project_root=root,
            project_id=str(request["project_id"]),
        )
        artifact_uris[artifact.artifact_id] = portable_uri
        artifacts.append(
            {
                "artifact_id": artifact.artifact_id,
                "uri": portable_uri,
                "media_type": artifact.media_type,
                "sha256": artifact.sha256,
            }
        )

    record_refs = [
        _write_record(
            run_root / "candidate.json",
            _submission_json(submission),
            run_root,
        ),
        _write_record(
            run_root / "validation.json",
            _validation_json(validation),
            run_root,
        ),
        _write_record(
            run_root / "evaluations.json",
            {
                "schema": "ProbeEvaluationSet@1",
                "observations": [
                    {
                        "observation_id": item.observation_id,
                        "evaluator": item.evaluator,
                        "submission_id": item.submission_id,
                        "checked_state": _state_ref_json(item.checked_state),
                        "status": item.status.value,
                        "metrics": [
                            {
                                "name": metric.name,
                                "score": metric.score,
                                "rationale": metric.rationale,
                            }
                            for metric in item.metrics
                        ],
                        "notes": list(item.notes),
                        "uncertainty": item.uncertainty,
                    }
                    for item in evaluations
                ],
            },
            run_root,
        ),
        _write_record(
            run_root / "canonical-state.json",
            _canonical_state_json(final_state, artifact_uris),
            run_root,
        ),
    ]
    if commit is not None:
        record_refs.append(
            _write_record(
                run_root / "commit.json",
                {
                    "schema": "ProbeCommitReceipt@1",
                    "receipt_id": commit.receipt_id,
                    "submission_id": commit.submission_id,
                    "validation_receipt_id": commit.validation_receipt_id,
                    "from_state": _state_ref_json(commit.from_state),
                    "to_state": _state_ref_json(commit.to_state),
                    "evaluation_ids": list(commit.evaluation_ids),
                    "artifact_ids": list(commit.artifact_ids),
                },
                run_root,
            )
        )

    record = {
        "schema": "ProbeFrameworkSmokeRecord@1",
        "project_id": request["project_id"],
        "run_id": run_id,
        "input_ref": input_path.relative_to(root).as_posix(),
        "input_sha256": hashlib.sha256(encoded).hexdigest(),
        "synthetic": True,
        "generation_authority": False,
        "framework_boundary_smoke": True,
        "architectural_usability_proven": False,
        "status": status,
        "canonical_state": {
            "before": state.ref.version,
            "after": final_state.ref.version,
        },
        "validation_receipt_id": validation.receipt_id,
        "validation_passed": validation.passed,
        "evaluation_ids": [
            item.observation_id for item in evaluations
        ],
        "commit_receipt_id": (
            commit.receipt_id if commit is not None else None
        ),
        "workspace_id": workspace.workspace_id,
        "artifacts": artifacts,
        "records": record_refs,
        "modules_exercised": [
            "state",
            "workspace",
            "runtime",
            "adapters",
            "submission",
            "validation",
            "evaluation",
            "commit",
        ],
    }
    record_path = run_root / "framework-smoke.json"
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return record_path


def _write_record(
    path: Path,
    payload: dict[str, object],
    run_root: Path,
) -> dict[str, str]:
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.write_bytes(encoded)
    return {
        "path": path.relative_to(run_root).as_posix(),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _submission_json(submission: CandidateSubmission) -> dict[str, object]:
    return {
        "schema": "ProbeCandidateSubmission@1",
        "submission_id": submission.submission_id,
        "base": _state_ref_json(submission.base),
        "workspace_id": submission.workspace_id,
        "intent": submission.intent,
        "claims": [
            {
                "key": item.key,
                "value": item.value,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in submission.claims
        ],
        "evidence_refs": list(submission.evidence_refs),
        "unresolved": list(submission.unresolved),
        "delta": {
            "facts_add": [item.key for item in submission.delta.facts_add],
            "commitments_add": [
                item.commitment_id
                for item in submission.delta.commitments_add
            ],
            "obligations_discharge": list(
                submission.delta.obligations_discharge
            ),
            "obligations_add": [
                item.obligation_id
                for item in submission.delta.obligations_add
            ],
            "artifacts_add": [
                item.artifact_id for item in submission.delta.artifacts_add
            ],
        },
    }


def _validation_json(receipt: ValidationReceipt) -> dict[str, object]:
    return {
        "schema": "ProbeValidationReceipt@1",
        "receipt_id": receipt.receipt_id,
        "submission_id": receipt.submission_id,
        "checked_state": _state_ref_json(receipt.checked_state),
        "passed": receipt.passed,
        "findings": [
            {
                "code": item.code,
                "message": item.message,
                "severity": item.severity.value,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in receipt.findings
        ],
    }


def _canonical_state_json(
    state: CanonicalState,
    artifact_uris: dict[str, str],
) -> dict[str, object]:
    return {
        "schema": "ProbeCanonicalStateSnapshot@1",
        "ref": _state_ref_json(state.ref),
        "goal": {
            "prompt": state.goal.prompt,
            "must": list(state.goal.must),
            "prefer": list(state.goal.prefer),
            "forbid": list(state.goal.forbid),
        },
        "program": (
            json.loads(state.legacy_program_view.to_json())
            if state.legacy_program_view is not None
            else None
        ),
        "facts": [
            {
                "key": item.key,
                "value": item.value,
                "source_ref": item.source_ref,
            }
            for item in state.facts
        ],
        "commitments": [item.to_dict() for item in state.commitments],
        "open_obligations": [
            {
                "obligation_id": item.obligation_id,
                "statement": item.statement,
                "source_ref": item.source_ref,
            }
            for item in state.open_obligations
        ],
        "artifacts": [
            {
                "artifact_id": item.artifact_id,
                "uri": artifact_uris[item.artifact_id],
                "media_type": item.media_type,
                "sha256": item.sha256,
            }
            for item in state.artifacts
        ],
        "evaluation_refs": list(state.evaluation_refs),
    }


def _state_ref_json(state: StateRef) -> dict[str, object]:
    return {"run_id": state.run_id, "version": state.version}


def _load_request(encoded: bytes) -> dict[str, object]:
    try:
        payload = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeSmokeError("probe request JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise ProbeSmokeError("probe request must be an object")
    if set(payload) != _EXPECTED_INPUT_FIELDS:
        raise ProbeSmokeError("probe request fields drifted")
    if payload.get("schema") != "ProbeSmokeInput@1":
        raise ProbeSmokeError("unsupported probe request schema")
    for field in ("project_id", "prompt"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ProbeSmokeError(f"{field} must be non-empty text")
    if payload.get("synthetic") is not True:
        raise ProbeSmokeError("the fake smoke accepts only synthetic probes")
    for field in ("supplied_constraints", "evidence_refs"):
        if not isinstance(payload.get(field), list):
            raise ProbeSmokeError(f"{field} must be an array")
    return payload


def _reject_case_executables(root: Path) -> None:
    for path in root.rglob("*"):
        if (
            path.is_file()
            and path.suffix.lower() in _FORBIDDEN_CASE_SUFFIXES
        ):
            raise ProbeSmokeError(
                f"probe contains executable case logic: {path.name}"
            )


def _file_uri_path(uri: str) -> Path:
    prefix = "file:///"
    if not uri.startswith(prefix):
        raise ProbeSmokeError("smoke artifact must use a local file URI")
    return Path(unquote(uri[len(prefix) :])).resolve()


def _project_artifact_uri(
    path: Path,
    *,
    project_root: Path,
    project_id: str,
) -> str:
    try:
        relative = path.resolve().relative_to(project_root.resolve())
    except ValueError as exc:
        raise ProbeSmokeError(
            "smoke artifact escaped the project directory"
        ) from exc
    return (
        f"project://{quote(project_id, safe='')}/"
        f"{quote(relative.as_posix(), safe='/._-')}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("probe", type=Path)
    parser.add_argument(
        "--run-id",
        default=datetime.now(UTC).strftime("smoke-%Y%m%dT%H%M%SZ"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    record = run_probe_smoke(args.probe, args.run_id)
    print(record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

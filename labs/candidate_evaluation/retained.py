"""Read one caller-selected P036 StateRecord without choosing or changing state.

The record reference, expected run/base, expected content and context are inputs
saved by the caller before this read. A successful read proves retention, not
candidate acceptance or that the expected base is still the project's HEAD.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import PurePosixPath

from archflow.project.record_kinds import STATE_RECORD
from archflow.project.refs import ProjectRecordRef, RunRef
from archflow.project.repository import FilesystemProjectRepository, ProjectRepositoryError
from archflow.state.state_record import StateRecord

from .evaluator import EvaluationRequest, EvaluationResult, MassingEvaluator


class RetainedEvaluationUnavailable(ValueError):
    """No trustworthy supported retained input was available to evaluate.

    This is not an invalid architectural candidate or a zero measurement. The
    adapter cannot manufacture an observed StateRecord to fill a result.
    """


def load_retained_request(
    repository: FilesystemProjectRepository,
    record_ref: ProjectRecordRef,
    *,
    expected_run: RunRef,
    expected_content_digest: str,
    context_refs: tuple[str, ...],
) -> EvaluationRequest:
    """Use the P036 integrity reader and StateRecord parser, keeping expectations.

    Only a run's retained ``state-record`` is supported. Missing/corrupt records,
    unsupported schemas and disagreement with the actual run manifest are
    unavailable. A sound record disagreeing with the caller's expected binding
    is left to the evaluator's existing ``exact_binding`` invalid result.
    No CAD bounding box, current projection or newest-run lookup substitutes.
    """

    try:
        if record_ref.record_kind != STATE_RECORD:
            raise ValueError("input must name a retained state-record")
        record = StateRecord.from_dict(repository.load_json(record_ref))
        expected_parent = PurePosixPath("runs", record.run_id, "records")
        if record_ref.project_id != record.project_id or PurePosixPath(record_ref.relative_path).parent != expected_parent:
            raise ValueError("retained reference does not belong to the record's project and run")
        actual_run = repository.load_run(record.run_id)
        if record.base is not None and record.run_ref != actual_run:
            raise ValueError("retained state disagrees with its run manifest")
    except (ProjectRepositoryError, OSError, ValueError, TypeError, KeyError) as exc:
        raise RetainedEvaluationUnavailable(f"retained input unavailable: {exc}") from exc
    return EvaluationRequest(record, expected_run, expected_content_digest, context_refs)


def evaluate_retained(
    repository: FilesystemProjectRepository,
    record_ref: ProjectRecordRef,
    *,
    expected_run: RunRef,
    expected_content_digest: str,
    context_refs: tuple[str, ...],
    evaluator: MassingEvaluator,
) -> EvaluationResult:
    """Evaluate declared massing only, preserving its immutable source URI."""

    request = load_retained_request(
        repository, record_ref, expected_run=expected_run,
        expected_content_digest=expected_content_digest, context_refs=context_refs,
    )
    result = evaluator.evaluate(request)
    return replace(result, evidence_refs=tuple(dict.fromkeys((*result.evidence_refs, record_ref.uri))))

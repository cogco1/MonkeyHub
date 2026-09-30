"""Request admission and recovery observations for one project runtime.

Every mutation a project's Studio is asked for is admitted here first: its
request signature is saved under the Hub's own runtime root before it can be
dispatched, and its outcome is reconciled only against retained results. No
request is ever replayed, and no request body or project result is kept.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit
from uuid import UUID

from ..models import HubFailure
from .models import OperationRecord
from .worker_http import HttpResult
from .workers import project_key


_CANDIDATE_REQUEST = re.compile(
    r"^/api/(proposals/[^/]+/candidate|candidates/combine|capabilities/[^/]+/run|options/[^/]+/select|program)$"
)
_ACCEPT_REQUEST = re.compile(r"^/api/candidates/([^/]+)/accept$")
_PROPOSAL_CANDIDATE = re.compile(r"^/api/proposals/([^/]+)/candidate$")
_ACTIVE = {"queued", "planning", "validated", "executing", "committing"}
# Finished outcomes a person can read and dismiss. One that needs recovery joins
# them only when the Hub has no way to recover it; otherwise it stays until it is.
_ACKNOWLEDGEABLE = {"failed", "stale"}
# A validation (422) or conflict (409) answer that names nothing the Studio took
# on is a refusal: the caller reads it in the reply and nothing is left to
# recover (#404 F10). Answers that say a run already exists are not refusals.
_REFUSAL_STATUSES = {409, 422}
_RETAINED_CODES = {"OPERATION_RETAINED", "CANDIDATE_ALREADY_RETAINED"}


def _dismissible(record: OperationRecord) -> bool:
    return record.status in _ACKNOWLEDGEABLE or (record.status == "needs_recovery" and not record.recoverable)


@dataclass
class _Admission:
    record: OperationRecord
    signature: tuple[str, str, str]
    expected_stage: str | None = None
    branch_id: str = "main"
    accepting_candidate: bool = False
    response: HttpResult | None = None
    finished: threading.Event = field(default_factory=threading.Event)
    # Whether the latest retained read left what recovery needs to resolve this
    # request: the run it named with its runner receipt and, for an acceptance,
    # a branch head its commit can still land on. None until a read after this
    # Hub started or lost the reply.
    resolvable: bool | None = None

    @property
    def expects_stage(self) -> bool:
        """An acceptance that named the Stage it succeeds: the only one whose commit can be found."""
        return self.accepting_candidate and bool(self.expected_stage)


class OperationManager:
    """Request admission and recovery observations, never a project writer."""

    def __init__(self, project_id: str, *, journal_path: Path | None = None,
                 project_dir: str | None = None):
        self.project_id = project_id
        self.journal_path = journal_path
        self.project_dir = project_key(project_dir) if project_dir is not None else None
        if journal_path is not None and self.project_dir is None:
            raise ValueError("A durable operation manager requires its exact project directory.")
        self._lock = threading.RLock()
        self._operations: dict[str, _Admission] = {}
        self._retained: dict[str, OperationRecord] = {}
        # Dismissed notices by operation id, for admitted requests and observed
        # runs alike: when, and the status the person read.
        self._acknowledged: dict[str, tuple[str, str]] = {}
        self._restore()

    def _restore(self) -> None:
        if self.journal_path is None or not self.journal_path.exists():
            return
        try:
            saved = json.loads(self.journal_path.read_text(encoding="utf-8"))
            if saved["projectId"] != self.project_id or saved["projectDir"] != self.project_dir:
                raise ValueError("The operation journal belongs to another project binding.")
            for row in saved["operations"]:
                record = OperationRecord.model_validate(row["record"])
                signature = row["signature"]
                if (record.projectId != self.project_id or str(UUID(record.operationId)) != record.operationId
                        or record.operationId in self._operations
                        or not isinstance(signature, list) or len(signature) != 3
                        or not all(isinstance(value, str) for value in signature)
                        or not re.fullmatch(r"[0-9a-f]{64}", signature[2])
                        or not isinstance(row["acceptingCandidate"], bool)
                        or not isinstance(row["branchId"], str)
                        or (row["expectedStage"] is not None and not isinstance(row["expectedStage"], str))):
                    raise ValueError("Invalid saved operation binding.")
                # A local journal never proves a successful P036 commit/result.
                record.committed, record.resultDigest, record.resultRevision = False, None, None
                if record.status in _ACTIVE or (record.candidateId and record.status == "completed"):
                    record.status = "needs_recovery"
                    record.reason = "Hub restarted before this operation's retained result was reconciled. No request was replayed."
                admission = _Admission(record, tuple(signature), row["expectedStage"],
                                       row["branchId"], row["acceptingCandidate"])
                admission.finished.set()  # A prior process cannot deliver its HTTP response.
                self._operations[record.operationId] = admission
            # A dismissal is a person's reading of one outcome, not a recovery
            # fact: an unreadable entry only brings its notice back.
            acknowledged = saved.get("acknowledged", {})
            for key, value in (acknowledged.items() if isinstance(acknowledged, dict) else ()):
                if isinstance(value, str):
                    # Kept before a dismissal named its status, when only a failed
                    # or stale one could be dismissed. A request keeps that status;
                    # an observed run could only have failed.
                    admitted = self._operations.get(key)
                    value = {"acknowledgedAt": value, "status": admitted.record.status if admitted else "failed"}
                    if value["status"] not in _ACKNOWLEDGEABLE:
                        continue
                if (isinstance(value, dict) and isinstance(value.get("acknowledgedAt"), str)
                        and value.get("status") in _ACKNOWLEDGEABLE | {"needs_recovery"}):
                    self._acknowledged[key] = (value["acknowledgedAt"], value["status"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise HubFailure(503, "OPERATION_LOG_INVALID", "The saved operation identities could not be read for this project. Requests were not replayed.") from exc

    def _save(self) -> None:
        if self.journal_path is None:
            return
        # Only recovery metadata crosses this Hub-runtime boundary. Request
        # bodies and successful project results stay with their existing owners.
        saved = {"projectId": self.project_id, "projectDir": self.project_dir, "operations": [{
            "record": row.record.model_dump(exclude={"committed", "resultDigest", "resultRevision", "reason",
                                                     "admissionSequence", "recoverable", "acknowledgedAt"}),
            "signature": row.signature, "expectedStage": row.expected_stage,
            "branchId": row.branch_id, "acceptingCandidate": row.accepting_candidate,
        } for row in self._operations.values()]}
        if self._acknowledged:
            saved["acknowledged"] = {key: {"acknowledgedAt": at, "status": status}
                                     for key, (at, status) in self._acknowledged.items()}
        temporary = self.journal_path.with_suffix(".tmp")
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with temporary.open("w", encoding="utf-8") as stream:
                    json.dump(saved, stream, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.journal_path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as exc:
            raise HubFailure(503, "OPERATION_LOG_UNAVAILABLE", "The operation identity could not be saved. Read runtime status before any new request; this request was not retried.") from exc

    def admit(self, operation_id: str, method: str, path: str, body: bytes, *,
              retained: dict | None, source: str, session_id: str | None) -> tuple[_Admission, bool]:
        try:
            operation_id = str(UUID(operation_id))
        except ValueError as exc:
            raise HubFailure(422, "OPERATION_ID_INVALID", "Idempotency-Key must be a UUID.") from exc
        signature = method, path, hashlib.sha256(body).hexdigest()
        try:
            payload = json.loads(body) if body else {}
        except (ValueError, UnicodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        with self._lock:
            existing = self._operations.get(operation_id)
            if existing:
                if existing.signature != signature:
                    raise HubFailure(409, "OPERATION_ID_CONFLICT", "This operation id already names a different request.")
                return existing, False
            route = urlsplit(path).path
            published = (retained or {}).get("published", {})
            candidate = f"hub-cand-{UUID(operation_id).hex}" if method == "POST" and _CANDIDATE_REQUEST.fullmatch(route) else None
            accepted = _ACCEPT_REQUEST.fullmatch(route) if method == "POST" else None
            if accepted:
                candidate = accepted.group(1)
            proposal = _PROPOSAL_CANDIDATE.fullmatch(route)
            record = OperationRecord(
                operationId=operation_id, projectId=self.project_id, kind=f"{method} {route}",
                source=source, status="committing" if accepted else "executing",
                baseRevision=published.get("version"),
                baseDigest=payload.get("stateDigest") or published.get("stateSha256"),
                sourceRunId=payload.get("sourceRunId"),
                sourceStageRef=payload.get("sourceStageRef") or payload.get("expectedHeadStageRef"),
                proposalId=proposal.group(1) if proposal else None,
                candidateId=candidate, sessionId=session_id,
                createdAt=datetime.now(timezone.utc).isoformat(),
            )
            expected_stage, branch_id = payload.get("expectedHeadStageRef"), payload.get("branchId", "main")
            admission = _Admission(record, signature,
                expected_stage if isinstance(expected_stage, str) else None,
                branch_id if isinstance(branch_id, str) else "main", bool(accepted))
            self._operations[operation_id] = admission
            try:
                self._save()  # Must succeed before a caller can dispatch this request.
            except HubFailure:
                del self._operations[operation_id]
                raise
            return admission, True

    def replied(self, admission: _Admission, response: HttpResult) -> None:
        with self._lock:
            admission.response = response
            payload = response.json()
            record = admission.record
            if payload.get("baseStateDigest"):
                record.baseDigest = payload["baseStateDigest"]
                record.baseRecordDigest = payload.get("recordDigest")
                record.sourceRunId = payload.get("sourceRunId")
                record.sourceStageRef = payload.get("sourceStageRef")
            for name in ("proposalId", "jobId", "candidateId"):
                if isinstance(payload.get(name), str):
                    setattr(record, name, payload[name])
            if response.status >= 400:
                code = str(payload.get("code", ""))
                admitted = any(isinstance(payload.get(name), str) for name in ("proposalId", "jobId", "candidateId"))
                if response.status in _REFUSAL_STATUSES and not admitted and code not in _RETAINED_CODES:
                    record.status = "refused"
                else:
                    record.status = "stale" if "STALE" in code else "failed"
                record.reason = str(payload.get("detail", f"HTTP {response.status}"))[:1200]
            elif record.candidateId:
                # Even a 200 accept or successful job needs retained evidence.
                record.status = "committing" if admission.accepting_candidate else "executing"
            else:
                record.status = "completed"
            try:
                self._save()
            finally:
                admission.finished.set()

    def interrupted(self, admission: _Admission, reason: str) -> None:
        with self._lock:
            admission.record.status = "needs_recovery"
            admission.record.reason = reason
            # Only a read after the lost reply can say whether it left a run to recover.
            admission.resolvable = None
            try:
                self._save()
            finally:
                admission.finished.set()

    def bind_proposal(self, admission: _Admission, proposal: dict, base_revision: int):
        with self._lock:
            record = admission.record
            record.baseRevision = base_revision
            record.baseDigest = proposal["baseStateDigest"]
            record.baseRecordDigest = proposal["recordDigest"]
            record.sourceRunId = proposal.get("sourceRunId")
            record.sourceStageRef = proposal.get("sourceStageRef")
            record.status = "validated"
            self._save()

    def reconcile(self, retained: dict, *, worker_alive: bool) -> None:
        candidates = {row["candidateId"]: row for row in retained.get("candidates", [])}
        jobs = {row["candidateId"]: row for row in retained.get("jobs", [])}
        stages = retained.get("stages", [])
        # A stage must be reachable from a committed branch. Prepared files are
        # intentionally absent from the existing inspector's answer.
        if not stages:
            stages = [stage for branch in retained.get("branches", []) for stage in branch.get("stages", [])]
        heads = {branch.get("branchId"): branch.get("headStageRef") for branch in retained.get("branches", [])}
        with self._lock:
            for admission in self._operations.values():
                record = admission.record
                if record.status in {"failed", "stale", "cancelled", "refused"}:
                    continue
                candidate = candidates.get(record.candidateId)
                # All that recovery can find again: the named run with its receipt.
                # An acceptance commits by compare-and-swap on the head it expected,
                # so once its branch has moved on without it, it never will.
                admission.resolvable = bool(candidate and candidate.get("receiptRef")) and (
                    not admission.expects_stage
                    or heads.get(admission.branch_id, admission.expected_stage) == admission.expected_stage)
                job = jobs.get(record.candidateId)
                if job:
                    record.jobId = job.get("jobId")
                    record.proposalId = record.proposalId or job.get("proposalId")
                if admission.expects_stage:
                    committed = next((stage for stage in stages
                        if stage.get("candidateId") == record.candidateId
                        and stage.get("branchId") == admission.branch_id
                        and stage.get("parentStageRef") == admission.expected_stage), None)
                    if committed:
                        record.status, record.committed, record.reason = "completed", True, None
                        record.resultDigest = committed.get("recordDigest") or committed.get("stateDigest")
                        # Stage acceptance is independent of formal issue.
                        record.resultRevision = None
                        continue
                elif not admission.accepting_candidate and candidate and candidate.get("status") in {"completed", "succeeded"}:
                    record.status, record.reason = "completed", None
                    record.resultDigest = candidate.get("resultStateDigest") or candidate.get("resultRecordDigest")
                    record.baseDigest = candidate.get("baseStateDigest") or record.baseDigest
                    record.baseRecordDigest = candidate.get("baseRecordDigest") or record.baseRecordDigest
                    record.baseRevision = (candidate.get("base") or {}).get("version", record.baseRevision)
                    continue
                if candidate and candidate.get("status") == "failed":
                    record.status = "failed"
                    record.reason = candidate.get("error") or "The retained candidate reports incomplete execution."
                    continue
                if job and worker_alive:
                    status = job.get("status")
                    record.status = {"queued": "queued", "running": "executing", "failed": "failed"}.get(status, "needs_recovery")
                    record.reason = job.get("error")
                elif record.candidateId and not worker_alive and record.status in _ACTIVE:
                    record.status = "needs_recovery"
                    record.reason = "The worker stopped before a complete retained result could be verified. This operation was not replayed."
            observed = {}
            tracked = {row.record.candidateId for row in self._operations.values()}
            for candidate_id, candidate in candidates.items():
                if candidate_id in tracked:
                    continue
                status = candidate.get("status", "needs_recovery")
                status = {"succeeded": "completed", "running": "executing"}.get(status, status)
                observed[candidate_id] = OperationRecord(
                    operationId=f"candidate:{candidate_id}", projectId=self.project_id,
                    kind="candidate", source="retained", candidateId=candidate_id, status=status,
                    baseRevision=(candidate.get("base") or {}).get("version"),
                    baseRecordDigest=candidate.get("baseRecordDigest"),
                    baseDigest=candidate.get("baseStateDigest"), resultDigest=candidate.get("resultStateDigest"),
                    jobId=candidate.get("jobId"), proposalId=candidate.get("proposalId"),
                    committed=bool(candidate.get("commitStageRefs")),
                    reason=candidate.get("error"),
                    # An observed run is never replayed either; only its receipt can resolve it.
                    recoverable=status == "needs_recovery" and bool(candidate.get("receiptRef")),
                )
            self._retained = observed

    def _recoverable(self, admission: _Admission) -> bool:
        """Whether reading retained results can still resolve this operation.

        Recovery replays nothing. It reconciles a lost reply against the run
        the request named and that run's runner receipt, and an acceptance
        against a commit under the Stage it named. A request that named no run,
        such as a proposal or a drawing sheet, leaves nothing to reconcile; nor
        does a run no read finds with its receipt, since whatever would have
        written that receipt is never run again, nor an acceptance whose branch
        has moved past the Stage it expected.
        """
        record = admission.record
        if record.status != "needs_recovery" or not record.candidateId:
            return False
        if admission.accepting_candidate and not admission.expects_stage:
            return False
        if admission.resolvable is None:
            # Not read since the reply was lost or this Hub started: the notice
            # stays, unless a person already read it as unrecoverable.
            return self._acknowledged.get(record.operationId, ("", ""))[1] != "needs_recovery"
        return admission.resolvable

    def _shown(self, record: OperationRecord, admission: _Admission | None = None, **update) -> OperationRecord:
        # Every OperationRecord field is a scalar, so a shallow copy is already a
        # detached one; a deep copy of each record was most of a snapshot (#363).
        shown = record.model_copy(update={**update, "acknowledgedAt": None})
        if admission is not None:
            shown.recoverable = self._recoverable(admission)
        # A dismissal speaks only for the status it read: an operation that
        # later reads otherwise, or turns out recoverable, is reported again.
        at, status = self._acknowledged.get(record.operationId, (None, None))
        if status == shown.status and _dismissible(shown):
            shown.acknowledgedAt = at
        return shown

    def records(self) -> list[OperationRecord]:
        with self._lock:
            # The existing journal retains this admission order across Hub
            # restarts. Derive it before the bounded/reordered display window;
            # it supplies no result status and is never written back to disk.
            values = [self._shown(row.record, row, admissionSequence=index)
                      for index, row in enumerate(self._operations.values(), start=1)]
            # Keep active work visible even after many completed requests, and
            # recovery until it is recovered or a person dismissed it.
            active = [row for row in values if row.status in _ACTIVE
                      or (row.status == "needs_recovery" and row.acknowledgedAt is None)]
            recent = [row for row in values if row not in active][-50:]
            return [*active, *recent, *(self._shown(row) for row in self._retained.values())]

    def _has_active(self) -> bool:
        # Liveness needs status only, not detached display copies of the whole
        # admission history on every project heartbeat.
        with self._lock:
            return (any(row.record.status in _ACTIVE for row in self._operations.values())
                    or any(row.status in _ACTIVE for row in self._retained.values()))

    def acknowledge(self, operation_id: str) -> OperationRecord:
        """Dismiss one operation's notice; its record and outcome stay as they are.

        A failed or stale operation can be dismissed, and one that needs
        recovery when the Hub has no way to recover it. The dismissal is kept
        in this journal with the status it read, so it outlasts a Hub restart
        and ends if the operation reads otherwise. An operation the Hub can
        still recover cannot be dismissed: it stays until a retained result
        resolves it.
        """
        with self._lock:
            admission = self._operations.get(operation_id)
            if admission is not None:
                record, sequence = admission.record, list(self._operations).index(operation_id) + 1
            else:
                record = next((row for row in self._retained.values() if row.operationId == operation_id), None)
                sequence = None
            if record is None:
                raise HubFailure(404, "OPERATION_NOT_FOUND", "This project runtime has no operation with that id.")
            shown = self._shown(record, admission)
            if not _dismissible(shown):
                raise HubFailure(409, "OPERATION_NOT_ACKNOWLEDGEABLE",
                                 "Only a failed or stale operation, or one the Hub cannot recover, can be dismissed. "
                                 "An operation that can still be recovered stays until it is.")
            if shown.acknowledgedAt is None:
                previous = self._acknowledged.get(operation_id)
                self._acknowledged[operation_id] = (datetime.now(timezone.utc).isoformat(), shown.status)
                try:
                    self._save()
                except HubFailure as exc:
                    if previous is None:
                        del self._acknowledged[operation_id]
                    else:
                        self._acknowledged[operation_id] = previous
                    raise HubFailure(503, "OPERATION_LOG_UNAVAILABLE",
                                     "The dismissal could not be saved, so the notice stays. Nothing else changed.") from exc
            return self._shown(record, admission, admissionSequence=sequence)

    def runs_of(self, session_id: str, since: datetime) -> list[OperationRecord]:
        """Copies of the new runs one chat asked this Hub for since ``since``, in admission order."""
        with self._lock:
            return [row.record.model_copy() for row in self._operations.values()
                    if row.record.sessionId == session_id and row.record.candidateId and not row.accepting_candidate
                    and row.record.createdAt and datetime.fromisoformat(row.record.createdAt) >= since]

    def made_by(self, session_id: str, run_id: object) -> bool:
        """Whether this chat asked this Hub for the new run ``run_id``."""
        with self._lock:
            return any(row.record.sessionId == session_id and row.record.candidateId == run_id and not row.accepting_candidate
                       for row in self._operations.values())

    def candidate_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(dict.fromkeys(row.record.candidateId for row in self._operations.values()
                if row.record.candidateId and row.record.status in _ACTIVE | {"needs_recovery"}))

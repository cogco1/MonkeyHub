"""One revision in one call: check what the change may not do, build it, read it back, validate, admit, continue.

The caller hands over a change against the exact working design it read, and the
architect's words it answers. The steps are the existing routes' own, in a fixed
order, and the answer is exactly one of five outcomes:

- ``landed``: the candidate was built, read back, found review-ready, admitted
  and continued onto the working design;
- ``admitted``: as ``landed``, for a change asked for with ``land=False`` (an
  alternative to compare); the working design did not move;
- ``problems``: the change could not be built, or was built and failed a check;
  every problem names where it came from, and nothing moved;
- ``needs_question``: the change meets something only the architect can settle -
  a target they asked to keep, an option they rejected, a condition in the
  change's reach that no check can verify, or a condition the change itself
  drops or rewrites. The answer quotes their words and nothing moved;
- ``refused``: the base is not the working design (``STALE_BASE``) or the request
  is malformed; nothing was proposed.

A condition no check can verify stops the call unless the architect's bound words
confirm it (``confirmed`` lists such ``obligation:<id>`` refs); a kept target or a
rejected option is changed by revising that decision first, never from here.

Nothing here compiles, builds, validates or writes on its own: every step is a
call through ``RevisionPorts``, which the route implements with the existing
routes and functions. Repair budgets and the conversation belong to the caller.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from archflow.state.state_record import StateRecord, obligation_checkable, obligations_in_scope

from ..errors import StudioError

LANDED, ADMITTED, PROBLEMS, NEEDS_QUESTION, REFUSED = "landed", "admitted", "problems", "needs_question", "refused"
CHANGE_KINDS = ("construction", "facets", "hosted-opening", "elevation", "proposal")
# The kinds that must leave a visible difference in the built model.
_GEOMETRY_KINDS = frozenset({"construction", "hosted-opening"})
KEEP, REJECTING = "keep", frozenset({"reject", "avoid"})
WAIT_S = 240.0
WAIT_MAX_S = 300.0
_POLL_S = 0.5
_SHOWN = 20
_LIVE = ("open", "blocked")


@dataclass(frozen=True, slots=True)
class RevisionRequest:
    project_id: str
    state_digest: str
    source_run_id: str
    kind: str
    proposal: Mapping[str, Any]
    summary: str
    keep: tuple[str, ...] = ()
    confirmed: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    land: bool = True
    message_source: Mapping[str, str] | None = None
    raw_language: str | None = None
    label: str | None = None
    wait_s: float = WAIT_S


@dataclass(frozen=True, slots=True)
class Problem:
    """One reason a built or refused change does not land, named by where it came from."""

    source: str
    kind: str
    detail: str


@dataclass(frozen=True, slots=True)
class Decision:
    """A decision that applies to the design: keep, reject or avoid one target, in the architect's words."""

    decision_id: str
    disposition: str
    target_ref: str
    words: str


@dataclass(frozen=True, slots=True)
class RevisionOutcome:
    outcome: str
    code: str
    detail: str
    proposal_id: str | None = None
    candidate_id: str | None = None
    state_digest: str | None = None
    problems: tuple[Problem, ...] = ()
    question_refs: tuple[str, ...] = ()
    # The conditions in the change's reach, each marked checkable or not.
    conditions: tuple[Mapping[str, Any], ...] = ()
    compare: Mapping[str, Any] | None = None
    working: Mapping[str, Any] | None = None
    seconds: Mapping[str, float] = field(default_factory=dict)


class RevisionPorts(Protocol):
    """The existing routes the transaction calls, in the bound project. Each raises ``StudioError`` as its route does."""

    def working_head(self) -> tuple[str | None, str | None, str | None]: ...   # run id, state digest, position revision
    def record(self, run_id: str) -> StateRecord: ...
    def decisions(self, run_id: str) -> Sequence[Decision]: ...
    def propose(self, kind: str, body: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def run(self, proposal_id: str) -> Mapping[str, Any]: ...
    def job(self, job_id: str) -> Mapping[str, Any]: ...
    def candidate(self, candidate_id: str) -> Mapping[str, Any]: ...
    def compare(self, candidate_id: str, against: str) -> Mapping[str, Any]: ...
    def validation(self, candidate_id: str) -> Mapping[str, Any]: ...
    def admit(self, body: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def continue_(self, run_id: str, revision: str | None) -> Mapping[str, Any]: ...


def revise(request: RevisionRequest, ports: RevisionPorts, *, clock: Callable[[], float] = time.monotonic,
           sleep: Callable[[float], None] = time.sleep) -> RevisionOutcome:
    """Carry one change through to the working design, or say exactly why it stopped."""

    started, seconds = clock(), {}

    def done(outcome: str, code: str, detail: str, **fields: Any) -> RevisionOutcome:
        seconds["total"] = round(clock() - started, 3)
        return RevisionOutcome(outcome, code, detail, seconds=dict(seconds), **fields)

    if request.kind not in CHANGE_KINDS:
        return done(REFUSED, "CHANGE_KIND_UNKNOWN", f"kind is one of {', '.join(CHANGE_KINDS)}; nothing was proposed.")
    wrong = [ref for ref in request.confirmed if not ref.startswith("obligation:")]
    if wrong:
        return done(REFUSED, "CONFIRMATION_INVALID", "confirmed names only conditions (obligation:<id>) the architect's "
                    f"words confirm; {', '.join(wrong)} is a decision, which is changed by revising it. Nothing was proposed.")
    if request.confirmed and request.raw_language is None:
        return done(REFUSED, "CONFIRMATION_INVALID", "A condition is confirmed only by the architect's own words; "
                    "this request carries none. Nothing was proposed.")
    head_run, head_digest, _ = ports.working_head()
    if (request.source_run_id, request.state_digest) != (head_run, head_digest):
        return done(REFUSED, "STALE_BASE", f"The working design is run {head_run} at state {head_digest}; this change was "
                    f"written against run {request.source_run_id} at {request.state_digest}. Read the design again and "
                    "write the change against it. Nothing was proposed.")

    decisions = tuple(ports.decisions(request.source_run_id))
    protected = tuple(dict.fromkeys((*request.keep, *(d.target_ref for d in decisions if d.disposition == KEEP))))
    body = {**request.proposal, "projectId": request.project_id, "stateDigest": request.state_digest,
            "sourceRunId": request.source_run_id, "keep": list(protected)}
    if request.kind in ("construction", "facets", "hosted-opening"):
        body["summary"] = request.summary
    try:
        proposal = ports.propose(request.kind, body)
    except StudioError as exc:
        if exc.code == "STALE_BASE":
            return done(REFUSED, exc.code, exc.detail)
        return done(PROBLEMS, "PROPOSAL_REFUSED", "The change could not be made into a proposal; correct it and submit it again.",
                    problems=(Problem("proposal", exc.code, _sentence(exc)),))
    proposal_id = str(proposal["proposalId"])
    impact = proposal.get("impact") or {}
    direct = tuple(impact.get("direct") or ())
    seconds["propose"] = round(clock() - started, 3)

    if proposal.get("status") == "conflict":
        refs = tuple(impact.get("conflicts") or ())
        kept = [d for d in decisions if d.disposition == KEEP and d.target_ref in refs]
        words = "; ".join(f"{d.target_ref}: \"{d.words}\"" for d in kept) or ", ".join(refs)
        return done(NEEDS_QUESTION, "KEEP_CONFLICT", f"The change reaches what the architect asked to keep ({words}). "
                    "Ask the architect before changing it; nothing was built.", proposal_id=proposal_id, question_refs=refs)
    base = ports.record(request.source_run_id)
    reached = _with_wholes(base, direct)
    rejected = [d for d in decisions if d.disposition in REJECTING and d.target_ref in reached]
    if rejected:
        return done(NEEDS_QUESTION, "REJECTED_OPTION", "The change touches what the architect rejected: "
                    + "; ".join(f"{d.target_ref}: \"{d.words}\"" for d in rejected) + ". Ask before going back to it; "
                    "nothing was built.", proposal_id=proposal_id, question_refs=tuple(d.target_ref for d in rejected))
    scope = obligations_in_scope(base, direct)
    conditions = tuple({"obligationId": o.obligation_id, "statement": o.statement, "checkable": obligation_checkable(o),
                        "validatorRef": o.validator_ref} for o in scope)
    confirmed = set(request.confirmed)
    unchecked = [o for o in scope if not obligation_checkable(o) and f"obligation:{o.obligation_id}" not in confirmed]
    if unchecked:
        return done(NEEDS_QUESTION, "UNCHECKED_CONDITION", "The change reaches conditions no check can verify: "
                    + "; ".join(f"obligation:{o.obligation_id}: \"{o.statement}\"" for o in unchecked)
                    + ". Ask the architect whether the change keeps them; nothing was built.", proposal_id=proposal_id,
                    question_refs=tuple(f"obligation:{o.obligation_id}" for o in unchecked), conditions=conditions)

    try:
        accepted = ports.run(proposal_id)
    except StudioError as exc:
        if exc.code == "STALE_BASE":
            return done(REFUSED, exc.code, exc.detail, proposal_id=proposal_id)
        return done(PROBLEMS, "RUN_REFUSED", "The candidate could not be started.", proposal_id=proposal_id,
                    problems=(Problem("build", exc.code, _sentence(exc)),), conditions=conditions)
    candidate_id, job_id = str(accepted["candidateId"]), str(accepted["jobId"])
    deadline = clock() + min(max(request.wait_s, 1.0), WAIT_MAX_S)
    while True:
        job = ports.job(job_id)
        if job.get("status") in ("succeeded", "failed"):
            break
        if clock() >= deadline:
            return done(PROBLEMS, "RUN_TIMEOUT", "The candidate did not finish in time; it was not admitted and the working "
                        f"design did not move. It is still building as job {job_id}.",
                        proposal_id=proposal_id, candidate_id=candidate_id, conditions=conditions,
                        problems=(Problem("build", "RUN_TIMEOUT", f"job {job_id} is still {job.get('status')}"),))
        sleep(_POLL_S)
    seconds["build"] = round(clock() - started, 3)
    if job.get("status") != "succeeded":
        return done(PROBLEMS, "BUILD_FAILED", "The candidate failed to build; nothing moved.", proposal_id=proposal_id,
                    candidate_id=candidate_id, conditions=conditions,
                    problems=(Problem("build", "BUILD_FAILED", str(job.get("error") or "the job failed without a sentence")[:600]),))

    candidate = ports.candidate(candidate_id)
    comparison = _compare(ports, candidate_id, request.source_run_id)
    validation = ports.validation(candidate_id)
    seconds["check"] = round(clock() - started, 3)
    built = dict(proposal_id=proposal_id, candidate_id=candidate_id, state_digest=candidate.get("stateDigest"),
                 conditions=conditions, compare=comparison)

    changed = _changed_conditions(base, ports.record(candidate_id), confirmed)
    after = {row.get("obligationId"): row for row in validation.get("obligationProblems") or ()}
    still_unchecked = [o for o in scope if (after.get(o.obligation_id) or {}).get("status") == "unchecked"
                       and f"obligation:{o.obligation_id}" not in confirmed]
    if changed or still_unchecked:
        lines = [f"obligation:{oid}: {what}" for oid, what in changed]
        lines += [f"obligation:{o.obligation_id}: \"{o.statement}\" could not be checked on the built model "
                  f"({(after.get(o.obligation_id) or {}).get('detail')})" for o in still_unchecked]
        refs = tuple(dict.fromkeys([f"obligation:{oid}" for oid, _ in changed]
                                   + [f"obligation:{o.obligation_id}" for o in still_unchecked]))
        return done(NEEDS_QUESTION, "CONDITION_CHANGED" if changed else "UNCHECKED_CONDITION",
                    "The built change leaves conditions only the architect can settle: " + "; ".join(lines)
                    + ". It was not admitted and the working design did not move; ask the architect.",
                    question_refs=refs, **built)

    problems = _readback_problems(candidate, comparison, request.kind) + _validation_problems(validation, candidate)
    if problems:
        return done(PROBLEMS, "CHECKS_FAILED", "The candidate was built and failed the checks below; it was not admitted and "
                    "the working design did not move. Submit a corrected change against the same base.",
                    problems=tuple(problems[:_SHOWN]), **built)

    admission = {"projectId": request.project_id,
                 "task": {"kind": "hub-chat" if request.message_source is not None else "ui", "ids": []},
                 "results": [{"runId": candidate_id, "outcome": "admitted", "supersedes": list(request.supersedes),
                              "label": request.label or request.summary[:240], "summary": request.summary[:2000],
                              "reason": _confirmation(scope, confirmed)}]}
    if request.message_source is not None:
        admission.update(messageSource=dict(request.message_source), rawLanguage=request.raw_language)
    try:
        ports.admit(admission)
    except StudioError as exc:
        return done(PROBLEMS, "ADMISSION_REFUSED", "The candidate passed its checks but could not be admitted; the "
                    "working design did not move.", problems=(Problem("admission", exc.code, _sentence(exc)),), **built)
    if request.land:
        try:
            _, _, revision = ports.working_head()
            ports.continue_(candidate_id, revision)
        except StudioError as exc:
            # The admission stands; only the move onto it was refused, for example by a Continue made meanwhile.
            return done(PROBLEMS, "CONTINUE_REFUSED", f"The candidate passed its checks and is admitted, but the working "
                        f"design did not move onto it. Read the design again; {candidate_id} stays an admitted candidate.",
                        problems=(Problem("continue", exc.code, _sentence(exc)),), **built)
    head_run, head_digest, revision = ports.working_head()
    seconds["land"] = round(clock() - started, 3)
    working = {"runId": head_run, "stateDigest": head_digest, "revisionSha256": revision}
    if not request.land:
        return done(ADMITTED, "ADMITTED", "The change passed its checks and is admitted as a candidate to compare; "
                    "the working design did not move.", working=working, **built)
    return done(LANDED, "LANDED", "The change is on the working design.", working=working, **built)


def _sentence(exc: StudioError) -> str:
    return f"{exc.code}: {exc.detail}"[:800]


def _confirmation(scope: Sequence[Any], confirmed: set[str]) -> str | None:
    named = [f"obligation:{o.obligation_id}" for o in scope if f"obligation:{o.obligation_id}" in confirmed]
    return f"The architect's words confirm the change keeps {', '.join(named)}." if named else None


def _with_wholes(record: StateRecord, refs: Sequence[str]) -> set[str]:
    """The refs, and every entity each referenced entity is part of: changing a part touches its whole."""

    reached = set(refs)
    known = {entity.entity_id: entity for entity in record.entities}
    for ref in refs:
        entity = known.get(ref.removeprefix("entity:")) if ref.startswith("entity:") else None
        while entity is not None:
            for whole in (entity.parent_id, entity.fields.get("component_id")):
                if isinstance(whole, str) and f"entity:{whole}" not in reached:
                    reached.add(f"entity:{whole}")
            parent = entity.parent_id or entity.fields.get("component_id")
            entity = known.get(parent) if isinstance(parent, str) and parent != entity.entity_id else None
    return reached


def _changed_conditions(base: StateRecord, built: StateRecord, confirmed: set[str]) -> list[tuple[str, str]]:
    """The base's live conditions the built record drops or rewrites, unless the architect confirmed them."""

    after = {o.obligation_id: o for o in built.obligations}
    changed = []
    for obligation in base.obligations:
        if obligation.status.value not in _LIVE or f"obligation:{obligation.obligation_id}" in confirmed:
            continue
        now = after.get(obligation.obligation_id)
        if now is None:
            changed.append((obligation.obligation_id, f"\"{obligation.statement}\" is removed by the change"))
        elif now.to_dict() != obligation.to_dict():
            changed.append((obligation.obligation_id, f"\"{obligation.statement}\" is rewritten by the change"))
    return changed


def _compare(ports: RevisionPorts, candidate_id: str, against: str) -> Mapping[str, Any]:
    """The built model against the design it was made from, or why that design has no model to compare."""

    try:
        answer = ports.compare(candidate_id, against)
    except StudioError as exc:
        return {"against": against, "unavailable": _sentence(exc)}
    changed = [row for row in answer.get("objects") or () if row.get("status") != "unchanged"]
    return {"against": against, **{key: answer.get(key) for key in ("changed", "unchanged", "added", "removed")},
            "objects": [{key: row.get(key) for key in ("name", "status", "before", "after")} for row in changed[:40]],
            "objectsOmitted": max(0, len(changed) - 40)}


def _readback_problems(candidate: Mapping[str, Any], comparison: Mapping[str, Any], kind: str) -> list[Problem]:
    """The built model must read back, and a change of form must show in it.

    A design with no model of its own (a start that was never exported) cannot be compared; the comparison then
    says why, and the built model alone is read back.
    """

    if candidate.get("objectReadbackError"):
        return [Problem("readback", "OBJECT_READBACK", str(candidate["objectReadbackError"])[:600])]
    if not candidate.get("objects"):
        return [Problem("readback", "OBJECT_READBACK", "the candidate's model reads back no object, so the change "
                        "cannot be confirmed; this Runtime may export no model")]
    if "unavailable" in comparison:
        return []
    if kind in _GEOMETRY_KINDS and not any(comparison.get(key) for key in ("changed", "added", "removed")):
        return [Problem("readback", "NO_VISIBLE_CHANGE", "the built model is the same as the design it was made from")]
    return []


def _validation_problems(validation: Mapping[str, Any], candidate: Mapping[str, Any]) -> list[Problem]:
    blocked = list(validation.get("blockedBy") or ())
    problems = []
    if "relations.held" in blocked or "relations.fully_checked" in blocked:
        for row in candidate.get("relationProblems") or ():
            problems.append(Problem(f"relation:{row.get('relationId')}", f"{row.get('status')} {row.get('kind')}",
                                    str(row.get("detail"))))
        if not problems:
            problems.append(Problem("relations", "relations", "relations were violated or left unchecked"))
    if "obligations.held" in blocked:
        for row in validation.get("obligationProblems") or ():
            if row.get("status") == "violated" and row.get("new"):
                problems.append(Problem(f"obligation:{row.get('obligationId')}", "violated", str(row.get("detail"))))
    for clause in blocked:
        if clause not in ("relations.held", "relations.fully_checked", "obligations.held"):
            lines = list(validation.get("honesty") or ()) if clause == "runner.exports_available" else []
            problems.append(Problem(clause, "not ready", "; ".join(lines)[:600] or f"{clause} does not hold"))
    return problems


# ---- what a reviser reads before writing a change -------------------------------------------------

_CONTEXT_ROWS = 40
_CONTEXT_TEXT = 300


def revision_context(*, working: Mapping[str, Any], decisions: Sequence[Decision], record: StateRecord,
                     results: Sequence[Any], last_change: Mapping[str, Any] | None,
                     tried: Sequence[Mapping[str, Any]], honesty: Sequence[str] = ()) -> dict[str, Any]:
    """The bounded reading a change is written from: every item names where it came from.

    ``results`` are the record's obligation results on the working design (``check_obligations``);
    ``last_change`` is the working design's own landed change, with its objects before and after, so that
    undoing it is an ordinary change; ``tried`` are results the architect rejected. Lists are cut at a fixed
    length and say how many were left out; nothing here is a judgement.
    """

    status = {row.obligation_id: row for row in results}
    conditions = []
    for obligation in record.obligations:
        if obligation.status.value not in _LIVE:
            continue
        result = status.get(obligation.obligation_id)
        conditions.append({"ref": f"obligation:{obligation.obligation_id}", "statement": obligation.statement[:_CONTEXT_TEXT],
                           "subjects": list(obligation.subject_refs), "validatorRef": obligation.validator_ref,
                           "checkable": obligation_checkable(obligation),
                           "onWorkingDesign": None if result is None else result.status,
                           "detail": None if result is None else result.detail[:_CONTEXT_TEXT]})
    keep = [{"ref": f"decision:{d.decision_id}", "targetRef": d.target_ref, "words": d.words[:_CONTEXT_TEXT]}
            for d in decisions if d.disposition == KEEP]
    rejected = [{"ref": f"decision:{d.decision_id}", "disposition": d.disposition, "targetRef": d.target_ref,
                 "words": d.words[:_CONTEXT_TEXT]} for d in decisions if d.disposition in REJECTING]
    answer: dict[str, Any] = {"working": dict(working), "lastChange": None if last_change is None else dict(last_change)}
    for key, rows in (("keep", keep), ("rejected", rejected), ("conditions", conditions), ("tried", list(tried))):
        answer[key] = rows[:_CONTEXT_ROWS]
        if len(rows) > _CONTEXT_ROWS:
            answer[f"{key}Omitted"] = len(rows) - _CONTEXT_ROWS
    answer["honesty"] = list(honesty)
    return answer

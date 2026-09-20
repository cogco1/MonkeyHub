"""GH-173: one synchronous fixture experiment, not an agent runtime.

The only design transition is the existing StateRecord operator. Persistence is
P036; reviews have no writer, and final grading never calls a language model.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from io import BytesIO
import json
from time import perf_counter
from typing import Any

from archflow.contracts.canonical import canonical_json_bytes
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STATE_RECORD
from archflow.project.refs import ProjectArtifactRef, ProjectRecordRef
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecord, StateRecordError, StateRecordOperator, apply_state_record_operator

from .fixture import checks, final_assessment, initial_record, policy_document, propose
from .provider import CallResult


ARMS = {
    "A": {"reviewer": "self", "timing": "after"},
    "B": {"reviewer": "self", "timing": "during"},
    "C": {"reviewer": "separate", "timing": "after"},
    "D": {"reviewer": "separate", "timing": "during"},
}


@dataclass(frozen=True)
class Budget:
    calls: int = 10
    seconds: float = 900
    call_seconds: float = 180
    api_equivalent_usd: float = 3
    repairs_per_review: int = 1
    proposal_attempts: int = 2
    branches: int = 0

    def __post_init__(self):
        if self.calls < 1 or min(self.seconds, self.call_seconds, self.api_equivalent_usd) <= 0:
            raise ValueError("budgets must be positive")
        if self.repairs_per_review != 1 or self.proposal_attempts not in (1, 2) or self.branches != 0:
            raise ValueError("this bounded experiment supports one repair, at most two attempts, no branches")


def _object(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TEXTS = {"type": "array", "items": {"type": "string"}, "maxItems": 5}
PROPOSAL_SCHEMA = _object({
    "checkpoint": {"type": "integer", "minimum": 0, "maximum": 3},
    "action": {"type": "object"},
    "assumptions": TEXTS, "unresolved": TEXTS, "summary": {"type": "string"},
}, ["checkpoint", "action", "assumptions", "unresolved", "summary"])


def review_schema(binding: dict) -> dict:
    return _object({
        "binding": {"const": binding},
        "requested_checks": {"type": "array", "items": {"type": "string"}, "maxItems": 8, "uniqueItems": True},
        "objections": {"type": "array", "maxItems": 5, "items": _object({
            "check": {"type": "string"},
            "category": {"enum": ["invariant", "obligation", "capability", "preference"]},
            "request": {"enum": ["hard_block", "revise", "defer", "consider"]},
            "summary": {"type": "string"},
        }, ["check", "category", "request", "summary"])},
        "summary": {"type": "string"},
    }, ["binding", "requested_checks", "objections", "summary"])


def adjudicate(challenge: dict, binding: dict, observed_checks: list[dict]) -> dict:
    """A requested deterministic check is evidence; a model objection is not.

Current invariants are always checked. Optional goal checks are returned only
when requested. The independent final checker is never fed back for repair.
"""
    if challenge.get("binding") != binding:
        return {"outcome": "stale_criticism", "checks": [], "false_objections": 0,
                "unsupported_hard_block_requests": 0, "rationale": "exact source/candidate/delta mismatch"}
    requested = challenge.get("requested_checks")
    objections = challenge.get("objections")
    if not isinstance(requested, list) or not all(isinstance(x, str) for x in requested) or not isinstance(objections, list):
        raise ValueError("malformed challenge")
    by_name = {item["name"]: item for item in observed_checks}
    if set(requested) - by_name.keys():
        raise ValueError("unsupported check request")
    false_objections = hard_requests = 0
    for obj in objections:
        if not isinstance(obj, dict) or set(obj) != {"check", "category", "request", "summary"}:
            raise ValueError("malformed objection")
        if obj["category"] not in ("invariant", "obligation", "capability", "preference") or obj["request"] not in ("hard_block", "revise", "defer", "consider"):
            raise ValueError("unsupported objection category or request")
        check = by_name.get(obj["check"])
        supported = check is not None and obj["category"] == check["category"]
        if obj["request"] == "hard_block":
            supported = supported and check["category"] == "invariant" and check["status"] == "fail"
            hard_requests += int(not supported)
        elif obj["request"] == "revise":
            supported = supported and check["status"] == "fail"
        else:
            supported = supported and check["status"] in ("pending", "unknown", "not_applicable", "fail")
        false_objections += int(not supported)
    selected = [x for x in observed_checks if x["category"] == "invariant" or x["name"] in requested]
    hard = [x for x in selected if x["category"] == "invariant" and x["status"] == "fail"]
    repair = [x for x in selected if x["category"] == "obligation" and x["status"] == "fail"]
    outcome = "hard_block" if hard else "revise" if repair else "retain_candidate_with_warning"
    return {"outcome": outcome, "checks": selected, "false_objections": false_objections,
            "unsupported_hard_block_requests": hard_requests,
            "rationale": "failed applicable invariant" if hard else "requested goal check failed" if repair
            else "no failed applicable check; pending/unknown is not a veto"}


class RetainedTrial:
    """A caller-assigned P036 run; no promotion or branch-writing capability."""

    def __init__(self, repository: FilesystemProjectRepository, run_id: str):
        self.repository = repository
        self.run = repository.create_run(run_id)

    def artifact(self, name: str, value: dict) -> dict:
        ref = self.repository.put_workspace_file(
            run=self.run, destination=PersistenceDestination(PersistenceArea.RUN_WORKSPACE, self.run.run_id),
            artifact_id=name, workspace_relative_path=f"checkpoint-critique/{name}.json",
            media_type="application/json", source=BytesIO(canonical_json_bytes(value)),
        )
        return asdict(ref)

    def state(self, record: StateRecord) -> dict:
        return self.repository.put_json(
            run=self.run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, self.run.run_id),
            record_kind=STATE_RECORD, payload=record.to_dict(),
        ).to_dict()


def _values(record: StateRecord) -> dict[str, Any]:
    return {p.ref: p.value for p in record.parameters}


def _propagation(record: StateRecord, root: str = "parameter:entry_side") -> dict:
    """Depth on actual retained StateRecord edges, not a proxy bounding box."""
    depths = {root: 0}
    edges = record.dependency_edges()
    for _ in range(len(edges) + 1):
        updated = False
        for edge in edges:
            if edge.upstream_ref in depths and depths.get(edge.downstream_ref, -1) < depths[edge.upstream_ref] + 1:
                depths[edge.downstream_ref] = depths[edge.upstream_ref] + 1
                updated = True
        if not updated:
            break
    else:
        raise ValueError("cyclic fixture dependency graph")
    downstream = {key: value for key, value in depths.items() if key != root}
    return {"depth": max(depths.values()), "downstream_refs": sorted(downstream),
            "downstream_count": len(downstream)}


def _entry_wrong(record: StateRecord) -> bool:
    return any(p.key == "entry_side" and p.value == 1 for p in record.parameters)


def run_trial(repository: FilesystemProjectRepository, run_id: str, arm: str, provider,
              budget: Budget = Budget(), *, repetition: int = 0) -> dict:
    """All provider, parser, missing-output, timeout and budget failures survive."""
    if arm not in ARMS:
        raise ValueError("unknown arm")
    store = RetainedTrial(repository, run_id)
    head = repository.read_head()
    before_branches = repository.read_design_branches()
    started = perf_counter()
    record = initial_record(store.run)
    initial_ref = current_ref = store.state(record)
    config_ref = store.artifact("policy", {"fixture": policy_document(), "arm": ARMS[arm],
                                         "budget": asdict(budget), "provider": provider.describe()})
    trajectory: list[dict] = []
    calls: list[dict] = []
    history: list[dict] = []  # only explicit, public actions/challenges/evidence
    decision_bundle: list[dict] = []
    errors: list[dict] = []
    repairs: list[dict] = []
    active_error: dict | None = None
    last_binding: dict | None = None
    status = "running"
    false_objections = unsupported_blocks = 0
    expense = 0.0
    telemetry_known = True
    reviewer_checks = 0

    def call(kind: str, checkpoint: int, payload: dict, schema: dict):
        nonlocal expense, status, telemetry_known
        elapsed = perf_counter() - started
        if len(calls) >= budget.calls or expense >= budget.api_equivalent_usd:
            status = "budget_exhausted"
            return None
        if elapsed >= budget.seconds:
            status = "timeout"
            return None
        context = [] if kind == "review" and ARMS[arm]["reviewer"] == "separate" else list(history)
        prompt = {"experiment": "GH-173 synthetic courtyard fixture", "task": kind,
                  "checkpoint": checkpoint, "policy": policy_document(), "candidate": record.to_dict(),
                  "public_history": context, "instructions": (
                      "Return only the requested public JSON, brief reasons, no private chain-of-thought. "
                      "Use only the declared finite operations and evidence. A review may request listed checks; "
                      "unknown and permitted unfinished work are not invariant failures. "
                      "No supported objection is a valid review. Do not invent criticism. "
                      "In review copy the exact binding and list requested_checks explicitly; mentioning a check "
                      "in an objection does not execute it. In repair choose exactly one allowed checkpoint/action."
                  ), **payload}
        call_id = f"call-{len(calls) + 1:02d}"
        request_ref = store.artifact(f"{call_id}-request", {"prompt": prompt, "schema": schema})
        call_started = perf_counter()
        try:
            result = provider.call(prompt, schema, timeout_seconds=min(budget.call_seconds, budget.seconds - elapsed),
                                   max_cost_usd=budget.api_equivalent_usd - expense)
        except Exception as exc:
            # An external failure is a scheduled observation, not a discarded trial.
            result = CallResult(None, {"api_equivalent_cost_usd": None}, perf_counter() - call_started,
                                "provider_error", None, {"error_category": type(exc).__name__})
        public = asdict(result)
        response_ref = store.artifact(f"{call_id}-response", public)
        cost = result.usage.get("api_equivalent_cost_usd")
        if cost is None:
            telemetry_known = False
        else:
            expense += cost
        entry = {"id": call_id, "kind": kind, "checkpoint": checkpoint, "request_ref": request_ref,
                 "response_ref": response_ref, **public}
        calls.append(entry)
        if result.outcome != "success":
            status = result.outcome
            return None
        if not isinstance(result.answer, dict):
            status = "malformed"
            return None
        if not telemetry_known and provider.describe().get("kind") != "deterministic":
            status = "telemetry_missing"
            return None
        # A reported overshoot is retained; it is never silently treated as within budget.
        if expense > budget.api_equivalent_usd or perf_counter() - started > budget.seconds:
            status = "budget_exhausted" if expense > budget.api_equivalent_usd else "timeout"
            return None
        history.append({"kind": kind, "checkpoint": checkpoint, "answer": result.answer})
        return result.answer

    def transition(answer: dict, checkpoint: int, *, repair: bool = False) -> bool:
        nonlocal record, current_ref, active_error, last_binding, status
        if set(answer) != set(PROPOSAL_SCHEMA["required"]) or not isinstance(answer.get("action"), dict):
            status = "malformed"
            return False
        stage = answer["checkpoint"]
        if not isinstance(stage, int) or isinstance(stage, bool) or stage not in (0, 1, 2, 3) or (not repair and stage != checkpoint):
            status = "malformed"
            return False
        step = f"step-{len(trajectory) + 1:02d}"
        before = record
        source_ref = current_ref
        event = {"id": step, "checkpoint": checkpoint, "operation_checkpoint": stage,
                 "repair": repair, "source_ref": source_ref, "call_ref": calls[-1]["response_ref"],
                 "answer": answer}
        try:
            operator = propose(record, stage, answer["action"])
            delta_ref = store.artifact(f"{step}-delta", operator.to_dict())
            event["delta_ref"] = delta_ref
            candidate = apply_state_record_operator(record, operator)
            invariants = [x for x in checks(candidate) if x["category"] == "invariant"]
            if any(x["status"] == "fail" for x in invariants):
                event.update(outcome="hard_block", checks=invariants,
                             rationale="independent current invariant failed")
                trajectory.append(event)
                return False
        except (ValueError, TypeError, KeyError, StateRecordError) as exc:
            # Only local validator text is retained, never arbitrary provider stderr.
            event.update(outcome="hard_block", checks=[{"name": "typed_operator", "status": "fail",
                         "category": "invariant", "reason": str(exc)}], rationale="existing operator refused")
            trajectory.append(event)
            return False
        current_ref = store.state(candidate)
        event.update(outcome="retained_candidate", candidate_ref=current_ref, checks=invariants,
                     changed_refs=sorted(k for k, v in _values(candidate).items() if _values(before).get(k) != v))
        trajectory.append(event)
        record = candidate
        last_binding = {"run": store.run.to_dict(), "source": source_ref, "candidate": current_ref, "delta": delta_ref}
        decision_bundle.append({"binding": last_binding, "delta": operator.to_dict(),
                                "proposal": answer})
        if _entry_wrong(record) and active_error is None:
            active_error = {"origin_step": step, "origin_ref": "parameter:entry_side", "detected_step": None,
                            "status": "open", "observed_depth_lower_bound": 0, "depth": None}
            errors.append(active_error)
        if active_error is not None:
            observed = _propagation(record)
            active_error["observed_depth_lower_bound"] = max(active_error["observed_depth_lower_bound"], observed["depth"])
            active_error["observed_downstream_refs"] = observed["downstream_refs"]
        if repair:
            # Actual changed old values, not all theoretically reachable graph vertices.
            changed = sorted(k for k, v in _values(before).items() if _values(record).get(k) != v)
            downstream = set(before.closure(("parameter:entry_side",))) - {"parameter:entry_side"}
            repairs.append({"step": step, "at_checkpoint": checkpoint, "changed_existing_refs": changed,
                            "actual_downstream_changed": sorted(set(changed) & downstream),
                            "affected_prior_decisions": [e["id"] for e in trajectory[:-1]
                                if e.get("outcome") == "retained_candidate" and not e.get("repair")
                                and set(e.get("changed_refs", ())) & set(changed) & downstream],
                            "declared_closure": sorted(downstream), "late_revision": checkpoint == 3})
        if active_error is not None and not _entry_wrong(record):
            active_error["resolved_step"] = step
            active_error = None
        return True

    def review(checkpoint: int):
        nonlocal status, false_objections, unsupported_blocks, reviewer_checks, active_error
        assert last_binding is not None
        response = call("review", checkpoint, {"binding": last_binding,
                        "proposal_bundle": list(decision_bundle),
                        "review_role": "self-review" if ARMS[arm]["reviewer"] == "self" else "independent-context Critic"},
                        review_schema(last_binding))
        if response is None:
            return
        observed = checks(record, final=checkpoint == 3)
        try:
            decision = adjudicate(response, last_binding, observed)
        except (ValueError, TypeError):
            status = "malformed"
            return
        reviewer_checks += len(decision["checks"])
        false_objections += decision["false_objections"]
        unsupported_blocks += decision["unsupported_hard_block_requests"]
        check_ref = store.artifact(f"review-{len(calls):02d}", {"binding": last_binding,
                                   "challenge_ref": calls[-1]["response_ref"], "decision": decision})
        history.append({"kind": "check_result", "checkpoint": checkpoint, "decision": decision})
        trajectory.append({"id": f"step-{len(trajectory) + 1:02d}", "checkpoint": checkpoint, "outcome": decision["outcome"],
                           "binding": last_binding, "challenge_ref": calls[-1]["response_ref"], "check_ref": check_ref,
                           "decision": decision})
        if decision["outcome"] == "stale_criticism":
            status = "stale_criticism"
            return
        failed = {c["name"] for c in decision["checks"] if c["status"] == "fail"}
        # The fixture's independently labeled root error, not a Critic's self-score.
        if active_error is not None and "entry_alignment" in failed:
            active_error.update(status="detected", detected_step=trajectory[-1]["id"], **_propagation(record))
        if decision["outcome"] in ("revise", "hard_block"):
            answer = call("repair", checkpoint, {"failed_checks": decision["checks"], "binding": last_binding}, PROPOSAL_SCHEMA)
            if answer is None:
                return
            if not transition(answer, checkpoint, repair=True):
                if status == "running":
                    status = "round_exhausted"
                return
            # Same requested checks after one bounded repair; no model self-certification.
            rerun = checks(record, final=checkpoint == 3)
            remaining = [c for c in rerun if c["name"] in failed and c["status"] == "fail"]
            check_ref = store.artifact(f"repair-check-{len(calls):02d}", {"candidate_ref": current_ref, "checks": rerun})
            trajectory.append({"id": f"step-{len(trajectory) + 1:02d}", "checkpoint": checkpoint,
                               "outcome": "round_exhausted" if remaining else "repair_checked", "check_ref": check_ref})
            reviewer_checks += len(rerun)
            if remaining:
                status = "round_exhausted"

    for checkpoint in (1, 2, 3):
        succeeded = False
        for attempt in range(budget.proposal_attempts):
            response = call("propose", checkpoint, {"required_checkpoint": checkpoint,
                            "previous_rejection": trajectory[-1] if attempt else None}, PROPOSAL_SCHEMA)
            if response is None:
                break
            succeeded = transition(response, checkpoint)
            if succeeded or status != "running":
                break
        if status != "running":
            break
        if not succeeded:
            status = "round_exhausted"
            break
        if ARMS[arm]["timing"] == "during" or checkpoint == 3:
            review(checkpoint)
        if status != "running":
            break

    final = final_assessment(record)
    if status == "running":
        status = "complete" if final["complete"] else "incomplete"
    for error in errors:
        if error["status"] == "open":
            error["status"] = "missed" if status in ("complete", "incomplete") else "censored"
            error["depth"] = None
    intact = repository.read_head() == head and repository.read_design_branches() == before_branches
    if not intact:
        raise RuntimeError("experiment changed protected project position")
    blocked = [e for e in trajectory if e["outcome"] == "hard_block"]
    false_blocks = sum(not any(c["status"] == "fail" and c["category"] == "invariant"
                               for c in e.get("checks", e.get("decision", {}).get("checks", []))) for e in blocked)
    summary = {
        "trial": run_id, "arm": arm, "repetition": repetition, "status": status,
        "complete": status == "complete" and final["complete"], "run": store.run.to_dict(),
        "policy_ref": config_ref, "initial_ref": initial_ref, "final_ref": current_ref,
        "final_content_digest": record.digest,
        "trajectory": trajectory, "calls": calls, "final_assessment": final,
        "propagation": errors, "repairs": repairs,
        "metrics": {"provider_calls": len(calls), "actual_model_requests": None,
                    "review_check_results": reviewer_checks,
                    "false_objections": false_objections, "unsupported_hard_block_requests": unsupported_blocks,
                    "false_hard_blocks": false_blocks, "hard_block_decisions": len(blocked), "human_interventions": 0,
                    "wall_seconds": perf_counter() - started, "api_equivalent_cost_usd": expense if telemetry_known else None,
                    "known_api_equivalent_cost_usd": expense, "actual_billed_cost_usd": None,
                    "subscription_quota_usage": None,
                    "repair_closure_size": sum(len(r["actual_downstream_changed"]) for r in repairs),
                    "late_revisions": sum(r["late_revision"] for r in repairs),
                    "discarded_operations": sum(len(r["affected_prior_decisions"]) for r in repairs),
                    "introduced_errors": len(errors), "detected_errors": sum(e["status"] == "detected" for e in errors),
                    "missed_errors": sum(e["status"] == "missed" for e in errors),
                    "censored_errors": sum(e["status"] == "censored" for e in errors)},
        "head_unchanged": intact,
    }
    summary["report_ref"] = store.artifact("report", summary)
    return summary


def _read_artifact(repository: FilesystemProjectRepository, ref: dict) -> dict:
    reference = ProjectArtifactRef(**ref)
    # P036's unrestricted verified JSON reader also reads its workspace artifacts.
    return repository.load_json(ProjectRecordRef(reference.project_id, reference.relative_path,
                                                 reference.sha256, reference.media_type))


def replay(repository: FilesystemProjectRepository, report_ref: dict) -> dict:
    """Cold-read byte identities, then execute retained deltas on their exact bases."""
    report = _read_artifact(repository, report_ref)
    record = StateRecord.from_dict(repository.load_json(ProjectRecordRef.from_dict(report["initial_ref"])))
    current_ref = report["initial_ref"]
    binding = None
    _read_artifact(repository, report["policy_ref"])
    for event in report["trajectory"]:
        if "source_ref" in event and event["source_ref"] != current_ref:
            raise ValueError("trajectory source mismatch")
        if "binding" in event:
            if event["binding"] != binding:
                raise ValueError("review binding mismatch")
            challenge = _read_artifact(repository, event["challenge_ref"])
            decision = adjudicate(challenge["answer"], binding, checks(record, final=event["checkpoint"] == 3))
            if decision != event["decision"]:
                raise ValueError("review decision did not replay")
        if "check_ref" in event:
            _read_artifact(repository, event["check_ref"])
        if event["outcome"] != "retained_candidate":
            continue
        operator = StateRecordOperator.from_dict(_read_artifact(repository, event["delta_ref"]))
        record = apply_state_record_operator(record, operator)
        retained = StateRecord.from_dict(repository.load_json(ProjectRecordRef.from_dict(event["candidate_ref"])))
        if record.to_dict() != retained.to_dict():
            raise ValueError("replayed candidate differs from retained state")
        current_ref = event["candidate_ref"]
        binding = {"run": record.run_ref.to_dict(), "source": event["source_ref"],
                   "candidate": current_ref, "delta": event["delta_ref"]}
    final = StateRecord.from_dict(repository.load_json(ProjectRecordRef.from_dict(report["final_ref"])))
    if (final.to_dict() != record.to_dict() or final_assessment(record) != report["final_assessment"]
            or report.get("final_content_digest", record.digest) != record.digest):
        raise ValueError("final independent assessment did not replay")
    for call in report["calls"]:
        request = _read_artifact(repository, call["request_ref"])
        response = _read_artifact(repository, call["response_ref"])
        if any(call.get(key) != value for key, value in response.items()):
            raise ValueError("call response mismatch")
        if request["prompt"]["task"] != call["kind"] or request["prompt"]["checkpoint"] != call["checkpoint"]:
            raise ValueError("call request mismatch")
    return {"trial": report["trial"], "status": report["status"], "candidate_digest": record.digest,
            "final_assessment": report["final_assessment"], "calls_verified": len(report["calls"])}

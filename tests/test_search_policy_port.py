from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace

from archflow.control.search_policy import (
    DecisionSpaceDescriptor,
    DecisionSpaceKind,
    ObjectiveDirection,
    RESERVED_OCBA_POLICY_FAMILY,
    SearchAction,
    SearchBudget,
    SearchBudgetAllocation,
    SearchCandidateEvaluation,
    SearchDirective,
    SearchObjectiveEstimate,
    SearchPolicyDescriptor,
    SearchPolicyError,
    SearchPolicyRequest,
    validate_search_directive,
)
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archflow.runtime.search_policy import (
    SearchPolicyRegistry,
    SearchPolicyRegistryError,
    SearchPolicyUnavailableError,
)


BASE_DIGEST = "a" * 64
STATE_DIGEST = "b" * 64
PORTFOLIO_DIGEST = "c" * 64


def _branch(*, epoch: int = 4, branch_id: str = "option-a") -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="search-project",
            run_id="run-001",
            base=ProjectVersionRef(
                project_id="search-project",
                version=3,
                state_sha256=BASE_DIGEST,
            ),
        ),
        branch_id=branch_id,
        epoch=epoch,
    )


def _descriptor(
    *,
    policy_id: str = "test-policy-v1",
    policy_family: str = "test-family",
) -> SearchPolicyDescriptor:
    return SearchPolicyDescriptor(
        policy_id=policy_id,
        policy_family=policy_family,
        policy_version="1.0.0",
        implementation_digest="d" * 64,
        supported_space_kinds=(
            DecisionSpaceKind.CATEGORICAL,
            DecisionSpaceKind.CONTINUOUS,
        ),
        supported_actions=(
            SearchAction.DEEPEN,
            SearchAction.HOLD,
            SearchAction.PRUNE,
            SearchAction.REQUEST_COMMIT,
            SearchAction.REQUEST_REOPEN,
            SearchAction.RESAMPLE,
            SearchAction.STOP,
        ),
        supports_multiobjective=True,
    )


def _evaluation(
    candidate: str,
    *,
    digest_char: str,
    mean: float,
    hard_failures: tuple[str, ...] = (),
    direction: ObjectiveDirection = ObjectiveDirection.MINIMIZE,
) -> SearchCandidateEvaluation:
    suffix = candidate.split(":", 1)[1]
    return SearchCandidateEvaluation(
        candidate_ref=candidate,
        candidate_digest=digest_char * 64,
        evaluation_ref=f"evaluation:{suffix}",
        hard_failure_refs=hard_failures,
        objectives=(
            SearchObjectiveEstimate(
                objective_ref="objective:embodied-carbon",
                direction=direction,
                mean=mean,
                variance=0.25,
                sample_count=4,
                source_ref=f"evidence:{suffix}-evaluation",
            ),
        ),
    )


def _request(
    *,
    policy: SearchPolicyDescriptor | None = None,
    evaluations: tuple[SearchCandidateEvaluation, ...] | None = None,
    allowed_actions: tuple[SearchAction, ...] | None = None,
) -> SearchPolicyRequest:
    exact_branch = _branch()
    return SearchPolicyRequest(
        request_id="search-request-001",
        policy=policy or _descriptor(),
        branch=exact_branch,
        stage_id="stage-3",
        state_digest=STATE_DIGEST,
        decision_space=DecisionSpaceDescriptor(
            descriptor_id="stage-3-space",
            stage_id="stage-3",
            branch=exact_branch,
            state_digest=STATE_DIGEST,
            kind=DecisionSpaceKind.CONTINUOUS,
            decision_refs=("decision:core", "decision:typology"),
            reopenable_decision_refs=("decision:typology",),
            candidate_refs=("candidate:a", "candidate:b"),
            objective_refs=("objective:embodied-carbon",),
            hard_constraint_refs=("constraint:structural-support",),
        ),
        portfolio_ref=(
            "project://search-project/runs/run-001/records/portfolio.json"
        ),
        portfolio_digest=PORTFOLIO_DIGEST,
        evaluations=evaluations
        or (
            _evaluation("candidate:a", digest_char="1", mean=8.0),
            _evaluation("candidate:b", digest_char="2", mean=11.0),
        ),
        budget=SearchBudget(
            evaluation_units=2,
            compute_millis=1_000,
            model_tokens=200,
        ),
        allowed_actions=allowed_actions
        or (
            SearchAction.DEEPEN,
            SearchAction.HOLD,
            SearchAction.PRUNE,
            SearchAction.REQUEST_COMMIT,
            SearchAction.REQUEST_REOPEN,
            SearchAction.RESAMPLE,
            SearchAction.STOP,
        ),
        evidence_refs=("evidence:rag-prior",),
    )


def _directive(
    request: SearchPolicyRequest,
    *,
    action: SearchAction = SearchAction.DEEPEN,
    targets: tuple[str, ...] = ("candidate:a",),
    allocations: tuple[SearchBudgetAllocation, ...] | None = None,
) -> SearchDirective:
    if allocations is None:
        allocations = (
            SearchBudgetAllocation(
                target_ref="candidate:a",
                evaluation_units=2,
                compute_millis=500,
                model_tokens=100,
            ),
        )
    return SearchDirective(
        directive_id="directive-001",
        request_digest=request.request_digest,
        policy_descriptor_digest=request.policy.descriptor_digest,
        branch=request.branch,
        stage_id=request.stage_id,
        state_digest=request.state_digest,
        action=action,
        target_refs=targets,
        allocations=allocations,
        evidence_refs=("evaluation:a",),
        reason_codes=("promising_candidate",),
    )


class _FakePolicy:
    def __init__(
        self,
        descriptor: SearchPolicyDescriptor,
        *,
        action: SearchAction = SearchAction.DEEPEN,
    ) -> None:
        self._descriptor = descriptor
        self.action = action
        self.calls = 0

    @property
    def descriptor(self) -> SearchPolicyDescriptor:
        return self._descriptor

    async def decide(self, request: SearchPolicyRequest) -> SearchDirective:
        self.calls += 1
        return _directive(request, action=self.action)


class _ExplodingPolicy(_FakePolicy):
    async def decide(self, request: SearchPolicyRequest) -> SearchDirective:
        self.calls += 1
        raise LookupError("team policy failed")


class SearchPolicyContractTests(unittest.TestCase):
    def test_request_and_directive_round_trip_without_authority(self):
        request = _request()
        directive = _directive(request)

        self.assertEqual(
            SearchPolicyRequest.from_dict(request.to_dict()),
            request,
        )
        self.assertEqual(
            SearchDirective.from_dict(directive.to_dict()),
            directive,
        )
        for payload in (request.to_dict(), directive.to_dict()):
            self.assertIs(payload["design_authority"], False)
            self.assertIs(payload["stage_acceptance_authority"], False)
            self.assertIs(payload["persistence_authority"], False)
            self.assertIs(payload["canonical_write_authority"], False)

        tampered = request.to_dict()
        tampered["canonical_write_authority"] = True
        with self.assertRaisesRegex(
            SearchPolicyError,
            "authority flags changed",
        ):
            SearchPolicyRequest.from_dict(tampered)

    def test_validator_rejects_stale_and_over_budget_outputs(self):
        request = _request()
        stale = replace(_directive(request), branch=_branch(epoch=5))
        with self.assertRaisesRegex(
            SearchPolicyError,
            "stale or cross-policy",
        ):
            validate_search_directive(request, stale)

        over_budget = replace(
            _directive(request),
            allocations=(
                SearchBudgetAllocation(
                    target_ref="candidate:a",
                    evaluation_units=3,
                    compute_millis=500,
                    model_tokens=100,
                ),
            ),
        )
        with self.assertRaisesRegex(SearchPolicyError, "exceeds"):
            validate_search_directive(request, over_budget)

    def test_commit_request_cannot_bypass_hard_failures(self):
        request = _request(
            evaluations=(
                _evaluation(
                    "candidate:a",
                    digest_char="1",
                    mean=8.0,
                    hard_failures=("constraint:structural-support",),
                ),
                _evaluation("candidate:b", digest_char="2", mean=11.0),
            )
        )
        directive = _directive(
            request,
            action=SearchAction.REQUEST_COMMIT,
            allocations=(),
        )
        with self.assertRaisesRegex(SearchPolicyError, "hard-feasible"):
            validate_search_directive(request, directive)

    def test_reopen_request_is_limited_to_explicit_envelope(self):
        request = _request()
        directive = _directive(
            request,
            action=SearchAction.REQUEST_REOPEN,
            targets=("decision:core",),
            allocations=(),
        )
        with self.assertRaisesRegex(SearchPolicyError, "reopen envelope"):
            validate_search_directive(request, directive)

    def test_candidate_objective_direction_cannot_drift(self):
        with self.assertRaisesRegex(SearchPolicyError, "direction changed"):
            _request(
                evaluations=(
                    _evaluation("candidate:a", digest_char="1", mean=8.0),
                    _evaluation(
                        "candidate:b",
                        digest_char="2",
                        mean=11.0,
                        direction=ObjectiveDirection.MAXIMIZE,
                    ),
                )
            )


class SearchPolicyRegistryTests(unittest.TestCase):
    def test_exact_registered_policy_is_dispatched_and_validated(self):
        request = _request()
        policy = _FakePolicy(request.policy)
        registry = SearchPolicyRegistry((policy,))

        result = asyncio.run(registry.decide(request))

        self.assertEqual(result, _directive(request))
        self.assertEqual(policy.calls, 1)
        self.assertEqual(registry.descriptors, (request.policy,))

    def test_reserved_ocba_family_has_no_builtin_implementation(self):
        self.assertEqual(RESERVED_OCBA_POLICY_FAMILY, "ocba")
        ocba = _descriptor(
            policy_id="team-ocba-v1",
            policy_family=RESERVED_OCBA_POLICY_FAMILY,
        )
        request = _request(policy=ocba)

        with self.assertRaisesRegex(
            SearchPolicyUnavailableError,
            "not registered",
        ):
            asyncio.run(SearchPolicyRegistry().decide(request))

    def test_policy_failure_is_not_retried_or_replaced(self):
        exact = _descriptor(
            policy_id="team-policy-exploding",
            policy_family="team-family",
        )
        alternate = _descriptor(
            policy_id="team-policy-alternate",
            policy_family="team-family",
        )
        failing = _ExplodingPolicy(exact)
        fallback = _FakePolicy(alternate)
        registry = SearchPolicyRegistry((failing, fallback))

        with self.assertRaisesRegex(LookupError, "team policy failed"):
            asyncio.run(registry.decide(_request(policy=exact)))
        self.assertEqual(failing.calls, 1)
        self.assertEqual(fallback.calls, 0)

    def test_duplicate_and_descriptor_drift_fail_closed(self):
        descriptor = _descriptor()
        policy = _FakePolicy(descriptor)
        registry = SearchPolicyRegistry((policy,))
        with self.assertRaisesRegex(
            SearchPolicyRegistryError,
            "already registered",
        ):
            registry.register(_FakePolicy(descriptor))

        policy._descriptor = replace(
            descriptor,
            implementation_digest="e" * 64,
        )
        with self.assertRaisesRegex(
            SearchPolicyRegistryError,
            "changed or was misbound",
        ):
            asyncio.run(registry.decide(_request(policy=descriptor)))


if __name__ == "__main__":
    unittest.main()

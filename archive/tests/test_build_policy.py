from __future__ import annotations

import json
import unittest
from pathlib import Path

from archive.archflow.adapters.site_observation import (
    SiteObservationAuthorization,
    authorize_site_observation,
)
from archive.archflow.capabilities.constructability import (
    build_constructability_snapshot,
)
from archive.archflow.compilers.brief import compile_design_brief
from archive.archflow.compilers.program import (
    ProgramProposalBundle,
    compile_design_program,
)
from archive.archflow.compilers.resources import (
    BuildAssumptionProposal,
    BuildBudgetProposal,
    BuildPolicyProposal,
    ResourceAvailabilityProposal,
    ResourceCompilationError,
    ResourceDemandProposal,
    StagingAssumptionProposal,
    compile_build_policy,
)
from archive.archflow.compilers.site import compile_site_context
from archflow.project.refs import ProjectVersionRef
from archive.archflow.state.build_policy import BuildPolicy, BuildStagingMode, ProtectedBlockAction, ResourcePolicyMode
from archflow.state.operational_state import FactEpistemicStatus
from archflow.state.spatial import SiteBounds


_SITE_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "site"


def _payload(name: str) -> dict[str, object]:
    return json.loads(
        (_SITE_FIXTURES / name).read_text(encoding="utf-8")
    )


def _base(project_id: str, digest_char: str) -> ProjectVersionRef:
    return ProjectVersionRef(
        project_id=project_id,
        version=0,
        state_sha256=digest_char * 64,
    )


def _contexts(
    *,
    project_id: str = "site-flat",
    digest_char: str = "a",
    fixture: str = "authorized_superflat.json",
    world_id: str = "fixture-flat-world",
):
    base = _base(project_id, digest_char)
    request_ref = f"project://{project_id}/input/raw-request.json"
    brief = compile_design_brief(
        project_id=project_id,
        run_id="site-001",
        base=base,
        raw_request_ref=request_ref,
    ).brief
    program = compile_design_program(
        brief=brief,
        proposals=ProgramProposalBundle(),
    ).program
    authorization = SiteObservationAuthorization(
        project_id=project_id,
        run_id="site-001",
        base=base,
        world_id=world_id,
        dimension_id="minecraft:overworld",
        authorized_envelope=SiteBounds(
            minimum=(0, 60, 0),
            maximum=(31, 90, 31),
        ),
        anchor=(16, 65, 16),
        authority_id="authority.user",
        authorization_ref=(
            f"project://{project_id}/input/site-authorization.json"
        ),
    )
    observation = authorize_site_observation(
        _payload(fixture),
        authorization=authorization,
    )
    site = compile_site_context(
        brief=brief,
        observation=observation,
    ).context
    return brief, program, site


def _source(project_id: str, name: str) -> str:
    return f"project://{project_id}/runs/site-001/records/{name}.json"


class BuildPolicyTests(unittest.TestCase):
    def test_explicit_creative_unbounded_sandbox_has_no_resource_default_gap(
        self,
    ) -> None:
        brief, program, site = _contexts()
        policy = compile_build_policy(
            brief=brief,
            program=program,
            site_context=site,
            proposal=BuildPolicyProposal(
                resource_mode=ResourcePolicyMode.CREATIVE,
                staging_mode=BuildStagingMode.SINGLE_PASS,
                disposable_sandbox=True,
                unbounded_resources=True,
                authority_id="authority.user",
                source_refs=(brief.raw_request_ref,),
            ),
        ).policy

        self.assertIs(
            policy.resource_mode,
            ResourcePolicyMode.CREATIVE,
        )
        self.assertIs(
            policy.staging_mode,
            BuildStagingMode.SINGLE_PASS,
        )
        self.assertTrue(policy.disposable_sandbox)
        self.assertTrue(policy.unbounded_resources)
        self.assertNotIn(
            "resolve.resource.availability",
            {item.obligation_id for item in policy.obligations},
        )
        self.assertEqual(BuildPolicy.from_dict(policy.to_dict()), policy)
        snapshot = build_constructability_snapshot(policy)
        self.assertTrue(snapshot.to_dict()["read_only"])
        self.assertFalse(snapshot.to_dict()["palette_authority"])

    def test_survival_policy_preserves_supply_demand_budget_and_staging(
        self,
    ) -> None:
        brief, program, site = _contexts()
        inventory_ref = _source("site-flat", "inventory")
        estimate_ref = _source("site-flat", "resource-estimate")
        result = compile_build_policy(
            brief=brief,
            program=program,
            site_context=site,
            proposal=BuildPolicyProposal(
                resource_mode=ResourcePolicyMode.SURVIVAL,
                staging_mode=BuildStagingMode.STAGED,
                disposable_sandbox=False,
                unbounded_resources=False,
                authority_id="authority.user",
                source_refs=(brief.raw_request_ref,),
                evidence_refs=(inventory_ref, estimate_ref),
                assumptions=(
                    BuildAssumptionProposal(
                        assumption_id="pre-topology-estimate",
                        statement=(
                            "The resource demand is a pre-topology range and "
                            "must be revised after an alternative is selected."
                        ),
                        authority_id="agent.constructability",
                        source_refs=(estimate_ref,),
                    ),
                ),
                availability=(
                    ResourceAvailabilityProposal(
                        resource_ref="resource:structural-unit",
                        minimum_available=100,
                        maximum_available=120,
                        unit="blocks",
                        epistemic_status=FactEpistemicStatus.OBSERVED,
                        authority_id="authority.inventory-observer",
                        source_refs=(inventory_ref,),
                    ),
                ),
                demands=(
                    ResourceDemandProposal(
                        demand_id="structural-demand",
                        resource_ref="resource:structural-unit",
                        minimum_required=70,
                        maximum_required=90,
                        unit="blocks",
                        authority_id="agent.constructability",
                        source_refs=(estimate_ref,),
                        assumption_ids=("pre-topology-estimate",),
                    ),
                ),
                budget_limits=(
                    BuildBudgetProposal(
                        budget_id="operation-budget",
                        metric="operation-count",
                        maximum=400,
                        unit="operations",
                        authority_id="authority.user",
                        source_refs=(brief.raw_request_ref,),
                    ),
                ),
                staging_assumptions=(
                    StagingAssumptionProposal(
                        stage_id="first-bounded-stage",
                        statement=(
                            "A first bounded stage must complete before the "
                            "second stage begins."
                        ),
                        predecessor_ids=(),
                        authority_id="agent.constructability",
                        source_refs=(estimate_ref,),
                        assumption_ids=("pre-topology-estimate",),
                    ),
                    StagingAssumptionProposal(
                        stage_id="second-bounded-stage",
                        statement=(
                            "The second bounded stage depends on the first."
                        ),
                        predecessor_ids=("first-bounded-stage",),
                        authority_id="agent.constructability",
                        source_refs=(estimate_ref,),
                        assumption_ids=("pre-topology-estimate",),
                    ),
                ),
            ),
        )
        policy = result.policy

        self.assertIs(
            policy.resource_mode,
            ResourcePolicyMode.SURVIVAL,
        )
        self.assertIs(
            policy.staging_mode,
            BuildStagingMode.STAGED,
        )
        self.assertEqual(len(policy.availability), 1)
        self.assertEqual(len(policy.demands), 1)
        self.assertEqual(len(policy.budget_limits), 1)
        self.assertEqual(len(policy.staging_assumptions), 2)
        self.assertFalse(
            any(
                ".shortage." in item.obligation_id
                for item in policy.obligations
            )
        )
        self.assertEqual(
            result.receipt.policy_digest,
            policy.policy_digest,
        )

    def test_material_shortage_creates_obligation_not_substitute_palette(
        self,
    ) -> None:
        brief, program, site = _contexts()
        inventory_ref = _source("site-flat", "inventory")
        estimate_ref = _source("site-flat", "resource-estimate")
        policy = compile_build_policy(
            brief=brief,
            program=program,
            site_context=site,
            proposal=BuildPolicyProposal(
                resource_mode=ResourcePolicyMode.SURVIVAL,
                staging_mode=BuildStagingMode.SINGLE_PASS,
                disposable_sandbox=False,
                unbounded_resources=False,
                authority_id="authority.user",
                source_refs=(brief.raw_request_ref,),
                assumptions=(
                    BuildAssumptionProposal(
                        assumption_id="demand-estimate",
                        statement="Demand remains a bounded estimate.",
                        authority_id="agent.constructability",
                        source_refs=(estimate_ref,),
                    ),
                ),
                availability=(
                    ResourceAvailabilityProposal(
                        resource_ref="resource:limited-unit",
                        minimum_available=30,
                        maximum_available=50,
                        unit="blocks",
                        epistemic_status=FactEpistemicStatus.OBSERVED,
                        authority_id="authority.inventory-observer",
                        source_refs=(inventory_ref,),
                    ),
                ),
                demands=(
                    ResourceDemandProposal(
                        demand_id="limited-demand",
                        resource_ref="resource:limited-unit",
                        minimum_required=80,
                        maximum_required=100,
                        unit="blocks",
                        authority_id="agent.constructability",
                        source_refs=(estimate_ref,),
                        assumption_ids=("demand-estimate",),
                    ),
                ),
            ),
        ).policy

        self.assertIn(
            "resolve.resource.shortage.limited-demand",
            {item.obligation_id for item in policy.obligations},
        )
        self.assertFalse(policy.to_dict()["palette_selected"])
        self.assertNotIn("substitute", policy.to_dict())

    def test_site_protected_cells_compile_into_protection_rule(self) -> None:
        brief, program, site = _contexts(
            project_id="site-protected",
            digest_char="c",
            fixture="protected_envelope.json",
            world_id="fixture-protected-world",
        )
        policy = compile_build_policy(
            brief=brief,
            program=program,
            site_context=site,
            proposal=BuildPolicyProposal(
                resource_mode=ResourcePolicyMode.CREATIVE,
                staging_mode=BuildStagingMode.SINGLE_PASS,
                disposable_sandbox=True,
                unbounded_resources=True,
                authority_id="authority.user",
                source_refs=(brief.raw_request_ref,),
            ),
        ).policy

        rule = next(
            item
            for item in policy.protected_rules
            if item.rule_id == "site-protected-cells"
        )
        self.assertIs(
            rule.action,
            ProtectedBlockAction.DO_NOT_REPLACE,
        )
        self.assertIn(
            site.context_digest,
            rule.target_ref,
        )

    def test_unknown_and_non_unbounded_policies_do_not_fall_back_to_creative(
        self,
    ) -> None:
        brief, program, site = _contexts()
        for resource_mode in (
            ResourcePolicyMode.UNKNOWN,
            ResourcePolicyMode.CREATIVE,
            ResourcePolicyMode.SURVIVAL,
            ResourcePolicyMode.EXTERNALLY_SUPPLIED,
        ):
            with self.subTest(resource_mode=resource_mode.value):
                policy = compile_build_policy(
                    brief=brief,
                    program=program,
                    site_context=site,
                    proposal=BuildPolicyProposal(
                        resource_mode=resource_mode,
                        staging_mode=BuildStagingMode.SINGLE_PASS,
                        disposable_sandbox=False,
                        unbounded_resources=False,
                        authority_id="authority.user",
                        source_refs=(brief.raw_request_ref,),
                    ),
                ).policy
                self.assertIs(policy.resource_mode, resource_mode)
                self.assertIs(
                    policy.staging_mode,
                    BuildStagingMode.SINGLE_PASS,
                )
                self.assertFalse(policy.unbounded_resources)
                self.assertIn(
                    "resolve.resource.availability",
                    {
                        item.obligation_id
                        for item in policy.obligations
                    },
                )

        staged = compile_build_policy(
            brief=brief,
            program=program,
            site_context=site,
            proposal=BuildPolicyProposal(
                resource_mode=ResourcePolicyMode.SURVIVAL,
                staging_mode=BuildStagingMode.STAGED,
                disposable_sandbox=False,
                unbounded_resources=False,
                authority_id="authority.user",
                source_refs=(brief.raw_request_ref,),
            ),
        ).policy
        self.assertIn(
            "resolve.build-policy.staging",
            {item.obligation_id for item in staged.obligations},
        )
        unknown_staging = compile_build_policy(
            brief=brief,
            program=program,
            site_context=site,
            proposal=BuildPolicyProposal(
                resource_mode=ResourcePolicyMode.CREATIVE,
                staging_mode=BuildStagingMode.UNKNOWN,
                disposable_sandbox=True,
                unbounded_resources=True,
                authority_id="authority.user",
                source_refs=(brief.raw_request_ref,),
            ),
        ).policy
        self.assertIn(
            "resolve.build-policy.staging-mode",
            {
                item.obligation_id
                for item in unknown_staging.obligations
            },
        )

        with self.assertRaises(ResourceCompilationError):
            compile_build_policy(
                brief=brief,
                program=program,
                site_context=site,
                proposal=BuildPolicyProposal(
                    resource_mode=ResourcePolicyMode.SURVIVAL,
                    staging_mode=BuildStagingMode.SINGLE_PASS,
                    disposable_sandbox=False,
                    unbounded_resources=True,
                    authority_id="authority.user",
                    source_refs=(brief.raw_request_ref,),
                ),
            )


if __name__ == "__main__":
    unittest.main()

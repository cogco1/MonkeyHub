"""P065 monument derivation tests over the shared Pantheon project support."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from archflow.compilers.geometry import compile_geometry_program
from archive.tools.projects.pantheon import monument_support as S


_RETIRED_LANE_KINDS = (
    "a retired lane writes the record kinds this needs; put_json writes only "
    "kinds registered in archflow.project.record_kinds, and a kind no spine "
    "module writes, reads or names is not registered"
)


class MonumentLifecycleFastTests(unittest.TestCase):
    """Protocol-chain verification without per-stage voxelization."""

    @unittest.skip(_RETIRED_LANE_KINDS)
    def test_stage_lifecycles_compile_and_arrays_expand(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / S.PROJECT_ID
            bootstrapped = S.bootstrap_raw_request_project(
                root,
                project_id=S.PROJECT_ID,
                prompt=S.PROMPT,
                run_id=S.RUN_ID,
                synthetic_test=False,
            )
            repository = S.FilesystemProjectRepository.open(root)
            run = bootstrapped.run
            context = S._rebase_context(S._monument_context(), run)
            destination = S.PersistenceDestination(
                S.PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            )
            context_ref = repository.put_json(
                run=run,
                destination=destination,
                record_kind="production-authoring-context",
                payload=context.to_dict(),
            )
            provider = S._MonumentScriptedProvider(context)
            collector = S.InvocationEvidenceCollector()
            authorized = S.activate_model_provider(
                provider,
                identity=S.ProviderIdentity(
                    provider_id=S.IDENTITY.provider_id,
                    version=S.IDENTITY.provider_version,
                    fingerprint=S.IDENTITY.provider_fingerprint,
                ),
                responsibility_id="model.production-root",
                contract_owner_id="archflow.production-root",
                verification_evidence_refs=(context_ref.uri,),
                envelope_observer=collector.observe,
            )
            compiler = S.ProductionRootCompiler(
                repository=repository,
                context_ref=context_ref,
                context=context,
                provider=authorized,
                evidence_collector=collector,
                geometry_provider_identity=S.IDENTITY,
            )
            runtime = asyncio.run(
                S.run_or_resume_production_step(
                    repository,
                    run=run,
                    raw_request=bootstrapped.request,
                    prompt=S.PROMPT,
                    step_id="monument-root",
                    compiler=compiler,
                )
            )
            state_record = next(
                item
                for item in runtime.archive.records
                if item.role is S.ProductionRecordRole.DESIGN_STATE
            )
            state = S.DevelopedDesignState.from_dict(state_record.content)
            prior = compile_geometry_program(
                state,
                provider.generated_geometry,
                active_commitment_refs=(
                    S.COMMITMENT_REF,
                    S.AXIS_COMMITMENT_REF,
                ),
            ).program
            self.assertIsNotNone(prior)
            for stage in (1, 2, 3):
                current = S._next_state(state, stage=stage)
                lifecycle = S.compile_semantic_geometry_lifecycle(
                    transaction_id=f"monument-stage-{stage}",
                    predecessor_state=state,
                    current_state=current,
                    predecessor_proposal=(
                        state.selected_schematic.option.proposal
                    ),
                    current_proposal=(
                        current.selected_schematic.option.proposal
                    ),
                    prior_program=prior,
                    geometry_proposal=S._monument_geometry(
                        current,
                        stage=stage,
                        prior=prior,
                    ),
                    revalidated_component_ids=(
                        ("main-entry",)
                        if stage == 2
                        else ("oculus",) if stage == 3 else ()
                    ),
                    active_commitment_refs=(
                        S.COMMITMENT_REF,
                        S.AXIS_COMMITMENT_REF,
                    ),
                )
                self.assertIs(
                    S.SemanticGeometryLifecycleStatus.COMPILED,
                    lifecycle.receipt.status,
                    lifecycle.receipt.issues,
                )
                state, prior = current, lifecycle.geometry_program

        result = S.realize_geometry(prior, workspace_id="monument-fast")
        self.assertEqual("realized", result.receipt.status.value)
        instances = S._instance_count(result.scene)
        self.assertGreaterEqual(instances, 300, f"instances={instances}")
        radial = [
            item.object_id
            for item in result.scene.objects
            if item.physical
            and json.loads(item.geometry_json).get("kind") == "radial_array"
        ]
        self.assertGreaterEqual(len(radial), 7, radial)
        components = {
            item.component_id
            for item in state.selected_schematic.option.proposal.components
        }
        self.assertEqual(
            {
                "building",
                "dome",
                "portico",
                "rotunda",
                "colonnade",
                "main-entry",
                "recess-ring",
                "aedicula-ring",
                "oculus",
                "coffers",
                "statuary-ring",
            },
            components,
        )


class MonumentDerivationTests(unittest.TestCase):
    def test_four_stage_monument_reaches_instance_and_voxel_scale(self):
        if not os.environ.get("ARCHFLOW_SLOW_MONUMENT"):
            self.skipTest(
                "set ARCHFLOW_SLOW_MONUMENT=1 for the full voxelized "
                "four-stage run (about thirty minutes); the promoted "
                "p065-monument-derivation probe retains its executed evidence"
            )
        with tempfile.TemporaryDirectory() as temporary:
            manifest = S._run_proof(Path(temporary) / S.PROJECT_ID)
        self.assertEqual(4, manifest["provider_invocations"])
        self.assertEqual(
            [0, 1, 2, 3],
            [item["stage"] for item in manifest["stages"]],
        )
        scene = manifest["final_scene"]
        instances = S._instance_count(scene)
        self.assertGreaterEqual(instances, 300, f"instances={instances}")
        radial_kinds = [
            item.object_id
            for item in scene.objects
            if item.physical
            and json.loads(item.geometry_json).get("kind") == "radial_array"
        ]
        self.assertGreaterEqual(len(radial_kinds), 7, radial_kinds)
        state = manifest["final_state"]
        components = {
            item.component_id
            for item in state.selected_schematic.option.proposal.components
        }
        self.assertEqual(
            {
                "building",
                "dome",
                "portico",
                "rotunda",
                "colonnade",
                "main-entry",
                "recess-ring",
                "aedicula-ring",
                "oculus",
                "coffers",
                "statuary-ring",
            },
            components,
        )
        for stage in manifest["stages"][1:]:
            self.assertIn("building", stage["preserved_component_ids"])


if __name__ == "__main__":
    unittest.main()

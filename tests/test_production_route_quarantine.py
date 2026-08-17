from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from archflow.adapters.minecraft_mcp import (
    MinecraftExportRequest,
    MinecraftMcpAdapter,
    MinecraftMcpConfig,
)
from archflow.production import (
    InvocationEvidenceCollector,
    ProviderIdentity,
    activate_model_provider,
)
from archflow.runtime.design_controller import prepare_design_turn
from archflow.runtime.primary_architect import run_primary_architect_turn
from archflow.runtime.production_compiler import ProductionRootCompiler
from archflow.state import ArtifactRef
from tests.test_design_controller import _checkpoint, _experts
from tests.test_production_root_compiler import (
    IDENTITY,
    _MemoryRepository,
    _context_and_options,
)


ROOT = Path(__file__).resolve().parents[1]


class _MarkedTestOnlyProvider:
    __archflow_test_only__ = True

    async def invoke(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError(f"test-only provider was invoked: {request}")


class _RawProvider:
    async def invoke(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError(f"raw provider was invoked: {request}")


class ProductionRouteQuarantineTests(unittest.IsolatedAsyncioTestCase):
    def test_runtime_module_delegates_to_formal_cli(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "archflow.runtime", "--help"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("run-project", completed.stdout)
        self.assertNotIn("FakeArchitect", completed.stdout)
        self.assertNotIn("FakeVoxel", completed.stdout)
        entry = (ROOT / "archflow/runtime/__main__.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("walking_skeleton", entry)

    def test_marked_test_provider_cannot_receive_production_authority(self) -> None:
        with self.assertRaisesRegex(TypeError, "test-only"):
            activate_model_provider(
                _MarkedTestOnlyProvider(),
                identity=ProviderIdentity(
                    provider_id="provider.test-only",
                    version="1",
                    fingerprint="a" * 64,
                ),
                responsibility_id="model.quarantine-test",
                contract_owner_id="tests.quarantine",
                verification_evidence_refs=("evidence:test-only",),
            )

    async def test_primary_architect_rejects_direct_provider_injection(self) -> None:
        checkpoint, _ = _checkpoint()
        registry, metadata = _experts()
        prepared = prepare_design_turn(
            checkpoint,
            registry,
            phase_metadata=metadata,
            obligation_topics={"resolve-grid": "structure"},
        )
        raw = _RawProvider()

        with self.assertRaisesRegex(TypeError, "P053-authorized"):
            await run_primary_architect_turn(
                checkpoint,
                prepared,
                registry,
                raw,  # type: ignore[arg-type]
                history_event_ref="design-event:raw-provider-rejected",
            )

    def test_active_geometry_compiler_rejects_direct_provider(self) -> None:
        context, _ = _context_and_options()
        repository = _MemoryRepository()
        context_ref = repository.put_json(
            run=context.run,
            destination=None,
            record_kind="production-context",
            payload=context.to_dict(),
        )

        with self.assertRaisesRegex(TypeError, "P053-authorized"):
            ProductionRootCompiler(
                repository=repository,
                context_ref=context_ref,
                context=context,
                provider=_RawProvider(),  # type: ignore[arg-type]
                evidence_collector=InvocationEvidenceCollector(),
                geometry_provider_identity=IDENTITY,
            )

    def test_downstream_minecraft_route_requires_typed_export_request(self) -> None:
        adapter = MinecraftMcpAdapter(
            MinecraftMcpConfig(command=("never-invoked",))
        )
        with self.assertRaisesRegex(TypeError, "compatibility-only"):
            adapter.preview_export(  # type: ignore[arg-type]
                object(),
                object(),
                {"cuboids": []},
            )

        request = MinecraftExportRequest.create(
            request_id="typed-downstream-export",
            source_artifact=ArtifactRef(
                artifact_id="accepted-neutral-package",
                uri=(
                    "project://quarantine/runs/run-001/exports/"
                    "neutral-building-package.json"
                ),
                media_type=MinecraftExportRequest.NEUTRAL_MEDIA_TYPE,
                sha256="b" * 64,
            ),
            acceptance_receipt_ref=(
                "project://quarantine/runs/run-001/records/"
                "neutral-package-acceptance.json"
            ),
            translated_plan={"schema": "MinecraftTranslatedPlan@1"},
        )
        payload = request.to_dict()
        self.assertFalse(payload["design_authority"])
        self.assertFalse(payload["validation_authority"])
        self.assertFalse(payload["persistence_authority"])

    def test_formal_runtime_has_no_candidate_or_mcp_fallback(self) -> None:
        active_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                ROOT / "archflow/project/runtime.py",
                ROOT / "archflow/runtime/production_runtime.py",
                ROOT / "archflow/runtime/production_compiler.py",
            )
        )
        self.assertNotIn("FakeVoxelAdapter", active_sources)
        self.assertNotIn("FakeArchitect", active_sources)
        self.assertNotIn("candidate_assembly", active_sources)
        self.assertNotIn("MinecraftMcpAdapter", active_sources)
        self.assertNotIn("fallback", active_sources.lower())


if __name__ == "__main__":
    unittest.main()

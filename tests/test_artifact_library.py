from __future__ import annotations

from copy import deepcopy
import unittest

from archflow.adapters.sandbox_render import render_paper_views
from archflow.project.refs import ProjectVersionRef
from archflow.runtime.artifact_library import (
    ArtifactLibraryError,
    ExportEquivalence,
    ImportedPackageReference,
    NeutralBuildingPackage,
    PackageEvidenceRecord,
    PackageRecordRole,
    PlatformExportReceipt,
    PlatformTarget,
    create_neutral_building_package,
)
from archflow.runtime.staged_build import create_build_checkpoint
from archflow.state import ArtifactRef
from tests.test_design_development import EVIDENCE
from tests.test_staged_build import staged_fixture


def neutral_package_fixture(
    *,
    base: ProjectVersionRef | None = None,
) -> NeutralBuildingPackage:
    candidate, policy, program, realized, account, plan = staged_fixture(base=base)
    assert realized.scene is not None
    rendered = render_paper_views(realized.scene)
    authority = PackageEvidenceRecord.create(
        record_id="save-authority",
        role=PackageRecordRole.AUTHORITY,
        authority_id="authority.user",
        payload={
            "schema": "SaveAuthority@1",
            "decision": "save-neutral-package",
            "candidate_digest": candidate.assembly_digest,
        },
        evidence_refs=(EVIDENCE,),
    )
    validation = PackageEvidenceRecord.create(
        record_id="sandbox-validation",
        role=PackageRecordRole.VALIDATION,
        authority_id="validator.sandbox",
        payload={
            "schema": "SandboxValidation@1",
            "scene_digest": realized.scene.scene_digest,
            "status": "passed",
        },
        evidence_refs=(EVIDENCE,),
    )
    return create_neutral_building_package(
        candidate=candidate,
        build_policy=policy,
        geometry_program=program,
        scene=realized.scene,
        realization_receipt=realized.receipt,
        render_set=rendered,
        material_account=account,
        staged_plan=plan,
        checkpoint=create_build_checkpoint(plan),
        evidence_records=(authority, validation),
    )


class NeutralBuildingPackageTests(unittest.TestCase):
    def test_package_retains_artifact_derivation_receipts_views_and_authority(self) -> None:
        package = neutral_package_fixture()
        reloaded = NeutralBuildingPackage.from_dict(package.to_dict())

        self.assertEqual(reloaded, package)
        self.assertEqual(reloaded.package_digest, package.package_digest)
        self.assertEqual(reloaded.geometry_program.program_digest, reloaded.scene.geometry_program_digest)
        self.assertIn("Transverse section A", reloaded.readme)
        self.assertIn("Longitudinal section B", reloaded.readme)
        self.assertIn("execution", reloaded.readme.lower())
        self.assertFalse(reloaded.to_dict()["generation_authority"])
        self.assertFalse(reloaded.to_dict()["execution_replay"])

    def test_cross_digest_or_missing_view_cannot_reload(self) -> None:
        package = neutral_package_fixture()
        payload = package.to_dict()
        payload["staged_plan"]["scene_digest"] = "f" * 64
        with self.assertRaises(ValueError):
            NeutralBuildingPackage.from_dict(payload)

        missing_view = deepcopy(package.to_dict())
        missing_view["render_set"]["views"].pop()
        with self.assertRaises(ValueError):
            NeutralBuildingPackage.from_dict(missing_view)

    def test_readme_and_evidence_authority_cannot_be_stripped(self) -> None:
        package = neutral_package_fixture()
        no_authority = package.to_dict()
        no_authority["evidence_records"] = [
            item
            for item in no_authority["evidence_records"]
            if item["role"] != PackageRecordRole.AUTHORITY.value
        ]
        with self.assertRaisesRegex(ArtifactLibraryError, "authority"):
            NeutralBuildingPackage.from_dict(no_authority)

        changed_readme = package.to_dict()
        changed_readme["readme"] += "untracked note"
        with self.assertRaisesRegex(ArtifactLibraryError, "README"):
            NeutralBuildingPackage.from_dict(changed_readme)


class PlatformExportBoundaryTests(unittest.TestCase):
    def test_exact_lossy_and_failed_exports_are_explicit_and_source_immutable(self) -> None:
        package = neutral_package_fixture()
        source_digest = package.package_digest
        artifact = ArtifactRef(
            artifact_id="building-schematic",
            uri="project://portfolio-project/objects/building-schematic",
            media_type="application/vnd.schematic",
            sha256="9" * 64,
        )
        exact = PlatformExportReceipt(
            source_package_digest=source_digest,
            source_geometry_digest=package.geometry_program.program_digest,
            target=PlatformTarget.SCHEM,
            adapter_id="adapter.schem",
            adapter_version="1.0",
            equivalence=ExportEquivalence.EXACT,
            artifact=artifact,
            loss_codes=(),
            evidence_refs=(EVIDENCE,),
        )
        lossy = PlatformExportReceipt(
            source_package_digest=source_digest,
            source_geometry_digest=package.geometry_program.program_digest,
            target=PlatformTarget.REVIT,
            adapter_id="adapter.revit",
            adapter_version="0.1",
            equivalence=ExportEquivalence.LOSSY,
            artifact=artifact,
            loss_codes=("analytic-surface-tessellated",),
            evidence_refs=(EVIDENCE,),
        )
        failed = PlatformExportReceipt(
            source_package_digest=source_digest,
            source_geometry_digest=package.geometry_program.program_digest,
            target=PlatformTarget.MINECRAFT,
            adapter_id="adapter.minecraft",
            adapter_version="0.1",
            equivalence=ExportEquivalence.FAILED,
            artifact=None,
            loss_codes=("adapter-unavailable",),
            evidence_refs=(EVIDENCE,),
        )
        for receipt in (exact, lossy, failed):
            self.assertEqual(PlatformExportReceipt.from_dict(receipt.to_dict()), receipt)
            self.assertEqual(receipt.source_package_digest, source_digest)
            self.assertFalse(receipt.to_dict()["source_geometry_mutated"])
        self.assertEqual(package.package_digest, source_digest)

    def test_export_cannot_hide_loss_or_claim_output_after_failure(self) -> None:
        package = neutral_package_fixture()
        with self.assertRaisesRegex(ArtifactLibraryError, "explicit losses"):
            PlatformExportReceipt(
                source_package_digest=package.package_digest,
                source_geometry_digest=package.geometry_program.program_digest,
                target=PlatformTarget.RHINO,
                adapter_id="adapter.rhino",
                adapter_version="0.1",
                equivalence=ExportEquivalence.LOSSY,
                artifact=ArtifactRef("rhino", "project://portfolio-project/objects/rhino", "model/3dm", "8" * 64),
                loss_codes=(),
                evidence_refs=(EVIDENCE,),
            )


class ImportedPackageTests(unittest.TestCase):
    def test_import_remains_reference_candidate_without_provider_authority(self) -> None:
        package = neutral_package_fixture()
        reference = ImportedPackageReference(
            source_artifact=ArtifactRef(
                artifact_id="community-package",
                uri="https://example.invalid/packages/community.json",
                media_type="application/json",
                sha256="7" * 64,
            ),
            package_digest=package.package_digest,
            project_id=package.project_id,
            evidence_refs=(EVIDENCE,),
        )
        self.assertEqual(ImportedPackageReference.from_dict(reference.to_dict()), reference)
        payload = reference.to_dict()
        payload["generation_authority"] = True
        with self.assertRaisesRegex(ArtifactLibraryError, "production authority"):
            ImportedPackageReference.from_dict(payload)


class RetainedProgramGenerationTests(unittest.TestCase):
    """@2 canonical program records (pre-P090) must still wrap exactly."""

    def _legacy_payload(self) -> dict:
        from tests.test_sandbox_realization import compiled_room

        _, program, _ = compiled_room()
        payload = {
            key: item for key, item in program.to_dict().items()
            if key not in ("interface_datums", "datum_bindings")
        }
        payload["schema"] = "CompiledGeometryProgram@2"
        return payload

    def test_retained_generation_wraps(self) -> None:
        from archflow.runtime.artifact_library import (
            CanonicalProgramRecord,
            _canonical,
            _digest,
        )

        payload = self._legacy_payload()
        record = CanonicalProgramRecord(
            program_json=_canonical(payload), program_digest=_digest(payload)
        )
        self.assertEqual(record.payload["schema"], "CompiledGeometryProgram@2")

    def test_unknown_generation_is_refused(self) -> None:
        from archflow.runtime.artifact_library import (
            ArtifactLibraryError,
            CanonicalProgramRecord,
            _canonical,
            _digest,
        )

        payload = self._legacy_payload()
        payload["schema"] = "CompiledGeometryProgram@1"
        with self.assertRaises(ArtifactLibraryError):
            CanonicalProgramRecord(
                program_json=_canonical(payload), program_digest=_digest(payload)
            )

    def test_accepted_schemas_single_sourced(self) -> None:
        from archflow.capabilities.geometry_proposal import (
            _COMPILED_PROGRAM_SCHEMA_KEYS,
        )
        from archflow.compilers.geometry import CompiledGeometryProgram

        self.assertEqual(
            set(_COMPILED_PROGRAM_SCHEMA_KEYS),
            set(CompiledGeometryProgram.ACCEPTED_SCHEMAS),
        )


if __name__ == "__main__":
    unittest.main()

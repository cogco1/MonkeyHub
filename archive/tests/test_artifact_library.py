from __future__ import annotations

from copy import deepcopy
import unittest

from archive.archflow.adapters.sandbox_render import render_paper_views
from archflow.project.refs import ProjectVersionRef
from archive.archflow.runtime.artifact_library import (
    NeutralBuildingPackage,
    PackageEvidenceRecord,
    PackageRecordRole,
    create_neutral_building_package,
)
from archive.archflow.runtime.staged_build import create_build_checkpoint
from archive.tests.test_design_development import EVIDENCE
from archive.tests.test_staged_build import staged_fixture


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


if __name__ == "__main__":
    unittest.main()

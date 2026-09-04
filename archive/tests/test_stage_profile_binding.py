from __future__ import annotations

import copy
import unittest

from archive.archflow.control.profile import (
    StageProfileBindingError,
    StageRequirementProfileBinding,
)
from archflow.project.refs import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)


def _branch(*, branch_id: str = "option-a") -> BranchRef:
    base = ProjectVersionRef(
        project_id="profile-fixture",
        version=0,
        state_sha256="a" * 64,
    )
    return BranchRef(
        run=RunRef(
            project_id=base.project_id,
            run_id="design-001",
            base=base,
        ),
        branch_id=branch_id,
        epoch=4,
    )


def _record(path: str, digest: str) -> ProjectRecordRef:
    return ProjectRecordRef(
        project_id="profile-fixture",
        relative_path=path,
        sha256=digest,
    )


def _binding(
    *,
    profile_digest: str = "b" * 64,
    inventory_digest: str = "1" * 64,
    branch: BranchRef | None = None,
) -> StageRequirementProfileBinding:
    exact_branch = _branch() if branch is None else branch
    prefix = (
        f"runs/{exact_branch.run.run_id}/branches/"
        f"{exact_branch.branch_id}/records"
    )
    return StageRequirementProfileBinding(
        binding_id="stage-profile-binding",
        profile_id="full-building-stage",
        profile_digest=profile_digest,
        branch=exact_branch,
        stage_id="schematic_design",
        stage_subject_ref="artifact://candidate/model",
        subject_digest="c" * 64,
        profile_ref=_record(
            f"{prefix}/stage-requirement-profile.json",
            profile_digest,
        ),
        stage_subject_inventory_ref=_record(
            f"{prefix}/stage-subject-inventory.json",
            "2" * 64,
        ),
        stage_subject_inventory_digest=inventory_digest,
        authority_refs=(
            _record(
                f"{prefix}/human-stage-authorization.json",
                "e" * 64,
            ),
            _record(
                f"{prefix}/project-policy-authorization.json",
                "d" * 64,
            ),
        ),
    )


class StageRequirementProfileBindingTests(unittest.TestCase):
    def test_legacy_v1_is_strictly_read_only(self) -> None:
        payload = _binding().to_dict()
        inventory_payload = copy.deepcopy(
            payload["stage_subject_inventory_ref"]
        )
        payload["schema"] = "StageRequirementProfileBinding@1"
        del payload["stage_subject_inventory_ref"]
        del payload["stage_subject_inventory_digest"]

        first = StageRequirementProfileBinding.from_dict(payload)
        second = StageRequirementProfileBinding.from_dict(copy.deepcopy(payload))
        self.assertEqual(first, second)
        self.assertEqual(first.binding_digest, second.binding_digest)
        self.assertTrue(first.is_legacy_read_only)
        self.assertIsNone(first.stage_subject_inventory_ref)
        self.assertIsNone(first.stage_subject_inventory_digest)
        with self.assertRaisesRegex(StageProfileBindingError, "read-only"):
            first.to_dict()

        legacy_with_inventory = copy.deepcopy(payload)
        legacy_with_inventory["stage_subject_inventory_ref"] = (
            inventory_payload
        )
        with self.assertRaisesRegex(StageProfileBindingError, "unsupported"):
            StageRequirementProfileBinding.from_dict(legacy_with_inventory)


if __name__ == "__main__":
    unittest.main()

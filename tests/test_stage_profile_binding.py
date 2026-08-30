from __future__ import annotations

import copy
import unittest

from archflow.control.profile import (
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
    def test_round_trip_is_order_invariant_and_fixed(self) -> None:
        binding = _binding()
        reversed_binding = StageRequirementProfileBinding(
            binding_id=binding.binding_id,
            profile_id=binding.profile_id,
            profile_digest=binding.profile_digest,
            branch=binding.branch,
            stage_id=binding.stage_id,
            stage_subject_ref=binding.stage_subject_ref,
            subject_digest=binding.subject_digest,
            profile_ref=binding.profile_ref,
            stage_subject_inventory_ref=(
                binding.stage_subject_inventory_ref
            ),
            stage_subject_inventory_digest=(
                binding.stage_subject_inventory_digest
            ),
            authority_refs=tuple(reversed(binding.authority_refs)),
        )

        self.assertEqual(binding, reversed_binding)
        self.assertEqual(binding.binding_digest, reversed_binding.binding_digest)
        self.assertEqual(
            binding,
            StageRequirementProfileBinding.from_dict(binding.to_dict()),
        )
        self.assertEqual(
            "85dcf074e65620628c348345a1f44db04260a0d47fa8b5b91d6525a5e85ab3e4",
            binding.binding_digest,
        )
        self.assertEqual(
            "StageRequirementProfileBinding@2",
            binding.to_dict()["schema"],
        )
        self.assertNotEqual(
            binding.stage_subject_inventory_digest,
            binding.stage_subject_inventory_ref.sha256,
        )
        self.assertFalse(binding.to_dict()["stage_acceptance_authority"])
        self.assertFalse(binding.to_dict()["canonical_write_authority"])

    def test_profile_record_must_identify_exact_digest_and_branch(self) -> None:
        binding = _binding()
        with self.assertRaisesRegex(
            StageProfileBindingError,
            "digest does not identify",
        ):
            StageRequirementProfileBinding(
                binding_id=binding.binding_id,
                profile_id=binding.profile_id,
                profile_digest=binding.profile_digest,
                branch=binding.branch,
                stage_id=binding.stage_id,
                stage_subject_ref=binding.stage_subject_ref,
                subject_digest=binding.subject_digest,
                profile_ref=_record(
                    binding.profile_ref.relative_path,
                    "f" * 64,
                ),
                stage_subject_inventory_ref=(
                    binding.stage_subject_inventory_ref
                ),
                stage_subject_inventory_digest=(
                    binding.stage_subject_inventory_digest
                ),
                authority_refs=binding.authority_refs,
            )
        with self.assertRaisesRegex(
            StageProfileBindingError,
            "exact branch",
        ):
            StageRequirementProfileBinding(
                binding_id=binding.binding_id,
                profile_id=binding.profile_id,
                profile_digest=binding.profile_digest,
                branch=_branch(branch_id="option-b"),
                stage_id=binding.stage_id,
                stage_subject_ref=binding.stage_subject_ref,
                subject_digest=binding.subject_digest,
                profile_ref=binding.profile_ref,
                stage_subject_inventory_ref=(
                    binding.stage_subject_inventory_ref
                ),
                stage_subject_inventory_digest=(
                    binding.stage_subject_inventory_digest
                ),
                authority_refs=binding.authority_refs,
            )

    def test_inventory_record_and_semantic_digests_are_distinct(self) -> None:
        binding = _binding()
        assert binding.stage_subject_inventory_ref is not None
        assert binding.stage_subject_inventory_digest is not None
        with self.assertRaisesRegex(
            TypeError,
            "stage_subject_inventory_ref",
        ):
            StageRequirementProfileBinding(
                binding_id=binding.binding_id,
                profile_id=binding.profile_id,
                profile_digest=binding.profile_digest,
                branch=binding.branch,
                stage_id=binding.stage_id,
                stage_subject_ref=binding.stage_subject_ref,
                subject_digest=binding.subject_digest,
                profile_ref=binding.profile_ref,
                stage_subject_inventory_ref=None,
                stage_subject_inventory_digest=None,
                authority_refs=binding.authority_refs,
            )
        alternate_record = StageRequirementProfileBinding(
            binding_id=binding.binding_id,
            profile_id=binding.profile_id,
            profile_digest=binding.profile_digest,
            branch=binding.branch,
            stage_id=binding.stage_id,
            stage_subject_ref=binding.stage_subject_ref,
            subject_digest=binding.subject_digest,
            profile_ref=binding.profile_ref,
            stage_subject_inventory_ref=_record(
                binding.stage_subject_inventory_ref.relative_path,
                "3" * 64,
            ),
            stage_subject_inventory_digest=(
                binding.stage_subject_inventory_digest
            ),
            authority_refs=binding.authority_refs,
        )
        self.assertNotEqual(
            alternate_record.stage_subject_inventory_ref.sha256,
            alternate_record.stage_subject_inventory_digest,
        )

        swapped_branch = _branch(branch_id="option-b")
        with self.assertRaisesRegex(
            StageProfileBindingError,
            "exact branch",
        ):
            StageRequirementProfileBinding(
                binding_id=binding.binding_id,
                profile_id=binding.profile_id,
                profile_digest=binding.profile_digest,
                branch=swapped_branch,
                stage_id=binding.stage_id,
                stage_subject_ref=binding.stage_subject_ref,
                subject_digest=binding.subject_digest,
                profile_ref=_record(
                    (
                        "runs/design-001/branches/option-b/records/"
                        "stage-requirement-profile.json"
                    ),
                    binding.profile_digest,
                ),
                stage_subject_inventory_ref=(
                    binding.stage_subject_inventory_ref
                ),
                stage_subject_inventory_digest=(
                    binding.stage_subject_inventory_digest
                ),
                authority_refs=binding.authority_refs,
            )

        foreign_inventory = ProjectRecordRef(
            project_id="foreign-project",
            relative_path=(
                "runs/design-001/branches/option-a/records/"
                "stage-subject-inventory.json"
            ),
            sha256=binding.stage_subject_inventory_digest,
        )
        with self.assertRaisesRegex(
            StageProfileBindingError,
            "another project",
        ):
            StageRequirementProfileBinding(
                binding_id=binding.binding_id,
                profile_id=binding.profile_id,
                profile_digest=binding.profile_digest,
                branch=binding.branch,
                stage_id=binding.stage_id,
                stage_subject_ref=binding.stage_subject_ref,
                subject_digest=binding.subject_digest,
                profile_ref=binding.profile_ref,
                stage_subject_inventory_ref=foreign_inventory,
                stage_subject_inventory_digest=(
                    binding.stage_subject_inventory_digest
                ),
                authority_refs=binding.authority_refs,
            )

        with self.assertRaisesRegex(ValueError, "SHA-256"):
            StageRequirementProfileBinding(
                binding_id=binding.binding_id,
                profile_id=binding.profile_id,
                profile_digest=binding.profile_digest,
                branch=binding.branch,
                stage_id=binding.stage_id,
                stage_subject_ref=binding.stage_subject_ref,
                subject_digest=binding.subject_digest,
                profile_ref=binding.profile_ref,
                stage_subject_inventory_ref=(
                    binding.stage_subject_inventory_ref
                ),
                stage_subject_inventory_digest="not-a-sha",
                authority_refs=binding.authority_refs,
            )

    def test_authority_refs_are_required_and_project_local(self) -> None:
        binding = _binding()
        with self.assertRaisesRegex(StageProfileBindingError, "authority_refs"):
            StageRequirementProfileBinding(
                binding_id=binding.binding_id,
                profile_id=binding.profile_id,
                profile_digest=binding.profile_digest,
                branch=binding.branch,
                stage_id=binding.stage_id,
                stage_subject_ref=binding.stage_subject_ref,
                subject_digest=binding.subject_digest,
                profile_ref=binding.profile_ref,
                stage_subject_inventory_ref=(
                    binding.stage_subject_inventory_ref
                ),
                stage_subject_inventory_digest=(
                    binding.stage_subject_inventory_digest
                ),
                authority_refs=(),
            )
        foreign = ProjectRecordRef(
            project_id="foreign-project",
            relative_path="input/authority.json",
            sha256="f" * 64,
        )
        with self.assertRaisesRegex(StageProfileBindingError, "another project"):
            StageRequirementProfileBinding(
                binding_id=binding.binding_id,
                profile_id=binding.profile_id,
                profile_digest=binding.profile_digest,
                branch=binding.branch,
                stage_id=binding.stage_id,
                stage_subject_ref=binding.stage_subject_ref,
                subject_digest=binding.subject_digest,
                profile_ref=binding.profile_ref,
                stage_subject_inventory_ref=(
                    binding.stage_subject_inventory_ref
                ),
                stage_subject_inventory_digest=(
                    binding.stage_subject_inventory_digest
                ),
                authority_refs=(foreign,),
            )

    def test_schema_and_authority_tampering_fail_closed(self) -> None:
        payload = _binding().to_dict()
        payload["canonical_write_authority"] = True
        with self.assertRaisesRegex(StageProfileBindingError, "flags changed"):
            StageRequirementProfileBinding.from_dict(payload)

        extra = copy.deepcopy(_binding().to_dict())
        extra["caller_passed"] = True
        with self.assertRaisesRegex(StageProfileBindingError, "unsupported"):
            StageRequirementProfileBinding.from_dict(extra)

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

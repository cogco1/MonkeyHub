from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from archive.archflow.adapters.skill_surfaces import (
    DEFAULT_SKILL_SURFACES,
    SkillSurfaceProfile,
    codex_skill_file,
    render_skill_surface,
)
from archive.archflow.skills.contracts import AvailableTool, DetachedSkillContext, SkillAdvice, SkillProposal, ToolAccess, ToolKind
from archive.archflow.skills.runtime import SkillInvocationStatus, SkillRuntimeError, discover_skills, invoke_skill
from archive.archflow.skills.package import SkillPackageError, load_skill_package, load_skill_packages
from archive.archflow.skills.package import sealed_manifest_payload
from archflow.state.decision_operator import DecisionOperator
from archive.archflow.state.design_state import (
    ContextSlice,
    DesignStateLayer,
    StatePath,
    StatePathSegment,
)
from archflow.state.operational_state import (
    DesignObligation,
    ObligationStatus,
)


ROOT = Path(__file__).resolve().parents[2]
BUILTIN = (
    ROOT
    / "archive"
    / "archflow"
    / "skills"
    / "packages"
    / "review-design-obligations"
)


def _context() -> ContextSlice:
    obligation = DesignObligation(
        obligation_id="resolve-current-relationship",
        statement="Resolve the current component relationship.",
        source_ref="request://current/relationship",
        status=ObligationStatus.OPEN,
        subject_refs=("semantic://component/current",),
    )
    return ContextSlice(
        tree_digest="a" * 64,
        target_node_ref="design-node:component-current",
        target_state_digest="b" * 64,
        path=StatePath(
            (
                StatePathSegment(
                    DesignStateLayer.GLOBAL_CONCEPT,
                    "concept",
                ),
                StatePathSegment(
                    DesignStateLayer.PHASE,
                    "schematic",
                ),
                StatePathSegment(
                    DesignStateLayer.DISCIPLINE,
                    "coordination",
                ),
                StatePathSegment(
                    DesignStateLayer.COMPONENT,
                    "current",
                ),
            )
        ),
        ancestor_node_refs=(
            "design-node:concept",
            "design-node:schematic",
            "design-node:coordination",
        ),
        concept_facts=(),
        ancestor_facts=(),
        local_facts=(),
        commitments=(),
        obligations=(obligation,),
        phase_deliverable_refs=("deliverable://schematic/current",),
        interfaces=(),
        allowed_authority_ids=("skill-agent",),
        evidence_refs=("evidence://analysis/current",),
        omitted_node_refs=("design-node:sibling",),
    )


def _manifest(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "SkillSpec@1",
        "skill_id": "test-current-obligation",
        "version": "1.0.0",
        "package_digest": "0" * 64,
        "description": "Review a current detached obligation for a test host.",
        "instruction_entrypoint": "instructions.md",
        "reference_entrypoints": [],
        "resource_entrypoints": [],
        "input_schema": "DetachedSkillContext@1",
        "output_schemas": ["SkillAdvice@1", "SkillProposal@1"],
        "obligation_topics": ["relationship"],
        "evidence_requirements": [],
        "admissible_phases": ["schematic"],
        "tool_requirements": [],
        "side_effect_class": "candidate_only",
        "authority_ceiling": "proposal",
        "budget": {
            "timeout_seconds": 0.1,
            "max_output_bytes": 4096,
            "max_attempts": 2,
        },
    }
    payload.update(overrides)
    return payload


def _write_package(
    parent: Path,
    *,
    name: str = "package",
    manifest: dict[str, object] | None = None,
    instructions: str = "Review only the supplied detached context.\n",
    references: dict[str, str] | None = None,
) -> Path:
    root = parent / name
    root.mkdir()
    payload = _manifest() if manifest is None else manifest
    (root / "instructions.md").write_text(instructions, encoding="utf-8")
    for relative, content in (references or {}).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (root / "skill.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    sealed = sealed_manifest_payload(root.resolve())
    (root / "skill.json").write_text(
        json.dumps(sealed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return root.resolve()


def _topics() -> dict[str, str]:
    return {"resolve-current-relationship": "relationship"}


class SkillPackageTests(unittest.TestCase):
    def test_builtin_manifest_digest_round_trip_and_explicit_root_only(self) -> None:
        package = load_skill_package(BUILTIN.resolve())
        reloaded = load_skill_package(package.root)

        self.assertEqual(reloaded.spec, package.spec)
        self.assertEqual(len(package.spec.package_digest), 64)
        self.assertEqual(package.spec.skill_id, "review-design-obligations")
        with self.assertRaisesRegex(SkillPackageError, "explicit Skill root"):
            load_skill_packages(())
        with self.assertRaisesRegex(SkillPackageError, "absolute"):
            load_skill_package(Path("archflow/skills"))

    def test_loader_rejects_tamper_duplicates_missing_cross_package_and_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            original = _write_package(parent, name="original")
            duplicate = parent / "duplicate"
            shutil.copytree(original, duplicate)
            with self.assertRaisesRegex(SkillPackageError, "duplicate Skill"):
                load_skill_packages((original, duplicate.resolve()))

            (original / "instructions.md").write_text(
                "tampered\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(SkillPackageError, "digest mismatch"):
                load_skill_package(original)

            missing = _write_package(
                parent,
                name="missing",
                manifest=_manifest(
                    reference_entrypoints=["references/criteria.md"]
                ),
                references={"references/criteria.md": "Criteria.\n"},
            )
            (missing / "references" / "criteria.md").unlink()
            with self.assertRaisesRegex(SkillPackageError, "missing Skill content"):
                load_skill_package(missing)

            crossing = parent / "crossing"
            crossing.mkdir()
            (parent / "outside.md").write_text("outside\n", encoding="utf-8")
            payload = _manifest(instruction_entrypoint="../outside.md")
            (crossing / "skill.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            with self.assertRaisesRegex(SkillPackageError, "unsafe Skill"):
                load_skill_package(crossing.resolve())

            malformed = parent / "malformed"
            malformed.mkdir()
            (malformed / "instructions.md").write_text("review\n", encoding="utf-8")
            payload = _manifest(input_schema="FullTranscript@1")
            (malformed / "skill.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "unsupported Skill input"):
                load_skill_package(malformed.resolve())

    def test_discovery_intersects_current_state_and_is_order_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            first = _write_package(parent, name="first")
            second_manifest = _manifest(
                skill_id="test-evidence-tool",
                version="2.0.0",
                evidence_requirements=["observation"],
                tool_requirements=[
                    {
                        "tool_id": "lookup",
                        "kind": "retrieval",
                        "access": "read_only",
                    }
                ],
            )
            second = _write_package(
                parent,
                name="second",
                manifest=second_manifest,
            )
            packages = load_skill_packages((second, first))
            kwargs = {
                "context": _context(),
                "phase": "schematic",
                "obligation_topics": _topics(),
                "evidence_kinds": frozenset({"observation"}),
                "available_tools": (
                    AvailableTool(
                        "lookup",
                        ToolKind.RETRIEVAL,
                        ToolAccess.READ_ONLY,
                    ),
                ),
            }
            forward = discover_skills(packages, **kwargs)
            reverse = discover_skills(tuple(reversed(packages)), **kwargs)

            self.assertEqual(
                tuple(item.package.spec.skill_id for item in forward),
                ("test-current-obligation", "test-evidence-tool"),
            )
            self.assertEqual(
                tuple(item.package.spec.identity for item in reverse),
                tuple(item.package.spec.identity for item in forward),
            )
            self.assertEqual(
                discover_skills(
                    packages,
                    **{**kwargs, "phase": "construction"},
                ),
                (),
            )
            self.assertEqual(
                len(
                    discover_skills(
                        packages,
                        **{
                            **kwargs,
                            "evidence_kinds": frozenset(),
                            "available_tools": (),
                        },
                    )
                ),
                1,
            )

    def test_detached_context_exposes_no_transcript_writer_or_sibling_state(self) -> None:
        context = _context()
        detached = DetachedSkillContext.detach(
            context,
            phase="schematic",
            obligation_topics=_topics(),
            evidence_kinds=frozenset({"observation"}),
            tool_grants=(),
        )
        payload = detached.to_dict()
        serialized = json.dumps(payload).casefold()

        self.assertNotIn("transcript", serialized)
        self.assertNotIn("canonical_writer", serialized)
        self.assertNotIn("world_handle", serialized)
        self.assertNotIn("repository", serialized)
        self.assertNotIn("sibling-fact", serialized)
        self.assertEqual(payload["context"]["omitted_node_refs"], ["design-node:sibling"])
        self.assertFalse(hasattr(detached, "commit"))
        self.assertFalse(hasattr(detached, "write"))

    def test_bounded_advice_execution_retries_and_receipts_are_detached(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = load_skill_package(
                _write_package(Path(temporary), name="advice")
            )
            attempts = 0

            def handler(value: object) -> SkillAdvice:
                nonlocal attempts
                attempts += 1
                self.assertFalse(hasattr(value, "canonical_writer"))
                if attempts == 1:
                    raise RuntimeError("transient")
                return SkillAdvice(
                    advice_id="review-current",
                    summary="Keep the response attached to the current obligation.",
                    responds_to_refs=(
                        "obligation:resolve-current-relationship",
                    ),
                    evidence_refs=("evidence://analysis/current",),
                )

            result = invoke_skill(
                package,
                provider_surface="codex",
                context=_context(),
                phase="schematic",
                obligation_topics=_topics(),
                evidence_kinds=frozenset(),
                available_tools=(),
                handler=handler,
            )

            self.assertEqual(result.receipt.status, SkillInvocationStatus.ADVICE)
            self.assertEqual(result.receipt.attempts_used, 2)
            self.assertEqual(result.output.ref, result.receipt.produced_output_ref)
            receipt = result.receipt.to_dict()
            self.assertFalse(receipt["verified"])
            self.assertFalse(receipt["accepted"])
            self.assertFalse(receipt["canonical"])
            self.assertFalse(receipt["world_write"])

    def test_timeout_and_output_budget_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            timeout_manifest = _manifest(
                budget={
                    "timeout_seconds": 0.01,
                    "max_output_bytes": 4096,
                    "max_attempts": 1,
                }
            )
            package = load_skill_package(
                _write_package(parent, name="timeout", manifest=timeout_manifest)
            )

            def slow(_: object) -> SkillAdvice:
                time.sleep(0.05)
                return SkillAdvice(
                    "late-advice",
                    "Late.",
                    ("obligation:resolve-current-relationship",),
                )

            result = invoke_skill(
                package,
                provider_surface="claude",
                context=_context(),
                phase="schematic",
                obligation_topics=_topics(),
                evidence_kinds=frozenset(),
                available_tools=(),
                handler=slow,
            )
            self.assertEqual(result.receipt.status, SkillInvocationStatus.TIMEOUT)
            self.assertIsNone(result.output)

            output_manifest = _manifest(
                skill_id="test-output-budget",
                budget={
                    "timeout_seconds": 0.1,
                    "max_output_bytes": 128,
                    "max_attempts": 1,
                },
            )
            output_package = load_skill_package(
                _write_package(
                    parent,
                    name="output-budget",
                    manifest=output_manifest,
                )
            )
            oversized = invoke_skill(
                output_package,
                provider_surface="codex",
                context=_context(),
                phase="schematic",
                obligation_topics=_topics(),
                evidence_kinds=frozenset(),
                available_tools=(),
                handler=lambda _: SkillAdvice(
                    "oversized-advice",
                    "x" * 500,
                    ("obligation:resolve-current-relationship",),
                ),
            )
            self.assertEqual(
                oversized.receipt.status,
                SkillInvocationStatus.OVERSIZED,
            )
            self.assertGreater(
                oversized.receipt.output_bytes,
                oversized.receipt.max_output_bytes,
            )

    def test_proposal_requires_exact_base_response_ref_and_allowed_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = load_skill_package(
                _write_package(Path(temporary), name="proposal")
            )
            context = _context()

            def proposal(
                *,
                base: str = context.target_state_digest,
                authority: str = "skill-agent",
                response: str = "obligation:resolve-current-relationship",
            ) -> SkillProposal:
                return SkillProposal(
                    proposal_id="resolve-current",
                    operator=DecisionOperator(
                        decision_id="resolve-current",
                        decision_type="state-responsive-proposal",
                        base_state_digest=base,
                        authority_id=authority,
                        intent="Propose a bounded response for runtime validation.",
                    ),
                    responds_to_refs=(response,),
                    rationale="Ground the proposal in the current obligation.",
                )

            valid = invoke_skill(
                package,
                provider_surface="kimi",
                context=context,
                phase="schematic",
                obligation_topics=_topics(),
                evidence_kinds=frozenset(),
                available_tools=(),
                handler=lambda _: proposal(),
            )
            self.assertEqual(valid.receipt.status, SkillInvocationStatus.PROPOSAL)
            self.assertTrue(valid.output.to_dict()["candidate_only"])
            self.assertFalse(valid.output.to_dict()["world_write"])

            for output in (
                proposal(base="c" * 64),
                proposal(authority="unknown-agent"),
                proposal(response="obligation:unknown"),
            ):
                with self.subTest(output=output):
                    invalid = invoke_skill(
                        package,
                        provider_surface="codex",
                        context=context,
                        phase="schematic",
                        obligation_topics=_topics(),
                        evidence_kinds=frozenset(),
                        available_tools=(),
                        handler=lambda _, item=output: item,
                    )
                    self.assertEqual(
                        invalid.receipt.status,
                        SkillInvocationStatus.INVALID_OUTPUT,
                    )
                    self.assertIsNone(invalid.output)

    def test_mcp_mutation_is_metadata_only_and_cannot_bypass_candidate_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = _manifest(
                skill_id="test-candidate-tool",
                tool_requirements=[
                    {
                        "tool_id": "world-candidate",
                        "kind": "mcp",
                        "access": "candidate_mutation",
                    }
                ],
            )
            package = load_skill_package(
                _write_package(
                    Path(temporary), name="candidate-tool", manifest=manifest
                )
            )
            tool = AvailableTool(
                "world-candidate",
                ToolKind.MCP,
                ToolAccess.CANDIDATE_MUTATION,
            )

            def handler(invocation: object) -> SkillAdvice:
                grant = invocation.context.tool_grants[0]
                self.assertEqual(grant, tool)
                self.assertFalse(hasattr(grant, "call"))
                self.assertFalse(hasattr(invocation, "world_handle"))
                return SkillAdvice(
                    "candidate-review",
                    "Describe a candidate; do not mutate a world.",
                    ("obligation:resolve-current-relationship",),
                )

            result = invoke_skill(
                package,
                provider_surface="codex",
                context=_context(),
                phase="schematic",
                obligation_topics=_topics(),
                evidence_kinds=frozenset(),
                available_tools=(tool,),
                handler=handler,
            )
            self.assertEqual(
                result.receipt.declared_tools,
                ("mcp:world-candidate:candidate_mutation",),
            )
            self.assertFalse(result.receipt.to_dict()["world_write"])

    def test_provider_exports_are_thin_and_codex_skill_loads(self) -> None:
        package = load_skill_package(BUILTIN.resolve())
        artifacts = {
            surface: render_skill_surface(package, surface)
            for surface in ("codex", "claude", "kimi")
        }

        self.assertEqual(
            {item.contract_digest for item in artifacts.values()},
            {artifacts["codex"].contract_digest},
        )
        skill_file = codex_skill_file(artifacts["codex"])
        text = skill_file.content.decode("utf-8")
        self.assertIn("name: review-design-obligations", text)
        self.assertIn(package.spec.package_digest, text)
        self.assertNotIn("verified: true", text.casefold())
        self.assertFalse(artifacts["codex"].to_dict()["runtime_authority"])
        custom = render_skill_surface(
            package,
            SkillSurfaceProfile("future-host", "extensions/{skill_id}"),
        )
        self.assertEqual(custom.contract_digest, artifacts["codex"].contract_digest)
        self.assertEqual(
            set(DEFAULT_SKILL_SURFACES),
            {"codex", "claude", "kimi"},
        )

    def test_instance_answer_assignment_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _write_package(
                Path(temporary),
                name="instance-answer",
                instructions="building_name: hidden-example\n",
            )
            with self.assertRaisesRegex(
                SkillPackageError, "project-instance assignment"
            ):
                load_skill_package(root)


if __name__ == "__main__":
    unittest.main()

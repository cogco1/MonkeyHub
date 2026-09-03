from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from archive.archflow.adapters.skill_surfaces import codex_skill_file, render_skill_surface
from archive.archflow.skills.contracts import SkillAdvice
from archive.archflow.skills.runtime import SkillInvocationStatus, invoke_skill
from archive.archflow.skills.package import load_skill_package
from archive.archflow.state.design_state import (
    ContextSlice,
    DesignStateLayer,
    StatePath,
    StatePathSegment,
)
from archflow.state.operational_state import DesignObligation


ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = (
    ROOT
    / "archflow"
    / "skills"
    / "packages"
    / "review-design-obligations"
)


def _context() -> ContextSlice:
    return ContextSlice(
        tree_digest="d" * 64,
        target_node_ref="design-node:smoke-component",
        target_state_digest="e" * 64,
        path=StatePath(
            (
                StatePathSegment(DesignStateLayer.GLOBAL_CONCEPT, "concept"),
                StatePathSegment(DesignStateLayer.PHASE, "schematic"),
                StatePathSegment(
                    DesignStateLayer.DISCIPLINE,
                    "coordination",
                ),
                StatePathSegment(DesignStateLayer.COMPONENT, "smoke"),
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
        obligations=(
            DesignObligation(
                obligation_id="review-smoke-component",
                statement="Review the current component response.",
                source_ref="request://smoke/current",
            ),
        ),
        phase_deliverable_refs=(),
        interfaces=(),
        allowed_authority_ids=("skill-agent",),
        evidence_refs=(),
        omitted_node_refs=(),
    )


class SkillSurfaceSmokeTests(unittest.TestCase):
    def test_builtin_package_runs_and_codex_export_is_cli_loadable(self) -> None:
        package = load_skill_package(PACKAGE_ROOT.resolve())
        result = invoke_skill(
            package,
            provider_surface="codex",
            context=_context(),
            phase="schematic",
            obligation_topics={"review-smoke-component": "component"},
            evidence_kinds=frozenset(),
            available_tools=(),
            handler=lambda _: SkillAdvice(
                advice_id="smoke-review",
                summary="Keep the next action attached to the current component.",
                responds_to_refs=("obligation:review-smoke-component",),
            ),
        )
        self.assertEqual(result.receipt.status, SkillInvocationStatus.ADVICE)

        artifact = render_skill_surface(package, "codex")
        codex_skill_file(artifact)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve()
            process = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "archive" / "tools" / "skillctl.py"),
                    "export",
                    "--root",
                    str(PACKAGE_ROOT.resolve()),
                    "--surface",
                    "codex",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            receipt = json.loads(process.stdout)
            self.assertEqual(receipt["source_of_truth"], "SkillSpec@1")
            exported = (
                output
                / "skills"
                / "review-design-obligations"
                / "SKILL.md"
            )
            self.assertTrue(exported.is_file())
            self.assertEqual(
                exported.read_bytes(),
                codex_skill_file(artifact).content,
            )


if __name__ == "__main__":
    unittest.main()

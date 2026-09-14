"""Regression coverage for GitHub-Issue work claims in ``archcheck --changed``."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.archcheck import check_changed_scopes


POLICY_PATH = "governance/architecture_policy.json"
REGISTRY_PATH = "governance/work_registry.json"
POLICY = {
    "shared_write_scope": [
        "governance/work_registry.json",
        "tests/",
    ]
}


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        (
            "git",
            "-c",
            "user.name=archcheck test",
            "-c",
            "user.email=archcheck@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ),
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return completed.stdout


def _write(root: Path, relative: str, value: object) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(value)
    path.write_text(text, encoding="utf-8", newline="\n")


def _work(work_id: str, *scope: str, lanes: list[dict[str, object]] | None = None) -> dict[str, object]:
    item: dict[str, object] = {
        "id": work_id,
        "status": "active",
        "write_scope": list(scope),
    }
    if lanes is not None:
        item["lanes"] = lanes
    return item


def _registry(*items: dict[str, object]) -> dict[str, object]:
    return {"schema": "ArchFlowDevelopmentRegistry@2", "items": list(items)}


class GithubWorkClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        _git(self.root, "init", "-q")
        self.registry = _registry(
            _work("GH-60", "apps/feature/"),
            _work("GH-61", "apps/other/"),
            _work("P301", "legacy/"),
        )
        _write(self.root, POLICY_PATH, POLICY)
        _write(self.root, REGISTRY_PATH, self.registry)
        _write(self.root, "README.md", "base\n")
        _git(self.root, "add", POLICY_PATH, REGISTRY_PATH, "README.md")
        _git(self.root, "commit", "-q", "-m", "base")
        self.base = _git(self.root, "rev-parse", "HEAD").strip()

    def commit(self, message: str, files: dict[str, object]) -> str:
        for relative, value in files.items():
            _write(self.root, relative, value)
        _git(self.root, "add", "--", *files)
        _git(self.root, "commit", "-q", "-m", message)
        return _git(self.root, "rev-parse", "HEAD").strip()

    def findings(self) -> tuple[object, ...]:
        return tuple(check_changed_scopes(self.root, self.base))

    def test_plain_github_issue_claim_uses_the_same_scope_rules_as_legacy_work(self) -> None:
        self.commit("GH-60: change owned feature", {"apps/feature/value.py": "VALUE = 1\n"})
        self.commit("P301: legacy claim still works", {"legacy/value.py": "VALUE = 1\n"})
        self.assertEqual((), self.findings())

        self.commit("GH-60: reach outside owned feature", {"apps/outside.py": "VALUE = 2\n"})
        findings = self.findings()
        self.assertEqual([("apps/outside.py", "SCOPE_VIOLATION")], [(row.path, row.code) for row in findings])
        self.assertIn("GH-60", findings[0].message)

    def test_subject_claim_wins_over_other_issue_ids_named_in_the_body(self) -> None:
        self.commit(
            "GH-60: change owned feature\n\nUnblocks GH-61 after merge.",
            {"apps/feature/value.py": "VALUE = 1\n"},
        )
        self.assertEqual((), self.findings())

    def test_body_claim_is_used_when_the_subject_has_no_work_id(self) -> None:
        self.commit(
            "Change owned feature\n\nWork item GH-60.",
            {"apps/feature/value.py": "VALUE = 1\n"},
        )
        self.assertEqual((), self.findings())

    def test_github_lane_claim_uses_both_lane_and_parent_scope(self) -> None:
        lane = {
            "id": "ui",
            "status": "active",
            "issue": "#61",
            "branch": "codex/61-ui",
            "worktree": "C:/fixture/61-ui",
            "base_ref": "main",
            "contributor": "fixture",
            "reviewer": None,
            "handoff": None,
            "modules": ["fixture"],
            "write_scope": ["apps/other/ui/"],
            "depends_on": [],
        }
        self.registry = _registry(
            _work("GH-60", "apps/feature/"),
            _work("GH-61", "apps/other/", lanes=[lane]),
            _work("P301", "legacy/"),
        )
        self.commit("P000-governance: declare GH lane", {REGISTRY_PATH: self.registry})
        self.base = _git(self.root, "rev-parse", "HEAD").strip()

        self.commit("GH-61/ui: change lane file", {"apps/other/ui/view.py": "VALUE = 1\n"})
        self.assertEqual((), self.findings())
        self.commit("GH-61/ui: exceed lane", {"apps/other/core.py": "VALUE = 2\n"})
        findings = self.findings()
        self.assertEqual([("apps/other/core.py", "SCOPE_VIOLATION")], [(row.path, row.code) for row in findings])
        self.assertIn("GH-61/ui", findings[0].message)

    def test_malformed_github_markers_do_not_partially_claim_a_valid_issue(self) -> None:
        malformed = (
            "GH-0: zero is not an issue id",
            "GH-060: leading zero is not canonical",
            "GH-60-extra: suffix is not a work id",
            "GH-60/ui/extra: nested lane is not valid",
            "GH-60oops: adjacent text is not valid",
        )
        for index, subject in enumerate(malformed):
            self.commit(subject, {f"apps/feature/bad_{index}.py": f"VALUE = {index}\n"})
        findings = self.findings()
        self.assertEqual(
            [(f"apps/feature/bad_{index}.py", "SCOPE_UNDECLARED") for index in range(len(malformed))],
            [(row.path, row.code) for row in findings],
        )

    def test_unknown_github_issue_does_not_gain_an_existing_scope(self) -> None:
        self.commit("GH-999: unknown work item", {"apps/feature/value.py": "VALUE = 1\n"})
        findings = self.findings()
        self.assertEqual([("apps/feature/value.py", "SCOPE_UNDECLARED")], [(row.path, row.code) for row in findings])
        self.assertIn("GH-999", findings[0].message)

    def test_closing_github_work_may_use_parent_scope_once_then_loses_it(self) -> None:
        self.commit(
            "GH-60: finish work",
            {
                REGISTRY_PATH: _registry(_work("GH-61", "apps/other/"), _work("P301", "legacy/")),
                "apps/feature/value.py": "VALUE = 1\n",
            },
        )
        self.assertEqual((), self.findings())
        self.commit("GH-60: stale work after close", {"apps/feature/late.py": "VALUE = 2\n"})
        findings = self.findings()
        self.assertEqual([("apps/feature/late.py", "SCOPE_UNDECLARED")], [(row.path, row.code) for row in findings])


if __name__ == "__main__":
    unittest.main()

"""Write scopes are the boundary between people developing in parallel.

Two checks live here. ``check_scopes`` reads the work registry and refuses two
live cards that claim the same path, so nobody starts a day owning the same
directory as somebody else. ``check_changed_scopes`` reads a branch and refuses
a commit that wrote outside the scope of the card it names, so the boundary is
enforced where it is actually crossed rather than only declared.

The second one needs real commits, so it builds a throwaway Git repository in a
temporary directory; nothing here reads or writes this repository.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.archcheck import check_changed_scopes, check_scopes


SHARED = (
    "docs/mapping/",
    "governance/module_registry.json",
    "governance/work_registry.json",
    "tests/",
)
POLICY = {"shared_write_scope": list(SHARED)}


def _registry(*items: dict[str, object]) -> dict[str, object]:
    return {"schema": "ArchFlowDevelopmentRegistry@2", "items": list(items)}


def _card(card_id: str, status: str, *scope: str) -> dict[str, object]:
    return {"id": card_id, "status": status, "write_scope": list(scope)}


def _git(cwd: Path, *args: str) -> str:
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
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return completed.stdout


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class ScopeOverlapTests(unittest.TestCase):
    def test_two_live_cards_claiming_the_same_directory_are_refused(self) -> None:
        registry = _registry(
            _card("P201", "active", "archflow/state/", "tests/"),
            _card("P202", "ready", "archflow/state/", "docs/mapping/"),
        )
        findings = tuple(check_scopes(Path("."), POLICY, registry))
        self.assertEqual(1, len(findings), findings)
        finding = findings[0]
        self.assertEqual("SCOPE_OVERLAP", finding.code)
        self.assertEqual("governance/work_registry.json", finding.path)
        self.assertIn("P201", finding.message)
        self.assertIn("P202", finding.message)
        self.assertIn("archflow/state/", finding.message)

    def test_a_directory_containing_another_cards_file_is_an_overlap(self) -> None:
        registry = _registry(
            _card("P203", "ready", "apps/archflow-studio/"),
            _card("P204", "ready", "apps/archflow-studio/api/settings.py"),
        )
        findings = tuple(check_scopes(Path("."), POLICY, registry))
        self.assertEqual(["SCOPE_OVERLAP"], [item.code for item in findings])
        self.assertIn("apps/archflow-studio/api/settings.py", findings[0].message)

    def test_the_shared_ledgers_are_not_an_overlap(self) -> None:
        registry = _registry(
            _card("P205", "active", *SHARED, "archflow/state/"),
            _card("P206", "ready", *SHARED, "archflow/runtime/"),
        )
        self.assertEqual((), tuple(check_scopes(Path("."), POLICY, registry)))

    def test_a_card_nobody_is_working_on_does_not_collide(self) -> None:
        registry = _registry(
            _card("P207", "active", "archflow/state/"),
            _card("P208", "blocked", "archflow/state/"),
        )
        self.assertEqual((), tuple(check_scopes(Path("."), POLICY, registry)))

    def test_no_registry_is_not_a_finding(self) -> None:
        self.assertEqual((), tuple(check_scopes(Path("."), POLICY, None)))


class ChangedScopeTests(unittest.TestCase):
    """One temporary repository, four commits, read back by ``--changed``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        root = Path(cls._temporary.name) / "repo"
        root.mkdir()
        cls.root = root
        _git(root, "init", "-q")
        _write(root, "README.md", "base\n")
        _git(root, "add", "README.md")
        _git(root, "commit", "-q", "-m", "base")
        cls.base = _git(root, "rev-parse", "HEAD").strip()

        _write(root, "archflow/state/evidence.py", "VALUE = 1\n")
        _git(root, "add", "archflow/state/evidence.py")
        _git(root, "commit", "-q", "-m", "P301 evidence ledger owner\n\nInside scope.")

        _write(root, "apps/archflow-studio/api/app.py", "VALUE = 2\n")
        _git(root, "add", "apps/archflow-studio/api/app.py")
        _git(root, "commit", "-q", "-m", "P301 reach into the Studio as well")

        _write(root, "tests/test_ledger.py", "assert True\n")
        _git(root, "add", "tests/test_ledger.py")
        _git(root, "commit", "-q", "-m", "Tidy the suite while passing through")

        _write(root, "tools/oneoff.py", "VALUE = 3\n")
        _git(root, "add", "tools/oneoff.py")
        _git(root, "commit", "-q", "-m", "P000-governance drive-by tool")

        _write(root, "archflow/state/ledger.py", "VALUE = 4\n")
        _git(root, "add", "archflow/state/ledger.py")
        _git(
            root,
            "commit",
            "-q",
            "-m",
            "P301 the ledger owner\n\nUnblocks P302 and supersedes P303.",
        )

        cls.registry = _registry(
            _card("P301", "active", "archflow/state/", "tests/"),
            _card("P302", "ready", "docs/mapping/"),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def _findings(self) -> tuple[object, ...]:
        return tuple(
            check_changed_scopes(self.root, POLICY, self.registry, self.base)
        )

    def test_a_commit_inside_its_cards_scope_passes(self) -> None:
        self.assertNotIn(
            "archflow/state/evidence.py",
            [finding.path for finding in self._findings()],
        )

    def test_a_commit_outside_its_cards_scope_is_a_violation(self) -> None:
        violations = [
            finding
            for finding in self._findings()
            if finding.code == "SCOPE_VIOLATION"
        ]
        self.assertEqual(
            ["apps/archflow-studio/api/app.py"],
            [finding.path for finding in violations],
        )
        self.assertIn("P301", violations[0].message)

    def test_a_commit_without_a_card_may_still_touch_a_shared_ledger(self) -> None:
        self.assertNotIn(
            "tests/test_ledger.py",
            [finding.path for finding in self._findings()],
        )

    def test_an_id_no_live_card_carries_falls_back_to_the_governance_paths(
        self,
    ) -> None:
        undeclared = [
            finding
            for finding in self._findings()
            if finding.code == "SCOPE_UNDECLARED"
        ]
        self.assertEqual(
            ["tools/oneoff.py"], [finding.path for finding in undeclared]
        )
        self.assertIn("P000", undeclared[0].message)

    def test_the_subject_decides_when_the_body_names_other_cards(self) -> None:
        """A body says what a change unblocks; that is not its own card."""

        self.assertNotIn(
            "archflow/state/ledger.py",
            [finding.path for finding in self._findings()],
        )

    def test_a_deleted_file_counts_as_changed(self) -> None:
        root = Path(self._temporary.name) / "deletion"
        root.mkdir()
        _git(root, "init", "-q")
        _write(root, "tools/gone.py", "VALUE = 1\n")
        _git(root, "add", "tools/gone.py")
        _git(root, "commit", "-q", "-m", "base")
        base = _git(root, "rev-parse", "HEAD").strip()
        _git(root, "rm", "-q", "tools/gone.py")
        _git(root, "commit", "-q", "-m", "P301 remove the tool")
        findings = tuple(
            check_changed_scopes(root, POLICY, self.registry, base)
        )
        self.assertEqual(
            [("tools/gone.py", "SCOPE_VIOLATION")],
            [(finding.path, finding.code) for finding in findings],
        )


if __name__ == "__main__":
    unittest.main()

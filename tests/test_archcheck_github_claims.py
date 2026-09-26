"""GitHub Issue work claims in ``archcheck --changed``.

Since #358 a commit claims ``GH-<issue>`` or ``GH-<issue>/<lane>`` and nothing
else, against a registry that holds live GitHub Issue claims only. A commit
made before the retirement keeps the reading it was checked with: the card-era
cases are in ``test_archcheck_scopes``, and the GitHub claims that were already
valid then are re-run here against the card-era schema. The retirement point
is the registry schema each commit carries.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.archcheck import REGISTRY_SCHEMA, ArchitecturePolicyError, check_changed_scopes, check_scopes


POLICY_PATH = "governance/architecture_policy.json"
REGISTRY_PATH = "governance/work_registry.json"
CARD_ERA_SCHEMA = "ArchFlowDevelopmentRegistry@2"
SHARED = ["governance/work_registry.json", "governance/architecture_policy.json", "tests/"]
POLICY = {"shared_write_scope": SHARED, "unclaimed_write_scope": ["docs/adr/", "CONTRIBUTING.md"]}
CARD_ERA_POLICY = {"shared_write_scope": [*SHARED, "docs/mapping/"]}


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
    if value is None:
        path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(value)
    path.write_text(text, encoding="utf-8", newline="\n")


def _claim(work_id: str, *scope: str, status: str = "active") -> dict[str, object]:
    return {
        "id": work_id, "status": status, "branch": f"codex/{work_id.lower()}",
        "worktree": f"C:/fixture/{work_id.lower()}", "base_ref": "main", "contributor": "fixture",
        "reviewer": None, "handoff": None, "modules": ["fixture"], "write_scope": list(scope),
        "depends_on": [],
    }


def _registry(*items: dict[str, object], schema: str = REGISTRY_SCHEMA) -> dict[str, object]:
    return {"schema": schema, "items": list(items)}


class _Repository:
    """One throwaway repository whose commits ``--changed`` reads back."""

    def make_repository(self, name: str = "repo") -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / name
        self.root.mkdir()
        _git(self.root, "init", "-q", "-b", "main")

    def commit(self, message: str, files: dict[str, object]) -> str:
        for relative, value in files.items():
            _write(self.root, relative, value)
        _git(self.root, "add", "-A", "--", *files)
        _git(self.root, "commit", "-q", "-m", message)
        return _git(self.root, "rev-parse", "HEAD").strip()

    def head(self) -> str:
        return _git(self.root, "rev-parse", "HEAD").strip()

    def findings(self) -> tuple[object, ...]:
        return tuple(check_changed_scopes(self.root, self.base))

    def found(self) -> set[tuple[str, str]]:
        return {(row.path, row.code) for row in self.findings()}


class _GithubClaimCases(_Repository):
    """Claim rules that read the same before and after the retirement."""

    SCHEMA: str
    BASE_POLICY: dict[str, object]

    def setUp(self) -> None:
        self.make_repository()
        self.base = self.commit("base", {
            POLICY_PATH: self.BASE_POLICY,
            REGISTRY_PATH: self.registry_of(_claim("GH-60", "apps/feature/"), _claim("GH-61", "apps/other/")),
            "README.md": "base\n",
        })

    def registry_of(self, *items: dict[str, object]) -> dict[str, object]:
        return _registry(*items, schema=self.SCHEMA)

    def laned(self, work_id: str, ceiling: str, *lanes: dict[str, object]) -> dict[str, object]:
        raise NotImplementedError

    def test_a_github_issue_claim_may_write_only_its_scope(self) -> None:
        self.commit("GH-60: change owned feature", {"apps/feature/value.py": "VALUE = 1\n"})
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

    def test_a_lane_claim_writes_only_its_lane(self) -> None:
        lane = {**_claim("ui", "apps/other/ui/"), "id": "ui"}
        self.base = self.commit("GH-61/ui: claim the lane", {
            REGISTRY_PATH: self.registry_of(_claim("GH-60", "apps/feature/"), self.laned("GH-61", "apps/other/", lane)),
        })
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
                REGISTRY_PATH: self.registry_of(_claim("GH-61", "apps/other/")),
                "apps/feature/value.py": "VALUE = 1\n",
            },
        )
        self.assertEqual((), self.findings())
        self.commit("GH-60: stale work after close", {"apps/feature/late.py": "VALUE = 2\n"})
        findings = self.findings()
        self.assertEqual([("apps/feature/late.py", "SCOPE_UNDECLARED")], [(row.path, row.code) for row in findings])


class CardEraGithubClaimTests(_GithubClaimCases, unittest.TestCase):
    """GitHub claims made before #358, on the card-era registry."""

    SCHEMA = CARD_ERA_SCHEMA
    BASE_POLICY = CARD_ERA_POLICY

    def laned(self, work_id: str, ceiling: str, *lanes: dict[str, object]) -> dict[str, object]:
        return {**_claim(work_id, ceiling), "lanes": list(lanes)}

    def test_card_and_github_claims_share_one_reading_before_the_retirement(self) -> None:
        self.base = self.commit("P000-governance: declare a card", {
            REGISTRY_PATH: self.registry_of(_claim("GH-60", "apps/feature/"), _claim("P301", "legacy/")),
        })
        self.commit("P301: legacy claim", {"legacy/value.py": "VALUE = 1\n"})
        self.commit("GH-60: issue claim", {"apps/feature/value.py": "VALUE = 1\n"})
        self.assertEqual((), self.findings())


class GithubClaimTests(_GithubClaimCases, unittest.TestCase):
    """GitHub claims on the current registry: the only claims there are."""

    SCHEMA = REGISTRY_SCHEMA
    BASE_POLICY = POLICY

    def laned(self, work_id: str, ceiling: str, *lanes: dict[str, object]) -> dict[str, object]:
        return {"id": work_id, "lanes": list(lanes)}

    def test_a_card_claim_is_retired(self) -> None:
        sha = self.commit("P301: legacy claim", {"legacy/value.py": "VALUE = 1\n"})
        findings = self.findings()
        self.assertEqual({(sha[:8], "RETIRED_WORK_CLAIM"), ("legacy/value.py", "SCOPE_UNDECLARED")},
                         {(row.path, row.code) for row in findings})
        retired = next(row for row in findings if row.code == "RETIRED_WORK_CLAIM")
        self.assertIn("P301", retired.message)
        self.assertIn("GH-<issue>", retired.message)

    def test_p000_governance_is_not_a_current_identity(self) -> None:
        sha = self.commit("P000-governance: maintain repository guidance", {
            "AGENTS.md": "rules\n", "README.md": "updated\n", "tools/archcheck.py": "# checker\n",
        })
        self.assertEqual({
            (sha[:8], "RETIRED_WORK_CLAIM"), ("AGENTS.md", "SCOPE_UNDECLARED"),
            ("README.md", "SCOPE_UNDECLARED"), ("tools/archcheck.py", "SCOPE_UNDECLARED"),
        }, self.found())

    def test_every_card_era_prefix_is_retired(self) -> None:
        for subject in (
            "P115/foo: lane claim", "P000 claim GH-60 feature", "P000-governance: release GH-60",
            "M012: model card", "R003: research card", "  P301 with leading space",
        ):
            with self.subTest(subject=subject):
                self.base = self.head()
                sha = self.commit(subject, {"tests/test_touch.py": f"# {subject}\n"})
                self.assertEqual({(sha[:8], "RETIRED_WORK_CLAIM")}, self.found())

    def test_history_that_names_cards_is_not_a_claim(self) -> None:
        self.commit(
            "GH-60: remove the P105 card\n\nP111 delivered the continuation; P000-governance kept the rules.",
            {"apps/feature/value.py": "VALUE = 1\n"},
        )
        self.commit('Revert "P301: legacy claim"', {"tests/test_revert.py": "assert True\n"})
        self.assertEqual((), self.findings())

    def test_unclaimed_commits_write_only_shared_and_policy_unclaimed_paths(self) -> None:
        self.commit("Clarify contribution guidance", {
            "CONTRIBUTING.md": "guidance\n", "docs/adr/ADR-009.md": "decision\n", "tests/test_doc.py": "assert True\n",
        })
        self.assertEqual((), self.findings())
        self.commit("Tidy the agent rules", {"AGENTS.md": "rules\n", "docs/REPO_LAYOUT.md": "layout\n"})
        self.assertEqual({("AGENTS.md", "SCOPE_UNDECLARED"), ("docs/REPO_LAYOUT.md", "SCOPE_UNDECLARED")}, self.found())

    def test_a_claimed_commit_does_not_gain_the_unclaimed_paths(self) -> None:
        self.commit("GH-60: also edit the guide", {"CONTRIBUTING.md": "guidance\n"})
        self.assertEqual({("CONTRIBUTING.md", "SCOPE_VIOLATION")}, self.found())

    def test_unclaimed_paths_come_from_the_commits_own_policy(self) -> None:
        self.commit("GH-60: drop the unclaimed allowance", {POLICY_PATH: {"shared_write_scope": SHARED}})
        self.commit("Clarify contribution guidance", {"CONTRIBUTING.md": "guidance\n"})
        self.assertEqual({("CONTRIBUTING.md", "SCOPE_UNDECLARED")}, self.found())

    def test_an_issue_claimed_through_lanes_is_claimed_by_lane(self) -> None:
        lane = {**_claim("api", "apps/api/"), "id": "api"}
        self.base = self.commit("GH-62/api: claim the lane", {
            REGISTRY_PATH: self.registry_of(_claim("GH-60", "apps/feature/"), self.laned("GH-62", "apps/", lane)),
        })
        self.commit("GH-62: no lane named", {"apps/api/value.py": "VALUE = 1\n"})
        findings = self.findings()
        self.assertEqual([("apps/api/value.py", "SCOPE_UNDECLARED")], [(row.path, row.code) for row in findings])
        self.assertIn("GH-62/<lane>", findings[0].message)


class RetirementTransitionTests(_Repository, unittest.TestCase):
    """Commits read by the registry schema they carry, across the retirement."""

    def setUp(self) -> None:
        self.make_repository()
        self.base = self.commit("base", {
            POLICY_PATH: CARD_ERA_POLICY,
            REGISTRY_PATH: _registry(_claim("P301", "archflow/state/"), _claim("GH-60", "apps/feature/"),
                                     schema=CARD_ERA_SCHEMA),
            "docs/mapping/planning/P301-card.md": "# P301\n",
            "README.md": "base\n",
        })

    def retire(self, *claims: dict[str, object]) -> str:
        return self.commit("GH-60: GitHub Issues are the only work claims", {
            REGISTRY_PATH: _registry(_claim("GH-60", "apps/feature/", "docs/mapping/"), *claims),
            POLICY_PATH: POLICY,
            "docs/mapping/planning/P301-card.md": None,
        })

    def test_commits_before_the_retirement_keep_their_reading_and_later_ones_do_not(self) -> None:
        self.commit("P301: card-era change", {"archflow/state/value.py": "VALUE = 1\n"})
        self.commit("P000-governance: card-era rules", {"AGENTS.md": "rules\n"})
        self.retire()
        self.assertEqual((), self.findings())
        late = self.commit("P301: card-era claim after the retirement", {"archflow/state/value.py": "VALUE = 2\n"})
        self.assertEqual({(late[:8], "RETIRED_WORK_CLAIM"), ("archflow/state/value.py", "SCOPE_UNDECLARED")},
                         self.found())

    def test_the_retirement_deletes_cards_only_under_its_own_claim(self) -> None:
        self.commit("GH-60: retire without claiming the cards", {
            REGISTRY_PATH: _registry(_claim("GH-60", "apps/feature/")),
            POLICY_PATH: POLICY,
            "docs/mapping/planning/P301-card.md": None,
        })
        self.assertEqual({("docs/mapping/planning/P301-card.md", "SCOPE_VIOLATION")}, self.found())

    def test_a_commit_after_the_retirement_cannot_restore_the_card_schema(self) -> None:
        self.retire()
        self.commit("GH-60: bring the cards back", {
            REGISTRY_PATH: _registry(_claim("P301", "archflow/state/"), _claim("GH-60", "apps/feature/"),
                                     schema=CARD_ERA_SCHEMA),
        })
        with self.assertRaisesRegex(ArchitecturePolicyError, "card-era"):
            self.findings()

    def lane_branch(self) -> str:
        """A lane claimed before the retirement, the way #337's lanes were."""

        _git(self.root, "switch", "-q", "-c", "lane")
        card_era_lane = {**_claim("ui", "apps/other/ui/"), "id": "ui", "issue": "#61"}
        self.commit("P000-governance: claim GH-61 ui", {
            REGISTRY_PATH: _registry(
                _claim("P301", "archflow/state/"), _claim("GH-60", "apps/feature/"),
                {**_claim("GH-61", "apps/other/"), "card": "docs/mapping/planning/GH-61-ui.md", "lanes": [card_era_lane]},
                schema=CARD_ERA_SCHEMA,
            ),
            "docs/mapping/planning/GH-61-ui.md": "# GH-61\n",
        })
        self.commit("GH-61/ui: change the view", {"apps/other/ui/view.py": "VALUE = 1\n"})
        _git(self.root, "switch", "-q", "main")
        main = self.retire()
        _git(self.root, "switch", "-q", "lane")
        return main

    def merge_main(self, registry: dict[str, object], message: str = "GH-61/ui: merge main") -> str:
        completed = subprocess.run(
            ("git", "-c", "user.name=archcheck test", "-c", "user.email=archcheck@example.invalid",
             "merge", "--no-commit", "--no-ff", "main"),
            cwd=self.root, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertIn(completed.returncode, (0, 1), completed.stderr)
        _write(self.root, REGISTRY_PATH, registry)
        # The lane's card was released with the merge: delete it here rather
        # than in a later commit, where docs/mapping/ is no longer shared.
        _git(self.root, "rm", "-q", "--ignore-unmatch", "docs/mapping/planning/GH-61-ui.md")
        _git(self.root, "add", "-A", "--", REGISTRY_PATH)
        _git(self.root, "commit", "-q", "-m", message)
        return self.head()

    def test_a_lane_from_before_the_retirement_merges_main_and_releases_as_a_github_claim(self) -> None:
        self.base = self.lane_branch()
        lane = {**_claim("ui", "apps/other/ui/"), "id": "ui"}
        self.merge_main(_registry(_claim("GH-60", "apps/feature/", "docs/mapping/"), {"id": "GH-61", "lanes": [lane]}))
        self.commit("GH-61/ui: finish the view", {"apps/other/ui/view.py": "VALUE = 2\n"})
        self.commit("GH-61/ui: release", {REGISTRY_PATH: _registry(_claim("GH-60", "apps/feature/", "docs/mapping/"))})
        self.assertEqual((), self.findings())
        late = self.commit("P000-governance: tidy the registry", {REGISTRY_PATH: _registry()})
        self.assertEqual({(late[:8], "RETIRED_WORK_CLAIM")}, self.found())

    def test_a_merge_that_keeps_the_card_schema_is_refused(self) -> None:
        self.base = self.lane_branch()
        self.merge_main(_registry(_claim("GH-60", "apps/feature/"), schema=CARD_ERA_SCHEMA))
        with self.assertRaisesRegex(ArchitecturePolicyError, "card-era"):
            self.findings()


class IssueClaimFlowTests(_Repository, unittest.TestCase):
    """Issue, claim, commits and release, knowing nothing about cards."""

    def setUp(self) -> None:
        self.make_repository()
        self.base = self.commit("base", {POLICY_PATH: POLICY, REGISTRY_PATH: _registry(), "README.md": "base\n"})

    def assert_registries_valid(self, *registries: dict[str, object]) -> None:
        for registry in registries:
            self.assertEqual((), tuple(check_scopes(self.root, POLICY, registry)))

    def test_an_issue_is_claimed_worked_and_released(self) -> None:
        claimed = _registry(_claim("GH-7", "apps/export/"))
        self.commit("GH-7: claim the export fix", {REGISTRY_PATH: claimed})
        self.commit("GH-7: write the export in one pass", {
            "apps/export/writer.py": "VALUE = 1\n", "tests/test_writer.py": "assert True\n",
        })
        self.commit("GH-7: release", {REGISTRY_PATH: _registry()})
        self.assertEqual((), self.findings())
        self.assert_registries_valid(claimed, _registry())

    def lanes(self) -> dict[str, object]:
        return _registry({"id": "GH-8", "lanes": [{**_claim(name, f"apps/{name}/"), "id": name} for name in ("api", "web")]})

    def test_an_issue_is_claimed_through_lanes_and_released(self) -> None:
        claimed = self.lanes()
        self.commit("GH-8/api: claim the api and web lanes", {REGISTRY_PATH: claimed})
        self.commit("GH-8/api: serve the export", {"apps/api/export.py": "VALUE = 1\n"})
        self.commit("GH-8/web: show the export", {"apps/web/export.ts": "export {}\n"})
        self.commit("GH-8/web: release both lanes", {REGISTRY_PATH: _registry()})
        self.assertEqual((), self.findings())
        self.assert_registries_valid(claimed)

    def test_a_lane_does_not_write_its_sibling_lanes_paths(self) -> None:
        self.commit("GH-8/api: claim the api and web lanes", {REGISTRY_PATH: self.lanes()})
        self.commit("GH-8/api: reach into the web lane", {"apps/web/other.ts": "export {}\n"})
        self.assertEqual({("apps/web/other.ts", "SCOPE_VIOLATION")}, self.found())


if __name__ == "__main__":
    unittest.main()

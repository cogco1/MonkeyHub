# GitHub work claims

`archcheck --changed` accepts GitHub Issue work identities after the #60 migration.

- New work: `GH-<issue-number>` or `GH-<issue-number>/<lane>`.
- Legacy work: existing `P###` and `P###/<lane>` claims remain valid and are not renamed.
- Governance bootstrap: `P000-governance` remains the narrow maintenance marker for the checker/policy surfaces it already owns.

A GitHub claim receives no special authority: the matching row in `governance/work_registry.json` supplies the same historical `write_scope` used for legacy work, and lane claims still have to fit both the lane and parent scope. Unknown or malformed GitHub claims are treated as undeclared work rather than partially matching another Issue.

Examples:

```text
GH-56: restore portable project archive
GH-60/checker: verify issue-native scope claims
```

This file records the migration boundary only; GitHub Issues remain the canonical work items and `work_registry.json` remains live source-scope/concurrency coordination, not a second backlog.

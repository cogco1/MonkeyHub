# Workspace

Owns isolation and lifecycle boundaries for mutable Working State, including
drafts, experiments, tool outputs, and branch-local artifacts.

Workspace contents are not canonical merely because they exist. Promotion
requires a Candidate Submission and the formal commit path described in
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).

Current status: P1 `WorkspaceManager` creates isolated branch-local directories
bound to an exact canonical `StateRef`. Contents remain non-canonical until a
submission is accepted.

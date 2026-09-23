# GH-122 retrieval baseline sub-slice

Status: blocked on human relevance review for the next experiment. Parent issue:
[#122](https://github.com/cogco1/MonkeyHub/issues/122).

Scope is `labs/retrieval/` only plus this card and its registry entry. The
coordinator owns the independent Study/ContextPack integration. Do not change
that public contract, Hub wiring, accepted project state or any user installation.

The existing `studio.shell` RetrievalProvider is a reserved protocol with no
callers or implementation at the inspected base. `studio.study` owns exact
Study source/ledger reads; `project.refs` owns P036 identities. This is a CREATE
of an unregistered **lab**, permitted by `labs/README.md`, because no experimental
retriever or query collection exists in the assigned base. It does not implement
the incomplete production protocol or invent a new production owner.

Delivered: frozen four-corpus proxy-labeled fixture; four real CPU baselines;
metadata/companion/budget ablations; inspectable per-query measurements and
failure cases; condition/budget/isolation tests. See the lab's
[results](../../../labs/retrieval/RESULTS.md). This is a development experiment,
not the parent issue's human-audited acceptance dataset.

Remaining acceptance: human review of the 41 relevance judgments, particularly
auxiliary precedent evidence, before using a new held-out query set to choose a
default. No reviewer identity or approval is supplied, so this work remains
blocked on that concrete input. The complete #122 historical-corpus, downstream
fixed-evidence and chunking studies remain open; this card does not authorize new
production wiring or assign their ownership away from the coordinator.

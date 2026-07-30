# State

Owns the contract for compact Canonical Markov State: state identity, current
commitments, open obligations, and stable references needed for the next formal
decision.

It does not own working drafts, complete transition history, or raw tool traces.
Those separations are described in
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).

Current status: immutable `CanonicalState`, `StateRef`, goal, fact, obligation,
and artifact-reference contracts are implemented in `model.py`.
`BuildingProgram@1` adds compact use, footprint, required-space, entrance,
circulation, clear-height, hard, soft, and prohibited clauses without encoding
a layout, expert sequence, or tool sequence. Raw transcript and tool-history
fields are absent by construction.

## Platform-neutral geometry program

`geometry_program.py` defines a typed, immutable geometry proposal rather than
a sequence of conversational tool calls. Generic operations use explicit
units, tolerances, coordinate frames, stable object ids, input objects,
semantic bindings, and deterministic parameter values. Hosted assemblies keep
door/window meaning outside the operation vocabulary while binding the host
cut, functional members, interfaces, and clearance objects.

External detail is content-addressed and provenance-bound. An asset instance
must name its immutable content, native unit, socket, coordinate frame, and
explicit scale. A proposal may reference no machine-local path and carries no
execution, hard-gate, or canonical-write authority.

Revisions bind the exact predecessor program and prior object digests.
Retirements are explicit. These contracts allow dependency-local invalidation
without silently replacing or deleting an accepted stable object.

P016 keeps interpreted values in typed `IntentTerm` objects while authorization
continues to use the P015 commitment lifecycle. A proposed term has no hard
authority. Only a named confirmation can produce an active lock, and revision
creates linked predecessor/successor commitments instead of overwriting the
old value.

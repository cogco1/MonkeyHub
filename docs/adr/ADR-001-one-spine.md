# ADR-001 — One production spine

**Decision (2026-09-03):** the record-driven chain (StateRecord → element producers → geometry program →
compiler → CAD adapters → relation checks → stage workflow → P036 repository; Studio on top) is the only
production path. The agent-portfolio, monument, controller, sandbox, research and v3 lanes live under
`archive/` and are never imported by the spine.

**Why:** five lanes had grown five vocabularies for the same ideas; cards indexed work, not concepts.

**Do not:** add a second runner, a second write path, or a second state vocabulary "for a lane". A lane
returns only as a fold onto the spine (`docs/CANONICAL_SPINE.md` §2.2).

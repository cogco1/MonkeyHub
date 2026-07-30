# P002 — Real MCP vertical slice

- Origin: Planning
- Status: Ready after P001
- Depends on: P001

Connect exactly one real voxel MCP adapter to the accepted P1 boundary. The
Architect retains freedom inside its isolated workspace; the adapter cannot
write canonical state.

Completion requires one explicitly invoked smoke run that produces a loadable
artifact, a bounded failure receipt, and proof that external failure leaves
canonical state unchanged.

## 2026-07-24 feasibility finding

Two upstream servers were checked in isolation:

- `gemini-minecraft` exposes the needed transaction-shaped surface: session,
  structured plan preview, exact cached-plan execution, capture, and undo. Its
  MCP handshake and bounded `BRIDGE_UNAVAILABLE` failure were verified locally;
  its Fabric project compiles, but it currently contains no automated tests.
- `yuniko-software/minecraft-mcp-server` compiled and passed 118 tests. It is a
  capable Mineflayer control surface, but its tools are primarily low-level
  movement and per-block actions rather than a preview/execute/undo building
  transaction. Its installed dependency audit also needs review before use.

Decision: reuse `gemini-minecraft` as the provisional Minecraft driver and own
the ArchFlow boundary locally. Do not fork or rewrite the game-side MCP unless
the live smoke exposes a driver defect.

The local boundary now requires explicit world-write authority, binds the
session and exact preview plan to a workspace artifact, captures visual
evidence when available, and emits bounded failure receipts. A successful MCP
call creates only a candidate artifact; it does not assert architectural
usability or permit canonical promotion.

## 2026-07-24 live acceptance evidence

- Fabric Loom launched Minecraft Java 1.21.1 in the isolated
  `gemini-minecraft/run/` directory and created only the
  `ArchFlow V4 Disposable` single-player world.
- The localhost health receipt reported `enabled=true`,
  `loopbackOnly=true`, and `serverResponsive=true`; stdio initialization,
  tool discovery, and `minecraft_session` succeeded.
- `python -m tests.integration.live_minecraft_smoke ... --allow-world-write`
  previewed and executed the exact same `plan-1`. The disposable smoke plan
  placed a 5x5 smooth-stone floor (25 blocks) and produced a loadable artifact
  plus PNG capture.
- A second adapter instance targeted unused localhost port `65534` and returned
  bounded `BRIDGE_UNAVAILABLE` before any write. The in-memory canonical state
  remained `run-p002-live@0`.
- Reloadable evidence is indexed by
  [`P002-live-smoke/manifest.json`](../evidence/P002-live-smoke/manifest.json).
  The PNG proves the capture chain, not architectural usability or quality.


## Completion

- Completed: 2026-07-24
- Evidence: Live Java 1.21.1/Fabric disposable-world smoke passed: localhost-only MCP session, exact plan-1 preview/execute, 25-block artifact and PNG reload, bounded BRIDGE_UNAVAILABLE with canonical state unchanged; 57 tests, compileall, and scope checks pass

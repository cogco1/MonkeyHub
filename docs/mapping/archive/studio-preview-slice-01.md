# Archive — ArchFlow Studio Preview Slice 01 (retired 2026-09-03)

**Tag:** `studio-preview-slice-01` — the last commit on `main` at which the slice is intact.
**Retired by:** P108 (third ruling, 2026-09-03). Not copied into a live `archive/` directory; this card and the tag
are the retention.

## What it was

A local, read-only `.3dm` previewer with an honest ArchFlow capability handshake, in `apps/archflow-studio`:

- `src/` — React 19 + three.js + rhino3dm-wasm; drag a `.3dm` in, it renders locally; layers map to components;
  monolithic `App.tsx` with `StageRail` and `CapabilityPanel`.
- `backend/` — a stdlib `ThreadingHTTPServer` gateway on port 8765 with three GET routes (`/api/health`,
  `/api/capabilities`, `/api/session`); every POST returned 501; `kernel.py` was a presence probe for archflow
  modules; `launch.py` started it as a detached background process.
- `backend/ports.py` / `src/ports/studioPorts.ts` — six reserved Protocols with no implementation (Intent, RAG,
  Preview, Artifact, Human Review, Event Stream), including `IntentProvider`: "Translate a user utterance into a
  proposal candidate, never a commit."

Its README fixed the boundary rules that outlive it: the browser never imports archflow; the backend is a narrow
facade; no canonical write, validation override, Stage advance, Agent, RAG, Rhino or Pascal execution path.

## Why it retired

Kaiwen's ruling: the slice was a low-cost reproduction demo; the formal product (P108) is rebuilt inside the same
namespace as `web/` + `api/` on FastAPI, with generated clients, and the demo's shell would otherwise be a parallel
abstraction beside it.

## What migrated (by moving, never copying)

`ThreeDmViewport.tsx`, `sceneInspection.ts`, the rhino3dm wasm sync/build path, the elevation / Z-up / fit-camera
logic, the boundary rules above, and the six Protocols — migrated verbatim into `api/archflow_studio_api/ports.py`.

## What was measured on it before retirement

The P108 benchmark (2026-09-03) had Fable 5.1 and Opus 5 each implement the same pinned read-model slice on this
gateway in isolated worktrees (`worktree-agent-a9c17353de2c18034`, `worktree-agent-ac27911dd6c673e95`): both
passed an independent judge 29/29 against kernel-computed truth. Those branches are measurement, not product.

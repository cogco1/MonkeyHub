# ArchFlow Studio

ArchFlow Studio is the product shell for ArchFlow. The first bounded slice is a
local, read-only `.3dm` previewer with an honest ArchFlow capability handshake.
It borrows the launcher/workspace idea from Pascal Editor without depending on
Pascal's UI or treating Pascal as a canonical design authority.

## Ownership and boundaries

- `src/` owns the browser product shell and direct, local `.3dm` inspection.
- `backend/` owns HTTP transport and invokes ArchFlow through a narrow facade.
- `archflow/` remains the reusable control kernel and is never imported by the
  browser bundle.
- Project state and evidence continue to live under `probes/<project_id>/` via
  the established project ports. Studio does not invent a persistence path.
- This slice has no canonical write, validation override, Stage advance, Agent,
  RAG, Rhino, or Pascal execution path.

The reserved ports in `src/ports/studioPorts.ts` and `backend/ports.py` define
future integration seams. They intentionally have no concrete implementation.

## Run the first slice

For the built, single-process previewer, run from this directory:

```powershell
npm install
npm start
```

`npm start` builds the frontend and launches the local gateway as a detached,
hidden process, so the preview remains available after the launching terminal
closes. Open `http://127.0.0.1:8765`.

For frontend development, start the foreground gateway and Vite in two
terminals:

```powershell
npm run backend
npm run dev
```

Open `http://127.0.0.1:5174`, then drag a `.3dm` file onto the viewport or use
the file chooser. The file remains in the browser and is not uploaded.

Build and verify:

```powershell
npm run typecheck
npm run build
npm run test:backend
```

The production build is served automatically by the Python gateway when
`dist/` exists. Start `npm run backend` and open `http://127.0.0.1:8765`.

If the page reports `Failed to fetch`, the local gateway is not listening.
Run `npm run launch`, then refresh the page. Runtime PID and logs stay under
the ignored `.generated/runtime/` directory; they are not project evidence.

## Planned slices

1. Read-only project/run snapshot bound to an explicit probe.
2. Candidate asset handoff and cached `.3dm`/mesh preview receipts.
3. Conversational proposal stream through an `IntentProvider`.
4. Stage-aware retrieval through a `RetrievalProvider`.
5. Human review followed by the existing validator/committer boundary.

Each slice must remain usable without implying that later slices are complete.

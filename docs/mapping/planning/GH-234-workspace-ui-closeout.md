# GH-234 workspace UI closeout

Source: existing `codex/workspace-handoff-ui` commit `8f89e34fd4d36c8be233266c20f3903736009d1f` by cogco1.
Base: `72904b1bb6394f7df826f9f642a1e5fec0c6460f`.

EXTEND `hub.shell` only: expose the existing viewed-model/edit-source distinction, Board drawing provenance and drawing revision controls. Keep exact source identity and existing persistence APIs. Use the shared Hub typography roles in touched surfaces.

Validate historical Stage source labels, retained Board CAS/review behavior, drawing appearance-versus-model rebuild behavior and wide/narrow layouts. Broader GH-234 screenshot/inference research and complete workspace redesign remain outside this slice.

The nullable Continue callback lets the integrating ingest lane withhold a semantic continuation capability for external geometry. Opening the Board replacement picker remains available during a read-only background refresh; actual replacement still waits on the existing queue and source checks.

# External runtime configuration

ArchFlow code, deterministic tests, governance, and explicitly promoted probes
remain in Git. Active project documents, rebuildable caches, and temporary tool
files use three explicit roots outside the checkout.

Copy `runtime.example.json` to the ignored `runtime.json`, edit the three
absolute paths, then initialize the roots:

```powershell
Copy-Item config/runtime.example.json config/runtime.json
archflow-runtime --config config/runtime.json init
```

Create an active external project through the existing P036 repository:

```powershell
archflow-runtime --config config/runtime.json bootstrap-project `
  --project-id my-building `
  --prompt "Design a building from this brief."
```

`workspace/projects/<project_id>/` uses the same `project.json`, `HEAD`, input,
objects, events, canonical, runs, and exports layout as a committed probe. Cache
and temp roots are non-canonical. Moving an accepted external project into
`probes/` is a separate reviewed promotion operation and is not automated here.

`runtime.codespaces.json` and `runtime.codex-cloud.json` are credential-free
environment contracts. The Codex Cloud `/tmp` runtime is disposable and does
not claim durable project storage.

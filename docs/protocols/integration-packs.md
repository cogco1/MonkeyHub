# Integration packs

MonkeyHub separates software detection, installed Monkey components, explicit
capability qualification and workflow availability. A detected executable alone
does not make a capability ready.

## Ownership and contract

This extends `adapters.cad_execution`, which already owns discovery,
runtime-qualified conversion providers and the compiled CAD interface.
`hub.shell` exposes its read-only diagnostic view. There is no second CAD
execution registry, project store or universal host lifecycle.

`packages/archflow/src/archflow/adapters/integration_packs.py` defines `IntegrationPack`,
`PackComponent`, `PackCapability`, `PackWorkflow` and `PackInstallation`.
`IntegrationPack.manifest()` serializes `MonkeyIntegrationPack@1`.
The manifest names product/component ids, version, bridge kind, installation
target, supported OSes, permissions/events, existing implementation references
and workflow prerequisites. It contains no executable paths or project data.
Implementation references describe known adapters; they are not dynamically
imported from manifest text.

| Pack | Shipped Monkey component | Existing implementation | Optional component |
| --- | --- | --- | --- |
| Rhino | Supervised desktop host | `CAD_BACKEND_REGISTRY["rhino"]` for execution and exact-source patching | None |
| Blender | Background worker; no Blender add-on | `CAD_BACKEND_REGISTRY["blender"]`; `execute_blender_projection` | None |
| SketchUp | Standalone SDK reader adapter | `read_skp`, including C API 9.0+ checks | Ruby live extension, not implemented or installed |

The existing Rhino host uses Rhino 8 COM. Discovery may report other Rhino
versions without making them compatible. Folder versions remain unverified
hints. Blender keeps its existing operation/runtime checks. The SketchUp reader
does not confer desktop conversion, live observation, capture or editing; the
existing conversion providers remain independent.

## Discovery and status

`SoftwareDiscoveryRegistry.discover(product_id)` serves `rhino`, `blender`,
`sketchup`, `autocad` and `autocad-core`. It checks only the requested product's
known installation locations and Windows App Paths. Blender also preserves
PATH resolution, keeping a symlinked command as PATH names it. An explicit
executable still wins and never silently falls back to another installation.
Legacy discovery functions use the same registry; `discover_rhino_executables`
still answers only on Windows, where the supervised COM host runs.

Discovery runs no shell, CAD host, DLL loader, network or license query; it never
recursively scans disks or writes project data. `Installation.executable` stays
local. Public views report its name, version hint and evidence category without
the full path; architecture is unknown unless explicitly supplied.

`GET /api/integrations` returns `packs`, each containing `manifest`, `software`,
`installation`, granular `capabilities` and `workflows`. The first read performs
bounded discovery; later reads use this Hub instance's in-memory snapshot.
`GET /api/integrations?rescan=true` refreshes it and revokes previous
qualification. There is no polling thread or remote inventory upload.
This is a local diagnostic surface, not an Agent tool or a project record.

Installed components come from host-supplied `PackInstallation` values, never
from detection. The default Hub ships three Monkey-side adapters, enabled but
unqualified, and no SketchUp live extension. Missing components report
`not-installed`; disabled integrations report `disabled`; mismatched pack
versions report `blocked-version`. Unknown capabilities, unimplemented
bridges and unsupported OSes are explicitly unavailable.

## Explicit qualification

Status reads never qualify a host. An authorized runtime caller can use:

- `qualify_cad(pack_id, request)`: the same registered backend executes the
  exact fixture in the caller-supplied speculative workspace. Its unchanged
  result must pass binding, saved-artifact and independent readback validation.
- `qualify_sketchup_read(data, sdk_path=...)`: the existing SDK reader opens
  the explicit source and checks the required API without a desktop host.
- `qualify_blender_projection(request, source, ...)`: the existing projection
  executor and retained-artifact validator check the scene, render and source.

These Python methods are not HTTP/Agent actions. They return the existing
results/receipts, not Pack receipts. Process exit alone never grants readiness.
Failed requalification removes earlier success. Qualifying one capability does
not qualify its siblings: modeling does not qualify rendering, and a Rhino
rebuild does not qualify patching. Workflows require all declared capabilities.

`ready` with `checkedAt` records this session's last successful explicit
qualification. It does not promise a future license/session or support for
every geometry operation. Each execution still uses the backend's normal
validation and the existing project/source authorization. Rescan and Hub restart
discard qualification. No software inventory, local path or qualification enters
P036, HEAD, a candidate or a Stage.

## Optional installation boundary

Components declare `bundled` or `optional-payload`, targeting `monkeyhub` or
`application-extension`. Bridge kinds include CLI, desktop host, native SDK,
local extension, .NET add-in, Python, HTTP, MCP and cloud. MCP is optional.

This slice does not download/install payloads or edit external applications.
A future installer must verify and stage an exact pack version before supplying
an activated `PackInstallation`. Failure leaves the previous installation
active. Disable/uninstall revokes qualification and affects only that Monkey
component, never the external application. Installation state belongs to Hub
application configuration, not P036. Live listeners may observe only an enabled
bridge's declared events and application/project scope.

## Revit as the fourth case

Revit uses the same contract: product `revit`, an optional `dotnet-addin`
component targeting `application-extension`, Windows-only `model.query` and
`view.capture`, and a `revit-current-view-to-board` workflow requiring capture.
Its own protocol/events fit the component contract without an MCP dependency.

Revit is not registered or installed in this release. Its future adapter must
add bounded discovery, qualify Revit/add-in versions and session readiness,
and retain Revit's transaction/lifecycle rules. Sheet/schedule operations and
bounded element writes need separate qualification; a read handshake does not
qualify writes. Query/capture do not require a compiled CAD backend.

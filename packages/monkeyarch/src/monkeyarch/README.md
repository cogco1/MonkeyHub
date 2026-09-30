# MonkeyArch

3D modeling and spatial revision: element production, wall/opening/reference solving, re-indexing, model proposal compilation, relation checks and project-run orchestration.

The workflow consumes shared ArchFlow facts, geometry values and P036 project ports; its runner executes CAD through `monkeycad`. The application host coordinates explicit handoffs with MonkeyDiagram; neither workflow imports the other.

Owners and public contracts: [system map](../../../../docs/architecture/system-map.md). File responsibilities: [repository layout](../../../../docs/architecture/repository-layout.md).

## Layers

The package is layered by whole file (#516), and imports run one way: `application` may import
the other three layers, `authoring` may import `domain`, and `domain` and `compilation` import no
other layer. Only `application` retains records, calls a model or runs CAD; the architecture
policy's `forbidden_layer_imports` holds each of these rules.

| Layer | Modules | What it does |
| --- | --- | --- |
| `domain/` | `wall_solver`, `opening_solver`, `reference_resolver`, `relation_checks`, `massing_metrics`, `domain_readiness`, `discipline_seats` | Plain values from a State Record: walls and the voids they host, the doors and windows that fill them, references resolved to plan points and datums, declared relations measured against realized bounds, massing measures, what a technical domain may read, and the seats that divide authoring |
| `authoring/` | `element_producers`, `producer_signatures`, `element_reindex`, `construction/` | Element@1 rows produced into geometry operations, datums and relations; the authoring contracts the producers advertise; Element@1 drafts recovered from an exported model; the agent's bounded construction script and its lowering to rows |
| `compilation/` | `geometry` | The one compiler: a neutral proposal bound to an exact state, its operations ordered and its objects digested, or an explicit rejection |
| `application/` | `project_runner`, `geometry_proposal` | `run_project` binds a record to a run, projects the developed-design view, runs the seats through the producers and the compiler, checks relations, exports through `monkeycad`, writes the stage closure and exit binding, and retains everything through P036; `geometry_proposal` holds the bounded rounds a provider answers |

The runner writes; it never decides what the design is. The shared project repository remains the only
persistent writer, and drawing execution belongs to `monkeydiagram`, coordinated by the application host.

## Window width and lintel relations

Use the existing `StateRecord.Parameter` expressions and an Element's explicit
`@parameter` bindings. For example, `window_center = window_left + window_width / 2`,
`lintel_left = window_left - bearing`, and
`lintel_right = window_left + window_width + bearing`. The exact-base
`apply_state_record_operator` evaluates downstream values; `record.closure` explains
which elements depend on the edit. The existing wall/window and prism producers
then emit concrete operations, and the runner reuses unchanged production and CAD
shapes from the explicitly selected source run.

A dependency relation may declare `ValidatorBinding("lintel_minimum_bearing")` with
`opening_object_id`, `lintel_object_id`, `span_axis` (`x` or `z`) and
`minimum_bearing_m` in its parameters. The two final objects must belong to the
relation endpoints. The check measures both span-end allowances, bottom-to-head
alignment and transverse overlap in program coordinates (Y up). Its result is
`held`, `violated` or `unchecked`. It measures the declared axis-aligned envelopes;
it does not determine actual bearing faces, structural capacity or compliance.
Include the kind in the stage envelope's `required_checks` when it must prevent
stage exit; registering the check alone does not make it mandatory.

The runnable [window example](../../../../tests/integration/test_window_relational_update.py) exercises
1200 to 1600 mm through P036 reload, the real producers and OCCT, preserves an
unrelated element in the same seat, and distinguishes a failed 150 mm bearing
condition from successful CAD execution:

```sh
python -m unittest tests.integration.test_window_relational_update
python -m pytest packages/monkeyarch/tests/test_lintel_bearing.py
```

Declared component material colours now become native materials in both CAD
export paths. MonkeyArch also restores saved display colours for older files
whose loader supplies only its default white material; existing native materials
and glass transparency retain their original appearance.

The ordinary prism producer also accepts `rectangular_cutouts` for material panels.
Each item names `cutout_id`, `span0`, `span1`, `bottom` and `top` in metres: X is the
span, bottom/top are relative to the prism base, and the cut crosses its Z thickness.
The profile must be an ordered axis-aligned rectangle. A cut may cross a panel edge
or consume it; unchanged pieces retain their identity and empty pieces retire through
the existing incremental runner. This does not relax the wall solver's hosted-opening
boundary rules. A trimmed panel publishes no complete-prism top datum.

Composed candidates inherit a missing donor material from the same source object,
or an unambiguous source component. Explicit donor materials remain authoritative.
This preserves PBR colours, transparency and textures during continuation. The OCCT
preview still supplies meshes for changed objects; retained Breps stay Breps, while
those changed objects continue to be editable through their StateRecord parameters.

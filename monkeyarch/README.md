# MonkeyArch

3D modeling and spatial revision: element production, wall/opening/reference solving, re-indexing, model proposal compilation, relation checks and project-run orchestration.

The workflow consumes shared ArchFlow facts, geometry values, CAD adapters and P036 project ports. The application host coordinates explicit handoffs with MonkeyDiagram; neither workflow imports the other.

Owners and public contracts: [SYSTEM_MAP](../docs/SYSTEM_MAP.md). File responsibilities: [REPO_LAYOUT](../docs/REPO_LAYOUT.md).

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

The runnable [window example](../tests/test_window_relational_update.py) exercises
1200 to 1600 mm through P036 reload, the real producers and OCCT, preserves an
unrelated element in the same seat, and distinguishes a failed 150 mm bearing
condition from successful CAD execution:

```sh
python -m unittest tests.test_window_relational_update tests.test_lintel_bearing
```

Declared component material colours now become native materials in both CAD
export paths. MonkeyArch also restores saved display colours for older files
whose loader supplies only its default white material; existing native materials
and glass transparency retain their original appearance.

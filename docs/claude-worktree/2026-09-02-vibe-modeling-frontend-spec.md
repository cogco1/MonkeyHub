# Vibe modeling client/server framework — user specification (2026-09-02, verbatim)

You are building an AI-native client/server architectural modeling framework for vibe modeling.

The goal is NOT to build another chat wrapper around Rhino, and NOT to let an agent directly manipulate CAD geometry as the source of truth.

The core idea is:

Human intent
→ semantic design state
→ dependency-aware mutation
→ task decomposition
→ parallel execution when safe
→ validation
→ geometry generation
→ 3DM export
→ human review

The architectural design state must be the source of truth.
The 3DM file is an execution/output artifact, not the canonical design model.

==================================================
1. PRIMARY GOAL
==================================================

Build a working prototype where a user can describe or modify an architectural model in natural language, and the system can:

1. Parse the request into explicit design-state changes.
2. Represent the building as semantic components with IDs.
3. Maintain dependencies between components.
4. Preserve locked/protected states during local edits.
5. Recompute only affected dependent elements.
6. Detect conflicts before execution.
7. Run independent modeling tasks in parallel where possible.
8. Validate the result after execution.
9. Export a valid Rhino .3dm file.
10. Record a machine-readable diff and decision history for every mutation.

The prototype should prove this specific claim:

"An architectural agent should modify a dependency-aware design state rather than directly editing geometry."

==================================================
2. DESIGN PRINCIPLES
==================================================

Follow these principles strictly.

A. DESIGN STATE IS THE SOURCE OF TRUTH

Do not treat Rhino geometry as canonical state.

Canonical state should contain:

- components
- parameters
- relationships
- constraints
- dependencies
- locks
- validation rules
- versions
- mutation history
- geometry bindings

Geometry is generated from this state.

B. LOCAL MUTATION, NOT GLOBAL REGENERATION

If the user says:

"Change the front portico from 4 columns to 6 columns."

The system should NOT redesign the entire facade.

It should produce a state patch such as:

ColumnArray.count:
4 → 6

Then determine:

- what must change
- what may change
- what must not change

Example:

MUST CHANGE:
- intercolumniation
- column support positions

MAY CHANGE:
- minor spacing resolution

MUST NOT CHANGE:
- portico width
- pediment width
- entrance axis
- facade boundary

C. DEPENDENCY-AWARE PROPAGATION

A component mutation should trigger updates only along valid dependency edges.

Example:

ColumnArray.count
→ spacing
→ support points
→ entablature support relation

Do not recompute unrelated components.

D. EXPLICIT LOCKS

Every component and parameter can be:

- locked
- flexible
- derived
- optional

Locked states must not change unless the user explicitly unlocks them.

E. VALIDATE BEFORE AND AFTER EXECUTION

Before mutation:
- detect dependency conflicts
- detect impossible constraints
- estimate affected scope

After mutation:
- validate geometry
- validate protected states
- validate semantic relationships
- validate hard constraints
- report unintended mutation

F. PARALLELISM SHOULD COME FROM DEPENDENCY ANALYSIS

Do not serialize everything through one agent.

If tasks do not conflict and share no dependency path, allow parallel execution.

Example:

- modify portico columns
- adjust landscape trees
- modify interior furniture

can run in parallel if independent.

But:

floor height
→ facade module
→ window height

must remain ordered.

==================================================
3. HIGH-LEVEL ARCHITECTURE
==================================================

Use a client/server architecture.

Suggested structure:

/client
  /web
  /rhino-adapter

/server
  /api
  /agent
  /state
  /dependency
  /planner
  /scheduler
  /validator
  /geometry
  /export
  /history

/shared
  /schemas
  /types
  /contracts

/tests
  /unit
  /integration
  /fixtures

Canonical architecture:

CLIENT
- user intent
- project view
- review
- approval
- visual feedback

SERVER
- design state
- dependency graph
- planner
- agent orchestration
- mutation engine
- validator
- task scheduler
- version history

EXECUTION LAYER
- RhinoCommon / rhino3dm / Rhino.Compute / local Rhino worker

OUTPUT
- .3dm
- state.json
- diff.json
- validation_report.json

==================================================
4. SOURCE-OF-TRUTH DATA MODEL
==================================================

Start simple and explicit.

Create a canonical project state schema.

Example:

{
  "project_id": "villa_001",
  "version": 12,
  "components": {},
  "relations": [],
  "constraints": [],
  "locks": [],
  "geometry_bindings": {},
  "history": []
}

Each component should have:

{
  "id": "portico.column_array.01",
  "type": "ColumnArray",
  "parent_id": "portico.01",
  "parameters": {
    "count": 4,
    "spacing_mode": "equal",
    "axis": "facade_x",
    "base_z": 0,
    "height": 5.4
  },
  "status": {
    "locked": false,
    "generated": true
  },
  "metadata": {}
}

==================================================
5. COMPONENT TREE
==================================================

Use a semantic component tree for ownership/hierarchy.

Example:

Building
├── Site
├── MainVolume
├── Roof
├── Facade
│   ├── Portico
│   │   ├── Pediment
│   │   ├── Entablature
│   │   │   ├── Cornice
│   │   │   ├── Frieze
│   │   │   └── Architrave
│   │   ├── ColumnArray
│   │   ├── Entrance
│   │   └── Base
│   └── Openings
├── Interior
└── Landscape

The component tree is NOT enough by itself.

==================================================
6. DEPENDENCY GRAPH
==================================================

Represent non-hierarchical relationships explicitly.

Example:

[
  {
    "source": "portico.column_array.01",
    "target": "portico.entablature.01",
    "relation": "supports"
  },
  {
    "source": "portico.column_array.01",
    "target": "portico.entrance.01",
    "relation": "aligned_to"
  },
  {
    "source": "portico.column_array.01",
    "target": "portico.width",
    "relation": "bounded_by"
  }
]

Support at minimum:

- depends_on
- derived_from
- supports
- hosted_by
- aligned_to
- bounded_by
- constrained_by
- symmetric_with
- intersects
- avoids
- references
- generated_from

Build traversal utilities:

- upstream dependencies
- downstream dependencies
- affected subgraph
- protected subgraph
- independent subgraphs
- topological ordering
- cycle detection

==================================================
7. CONSTRAINT MODEL
==================================================

Support two constraint categories.

HARD CONSTRAINTS:
must always pass.

Examples:
- portico width fixed
- entrance axis fixed
- wall cannot intersect stair
- column count must be integer >= 2
- minimum clearance
- floor level consistency

SOFT CONSTRAINTS:
can be traded off.

Examples:
- preferred symmetry
- preferred bay proportion
- style preference
- visual balance
- daylight preference

Schema example:

{
  "id": "constraint.portico.width",
  "type": "hard",
  "target": "portico.01",
  "rule": "width == 12.0",
  "tolerance": 0.001
}

==================================================
8. STATE MUTATION MODEL
==================================================

All agent edits should be represented as state patches.

Example user intent:

"Change the four columns on the front portico to six, but keep the pediment, entrance axis, and portico width unchanged."

Convert to:

{
  "operation": "update_parameter",
  "target": "portico.column_array.01",
  "parameter": "count",
  "old_value": 4,
  "new_value": 6,
  "scope": "local",
  "protected": [
    "portico.pediment.01",
    "portico.entrance.axis",
    "portico.width"
  ]
}

Before applying the patch:

1. Resolve target.
2. Resolve protected states.
3. Compute affected dependency subgraph.
4. Detect conflict.
5. Build execution plan.
6. Show mutation preview.

==================================================
9. MUTATION PREVIEW
==================================================

Before execution, produce something readable like:

CHANGE REQUEST

Target:
Portico / Column Array

Requested:
column_count: 4 → 6

LOCKED:
- Entrance axis
- Portico width
- Pediment geometry
- Main facade boundary

DEPENDENT UPDATES:
- Column spacing
- Column x positions
- Entablature support points

UNAFFECTED:
- Main volume
- Roof
- Interior
- Landscape

RISK:
Low

Then await execution.

For MVP, auto-execute is acceptable behind a config flag.

==================================================
10. TASK PLANNER
==================================================

Convert an approved mutation into modeling tasks.

Example:

Task 1:
recalculate equal column spacing

Task 2:
generate six column transforms

Task 3:
update support reference points

Task 4:
regenerate column geometry

Task 5:
validate entablature support relation

Tasks should include:

- task_id
- target component
- read dependencies
- write dependencies
- required worker
- priority
- retry policy

==================================================
11. PARALLEL SCHEDULER
==================================================

Build a DAG-based scheduler.

Two tasks may run in parallel if:

- no write/write conflict
- no read/write conflict
- no dependency ordering
- no shared lock conflict

Use topological sorting.

Expose:

- critical path
- parallel task groups
- blocked tasks
- completed tasks

Example:

Wave 1:
- portico column regeneration
- landscape tree update
- furniture layout update

Wave 2:
- portico validation

==================================================
12. AGENT RESPONSIBILITIES
==================================================

Separate reasoning roles.

Do not build one monolithic "do everything" agent.

Suggested agents/modules:

INTENT PARSER
- parse user request
- identify target
- identify parameters
- identify locks
- identify scope

STATE RESOLVER
- locate semantic objects
- normalize references
- detect ambiguity

PLANNER
- compute affected subgraph
- generate state patch
- generate task DAG

EXECUTION AGENT
- execute geometry operations

VALIDATOR
- check constraints
- check protected states
- detect mutation leakage

REPAIR AGENT
- repair failed mutations using smallest possible scope

Do not allow the execution agent to silently redefine the task.

==================================================
13. RHINO / 3DM STRATEGY
==================================================

Use 3DM as the primary geometry artifact.

Do NOT use it as canonical design state.

Each Rhino object should be bound back to semantic state.

Use object user strings where possible:

archflow_id
archflow_component_type
archflow_version
archflow_generated_from

Example:

archflow_id = "portico.column.03"
archflow_component_type = "Column"
archflow_version = "13"

Maintain:

state object
↕
geometry binding
↕
Rhino object GUID

==================================================
14. RHINO IMPLEMENTATION
==================================================

Prefer this stack:

- Python
- FastAPI server
- Pydantic schemas
- networkx for dependency graph initially
- rhino3dm for headless .3dm writing
- RhinoCommon / Rhino.Compute only when required
- JSON state store for MVP
- SQLite optional for version/history later

Do not over-engineer with Neo4j unless graph requirements justify it.

For MVP:
JSON + networkx is enough.

==================================================
15. 3DM EXPORT
==================================================

Implement deterministic 3DM export.

Function:

export_3dm(project_state, output_path)

Requirements:

1. Create valid .3dm.
2. Generate layers by semantic category.
3. Preserve component IDs.
4. Write user strings.
5. Organize blocks/instances when appropriate.
6. Avoid anonymous duplicated geometry.
7. Save version metadata.
8. Return export receipt.

Suggested layers:

00_SITE
10_MASSING
20_STRUCTURE
30_ENVELOPE
40_OPENINGS
50_PORTICO
60_INTERIOR
70_LANDSCAPE
90_DEBUG

Export receipt example:

{
  "project_id": "villa_001",
  "version": 13,
  "file": "villa_001_v013.3dm",
  "object_count": 147,
  "component_count": 42,
  "timestamp": "...",
  "validation_passed": true
}

==================================================
16. VERSIONING
==================================================

Every accepted mutation creates a new immutable version.

Example:

v012
→ request: column count 4 → 6
→ mutation plan
→ execution
→ validation
→ v013

Save:

/projects/villa_001/
  /v012/
    state.json
    model.3dm
    validation.json
  /v013/
    state.json
    diff.json
    model.3dm
    validation.json

Never overwrite the previous version.

==================================================
17. DIFF MODEL
==================================================

Generate semantic diff, not only geometry diff.

Example:

{
  "from_version": 12,
  "to_version": 13,
  "requested_changes": [
    {
      "target": "portico.column_array.01",
      "parameter": "count",
      "from": 4,
      "to": 6
    }
  ],
  "dependent_changes": [
    {
      "target": "portico.column_array.01",
      "parameter": "spacing",
      "from": 3.2,
      "to": 1.92
    }
  ],
  "protected_states_changed": [],
  "unexpected_changes": []
}

==================================================
18. MUTATION LEAKAGE METRIC
==================================================

Implement an experimental metric:

Mutation Leakage

Possible first definition:

unexpected_changed_state_count
/
total_changed_state_count

Also report:

- requested changes
- necessary dependent changes
- unexpected changes
- protected-state violations

Ideal:

mutation_leakage = 0

This metric should be designed cleanly enough to support future research benchmarking.

==================================================
19. VALIDATION
==================================================

Validation must include:

A. SCHEMA VALIDATION
- valid state
- valid component types
- valid parameter values

B. GRAPH VALIDATION
- no illegal cycles
- all dependency targets exist
- no dangling references

C. CONSTRAINT VALIDATION
- hard constraints pass
- soft constraints scored

D. GEOMETRY VALIDATION
- valid geometry
- finite coordinates
- no invalid Breps where required
- expected object count
- no unintended duplicates

E. PROTECTED STATE VALIDATION
Compare protected states before and after.

F. SEMANTIC VALIDATION
Example:
- columns support entablature
- doors remain hosted by wall
- openings remain inside host surface
- floor remains attached to level

==================================================
20. REPAIR
==================================================

Repair must be minimal.

If validation fails:

1. identify failed rule
2. identify smallest affected subgraph
3. attempt local repair
4. revalidate

Do NOT regenerate the whole building unless explicitly allowed.

==================================================
21. CLIENT UX
==================================================

Build a minimal web client.

Primary screen:

LEFT:
- component tree
- project versions

CENTER:
- 3D viewer
- current state

RIGHT:
- natural language input
- requested mutation
- affected components
- validation result

Bottom panel:
- diff
- execution tasks
- logs

The UX should make the user feel they are modifying a design system, not chatting with a generic chatbot.

==================================================
22. FIRST MVP
==================================================

Do NOT try to support arbitrary architecture immediately.

Build one constrained prototype first:

CLASSICAL VILLA / PORTICO TEST CASE

Required components:

- main rectangular volume
- roof
- portico
- pediment
- entablature
- column array
- entrance
- base
- several windows

Required supported mutations:

1. column count
2. column height
3. column spacing mode
4. portico width
5. cornice depth
6. pediment height
7. entrance width
8. window count
9. window spacing
10. roof pitch

Required dependency examples:

column count
→ spacing
→ transforms
→ supports

portico width
→ column spacing
→ pediment width if unlocked

roof pitch
→ ridge height

window count
→ spacing
→ opening positions

==================================================
23. REQUIRED DEMO SCENARIO
==================================================

Initial state:

Portico:
- width = 12 m
- column count = 4
- entrance axis = centered
- pediment locked
- facade boundary locked

User:

"Change the front portico from four columns to six. Keep the portico width, entrance axis, pediment, and main facade unchanged."

Expected:

- column count = 6
- spacing recalculated
- support points updated
- entrance axis unchanged
- portico width unchanged
- pediment unchanged
- facade unchanged
- valid .3dm exported
- mutation leakage = 0
- version incremented

This demo must be fully testable.

==================================================
24. SECOND DEMO
==================================================

User:

"Increase cornice depth by 20%, but do not change the pediment or column positions."

Expected:

- only cornice depth changes
- affected entablature geometry updates
- column positions preserved
- pediment preserved
- no global regeneration

==================================================
25. TESTING
==================================================

Write tests from the beginning.

Minimum tests:

- state schema test
- component creation test
- graph traversal test
- affected-subgraph test
- topological sort test
- lock preservation test
- local mutation test
- mutation leakage test
- version diff test
- 3dm export test
- user string binding test
- deterministic export test

Integration test:

4-column state
→ mutation request
→ 6-column state
→ exported .3dm
→ re-open .3dm
→ verify semantic IDs
→ verify geometry count
→ verify protected states

==================================================
26. ARCHITECTURAL INVARIANTS
==================================================

Do not violate these invariants.

1. No geometry without semantic ownership.
2. No mutation without state diff.
3. No execution without dependency resolution.
4. No accepted version without validation.
5. No protected-state mutation without explicit permission.
6. No global regeneration for a local request unless required.
7. No hidden agent action.
8. Every geometry object must be traceable to semantic state.
9. Every accepted state must be reproducible.
10. Every output .3dm must be reconstructible from canonical state.

==================================================
27. LOGGING
==================================================

Log every run.

Suggested record:

{
  "request_id": "...",
  "user_intent": "...",
  "parsed_intent": {},
  "state_before": "v012",
  "patch": {},
  "affected_components": [],
  "task_dag": {},
  "execution_results": [],
  "validation": {},
  "repair_attempts": [],
  "state_after": "v013",
  "token_usage": {},
  "wall_time": 0
}

This should later support research evaluation.

==================================================
28. FUTURE RESEARCH SUPPORT
==================================================

Design the architecture so these can be added later without rewrite:

- MCTS search over design states
- Pareto optimization
- multi-agent execution
- learned evaluator
- visual evaluator
- building-code validator
- zoning RAG
- human preference memory
- principal judgment memory
- Revit export
- IFC export
- drawing export
- remote Rhino workers
- multiple project workers
- cloud task queue
- enterprise permission control

Do not implement all of them now.

Just keep interfaces clean.

==================================================
29. IMPORTANT ANTI-GOALS
==================================================

Do NOT:

- build a generic text-to-3D generator
- make geometry the source of truth
- directly translate every natural-language request into Rhino commands
- rely on the LLM to remember project state
- let the LLM silently change unrelated components
- rebuild the whole model after every local edit
- hide dependency propagation
- hide validation failures
- create one massive agent class
- prematurely build complex enterprise infrastructure
- overuse databases or distributed systems before MVP works

==================================================
30. DEVELOPMENT STRATEGY
==================================================

Work incrementally.

Phase 1:
- repo structure
- schemas
- project state
- component tree
- dependency graph

Phase 2:
- deterministic villa generator
- 3dm export
- semantic geometry binding

Phase 3:
- mutation patches
- dependency propagation
- locks
- diff

Phase 4:
- planner
- DAG scheduler
- validation

Phase 5:
- natural-language parser
- web client
- demo workflows

Phase 6:
- benchmark and metrics

At the end of every phase:

- run tests
- document assumptions
- record unresolved issues
- do not silently change architecture

==================================================
31. FIRST TASK
==================================================

Start by creating:

1. architecture.md
2. canonical JSON/Pydantic schemas
3. component + dependency graph implementation
4. deterministic classical villa fixture
5. 3DM exporter
6. tests proving a 4-column portico exports correctly

Then implement the mutation:

4 columns → 6 columns

while preserving:
- portico width
- entrance axis
- pediment
- facade boundary

Return:

- implementation plan
- file tree
- first-pass code
- tests
- known limitations

Do not build the LLM chat interface until the deterministic state/mutation/export pipeline works.

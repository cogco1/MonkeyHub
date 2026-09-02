# Position (fixed 2026-09-02)

**Status:** the paper's stance; subordinate to the submitted abstract, which it must still deliver.

## The target

Not Rhino, Revit, REER or MCP. The default assumption behind them:

> Agent + the existing CAD interface = AI-native modeling.

Command-centric CAD interaction (extrude, boolean, move, object ids, vendor
APIs) was designed for a human who holds the design in their head. It is an
inadequate *primary* abstraction for long-horizon, stateful, dependency-rich
architectural agents. Element-level APIs (walls, levels) expose a *current*
model; they still do not expose the derivation, the stage, the authority of
earlier decisions, or the obligations they created.

## Four positions

1. **Agents operate design state, not CAD.** `Level_2.height: 3.9 -> 4.2`,
   not "stretch these Breps". CAD commands are a backend.
2. **Geometry is not the source of truth.** The model is design state +
   relations + provenance; geometry is its materialization at a stage and
   fidelity: `G_t = Materialize(S_t)`. A `.3dm` may go stale; the
   authoritative state may not.
3. **Modeling is state evolution, not generation.** `State_k -> transaction
   -> State_k+1`, and the system knows what changed, why, at which stage, who
   is affected, what is stale, what must be re-verified. *Generation produces
   artifacts; modeling evolves states.*
4. **Dependencies participate in generation.** `House SUPPORTED_BY Terrain`
   is an input to production, not a post-hoc check: correctness by
   construction wherever the relation vocabulary allows it.

## Three scales

- Conservative (claimable now): *a stateful, provenance-aware architectural
  modeling runtime for agents.*
- Middle (the program): *an agent-native architectural modeling substrate* —
  agents build through architectural state, relations and producers, not
  CAD commands.
- Far: *an executable architectural knowledge ecosystem* — producers,
  relations, validators and assemblies accumulate across projects under
  evidence and votes, so the next project does not re-derive what a previous
  one already proved.

## One sentence

> Architectural agents should not learn to operate CAD; architectural
> modeling should be redesigned to operate natively on design states.

## The contrast the paper draws

```
COMMAND-CENTRIC                       STATE-CENTRIC
Intent -> Agent -> CAD actions        Intent -> Agent -> Design transaction
-> Geometry -> Observe -> Repair      -> Authoritative state -> Relations
                                      -> Producers -> Geometry
                                      -> Verification -> Commit
```

Experiment A is this contrast made measurable: baseline (i) is a
command-centric agent over MCP, baseline (ii) a staged prompt chain without
authority, (iii) the state-centric protocol.

## What is already evidence and what is not

| Position | Evidence in hand | Not yet |
| --- | --- | --- |
| 1 | seats author proposals under scope; datums bound by role; P097 fills bookkeeping from records | seats still write extrusions: the element layer (P098/P092/P099) is the frontend that makes 1 fully true |
| 2 | run-016 -> run-017: receipts authoritative, `.3dm` derived and read back; contacts derived from datums (80/80 within 1 mm) | villa design state still partly lives in a script, not a record (P089) |
| 3 | P063 repair locality (0.173, P/R 1.0); facade revisions A/B with retained / recomputed / retired accounting; P097 transactions | type-to-instance propagation (P099) |
| 4 | interface datums make contact hold by construction; HOST_CUT assemblies exist | element relations (support, host, join) executed at generation, not only declared |

## Guard rails

- Argue the paradigm, not the products; REER and MCP are baselines.
- Do not claim the middle scale; state it as the program with the villa as
  the first block.
- Against "BIM is already state-centric": the distinguishing column is
  derivation history with authority and obligations, not state per se.

# P044 — Portable ArchFlow Skill packages

- Origin: Planning
- Status: Ready after P022 and P028
- Depends on: P006, P022, P028
- Reasoning budget: high reasoning is explicitly authorized for architecture,
  protocol, permission, and cross-provider compatibility decisions. Mechanical
  packaging and validation should remain deterministic.

## Goal

Turn ArchFlow's existing state-responsive capabilities into independently
versioned, discoverable, testable Skill packages without moving state,
validation, persistence, or design authority out of the ArchFlow runtime.

The framework owns one provider-neutral `SkillSpec`. Codex, Claude, Kimi, and
future model surfaces receive thin adapters generated from that same contract;
provider-specific files cannot become competing sources of architectural logic.

## Boundary

```text
ContextSlice@1 + current obligations + declared tool availability
  -> discover provider-neutral SkillSpec
  -> host adapter invokes bounded Skill package
  -> SkillAdvice or grounded DecisionOperator proposal
  -> ArchFlow deterministic checks and dependency closure
  -> candidate only; canonical/world writes remain separately authorized
```

Skill packages may contain instructions, references, deterministic scripts,
templates, and declared CLI/MCP tool requirements. They may not contain a
project's dimensions, rooms, topology, palette, coordinates, precedent answer,
or hidden expert order.

## Write scope

- `archflow/skills/`
- `archflow/adapters/skill_surfaces.py`
- `tools/skillctl.py`
- `tests/test_skill_packages.py`
- `tests/integration/test_skill_surface_smoke.py`
- `docs/skills/`
- `docs/mapping/`

## Acceptance

- `SkillSpec@1` declares stable id, semantic version, package digest,
  instruction/reference entry points, input/output schemas, obligation topics,
  evidence requirements, admissible phases, tool requirements, side-effect
  class, authority ceiling, and explicit time/output/retry budgets.
- Skill discovery is recomputed from the current P022 `ContextSlice`, phase,
  obligations, evidence, and available tools. Directory order, registration
  order, and building name never define an invocation schedule.
- Every package is independently loadable, runnable with fixtures, testable,
  and reproducible from its content digest.
- Loaders accept only explicit Skill roots and fail closed on missing,
  duplicate, stale, malformed, cross-package, or digest-mismatched content.
  There is no hidden global directory or fallback package.
- A Skill receives a bounded detached context slice, not the full transcript,
  live canonical state, repository writer, committer, or unbounded world
  handle.
- Skill output is either read-only advice or an exact-base proposal carrying
  non-empty `responds_to_refs`. It cannot declare its own output verified,
  usable, accepted, or canonical.
- Read-only CLI/retrieval requirements remain read-only. Any MCP mutation intent
  is routed through the candidate workspace and P024/P025 authority boundaries;
  a Skill never writes a world directly.
- Invocation receipts preserve Skill/package/version digest, context and
  obligation refs, selected provider surface, declared tools, input/output
  digests, budget use, failures, and produced proposal/advice refs.
- Provider adapters for Codex, Claude, Kimi, or later hosts remain thin
  translations. Removing one adapter does not remove the provider-neutral
  package or alter its behavior contract.
- At least one Codex-compatible `SKILL.md` export is generated and smoke-tested
  from `SkillSpec@1`; generated host artifacts contain no additional design
  authority.
- Framework scans prove that Skill packages contain no project-specific
  dimensions, rooms, topology, palette, coordinates, precedent answer, or
  expert order.

## Tests

- Skill manifest and package digest round trip.
- Explicit-root discovery and no-fallback failure.
- Phase, obligation, evidence, and tool availability intersection.
- Registration and filesystem order independence.
- Context-slice isolation from transcript, canonical writer, and sibling state.
- Independent fixture execution with bounded timeout/output/retry.
- Exact-base and `responds_to_refs` proposal validation.
- Advice remains detached and cannot waive hard gates.
- MCP mutation request cannot bypass candidate/authority boundaries.
- Codex export/load smoke and provider-adapter parity.
- Package tamper, duplicate id/version, missing reference, and schema mismatch.
- No instance-answer static scan.

## Stop conditions

- Stop if `SKILL.md` or another host format becomes the core source of truth.
- Stop if a Skill can commit canonical state, waive hard gates, or write an
  external world directly.
- Stop if packaging copies project/probe data into framework modules.
- Stop if cross-provider support requires duplicating architectural logic.


## Completion

- Completed: 2026-08-10
- Evidence: Implemented provider-neutral SkillSpec@1 packages with explicit-root content-addressed loading, current ContextSlice discovery, detached bounded advice/exact-base proposal execution, authority-preserving receipts, thin Codex/Claude/Kimi exports, a generic obligation-review package, deterministic CLI controls, and fail-closed tamper/instance-answer checks. Targeted 11 tests pass; full regression passes 473 tests with 2 skips; architecture firewall passes 109 files; generated Codex SKILL.md passes the skill-creator validator.

# Portable ArchFlow Skills

P044 packages state-responsive reasoning as versioned content without moving
design state or execution authority into a model-host folder.

## One contract, several surfaces

```text
explicit package root
  -> verify SkillSpec@1 + every declared content file + package digest
  -> intersect current ContextSlice@1, phase, obligations, evidence, and tools
  -> detach one bounded SkillInvocationInput@1
  -> run through a Codex / Claude / Kimi / future thin host surface
  -> SkillAdvice@1 OR exact-base SkillProposal@1
  -> normal ArchFlow dependency closure, candidate assembly, player authority,
     hard validation, and promotion boundaries
```

`skill.json` is the source of truth. Generated `SKILL.md` files are disposable
host adapters. Deleting a host export does not delete the package or change its
behavior contract.

## Package layout

Every package root is supplied explicitly. There is no global search path,
default package directory, or fallback.

```text
<explicit-root>/
  skill.json
  instructions.md
  references/...       # optional and declared
  scripts|templates/... # optional and declared as resources
```

The manifest declares:

- stable lower-hyphen id and semantic version;
- content digest and all instruction/reference/resource entrypoints;
- detached input and allowed advice/proposal schemas;
- obligation topics, evidence kinds, phases, and exact tool grants;
- read-only or candidate-only side-effect class;
- advice or proposal authority ceiling;
- timeout, output-byte, and retry budgets.

The package digest covers the manifest contract with a normalized digest field
and the path, size, and SHA-256 of every declared file. Missing, undeclared,
symlinked, cross-root, duplicate, stale, or tampered content fails closed.

## Discovery is not a workflow

Discovery is recomputed for every current `ContextSlice@1`. A package is
eligible only when all of these are true:

- at least one current open obligation topic matches;
- the current phase is admissible;
- every required evidence kind is available;
- every exact tool kind and access grant is available.

Results are sorted only for reproducibility. Sorting, registration order,
filesystem order, provider name, and building name never define an invocation
schedule. The Architect/runtime still decides which eligible capability to
invoke and how to account for its result.

## Detached execution boundary

A Skill receives a canonical JSON copy of one context slice, its current
obligation-topic mapping, evidence kinds, and declarative tool grants. It does
not receive a transcript, sibling branch state, repository, committer,
canonical writer, workspace path, MCP client, or world handle.

Read-only CLI and retrieval grants cannot request mutation. An MCP package may
declare `candidate_mutation`, but the invocation input still contains metadata,
not a callable client. Any later MCP execution must be compiled from the Skill
proposal into the existing P024 candidate assembly and pass the P025 preview
and player-authority boundary. MCP success remains candidate evidence; it does
not verify usability or advance canonical state.

`SkillAdvice@1` is explicitly read-only. `SkillProposal@1` must:

- bind the exact target-state digest;
- use an authority id present in the context slice;
- carry at least one current `responds_to_refs` value;
- cite only evidence present in the slice;
- remain candidate-only, unverified, unaccepted, non-canonical, and without a
  world write.

Invocation receipts bind the package id/version/digest, provider surface,
context and obligation refs, exact declared tools, input/output digests,
budget and use, failure, produced output ref, and response refs. Receipts do
not acquire validation, acceptance, persistence, or promotion authority.

## Built-in generic package

`archflow/skills/packages/review-design-obligations` is the first generic
package. It reviews whichever current obligations are supplied; it contains no
room list, dimensions, topology, palette, coordinates, precedent answer,
building identity, or expert order.

## Deterministic controls

Use absolute roots so package identity never depends on the current directory:

```powershell
python tools/skillctl.py validate D:\path\to\skill-root
python tools/skillctl.py seal --root D:\path\to\skill-root
python tools/skillctl.py export --root D:\path\to\skill-root --surface codex --output D:\explicit\export-root
```

`seal` rewrites only the named package manifest. `export` refuses to overwrite
an existing generated file. The core adapter renders files in memory and has
no filesystem write or project persistence authority.

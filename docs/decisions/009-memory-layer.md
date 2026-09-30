# ADR-009 — Memory is its own layer: `studio.memory`, beside State, not inside decisions

**Decision (Kaiwen, 2026-09-28, #252):** project memory is its own owner, module `studio.memory`, with its own
records (`StudioMemoryRecord@1`, record kind `studio-memory-record`, in the fixed `studio-memory` run through
the P036 ports) and its own routes (`/api/memory`). It is not the scoped decisions: those stay Project State
under #185 (`studio-decisions`, module `studio.intent`). This replaces the earlier ADR-009, "memory is scoped
decisions".

1. **Six layers, each with one owner.**
   - **Files / artifacts** hold the bytes.
   - **State** (StateRecord, decisions, dependencies, lineage) remembers what the project *is* and what it settled.
   - **The tree** (State / Stage / Candidate / Working Head) is a projection of State and owns no fact.
   - **Memory** remembers how we *work*: locators, source policies, recipes; later preferences, habits, standards.
   - **Skills** know how to act: versioned procedures loaded on demand.
   - **The context compiler** (`POST /api/intents/context`) compiles State + applicable memory + skills into one
     bounded ContextPack: `scopedDecisions` beside `memory`.
2. **The record** has the owner's shape: `key`, `kind`, `scope`, `appliesWhen` (domains, topics, keys, optional
   Stage), a kind-specific `value`, `authority`, `provenance` (the user's raw words, `messageSource`, `sourceKind`,
   evidence refs), `version` and `status` (active, superseded, revoked).
   - Kinds now: `locator` (`{label, target}`; the target is retained project content, never a machine path or a
     URL), `source_policy` (`{topic, keys, prefer, avoid, note}`) and `recipe` (`{task, skill, note}`). Reserved:
     `preference`, `habit`, `standard`.
   - Scope now: `project`. Reserved for the library project: `organization`, `team`, `user`.
3. **Cross-project memory** lives in a **library project**: an ordinary P036 project that other projects consume
   by explicit, pinned import, as #331 does for recipes. No new persistence authority.
4. **Skills: option A**, in a separate PR (`GH-252/skill-library`). Skills are versioned items retained in the
   library project and handed to agents through their native skill mechanism; no MonkeyHub-specific loader.
5. **Authority is kept apart from confidence.** `authority` says whose rule it is: `explicit` now (the user's words or
   a person's action); `observed` and `inferred` are reserved. `confidence`, `supportCount` and
   `contradictionCount` stay out of the record until something computes them, and a score never makes an inferred
   item the user's own.
6. **Retrieval order.** Scope, then `appliesWhen`, then lexical matching (NFKC, casefold, CJK bigrams), then an
   optional rerank later (never the first filter), then the exact source read: a locator's target is read again
   through P036 on every lookup, and one that no longer resolves is returned stale with its reason.
7. **A recipe is a memory item that names a skill version** (#252 round 3c). Its value is the task in the user's
   words and one exact library skill, `skill:<name>@<version>`; the steps stay in the skill (Kaiwen, #252
   comment 5877462835, point 3). `appliesWhen.domains` are the ones the user's words indicate, its topics the task
   words, and it matches a turn's words as a source policy does. `studio.memory` checks only the ref's spelling and
   never reads the library: when a chat saves one, the Hub resolves the named skill against the configured library's
   current index, read through the library's own Runtime, and fills the exact version, refusing with the reason when
   no library is set or it holds no such skill. Every recipe the Hub puts into a turn says which skill to load and
   whether the pinned version is still the library's current one ("pinned 1, library now 2", or "not in the
   library"); nothing is guessed or swapped, the agent tells the user.
8. **The drawing recipe decision is a separate thing.** The accepted drawing recipe (`require` + `recipe` typed
   binding in `studio-decisions`) stays a decision until its own migration; a `recipe` memory item does not
   replace, read or shape it.

**Why:** a memory item is not a judgement about this design. Keeping it in its own run and kind means a build that
knows only decisions never meets one (a locator in `studio-decisions` made older builds answer 500), and the two
layers can change apart. The revision chain, its fail-closed checks and message provenance are the decisions'
machinery, reused by import rather than copied.

**Do not:**
- Build a second fact store: memory copies no geometry, dimensions, dependencies or file content.
- Put memory back into decisions, or decisions into memory.
- Save an item as the user's without their words, or infer one from behaviour before #253.
- Make embeddings the first filter, or inject all memory into every turn.
- Hand a source policy to a turn that named design, drawing or copy.

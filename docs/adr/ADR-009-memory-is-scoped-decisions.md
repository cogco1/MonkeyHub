# ADR-009 — Memory is scoped decisions; cross-project memory is a library project

**Decision (2026-09-28, #252):** project memory is the existing scoped decisions (module `studio.intent`).
Each memory item is a revisioned decision retained through the P036 ports. It keeps the user's raw words,
their message source, the evidence it was said against, its scope (domain, then project, Stage or targets),
its strength, and its revoke/supersede chain. Locators and source policies sit in their own fixed run,
`studio-memory`, beside `studio-decisions`, with the same revision format and owner; a build that predates
them reads only `studio-decisions` and never meets one.

1. **Kinds.** Each kind is one decision form, told apart by its typed binding:
   - **standard**: a `hard` decision; **recipe**: a drawing `recipe` binding (the one a new drawing starts from);
   - **preference**: a `strong_preference` or `soft_preference` decision;
   - **locator**: domain `locator`, disposition `refer`. It points at retained project content: a registered
     document page, an artifact by sha256, or a board element. It is not a machine path or a URL;
   - **source policy**: domain `research`. It holds a topic in the user's words, normalized keys, ordered
     `prefer` and `avoid` sources, and a note. Its evidence may be the user's message alone
     (`source: {kind: 'words'}` with `messageSource`), so a project with no design can hold one;
   - a locator's and a source policy's one `targetRef` may be omitted.
   - **Inferred habits come later.** They wait for #253 accept/reject evidence and will be marked inferred.
2. **Authority is kept apart from confidence.** `sourceKind` and `messageSource` say whose words these are.
   `strength` says how firmly the item holds. A future confidence score says how often behaviour supported it.
   A score never turns an inferred item into the user's own, and a user's item said once stays authoritative.
3. **Retrieval order.**
   1. Filter by scope: domain, Stage, targets.
   2. Match lexically: NFKC, casefold, CJK bigrams.
   3. Optionally rerank. This comes later and is never the first filter.
   4. Read the exact source. A locator's target is read again through P036 on every lookup, and one that no
      longer resolves is returned stale with its reason.
4. **Cross-project scope** (person, team or firm) lives in a **library project**. That is an ordinary P036
   project, and other projects consume a pinned revision of it by explicit import, as #331 does for recipes
   (Kaiwen, 2026-09-28).

**Why:** the decisions owner already keeps words, provenance, scope, strength and revisions. A second store
would duplicate all of that and drift from it. Filtering by scope first stops a large memory from being
injected into every turn.

**Do not:**
- Build a memory service or a second fact store.
- Copy geometry, dimensions or dependencies into a memory item.
- Save an item as the user's without their words.
- Infer a preference from behaviour before #253.
- Make embeddings the first filter.
- Hand a source policy to a turn whose words are not about its topic, or to a turn that named design or drawing.

# W1-C — The capability panel: what a selected element can be asked

## Status

DONE_WITH_CONCERNS

The panel is built, mounted, translated, styled, and verified live against two
synthetic fixtures. The concerns below are readings of the brief I had to make
and one thing the brief's own condition leaves uncovered; nothing was left
unimplemented.

## Commits

- `a757ef9` — Studio web: the capability panel says what a selection can be asked
  (the panel, the mount, both i18n tables, the styles, and this report)
- `ac67a9c` — this report, with the hash of the commit above filled in

Branch `worktree-agent-a20b32f2d1b09afa6`, based on `main` at `bc605aa`. Nothing
merged, rebased or pushed.

## Files

| File | What |
| --- | --- |
| `apps/archflow-studio/web/src/features/conversation/CapabilityPanel.tsx` | new — the panel |
| `apps/archflow-studio/web/src/features/conversation/Composer.tsx` | mounts it under the tree; docstring extended |
| `apps/archflow-studio/web/src/i18n/messages.en.ts` | 20 `capability.*` keys |
| `apps/archflow-studio/web/src/i18n/messages.zh-CN.ts` | the same 20 keys |
| `apps/archflow-studio/web/src/styles.css` | `/* capability panel … */` block appended |

Not touched: `ComponentTree.tsx`, `App.tsx`, the API, the generated client.

## What it does

Props are `projection: StateProjectionDto`, `selection: Selection` (the type the
Composer already exports — no second selection state), and
`onPrefill(sentence: string)`.

- **element selected** → one row per capability of that element.
- **component selected** → the capabilities of its descendant elements, grouped
  by element, elements that have an editable capability first, at most 12 rows
  then `capability.andMore`.
- **component with objects and no descendant element**
  (`objectCount > 0 && descendantElementIds.length === 0`, the tree's own
  condition) → the catalog-missing sentence and a **declare a control** button
  that prefills `补充 <componentId> 字段`.
- **no catalog** → `capability.noCatalog`.
- **catalog but nothing to show** → `capability.noneElement` /
  `capability.noneComponent`.

A row carries: key (mono), value with unit, status chip (`editable / locked /
derived / missing`, translated), source, confidence when `< 1`, bounds when any,
validator count when any, and — only when `status === "editable"` — a `set…`
button that calls `onPrefill("set <key> to <value>")`.

`derived` and `locked` rows offer no button; `locked` carries
`capability.lockedHint` as its chip title.

**Prefill needed no new prop and no App.tsx edit.** `Composer` already takes
`draft` / `onDraft`, threaded from `App.tsx` through `Conversation.tsx`; that is
the same text state the transcript's `onReply` and `onAdjust` already write, so
`onPrefill={onDraft}` reuses the existing seam rather than adding a second one.

Numbers are printed `Number(value.toFixed(6))`, which is exactly how `App.tsx`
already builds a `set <key> to <n>` sentence for the refine slider, so the
prefilled sentence and a refined one are the same string for the same number.

## i18n keys added (both tables, identical key sets)

`capability.ariaLabel`, `capability.title`, `capability.show`,
`capability.hide`, `capability.noCatalog`, `capability.noneElement`,
`capability.noneComponent`, `capability.catalogMissing`,
`capability.declareControl`, `capability.set`, `capability.setAria`,
`capability.andMore`, `capability.source.authored`,
`capability.source.derived`, `capability.source.derivedFrom`,
`capability.source.reindexed`, `capability.confidence`,
`capability.validatorsOne`, `capability.validators`, `capability.bounds`,
`capability.lockedHint`.

Reused rather than duplicated: `tree.state.editable / locked / derived /
missing` for the status words, and `tree.capabilities` for the header count.
No literal UI string is in the TSX.

## Styles

Appended to `src/styles.css` under
`/* capability panel (CapabilityPanel.tsx), on the picker's frame, tree chips reused */`.
The chips are the tree's `tree__chip` / `tree__chip--<status>` classes. One
addition to that family: `.tree__chip--editable`, which `ComponentTree.tsx`
already emits for a component's `states` but which had no rule, so editable
chips were unstyled in both the tree and this panel until now.

## Tests

There is **no JavaScript test runner in this repo** — `apps/archflow-studio/web`
has no `test` script and no vitest/jest dependency, and there is not one
`*.test.ts(x)` under `web/`. So common-brief rule 5 has no web harness to hang a
unittest on, and the brief's own gate for this task is typecheck + build. Both
were run in the worktree (after `npm ci`, which the worktree needed):

```
cd apps\archflow-studio\web
npm run -s typecheck     # clean, no output, exit 0
npm run -s build         # ✓ built in 274ms
```

`npm run -s build` runs `sync && tsc --noEmit && vite build`; its only warning is
the pre-existing "chunks larger than 500 kB" notice, unchanged from `main`.

No API DTO or route changed, so `api:generate` / `api:check` were not needed and
`src/api/generated/*` is untouched.

### Preview: verified, twice, against synthetic fixtures only

Both fixtures were built by the API's own `apps/archflow-studio/api/tests/support.py`
into `%TEMP%`, never under `D:\PROJECTS` and never in the repo. The two scratch
builder scripts live in the session scratchpad and are **not committed**. The
main session's studio (API :8000, web :5174, bound to a real project) was not
touched: my API ran on :8123 / :8124 and my Vite on :5199 / :5200 with
`ARCHFLOW_STUDIO_API_URL` pointing at mine.

**Fixture 1** — `make_portico_project()`, no inspection. Catalog present, three
editable `height` capabilities.

- element `portico-base` → panel open by default, row
  `height · 0.6 · editable · authored · 1 validator · set…`;
- clicking `set…` put **`set height to 0.6`** in the composer's box (read back
  from the live input element);
- component `portico` → collapsed by default, expands to three groups
  (`portico-base`, `portico-cornice`, `portico-roof-abutment-west`);
- component `portico-columns` (no elements, no objects) →
  `capability.noneComponent`;
- both languages checked: the English table renders
  `Can be asked / portico-cornice / 1 capabilities / hide / height 0.6 editable
  authored 1 validator set…`.

**Fixture 2** — the same project plus a `seat-3dm-inspection` whose objects
`obj-portico-columns-0/1` claim `archflow:component=portico-columns`, a
component with no `Element@1` row. The catalog then reports
`portico-columns: desc 0, obj 2, unbound 2`, which is the catalog-missing
condition.

- selecting it showed the catalog-missing sentence and the **declare a control**
  button;
- the button prefilled **`补充 portico-columns 字段`**;
- **sending that sentence** was answered by the server with a refusal card
  `该对象没有可编辑的控制项` and a draft-control card
  `需要有人补充的控制项草案 · portico-columns-control`, i.e. the clarification
  seam read it as `declare_missing_control` / `MISSING_EDITABLE_CONTROL`, which
  is what the brief asked the sentence to be.

Two code changes were made after that first preview — the singular
`capability.validatorsOne` (concern 5) and removing a stray NUL byte
(concern 6). Typecheck and build were re-run clean afterwards, and all three
branches were re-verified live on fixture 2: element auto-opens with its row,
component defaults collapsed and expands into three groups, catalog-missing
shows its sentence and its button.

## Registrations needed

**None.** `governance/module_registry.json` lists Python modules only — the
registry's own note says so of the client half of the seam
(`apps/archflow-studio/web/src/api/connection.ts` "which this registry does not
list because it lists Python modules only"). No record kind, DTO or route
changed. `governance/module_registry.json` and
`archflow/project/record_kinds.py` are untouched.

## Concerns

1. **"editable descendants", widened by one step.** The brief says a selected
   component shows "the capabilities of its editable descendants grouped by
   element". I render every descendant element that has capabilities, ordering
   elements with an editable capability first, rather than dropping elements
   whose capabilities are all `locked` / `derived` / `missing`. Hiding them
   would hide from the architect that the field exists at all, which is the
   failure this panel is for. Today the API only ever emits `status=editable`
   and `source=authored` (`application/catalog.py` builds every `Capability`
   that way), so the two readings are identical in practice; they diverge only
   once W1-B/W1-D start emitting other statuses. Say the word and it is a
   one-line filter.
2. **The 12-row cap is on the component branch only**, as the brief scopes it. A
   single element with very many params is therefore uncapped. Trivial to extend
   if that is wanted.
3. **`portico-columns` gets no "declare a control" button when it has no
   objects.** The brief gives the condition verbatim
   (`objectCount > 0 && descendantElementIds.length === 0`) and it is the tree's
   own `catalogMissing()`, so the two panels agree — but the catalog itself is
   broader: `application/catalog.py` puts `missing` in a component's `states`
   whenever it has **no descendant elements at all**, objects or not. So a
   component the record declares with nothing under it and nothing exported
   reads `capability.noneComponent` and offers no way forward, even though
   declaring a control is exactly the next move. Relaxing the panel's condition
   to `descendantElementIds.length === 0` (dropping the object test) would cover
   it; I did not, because the brief named the condition.
4. **`statusLabel` is a second small lookup**, not a second vocabulary:
   `ComponentTree.tsx` has an identical four-entry map over the same
   `tree.state.*` keys. I did not touch `ComponentTree.tsx` to export its
   helper, because the file is another worker's this wave and a merge conflict
   there costs more than four lines. Worth folding into one exported helper
   after the wave lands.
5. **`tree.capabilities` reads "1 capabilities".** That is the tree's existing
   key and I reused it rather than forking a plural of my own. My own
   `capability.validators` did get a singular (`capability.validatorsOne`)
   because I saw "1 validators" in the live preview; the tree's `{n} elements` /
   `{n} capabilities` chips have the same defect and are not mine to change.
6. **A raw NUL byte had got into the new file and is gone.** The first draft
   keyed the panel's fold state on a `` `${componentId}<sep>${elementId}` ``
   template string, and the separator I wrote landed on disk as a literal
   `U+0000`. Typecheck, build and the browser all accepted it silently; `od`
   found it. The fix removes the string key entirely — the fold state now holds
   the `Selection` itself and compares `componentId` / `elementId` — so there is
   no separator to get wrong. All five changed files were then re-scanned
   (`grep -P '[\x00-\x08\x0b\x0c\x0e-\x1f\r]'`): zero control bytes, zero CR,
   pure LF, matching the rest of the repo (`core.autocrlf=false`). Worth knowing
   for the lane: an invisible control byte survives the whole web gate.
7. **The worktree started stale.** Its branch was at `6a4a31a`, several commits
   behind `main`, and did not contain `ComponentTree.tsx` at all. The branch had
   no commits of its own (HEAD == merge-base), so I fast-forwarded it to `main`
   (`bc605aa`) before starting — no merge of divergent work, no rebase of
   anything, nothing pushed. Without it I would have been extending a file that
   does not exist on this branch.
8. **`npm ci` was needed** in the worktree; `node_modules` existed only in the
   main tree. It writes only `apps/archflow-studio/web/node_modules`, which is
   gitignored and not committed.

## Left out and why

- **No unittest**, because the web app has no test runner at all (no `test`
  script, no vitest/jest dependency, no `*.test.ts(x)` anywhere under `web/`).
  Adding a runner would be a toolchain decision well outside this brief. The
  behaviour is instead verified by the live preview above, branch by branch,
  including the round trip through the server for the declare-a-control
  sentence. If the lane wants a runner, that is its own task and I did not open
  it unasked.
- **`.claude/launch.json` not extended.** I tried to drive the preview through
  `preview_start` by name, but that tool reads the *main* checkout's
  `.claude/launch.json`, which is another session's uncommitted working file. I
  reverted the entry I had added to my worktree's copy and started the two
  servers directly instead; `launch.json` is unmodified in the commit.
- **Nothing written to canonical HEAD or to any `D:\PROJECTS` path.** Both
  fixtures were built under `%TEMP%` from the API's synthetic test support.

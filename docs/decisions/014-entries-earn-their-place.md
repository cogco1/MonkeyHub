# ADR-014 — A new entry earns its place: where it goes, and how many a place holds

**Decision (Kaiwen, 2026-10-21):** [ADR-011](011-interface-information-hierarchy.md) fixes which row an element
sits in; this record fixes whether a new entry is shown at all, and how many entries a place may hold. An entry
is a control a person can see and click, fill or switch, counted as in [the entry audit](../audits/hub-entries.md).

1. **Three questions first.** Who uses it, in what state; how often (high, medium, low); can the AI do it. The
   answers go in the PR's 入口去向 table.
2. **Placement order:** the conversation (an AI tool) → the command palette → shown with a state (a context bar,
   a result card, the selection's card) → an existing menu → a standing button. The further along the order, the
   more reason the PR gives; a low-frequency entry does not take a standing place.
3. **Budgets,** counted per surface over the places the command tree's commands stand in; going over fails.
   Each row's key is the one `ui_entries.budgets` in `governance/architecture_policy.json` uses.

   | Place | At most | Policy key |
   |---|---|---|
   | Project bar: standing menu words (the mode switch at the left and the Stage words at the right end not counted) | 4 | `project_bar` |
   | Project bar: words shown with a state \* | 1; the rest go in a context bar | `project_bar_state` |
   | A surface's tool group | 9 buttons, "…" included | `tool_group` |
   | One dropdown or menu | 8 items, of one kind | `menu` |
   | Rail | 6 | `rail` |
   | Top of the sidebar \* | 3 | `sidebar_top` |
   | Controls on screen in one panel | 10; beyond that, pages or folds | `panel` |

   \* Added by the coordinating session from the interface review's section 3.3; not yet confirmed by Kaiwen,
   who chose the other five; their policy keys are `project_bar_state` and `sidebar_top`. Removed if Kaiwen
   declines them.

   Over budget at the audit: Modeling's More (15 controls of five kinds), the Board's More board actions (9, three
   kinds), the rail (9), the tracing-paper tool row (about 21), the document page's right column (22), the
   Drawings parameter panel (32), and Render (three bars where ADR-011 allows one).
4. **One main entry per action.** The command palette and keyboard shortcuts are not second entries; any other
   second entry states why.
5. **Two tiers.** Each command is basic or advanced. Students start in basic; View › Show advanced tools
   turns advanced on; the command palette finds every entry in either tier. The document page's research mode is
   advanced.
6. **Command palette:** Ctrl+K on every surface. On the Board the Hub takes Ctrl+K before Excalidraw, and
   inserting a link moves to the right-click menu.
7. **The rail loses three items:** Usage moves into Hub settings; Fabrication into Export and the command
   palette; This project opens by clicking the project's name.
8. **Drawings reach the Board by hand only.** A drawing or render result offers Put on Board; the Board's
   automatic receiving of new drawings is turned off.
9. **Names** enter the glossary first, then both catalogues (`messages.zh-CN.ts`, `messages.en.ts`), never as
   `zh ? … : …` in JSX. The Chinese terms: 当前方案 (in place of 修改起点 and 当前), 候选 (candidate), 阶段
   (Stage), 状态树 (unchanged), 画板 (Board), 交给 AI (the action that hands a page or member to the AI).
10. **Checks:** the PR template's 入口去向 table lists added, removed and moved entries by command id; CI counts
    each place from the command tree.

**Until the command tree and the budget check exist** (B1 and B8 below, after the 2026-10-26 review), a change adds no
standing button, menu item or toolbar entry; a new capability reaches people through AI tools and the
conversation. The entries added on 2026-10-21 before this decision (the Section group in Modeling's More, its
Draw this section, and the Put on Board line under the drawn result) stay in More until B2 moves them.

**Why:** the audit counted 711 entries, 81 of them always on screen. The crowding came less from that number
than from three things: one action reached from several places (the Design tree opens from 7); unrelated actions
in one panel (Modeling's More mixes camera, display, section, file and parameter locks); and state-dependent
buttons in the standing project bar, which wraps to three rows at 620 px. 412 entries arrived between 2026-09-15
and 10-05; the 24 added after 10-06 landed in the most crowded places. The capabilities added from 10-19 to 10-21
(camera passes, member quantities, Live Rhino, STL export) have no buttons and are used through AI tools, and
that route works.

**Do not:** give a low-frequency action a standing button; put a second kind of thing in an existing menu; add
a second main entry without a stated reason; write a label as `zh ? … : …`; turn the Board's automatic receiving
back on; remove an entry before the command palette can find it.

## Order of work

B1 comes before B2 and B3: a removed entry needs a place to be found first.

| Step | Work | Accepted when |
|---|---|---|
| B1 | The command tree, a view of the capabilities of `governance/module_registry.json`: a capability is a group and its `ui.commands` are the commands (id, Chinese and English names, aliases, surface, place, tier, when it is enabled); `devctl render-map` writes the view the web reads, `apps/monkeyhub/web/src/features/commands/tree.generated.ts`, as it writes the system map, and the web keeps no list of its own. The command palette, adding only; Ctrl+K taken on the Board; a local click count (command id and count, kept on the machine) | every existing menu item has a command id; in Modeling and on the Board, searching 剖切 and 导出 runs the command |
| B2 | Modeling's More split: a View menu, a section bar shown while a section is on, Export takes the screenshot, File takes opening a local model; parameter locks into the member card; the reference model and unloading into the palette | each of More's 15 controls runs from its new place or the palette; at 620 px the project bar is one row |
| B3 | Duplicates, first pass: the rail as in point 7; New project into the sidebar's + menu; Modeling drops View in Design tree, Board and Member info; Render drops its own toolbar; refresh buttons appear only after a failed read | Design tree entries 7 → 3, rail 9 → 6, the Hub shell's standing entries 25 → 20, as the budget test counts them |
| B4 | One shared decision row in place of the 13 separate "Continue from here" and "Record changes and continue" rows | the three states look the same in Modeling, the document page and the Design tree |
| B5 | Context actions: the tracing-paper bar 21 → 7; the member card; drawing and render result cards offer the same three actions (to the Board, into the layout, download) | each context bar has a shown and a hidden screenshot; nothing renders while its condition is false |
| B6 | The document page gets its own project bar menus; its right column in three pages; research mode advanced | at most 10 controls on screen in the right column; the basic tier does not show research mode and the palette opens it |
| B7 | The Board's and Drawings' project bars regrouped; Excalidraw's main menu, help and tool lock off; Export's "Prepare for 3D printing…" hands an STL to Fabrication; the Board's automatic receiving off | the project bars' word counts are within budget; one path runs from the model to the Fabrication page |
| B8 | The tier switch and the CI budget check | a deliberately over-budget change fails |
| B9 | The terms of point 9 in both catalogues, with a catalogue check | every Chinese catalogue value equal to its English one is on an allow-list |
| B10 | Scoped on its own: Modeling's inner conversation panel into the Hub conversation; what is left of the Versions panel into the Design tree (both decided 2026-09-25) | its own acceptance |

Sources: [the entry audit](../audits/hub-entries.md) at `bf64552d`; Kaiwen's choices on the interface review,
2026-10-21.

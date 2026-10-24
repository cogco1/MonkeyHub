# ADR-011 — One information hierarchy on every surface: text menus, focused instruments, status text

**Decision (Kaiwen, 2026-09-26, #337):** every element of a Hub surface is one of three kinds, each with a
fixed place, and every surface stacks the same six rows.

1. **Three kinds.**
   - **Text menus (文字菜单):** navigation, parallel modes, commands. Plain words, as at Codex's top left:
     no box, no pill. The current item is underlined.
   - **Focused instruments (重点仪表):** only the object being worked on and states that need handling,
     such as the centred tool palette or the "3 options ready" card. At most one group per surface.
   - **Status text (状态文字):** saved, what is selected, how to work it. Faint small words on the status
     line, never a button.
2. **Six rows.** These L numbers name rows on screen, not the construction layers L0–L5 in
   [the architecture overview](../architecture/overview.md).

   | Row | Holds | Form |
   |---|---|---|
   | L0 | Window bar | `← → ⧉ 文件 编辑 视图 帮助` |
   | L1 | Hub navigation | projects and conversations left; surfaces and tools right |
   | L2 | Surface bar | text menus, 44 px |
   | L3 | Canvas | one centred group of tools |
   | L4 | Inspector | the selection's details and versions, on demand |
   | L5 | Status line | status text, 28 px |

3. **Stage position:** normally faint words at the right end of the L2 bar ("S2 · Layout — Current ·
   2 running"); a notice card only when options are ready. That card and read-only viewing (back to
   Current, Continue from here) stay boxed instruments because they need handling (#344).
4. **Sorting** (#349–#354): modes and commands are words; a state that needs handling (notice, conflict,
   failure, read-only view) keeps a boxed instrument in a fixed row above the content, taking no room
   otherwise; explanatory words go on the status line or at the bar's right end; one group of tools per
   surface; the bar does not repeat the surface's name, which the rail already shows, and a product
   name appears once (#349, #351).
5. **Narrow widths: wrap, never scroll.** Menus that do not fit wrap onto another row, so every command
   stays visible and no panel is clipped (#348). Inspectors dock at the right and the canvas makes room;
   they cover the canvas only when the surface itself, not the window, is under 560 px (#345, #353).
6. **Keyboard:** every menu works with the arrow keys, Enter and Esc; the Hub row is a menubar (#338).
7. **Checks:** screenshots in Chinese and English, light and dark, and all four interface styles;
   keyboard use; the affected browser tests.
8. **Interface rules (2026-10-24, GH-300).** Every interface change follows this one list. A new
   article or reference is folded into it here rather than written up as a separate note. Sources:
   two Xiaohongshu notes (client polish; design terms for layout), Nutlope/hallmark (MIT, ideas
   only, no files copied) and the loading tiers.

   **Layout.** Name the layout before drawing it (hero, bento grid, board, timeline, map overview),
   so the person building it knows which part leads.
   - Conclusion first, details after. A page that summarises leads with one conclusion ("2 项需要关注"),
     puts two or three secondary figures beside it in smaller tiles (what runs and how long it has
     left, what just finished), and lists the details below. Never a wall of equal cards.
   - A secondary tile shows only when it has something to say: nothing running, no tile.
   - Work screens fill the window and keep density; the landing-page rules (word budget, first-screen
     headline, capped width) do not apply.

   **Surfaces and colour.**
   - Layers are told apart by a 1–3% step of tone, not by an outline. No card inside a card.
   - Every colour and font comes from a `:root` token; light and dark swap tokens only.
   - Dark is re-derived, not inverted: ground at 12–18% lightness with a slight tint, text at 92–96%,
     higher layers lighter, no drop shadows.
   - The accent marks only the main action, the current selection and states. One filled main button
     per screen.

   **Type and figures.**
   - In dark, titles sit one weight lighter. Noto Sans SC and Microsoft YaHei draw 600 like 700, so
     weights are chosen from 400, 500 and 700 and the step down from bold is 500.
   - Figures that change in place use tabular numerals.
   - No all-caps tracked labels, no small label above a heading, no invented numbers.

   **Motion.**
   - Dragging and pressing land at once; feedback (an answer, an arriving card) springs a little.
   - Motion only shows a change of state, 0.14–0.42 s, on opacity and transform only; never
     `transition: all`, never width, height or position.
   - A reply being written washes in block by block instead of appearing a character at a time.
   - Under `prefers-reduced-motion` the marks still appear after their delay, without fading or
     sliding, and bars jump instead of gliding.

   **Waiting.** A wait is shown by how long it lasts:

   | Wait | Shown |
   |---|---|
   | under 1 s | nothing: a loading mark appears only after a second (`.boot`, `.chat-thinking`; a reading or loading line takes the class `wait-late` from `base.css`: its words are in the page at once, it is shown after a second), though input stays blocked meanwhile |
   | first load of a whole page | a skeleton in the final layout (`LoadingOverlay`), not a spinner in the middle |
   | one module | a spinner or reading line in that module only |
   | over 10 s (drawing, export, model sync, a candidate's job) | a measured bar and the time left, from this project's earlier jobs in the same lane (the median `wallTimeS`); with no history, the time so far; past the estimate, 「比平常久 · 已 N 秒」 |
   | upload, export | progress or the time so far, never a skeleton |

   **States.**
   - Zero, not filled in, and not read yet are three different words: 「现在没有要你处理的」 only after
     the queue was read, 「正在读取…」 before; 「没有正在进行或排队的工作」 only after the Hub's state arrived.
   - Every data region has four looks: reading, empty (what is missing and one action to fill it),
     failed (what broke and what to do), full.

   **Interaction.**
   - Undo instead of a confirmation dialog wherever the change can be undone.
   - Everything clickable shows a keyboard focus outline at once.
   - At most two button sizes; an input matches the height of the button beside it; disabled is half
     opacity with the native `disabled` attribute.
   - The page never scrolls sideways.

   **Checks.** Screenshots in light and dark, with the font named (Noto Sans SC on the build
   workstation and the laptop); for a wait, one operation at 0.5 s, 3 s and 15 s.

**Why:** surfaces drew the same kind of information differently. Board stacked three layers above its
content (Stage capsule, Board/Layout segmented buttons, title field with buttons); Modeling crowded
Versions, "View in Design tree", the selection and source tags into its bottom-left corner. Its centred
tool palette, meanwhile, read at a glance.

**Do not:** stack a second bar on a surface or give the Stage position its own row; draw a mode, command
or menu as a box or pill; make status words a button; add a second tool group; add `overflow` to
`.surface-bar__items`.

## Building blocks

Paths are under `apps/monkeyhub/web/`. Shared chrome: `src/features/chrome/SurfaceChrome.tsx`
and `chrome.css`.

- `ProjectBar`: the one L2 bar over all project surfaces, rendered once by
  `src/app/ProjectWorkspace.tsx`: Board | Layout (`BoardModeSwitch.tsx`) on the left, the
  Stage position (`src/features/designTree/DesignTreeBar.tsx`) at the right. Empty, it takes
  no room.
- `SurfaceMenus({ label, active, end })`: a project surface's menus in that bar while it is on screen;
  `end` holds quiet right-end words such as save state. Without a project bar it draws a `SurfaceBar`.
- `SurfaceBar`: the bar of a page outside a project (Usage, Fabrication).
- `MenuTabs` (parallel modes, the pressed one underlined), `MenuCommand` (chosen style with
  `aria-expanded` or `aria-pressed`), `MenuSeparator`, `input.surface-title` (a name edited in place).
- `StatusLine({ end, className })`: L5; its right end hides at 640 px or narrower.

L0 is `src/HubMenu.tsx`, rendered by `src/ChatShell.tsx`. `src/surfaceBarRows.ts` hides a separator
that ends a wrapped row. A field in the bar needs `.surface-title`: the Hub's input rule (0,2,1) beats a
single class. Tests find bar menus in the visible `.project-bar`, not in `[data-project-surface=…]`.

## As built (main `44287914`)

| Surface | File | In the bar · canvas · inspector · status line |
|---|---|---|
| Hub | `src/HubMenu.tsx` | the L0 row replaced the "MonkeyHub" wordmark row (#338) |
| Modeling | `src/features/stage/Stage.tsx` | model, Versions, Design tree, Export, More; a decision row only when needed; one-row tool palette; docked Versions; selection and editing base (#345, #352) |
| Board, Layout | `src/workspaces/monkeyboard/Board.tsx`, `src/workspaces/publish/PublishWorkspace.tsx` | editable name, commands, save state; hints and selection (#348) |
| Design Tree | `src/features/designTree/DesignTreeSurface.tsx` | Canvas \| List; a column per Stage; docked details; selection and legend (#344, #353) |
| Drawings | `src/workspaces/monkeydiagram/DrawingCanvas.tsx` | menus; a row only when something needs handling; zoom buttons as the one tool group (#349) |
| Usage, Fabrication | `src/MonitorPage.tsx`, `src/FabPage.tsx` | own `SurfaceBar` and `StatusLine`; product name once (#351) |
| Model comparison | `src/workspaces/monkeyarch/viewer/ModelComparison.tsx` | `SurfaceMenus` (#432) |

**Open:** Render (#350, after #330; its title belongs to #280); the menu row merged into the desktop
title bar (#354, evaluated separately; browser mode unchanged); whether Usage's and Fabrication's
boxed cards are flattened (left to the owner in #359).

Sources: #337 (2026-09-26); PRs #338, #344, #345, #348; issues #349–#354 with PRs #369, #359, #360,
#361.

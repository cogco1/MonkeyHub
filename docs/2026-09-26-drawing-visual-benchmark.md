# Drawing consumer of visual observation: the A–D benchmark (2026-09-26)

**Issue:** [#303](https://github.com/cogco1/MonkeyHub/issues/303), lane `GH-303/drawing-benchmark` (slice 303-S3).
**Base:** `b55d1484` (batch H2 and the batch I claim).
**Scope:** measures the Drawing consumer of the visual observation channel in the owner's order: exact drawing checks, then the representation recipe, then one visual review, then a typed representation repair, then at most one re-check. The change is a benchmark mode of an existing tool and this document. No product code changed; the module registry now declares the drawing SVG owner the tool imports.
**Method:** `tools/benchmark_visual_observation.py --drawing` against one project runtime. Smoke run only: a synthetic fixture project, three cut plans, four looks. Images, raw results, Monitor rows and logs stay outside the repository ("local evidence").

**Path abbreviations**

| Short | Path |
| --- | --- |
| `TOOL` | `tools/benchmark_visual_observation.py` |
| `API` | `apps/archflow-studio/api/archflow_studio_api` |

## 0. Summary

**What exists now**
- `TOOL --drawing` runs four arms on the same drawings: A the compiler's output, B the same drawing with the project recipe, C one look at B's page, D a typed repair from that look's findings and one more look (`arm_plan`).
- Every step goes through the runtime's own routes. Drawing revisions come from `POST /api/drawings/plans`, marked `sourceKind: agent`, so none counts toward a recipe suggestion. Looks go through `POST /api/visual-reviews`, which renders the page itself. The tool writes no file and no decision.
- Per arm it reports the remaining manual corrections (a deterministic proxy, §3.4), the looks' provider and image cost, wall clock, and for D the acceptance and open findings with and without the second look.

**Smoke run** (synthetic fixture, three plans, four looks)
- **Exact checks and the recipe** took under 1.5 s per arm and no provider call. The recipe removed the same three corrections from every plan: A leaves 4–5, B 1–2.
- **The exact checks found two things before any look.** Every plan draws 8 cut lines that one wall lays on another wall's face; the cleanup's rules do not cover this (a finding for #244). The over-dressed plan has 11 entourage objects whose strokes meet other marks.
- **Ordinary plans.** The look confirmed every criterion (7 `info` findings each), so C and D equal B. Each look cost about 19 k input tokens and 17–27 s.
- **Over-dressed plan.** The look found 3 actionable findings that B would have handed over as done: density and entourage major, balance minor. The repair (fade the lines below the cut, halve the entourage) addressed the first two.
  - The second look still saw both, now minor, and the balance knot.
  - Acceptance did not turn: the balance finding has no typed lever, so D stayed open either way.
  - But the second look reopened the two criteria the repair claimed. D reports 5 remaining corrections, not the 3 it would have reported without that look.
- **Cost:** 4 looks; 76,812 input tokens (0 cached); 2,747 output tokens (177 reasoning); 98.9 s of provider time; 63,852 image bytes.

**What the smoke suggests** (three synthetic plans and one model: indications, not evidence)
- The deterministic stages did most of the correction work, and cheaply.
- A look pays off on drawings with perceptual problems; on the others it bought a confirmation.
- The repair should read the exact facts. The entourage lever halved the entourage without knowing which objects collide. The second look then reported exactly the two collisions the exact check still listed.
- The second look's value showed as reopened criteria, not as a flip of acceptance (§5.3, §5.6).

## 1. The owner's order, as the tool runs it

The owner's comment on #303 fixes the Drawing consumer's order and asks for four arms on the same sheet. `arm_plan` (`TOOL:228`) runs these steps for every drawing:

| Step | Stage of the owner's order | What runs |
| --- | --- | --- |
| A.compile | — | `POST /api/drawings/plans` with the code's default pens named explicitly (0.35 / 0.18 mm, 2 mm hatch; `PAPER_DEFAULTS`) |
| A.checks | exact drawing checks | `GET /api/drawings/plans/vector` and `POST /api/drawings/plans/status`, then `drawing_checks` on the drawn SVG |
| B.compile | — | the same request with no pen named, so the project recipe fills them (explicit > previous revision > project recipe > default) |
| B.checks | exact drawing checks | as A |
| B.recipe | recipe | `recipe_deviations`: the recipe keys A draws otherwise than B |
| C.review | visual review | one `first_bundle` look at B's page (`domain: drawing`, `page-0`), only while B's page is exactly bound |
| D.repair | typed repair | `repair_plan` over C's actionable findings, then `POST /api/drawings/plans` with `previousRevisionRef` = B's revision |
| D.checks | exact drawing checks | as A, on the repaired page |
| D.recheck | re-check | one `after_repair` look at the repaired page, naming the addressed findings; the loop's second and last look |

- A page's exact checks always run before anything looks at it. On the path to D (B, C, D) the stages come in the owner's order; a test asserts it.
- C runs only when B's page passes the source-binding check (`status: current`, nothing unresolved). A cut plan is reviewed only while it is current ([visual observation §7](2026-09-25-visual-observation.md)).
- D repairs only when an actionable finding has a typed lever. It re-checks only after a repair: the allowance (`spatial_formal`, two looks) would refuse an `after_repair` look without one.

## 2. Arms

| Arm | Page | Pens | Looks |
| --- | --- | --- | --- |
| A | its own revision (`bench-<tag>-<name>-a`) | code defaults, named in the request | none |
| B | its own revision (`bench-<tag>-<name>-b`) | the project recipe | none |
| C | B's page | B's | one (`first_bundle`) |
| D | B's revision repaired, or B's page when nothing was repaired | B's, plus the repair's explicit values | C's look plus one `after_repair` |

- **Same drawings.** A spec entry's `plan` is the part of the drawing request every arm shares: one source (`sourceStageRef`, `modelSource` or `sourceAsset`), the frame (cut height, bottom, scale, crop) and the content (dimensions, entourage). The spec may not set what an arm sets itself: pens, hatch and fade rules, identity, continuation and provenance (`ARM_FIELDS`).
- **What is written.** A, B and D are new drawing revisions of the benchmark project. They are representation only, so the Design HEAD never moves. They carry `sourceKind: agent` and a `reason` naming the arm. No decision is written. Run the tool on a copy of a project.
- **Only cut plans.** They are the drawing kind with a project recipe, a typed repair and a status the checks can read. A section perspective has none of the three yet.

## 3. Metrics

### 3.1 Exact drawing checks (`drawing_checks`, `TOOL:462`)

Each check reads what the page draws: the retained SVG, its status and its cleanup report. A count greater than zero fails the check.

| Check | Counts |
| --- | --- |
| `duplicates` | Lines drawn over other lines, three ways. (1) A projected line lying on the cut, found by re-running the cleanup owner's own rules (`clean_drawing`) on the drawn lines. (2) A line drawn twice. (3) A straight line of one object lying end to end, within the cleanup tolerance, on lines of another object in its group (`_on_other_objects`). Two solids that meet both draw their shared face; the cleanup keeps each object's line. |
| `hidden_edges` | Hidden lines the cleanup's rules would still drop (under visible lines), and any hidden line on a page drawn without hidden lines. |
| `micro_segments` | Strokes the cleanup's rules would still drop as shorter than its tolerance (0.05 mm on the sheet). |
| `out_of_bounds` | Marks that reach past the sheet, and entourage the status reports `outside-view`. |
| `missing_hatch` | Cut objects (objects with a line in the section group) with neither hatch strokes nor poché. |
| `source_binding` | What the status cannot vouch for: a page that is not `current` or whose binding changed; hidden objects, dimensions or entourage anchors that no longer resolve; a projected mark naming no object, or an object the exact source does not hold. |
| `collisions` | Entourage objects whose strokes meet a cut line, a dimension or another entourage object. |

The cleanup rules run at the tolerance the page's own cleanup report names. A page drawn before cleanup existed has no report, and those three checks read `None` (not measured). Every check is recorded with what it found (objects, failures), not only its count.

### 3.2 Recipe deviations (`recipe_deviations`, `TOOL:562`)

- These are the project-recipe keys (`cutLineMm`, `visibleLineMm`, `hatchSpacingMm`) that A draws otherwise than B.
- B is a new drawing whose request names no pen. Each key therefore holds the project recipe's value, or the code default where the recipe is silent. No precedence logic is repeated in the tool.
- Only A is scored for deviations. A repair's explicit pen answers a finding (explicit beats recipe), so it is not a deviation.
- Without an active project recipe, B draws what A draws; the result's `projectRecipe` is then empty.

### 3.3 Visual findings

- A finding is **actionable** at `minor` or `major` severity. An `info` finding is a fact or a criterion that visibly holds.
- A finding that names a `preserve:*` condition is **escalated**: it goes to the architect and is never repaired, as the Hub's `visual_review` marks it.
- **Open findings** are the actionable findings of the arm's last look. For D without a second look, they are the first look's actionable findings less those the repair addressed.

### 3.4 Remaining manual corrections (the proxy)

One correction per instruction a person would still give on the arm's page:

```text
remaining = (exact checks that fail)          one each: "remove the double lines", however many lines
          + (recipe keys drawn otherwise)     one each: "the hatch is too dense again"
          + (open actionable findings)        one each
```

- A check that was not measured counts nothing.
- A and B take no look, so their number is a lower bound. C's larger number is what its look revealed, not what it caused; B reports it as `laterObservedFindings`.
- The count ignores size. A check that finds 4 colliding objects counts the same as one that finds 11, and a minor finding the same as a major one. The raw counts and severities are reported beside it (`checks`, `openBySeverity`).

### 3.5 Cost

- The provider and image cost of an arm's looks, summed from each answer's `usage`: provider calls, image inputs and bytes, input, cached, output and reasoning tokens, and provider milliseconds. C carries one look; D carries C's and its own.
- A refused look costs nothing; a failed provider call keeps the usage it reported.
- The runtime's Monitor rows give the same numbers independently: one `visual_observation` span per look with its `model_request` row nested.

### 3.6 Wall clock

- The sum of the arm's own steps, measured around each runtime request: compile, page and status reads with the local checks, and looks. C adds its look to B, and D adds its steps to C.
- The first compile of a run also pays the runtime's warm-up.

### 3.7 Acceptance and the second look

- A **visual acceptance** is only what a look can decide: no actionable finding known to be open. The exact checks are the same before and after a look, so they are reported beside it, not folded in.
- For D the tool reports both sides:
  - **Without the second look,** D would hand over the repair believing every finding it addressed was resolved: `visuallyAcceptedWithoutSecondLook`, `openWithoutSecondLook`, `remainingCorrectionsWithoutSecondLook`.
  - **With it,** the re-check's actionable findings are what stays open.
- `secondLookChanged` says whether the acceptance differs.
- `reopenedCriteria` names the criteria the repair addressed that the re-check still finds. `newCriteria` names those the re-check finds and the first look did not, such as a repair that made something else worse.

## 4. The look and the repair

**Criteria** (`DRAWING_CRITERIA`, `TOOL:194`). There is one criterion per perceptual question the owner lists for a drawing. Each is worded one way, so a minor or major finding on it says which way to go. Each has at most one typed paper-space lever.

| Criterion | Wording | Lever (one step) |
| --- | --- | --- |
| `hierarchy` | The cut walls read clearly heavier than every line below the cut plane. | cut pen × 1.4, at most 2 mm |
| `hatch` | The section hatch stays quieter than the cut outline; it does not read as dense or dominant. | hatch spacing × 1.5, at most 20 mm |
| `density` | Lines below the cut stay in the background; the sheet does not feel busy. | `beyond.fade` + 0.4, at most 0.8 |
| `entourage` | People and trees are few enough that they do not crowd or hide the plan. | delete every second entourage object by id |
| `balance` | No local area carries awkward visual weight, such as a dark clump or a stray mark. | none: the finding stays for the architect |

**The request**
- The task asks how the plan reads as a printed drawing. It says the known facts are exact checks already made.
- Two preserve conditions: the drawn geometry is an exact projection and stays; scale, crop and dimensions stay.
- **Known facts** (`known_facts`, `TOOL:578`), at most 8 of at most 120 characters: scale and cut height; pens (and fade); hatch spacing and how many cut objects are hatched; the exact line checks; what the cleanup already took; the entourage count and how many objects meet other marks; the source binding. The observer is told not to ask about them again.
- **Allowance:** `spatial_formal`, two looks, held by the tool and sent back with each look as the route requires.

**The repair** (`repair_plan`, `TOOL:681`)
- It stands in for the Agent's choice (303-S2), fixed so that arm D is reproducible.
- Escalated findings are not repaired. Every other actionable finding turns the lever of each criterion it names, once per lever however many findings name it.
- A finding is addressed when one of its levers changed something. A criterion without a lever, or a lever at its bound, addresses nothing.
- The repair is one `POST /api/drawings/plans` that continues B's revision (`previousRevisionRef`). It sends only the source and the changed values, `beyond`, or `dressingOperations`; everything else is kept from B's revision.

## 5. Smoke run

### 5.1 Fixture (synthetic)

- **Project.** The Studio API suite's own fixture project (`support.make_project`), with the cut-plan room of `test_drawing_plans.room_edit`: four 0.3 m walls, 3 m high, around a 4 m square, and one 2 m door. Inside it sits the fixture's low base block, whose edge is the one line below the cut. It was executed with OCCT and accepted as the first Stage, in a scratch folder outside the repository; no real project was touched.
- **Recipe.** Two recipe decisions were confirmed on one evidence page (the benchmark operator standing in for a person): 3 mm hatch spacing (`drawing:hatch`), and a 0.5 mm cut pen with a 0.13 mm pen below the cut (`drawing:lineweight`). Both are `strong_preference` for the project.
- **Drawings**

| Plan | Scale | Cut | Content | Page frame |
| --- | --- | --- | --- | --- |
| `plan-1200` | 1:50 | 1.2 m | door dimension; 6 people, 2 trees | 697 × 697 px, 17,185 B |
| `plan-2300` | 1:100 | 2.3 m | 1 person | 437 × 437 px, 3,205 B |
| `plan-crowded` | 1:50 | 1.2 m | door dimension; 16 people, 4 trees | 697 × 697 px, 25,039 B (18,423 B after the repair) |

The first two plans produced no actionable finding, so arm D had nothing to repair. `plan-crowded` was then added, deliberately over-dressed, to exercise D's repair and re-check live; it reflects the owner's own "fewer people" correction on #244. It is a test of the pipeline, not a sample of real work.

### 5.2 Runtime and provider

- One project runtime from this branch (`python -m archflow_studio_api.main`) at low priority, with `ARCHFLOW_STUDIO_CAD_EXPORT=occt`, `ARCHFLOW_STUDIO_INTENT_PROVIDER=codex` and `ARCHFLOW_STUDIO_INTENT_TIMEOUT_S=300`. Monitor rows were kept locally.
- Provider: Codex CLI 0.153.4 through the runtime's configured transport, reported as `codex-cli-default`.
- Two tool runs (tags `smoke1` and `smoke2`), four looks in all. Before them, a dry run in process with the default deterministic provider checked every step (each look refused with `VISUAL_PROVIDER_UNAVAILABLE`, no provider call).
- The numbers below are the final `arm_results` over the recorded state of both runs. The first run was recorded before D's second-look fields (§3.7) existed; every other number is the same as printed. The looks were told the hatch pen width too, which the tool no longer restates.

### 5.3 Results

Remaining corrections for A and B are lower bounds (no look). Wall clock is cumulative.

| Plan | Arm | Remaining corrections | Failing exact checks | Recipe keys drawn otherwise | Open actionable findings | Visually accepted | Looks | Input / cached / output (reasoning) tokens | Provider s | Wall s |
| --- | --- | ---: | --- | ---: | --- | --- | ---: | --- | ---: | ---: |
| `plan-1200` | A | ≥ 4 | duplicates (8) | 3 | not looked | — | 0 | — | — | 1.4 |
| | B | ≥ 1 | duplicates (8) | 0 | not looked | — | 0 | — | — | 0.8 |
| | C | 1 | duplicates (8) | 0 | 0 | yes | 1 | 19,740 / 0 / 767 (78) | 27.3 | 28.2 |
| | D | 1 | duplicates (8) | 0 | 0 (nothing to repair) | yes | 1 | = C | 27.3 | 28.2 |
| `plan-2300` | A | ≥ 4 | duplicates (8) | 3 | not looked | — | 0 | — | — | 0.6 |
| | B | ≥ 1 | duplicates (8) | 0 | not looked | — | 0 | — | — | 0.7 |
| | C | 1 | duplicates (8) | 0 | 0 | yes | 1 | 18,739 / 0 / 423 (0) | 16.8 | 17.6 |
| | D | 1 | duplicates (8) | 0 | 0 (nothing to repair) | yes | 1 | = C | 16.8 | 17.6 |
| `plan-crowded` | A | ≥ 5 | duplicates (8), collisions (11) | 3 | not looked | — | 0 | — | — | 0.8 |
| | B | ≥ 2 | duplicates (8), collisions (11) | 0 | not looked | — | 0 | — | — | 0.8 |
| | C | 5 | duplicates (8), collisions (11) | 0 | 3 (2 major, 1 minor) | no | 1 | 19,082 / 0 / 896 (28) | 31.4 | 32.3 |
| | D | 5 (3 without the second look) | duplicates (8), collisions (4) | 0 | 3 (3 minor) | no (also without the second look) | 2 | 38,333 / 0 / 1,557 (99) | 54.8 | 56.6 |

- **Second look** (`plan-crowded`, D). The repair addressed density and entourage and left balance. The re-check reopened both addressed criteria (now minor) and found no new one. `secondLookChanged` is false: the balance finding kept D open with or without the look. The look still raised D's remaining corrections from 3 to 5.
- **B's claim.** On `plan-crowded`, the look at B's own page found 3 actionable findings that B would have handed over as done. On the other two plans it found none.
- **Deterministic time.** A compile took 0.2–0.4 s (0.95 s for the run's first), and the exact checks 0.4–0.5 s. The route's own page rendering and binding added under 0.1 s per look over the provider time.

### 5.4 Looks

| Plan | Look | Frame | Input / cached / output (reasoning) | Provider s | Findings (actionable) |
| --- | --- | --- | --- | ---: | --- |
| `plan-1200` | C, `first_bundle` | 697 px, 17,185 B | 19,740 / 0 / 767 (78) | 27.3 | 7 (0) |
| `plan-2300` | C, `first_bundle` | 437 px, 3,205 B | 18,739 / 0 / 423 (0) | 16.8 | 7 (0) |
| `plan-crowded` | C, `first_bundle` | 697 px, 25,039 B | 19,082 / 0 / 896 (28) | 31.4 | 7 (3) |
| `plan-crowded` | D, `after_repair` | 697 px, 18,423 B | 19,251 / 0 / 661 (71) | 23.4 | 7 (3) |
| **Total** | 4 looks | 63,852 B | 76,812 / 0 / 2,747 (177) | 98.9 | 28 (6) |

- Every answer was valid against the schema on the first attempt. The runtime's Monitor recorded each look as a `visual_observation` span (scope `visual-review:drawing`) with its `model_request` row and the same tokens.
- Each saved frame is byte-identical to what its look was sent: the SHA-256 of a one-page 1600 px export equals the answer's `frameSha256`.
- The input is nearly constant (18.7–19.7 k tokens) whatever the image size (3–25 kB). This matches the V0 measurement that about 17.3 k of each look is the Codex CLI's fixed agent prompt ([visual observation §6.2](2026-09-25-visual-observation.md)).

### 5.5 What the looks said (paraphrased)

- **`plan-1200`, `plan-2300`.** Every criterion held, at confidence 0.95–0.99: the cut reads heaviest, the hatch stays light, the interior keeps white space, the entourage is dispersed or small, and the weight is even.
  - The observer used the two preserve refs only for `info` notes, such as that one image cannot show the geometry unchanged after a repair.
  - No finding restated an exact fact (double lines, hatch presence, bounds), except `info` notes under the preserve conditions that the plan fits its page.
- **`plan-crowded`, first look.**
  - Density (major): the interior lines are still black, and repeated figures and tree contours make the interior busy.
  - Entourage (major): people fill the gaps, and trees overlap figures.
  - Balance (minor): the upper-left tree and its neighbours form a knot of crossing lines.
  - Hierarchy and hatch held.
- **`plan-crowded`, after the repair.**
  - Density (minor): the tree contours and figures still attract attention.
  - Entourage (minor): a figure overlaps the upper-left tree, and another meets the central tree.
  - Balance (minor): the same knot, and the central tree weighs down the left half.
  - The two overlaps named are exactly the two pairs the exact collision check still lists after the repair. The known facts had told the observer that 4 objects meet other marks, and it re-reported them.

### 5.6 Observations

1. **The deterministic stages did the cheap work.**
   - The cleanup had already removed 16–20 cut edges drawn twice per plan.
   - The recipe settled 3 corrections per plan.
   - The exact checks named 8 double-drawn lines per plan and 11 colliding entourage objects on the over-dressed plan.
   - Each took under a second, and none needed a look.
2. **Cleanup gap (#244).** When two solids meet, both draw their shared face; each wall's end lies on its neighbour's face. The cleanup's rules compare lines of different kinds (a projected line against the cut, a hidden line against a visible one) and join one object's own pieces; two objects' lines of the same kind are never compared. So these 8 lines per plan survive. A rule "drop a cut line lying on another object's cut line" would remove them.
3. **Corner joins are not checked.** Every plan draws one wall's cut boundary through its neighbour's cut at the four corners. Whether that is a defect depends on the materials (a join between like materials is cleaned, one between unlike materials is kept), so no exact check covers it. The observer called the corners "compact". A join rule with material semantics belongs to the cleanup owner (#244).
4. **The repair should read the exact facts.** The entourage lever halves the entourage by id, not knowing which objects collide. It left two colliding pairs, and the second look reported exactly those. A lever that first removes or moves the objects the exact collision check names would do the same deterministically, before a look. This is a candidate for the Agent's repair policy (303-S2) or a deterministic entourage rule in the Drawing owner.
5. **The second look mattered without flipping acceptance.** A finding without a lever kept D open either way. The second look still showed that the repair did not resolve the two findings it addressed, and it moved the correction count D would report from 3 to 5. For #268's cost signal, the second look's value showed as reopened criteria, not as an acceptance flip.
6. **Budget.** On the two ordinary plans the look cost about 19 k tokens and 17–27 s to confirm what the exact checks could not judge. On the over-dressed plan the first look was the only source of its 3 findings, and it was worth it there.

## 6. Limits

- **Validity**
  - One synthetic room, three plans, one run each, one model: no repetition and no statistics.
  - The third plan was added and dressed so that D would run.
  - No person judged a drawing. The owner's judgement of the perceptual criteria on a real section remains the open acceptance (D-303-3).
- **The proxy**
  - It counts instructions, not their size: the repair cut colliding objects from 11 to 4 and two findings from major to minor, and the count did not move.
  - A and B are lower bounds.
- **The checks**
  - `collisions` are stroke crossings: a symbol wholly inside a wall or another symbol does not count.
  - `duplicates` across objects compare straight lines only, bucketed by direction (1 degree) and offset (twice the tolerance).
  - `missing_hatch` assumes that every object with a cut line is a solid.
  - The cleanup's hidden-in-the-cut rule cannot be re-measured from the SVG, which holds no section regions. The cut plans draw no hidden lines, so nothing here depends on it.
- **The repair**
  - One fixed step per lever, with the direction the criterion's one-way wording implies. In production the Agent chooses (303-S2).
  - Material hatch rules keep their own spacing; the hatch lever changes `hatchSpacingMm` only.
- **Cost**
  - The Codex CLI's fixed prompt dominates each look's input tokens. A direct Messages provider behind the same seam would change the cost picture, not the arms.
  - The CLI reports no concrete model id and offers no seed.
- **Load.** The machine was busy and every process ran at low priority, so wall clock is an upper bound.

## 7. Full run (for the owner)

The full run is one tool invocation over a spec of real cut plans. It makes at most two looks per drawing.

1. **Copy the project.** Never point the runtime at the original:

   ```bat
   robocopy "<project dir>" "D:\MONKEYHUB_DEV\temp\drawing-benchmark\full\<project id>" /E
   ```

2. **Start one runtime** in a console of its own, at low priority. Start it from a neutral directory: `python -m` puts the current directory first on `sys.path`, which picks up another checkout's packages.

   ```bat
   cd /d D:\MONKEYHUB_DEV\temp\drawing-benchmark\full
   set PYTHONPATH=<repo>;<repo>\apps\archflow-studio\api
   set ARCHFLOW_STUDIO_CAD_EXPORT=occt
   set ARCHFLOW_STUDIO_INTENT_PROVIDER=codex
   set ARCHFLOW_STUDIO_INTENT_TIMEOUT_S=300
   set MONKEYMONITOR_DATA_DIR=D:\MONKEYHUB_DEV\temp\drawing-benchmark\full\monitor
   start /b /low /wait python -m archflow_studio_api.main --project-dir D:\MONKEYHUB_DEV\temp\drawing-benchmark\full\<project id> --host 127.0.0.1 --port 8765
   ```

3. **Write `drawings.json`** with one entry per real cut plan. A drawing's latest revision in `GET /api/documents` gives the values:
   - `sourceStageRef` is the document's `sourceStageRef`;
   - `cutHeight` is `viewRecipe.frame.origin[2]`;
   - `bottom` is that minus `far_depth`;
   - `scaleDenominator` comes from `frame.scale`, and `cropUv` is `frame.crop_uv`;
   - `dimensions` and `dressing` are the recipe's own.

   ```json
   {"projectId": "<project id>", "drawings": [
     {"name": "ground-floor", "plan": {"sourceStageRef": "project://…", "cutHeight": 1.2, "bottom": 0,
      "scaleDenominator": 100, "cropUv": [-2, -2, 30, 20], "dimensions": [], "dressing": []}}]}
   ```

   Arm B needs an active project recipe (a recipe decision a person confirmed). Without one, B draws what A draws, and the result's `projectRecipe` is empty.

4. **Run the benchmark** in a second console:

   ```bat
   cd /d D:\MONKEYHUB_DEV\temp\drawing-benchmark\full
   set PYTHONPATH=<repo>;<repo>\apps\archflow-studio\api
   start /b /low /wait python <repo>\tools\benchmark_visual_observation.py --drawing --runtime http://127.0.0.1:8765 --spec drawings.json --tag full1 > result.json 2> progress.log
   ```

5. **Stop the runtime** (Ctrl+C in its console). Read `result.json`: its `summary` has one row per plan and arm. Judge the perceptual criteria on the B and D pages: open the benchmark project, or export the pages with `POST /api/board/export` (one page, PNG, `maxEdge` 1600, as the look saw it).

## Checks run

- `tests/test_benchmark_visual_observation.py` (20 tests, no runtime and no provider):
  - the arm order and spec refusals;
  - each exact check against a synthetic page, including lines on either side of vertical;
  - recipe deviations; the repair's levers, escalation and bounds; known facts within the route's bounds;
  - the metrics with and without the second look;
  - the four arms against a fake runtime: request order and bodies, the allowance carried between looks, no repair or second look without an actionable finding, no look at an unbound page, and a refused look that ends the loop;
  - argument handling for both modes.
- `python tools/archcheck.py` and `python tools/archcheck.py --changed origin/main`.
- The in-process dry run and the smoke run above.

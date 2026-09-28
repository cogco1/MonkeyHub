import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { messagesEn } from "../../src/i18n/messages.en.ts";
import { messagesZhCN } from "../../src/i18n/messages.zh-CN.ts";

type Model = typeof import("../src/features/designTree/model.ts");
type Layout = typeof import("../src/features/designTree/layout.ts");
type Scene = typeof import("../src/features/designTree/scene.ts");
type Fixture = typeof import("../src/features/designTree/fixture.ts");
type Hit = typeof import("../src/features/canvas/sceneHit.ts");
type Words = typeof import("../src/features/designTree/words.ts");
type Undo = typeof import("../src/features/designTree/continueUndo.ts");
type Previews = typeof import("../src/features/designTree/previews.ts");

async function harness(t: TestContext) {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  return {
    ...await vite.ssrLoadModule("/src/features/designTree/model.ts") as Model,
    ...await vite.ssrLoadModule("/src/features/designTree/layout.ts") as Layout,
    ...await vite.ssrLoadModule("/src/features/designTree/scene.ts") as Scene,
    ...await vite.ssrLoadModule("/src/features/designTree/fixture.ts") as Fixture,
    ...await vite.ssrLoadModule("/src/features/canvas/sceneHit.ts") as Hit,
    ...await vite.ssrLoadModule("/src/features/designTree/words.ts") as Words,
    ...await vite.ssrLoadModule("/src/features/designTree/continueUndo.ts") as Undo,
    ...await vite.ssrLoadModule("/src/features/designTree/previews.ts") as Previews,
  };
}

/** The catalogs' own copy, filled the way the app fills it. */
const translator = (catalog: Readonly<Record<string, string>>) => (key: string, parameters: Readonly<Record<string, string | number>> = {}) =>
  catalog[key]!.replace(/\{(\w+)\}/g, (placeholder, name: string) => name in parameters ? String(parameters[name]) : placeholder);

type Api = Awaited<ReturnType<typeof harness>>;
type Tree = ReturnType<Api["buildGrowthTree"]>;
type Drawing = ReturnType<Api["layoutGrowthTree"]>;

const sourceOf = (fixture: ReturnType<Api["createDesignTreeFixture"]>) => ({
  projectId: fixture.state.head ? "riverside-library" : "", history: fixture.designHistory(),
  workingSource: fixture.workingSource(), worktrees: fixture.worktrees(),
});

/** The #284 layout promises: one left-to-right trunk, N-1 twigs per Study, no crossings, no overlaps. */
function planarProblems(tree: Tree, drawing: Drawing): string[] {
  const problems: string[] = [];
  const points = drawing.trunk;
  for (let index = 1; index < points.length; index += 1) {
    if (!(points[index][0] > points[index - 1][0]) || points[index][1] !== points[0][1]) problems.push(`trunk turns back at ${index}`);
  }
  if (points.length !== tree.trunk.length) problems.push(`${points.length} trunk points for ${tree.trunk.length} trunk nodes`);
  if (tree.trunk.at(-1) !== "current") problems.push("the trunk does not end at Current");
  const onTrunk = new Set(tree.trunk);
  for (const study of tree.studies.values()) {
    const parent = tree.nodes.get(study.members[0])?.parent;
    if (!parent || !onTrunk.has(parent)) continue;
    const chosen = study.members.filter((id) => onTrunk.has(id)).length;
    const twigs = [...drawing.nodes.values()].filter((node) => node.role === "twig" && tree.nodes.get(node.id)!.studyId === study.id).length;
    if (twigs !== study.members.length - chosen) problems.push(`${study.id}: ${twigs} twigs for ${study.members.length} options, ${chosen} chosen`);
  }
  const segments: [readonly [number, number], readonly [number, number]][] = [];
  const add = (line: readonly (readonly [number, number])[]) => { for (let index = 1; index < line.length; index += 1) segments.push([line[index - 1], line[index]]); };
  add(points);
  for (const edge of drawing.edges) add(edge.points);
  const inside = (value: number, a: number, b: number) => value > Math.min(a, b) + 1e-6 && value < Math.max(a, b) - 1e-6;
  const shared = (a1: number, a2: number, b1: number, b2: number) => Math.min(Math.max(a1, a2), Math.max(b1, b2)) - Math.max(Math.min(a1, a2), Math.min(b1, b2));
  let crossings = 0;
  for (let i = 0; i < segments.length; i += 1) {
    for (let j = i + 1; j < segments.length; j += 1) {
      const [a, b] = [segments[i], segments[j]];
      const aH = a[0][1] === a[1][1], bH = b[0][1] === b[1][1];
      if (aH !== bH) {
        const [h, v] = aH ? [a, b] : [b, a];
        if (inside(v[0][0], h[0][0], h[1][0]) && inside(h[0][1], v[0][1], v[1][1])) crossings += 1;
      } else if (aH && a[0][1] === b[0][1] && shared(a[0][0], a[1][0], b[0][0], b[1][0]) > 1e-6) crossings += 1;
      else if (!aH && a[0][0] === b[0][0] && shared(a[0][1], a[1][1], b[0][1], b[1][1]) > 1e-6) crossings += 1;
    }
  }
  if (crossings) problems.push(`${crossings} edge crossing(s)`);
  const boxes = [...drawing.nodes.values()].map((node) => node.footprint);
  let overlaps = 0;
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      const [p, q] = [boxes[i], boxes[j]];
      if (p.x < q.x + q.width - 1e-6 && q.x < p.x + p.width - 1e-6 && p.y < q.y + q.height - 1e-6 && q.y < p.y + p.height - 1e-6) overlaps += 1;
    }
  }
  if (overlaps) problems.push(`${overlaps} overlapping footprint(s)`);
  // Every edge ends on the card it leads to.
  for (const edge of drawing.edges) {
    const target = drawing.nodes.get(edge.to)!;
    const [x, y] = edge.points.at(-1)!;
    if (Math.abs(x - target.card.x) > 1e-6 || Math.abs(y - target.y) > 1e-6) problems.push(`edge to ${edge.to} misses its card`);
  }
  // #353: each Stage on the trunk heads a column that holds what led to it, and stands at its right end; what
  // grows past the last Stage, Current among it, stands in a trailing column. The columns meet edge to edge, left
  // to right, and every node lies inside its own, so a header names everything under it.
  const isStage = (id: string) => tree.nodes.get(id)!.kind === "stage";
  const tip = tree.trunk.at(-1)!;
  const trailing = !isStage(tip) || (tree.children.get(tip) ?? []).length > 0;
  const heads = [...tree.trunk.filter(isStage), ...(trailing ? [null] : [])];
  if (JSON.stringify(drawing.columns.map((column) => column.node)) !== JSON.stringify(heads)) problems.push("the columns are not the trunk's Stages");
  drawing.columns.forEach((column, index) => {
    const next = drawing.columns[index + 1];
    if (!(column.width > 0) || (next && Math.abs(column.x + column.width - next.x) > 1e-6)) problems.push(`column ${index} does not meet the next`);
  });
  const columnOf = (id: string) => drawing.nodes.get(id)!.column;
  // A trunk node stands in the column of the next Stage it leads to: as many columns in as Stages come before it.
  tree.trunk.forEach((id, index) => {
    if (columnOf(id) !== tree.trunk.slice(0, index).filter(isStage).length) problems.push(`${id} stands in the wrong column`);
  });
  // Off the trunk, a node stands with what it grew towards: past a Stage it grew from, beside any other trunk node.
  for (const id of drawing.nodes.keys()) {
    if (onTrunk.has(id)) continue;
    let anchor = tree.nodes.get(id)!.parent;
    while (anchor !== null && !onTrunk.has(anchor)) anchor = tree.nodes.get(anchor)!.parent;
    if (anchor === null || columnOf(id) !== columnOf(anchor) + (isStage(anchor) ? 1 : 0)) problems.push(`${id} stands in the wrong column`);
  }
  for (const node of drawing.nodes.values()) {
    const column = drawing.columns[node.column];
    const box = node.footprint;
    if (!column || box.x < column.x || box.x + box.width > column.x + column.width) problems.push(`${node.id} leaves its column`);
    // A column's Stage stands at its right end, clear of everything else the column holds.
    const head = column?.node ? drawing.nodes.get(column.node)! : null;
    if (head && head.id !== node.id && box.x + box.width > head.card.x - 1e-6) problems.push(`${node.id} reaches past ${head.id}`);
  }
  const count = (kind: string) => [...tree.nodes.values()].filter((node) => node.kind === kind).length;
  if (drawing.columns.reduce((sum, column) => sum + column.options, 0) !== count("candidate") ||
    drawing.columns.reduce((sum, column) => sum + column.pending, 0) !== count("pending")) problems.push("the columns miscount their options");
  return problems;
}

test("the Riverside Library fixture grows one trunk through the chosen options", async (t) => {
  const api = await harness(t);
  const fixture = api.createDesignTreeFixture();
  const tree = api.buildGrowthTree(sourceOf(fixture));
  const stage = (run: string) => `stage:${fixture.state.stages.find((row) => row.run === run)!.ref}`;
  assert.deepEqual(tree.trunk, [stage("run-site"), "candidate:run-massing-c", stage("run-s1-massing"), "candidate:run-facade-b", stage("run-s2-layout"), "current"]);
  assert.equal(tree.nodes.get(stage("run-s2-layout"))!.stage!.number, 2);
  assert.equal(tree.nodes.get(stage("run-s2-layout"))!.stage!.name, "Layout");
  assert.deepEqual([...tree.studies.get("study-massing")!.members].map((id) => tree.nodes.get(id)!.letter), ["A", "B", "C", "D", "E"]);
  assert.equal(tree.nodes.get("current")!.parent, stage("run-s2-layout"), "Current is exactly S2");
  assert.equal(tree.nodes.get("current")!.current!.editsAfter, 0);
  assert.equal(tree.nodes.get("candidate:run-facade-a2")!.parent, "candidate:run-facade-a", "a continued option grows from its option");
  assert.equal(tree.nodes.get("pending:running:job-entrance-b")!.parent, stage("run-s2-layout"), "running work waits where it started");
  assert.deepEqual(tree.counts, { running: 1, queued: 1, interrupted: 0 });
  assert.deepEqual(tree.freshCandidates, ["candidate:run-entrance-a"]);
  assert.ok(tree.continued.has("candidate:run-massing-c") && tree.continued.has("candidate:run-facade-b"));
  assert.equal(tree.accept.allowed, false);
  assert.equal(tree.accept.block, "already-stage");
  // The runtime lists each Stage's own run as a legacy "stage" Candidate; it is drawn as that Stage, never as a twig.
  assert.equal(fixture.designHistory().candidates.filter((row) => row.legacy === "stage").length, 3);
  assert.deepEqual(["run-site", "run-s1-massing", "run-s2-layout"].filter((run) => tree.nodes.has(`candidate:${run}`)), []);
  assert.deepEqual(tree.nodes.get("candidate:run-facade-c")!.candidate!.blockedBy, ["daylight:reading-room"]);
  const drawing = api.layoutGrowthTree(tree);
  assert.deepEqual(planarProblems(tree, drawing), []);
  assert.equal(drawing.nodes.get("candidate:run-facade-a2")!.role, "option", "an option continued from a twig is a muted option of that twig");
  assert.equal(drawing.nodes.get("candidate:run-facade-a2")!.muted, true);
  const massing = drawing.forks.find((fork) => fork.node === stage("run-site"))!;
  assert.deepEqual([massing.options, massing.continued, massing.pending], [5, 1, 0]);
  const s2 = drawing.forks.find((fork) => fork.node === stage("run-s2-layout"))!;
  assert.deepEqual([s2.options, s2.continued, s2.pending], [1, 0, 2]);
});

test("Continue from an older twig re-roots the trunk and keeps the abandoned future", async (t) => {
  const api = await harness(t);
  const fixture = api.createDesignTreeFixture();
  fixture.selectWorkingDraft({ projectId: "riverside-library", runId: "run-massing-d", baseRevisionSha256: fixture.workingDraft().revisionSha256 ?? null });
  const tree = api.buildGrowthTree(sourceOf(fixture));
  const s0 = `stage:${fixture.state.stages[0].ref}`, s2 = `stage:${fixture.state.stages[2].ref}`;
  assert.deepEqual(tree.trunk, [s0, "candidate:run-massing-d", "current"]);
  assert.ok(tree.nodes.has(s2), "the Stages left behind stay in the tree");
  assert.equal(tree.accept.block, "older-stage", "a Stage can only follow the line's newest Stage");
  const drawing = api.layoutGrowthTree(tree);
  assert.deepEqual(planarProblems(tree, drawing), []);
  assert.equal(drawing.nodes.get(s2)!.muted, true);
  assert.equal(drawing.nodes.get(s2)!.role, "branch");
  assert.equal(drawing.nodes.get("candidate:run-massing-c")!.role, "twig");
  // The twigs off S0 are the four options not taken.
  assert.equal([...drawing.nodes.values()].filter((node) => node.role === "twig" && tree.nodes.get(node.id)!.studyId === "study-massing").length, 4);
});

test("Accept after Continue adds the next Stage on the trunk, from the chosen option", async (t) => {
  const api = await harness(t);
  const fixture = api.createDesignTreeFixture();
  fixture.selectWorkingDraft({ projectId: "riverside-library", runId: "run-entrance-a", baseRevisionSha256: fixture.workingDraft().revisionSha256 ?? null });
  let tree = api.buildGrowthTree(sourceOf(fixture));
  assert.equal(tree.accept.allowed, true);
  assert.equal(tree.accept.candidateId, "run-entrance-a");
  assert.equal(tree.accept.expectedHeadStageRef, fixture.state.branchHead);
  assert.equal(tree.accept.nextLabel, "S3");
  assert.equal(tree.nodes.get("current")!.parent, "candidate:run-entrance-a");
  fixture.accept(tree.accept.candidateId!, { projectId: "riverside-library", branchId: tree.accept.branchId!, expectedHeadStageRef: tree.accept.expectedHeadStageRef! });
  tree = api.buildGrowthTree(sourceOf(fixture));
  const s3 = `stage:${fixture.state.branchHead}`;
  assert.deepEqual(tree.trunk.slice(-3), ["candidate:run-entrance-a", s3, "current"]);
  assert.equal(tree.nodes.get(s3)!.stage!.number, 3);
  assert.equal(tree.accept.block, "already-stage");
  assert.deepEqual(planarProblems(tree, api.layoutGrowthTree(tree)), []);
});

test("Undo is the same Continue write, onto the exact Current the Continue replaced", async (t) => {
  const api = await harness(t);
  const project = "riverside-library";
  const fixture = api.createDesignTreeFixture();
  const before = api.buildGrowthTree(sourceOf(fixture));
  // Continue from Massing D as the tree does: read the position, write against its revision.
  const read = fixture.workingDraft();
  const request = api.continueRequest(project, { runId: "run-massing-d", branchId: null }, read);
  assert.deepEqual(request, { projectId: project, runId: "run-massing-d", baseRevisionSha256: "rev-0001", branchId: null });
  fixture.selectWorkingDraft(request);
  // The previous Current is recorded from the position that write replaced, before it.
  const undo = api.continueUndo(read, "run-massing-d");
  assert.deepEqual(undo, { runId: "run-s2-layout", branchId: "main", continued: "run-massing-d" });
  assert.deepEqual(api.buildGrowthTree(sourceOf(fixture)).trunk.map((id) => fixture.state.stages.some((stage) => id === `stage:${stage.ref}`) ? "stage" : id),
    ["stage", "candidate:run-massing-d", "current"]);
  const back = api.undoRequest(project, undo!, fixture.workingDraft());
  assert.deepEqual(back, { projectId: project, runId: "run-s2-layout", baseRevisionSha256: "rev-0002", branchId: "main" },
    "the same request shape, the previous run and its line, against the position as read now");
  fixture.selectWorkingDraft(back!);
  assert.equal(fixture.state.head, "run-s2-layout");
  assert.equal(fixture.workingSource().head!.origin, "working-position");
  assert.deepEqual(api.buildGrowthTree(sourceOf(fixture)).trunk, before.trunk, "the trunk is back where it was");
  // The previous Current's own line goes with it.
  assert.deepEqual(api.continueUndo({ projectId: project, revisionSha256: "rev-x",
    current: { runId: "run-s1-alt", branchId: "line-2", updatedAt: "2026-09-25T00:00:00Z" } }, "run-massing-d"),
  { runId: "run-s1-alt", branchId: "line-2", continued: "run-massing-d" });
});

test("no Undo rather than a guess: a line-head Current, a Continue that moved nothing, a Current that moved on", async (t) => {
  const api = await harness(t);
  const project = "riverside-library";
  // With no saved working position the line's accepted head answers for Current: there is no entry to put back.
  const fixture = api.createDesignTreeFixture();
  fixture.selectWorkingDraft({ projectId: project, runId: null, baseRevisionSha256: fixture.workingDraft().revisionSha256 ?? null });
  assert.equal(fixture.workingDraft().current, null);
  assert.equal(fixture.workingSource().head!.origin, "branch-head");
  assert.equal(api.continueUndo(fixture.workingDraft(), "run-massing-d"), null, "a Stage head is not a working draft");
  // A Continue onto the run Current already stands on changed nothing.
  const same = api.createDesignTreeFixture();
  assert.equal(api.continueUndo(same.workingDraft(), "run-s2-layout"), null);
  // Once Current moves on from the continued run, Undo puts nothing back.
  const moved = api.createDesignTreeFixture();
  const read = moved.workingDraft();
  moved.selectWorkingDraft(api.continueRequest(project, { runId: "run-massing-d", branchId: null }, read));
  const undo = api.continueUndo(read, "run-massing-d")!;
  assert.ok(api.undoRequest(project, undo, moved.workingDraft()), "right after the Continue it applies");
  moved.selectWorkingDraft({ projectId: project, runId: "run-massing-e", baseRevisionSha256: moved.workingDraft().revisionSha256 ?? null });
  assert.equal(api.undoRequest(project, undo, moved.workingDraft()), null, "another write moved Current since");
});

test("the toasts and an Undo refusal read in both languages", async (t) => {
  const api = await harness(t);
  const [en, zh] = [api.actionWords("en"), api.actionWords("zh-CN")];
  assert.equal(zh("continued", { name: "D · Terraced wedge" }), "当前已改为「D · Terraced wedge」");
  assert.equal(en("continued", { name: "D · Terraced wedge" }), "Current is now “D · Terraced wedge”");
  assert.deepEqual([zh("undo"), en("undo")], ["撤销", "Undo"]);
  assert.deepEqual([zh("accepted", { stage: "S3" }), en("accepted", { stage: "S3" })], ["已接受为 S3", "Accepted as S3"]);
  const moved = { code: api.DESIGN_TREE_UNDO_MOVED, detail: "Current moved on after the Continue; nothing was undone." } as never;
  assert.equal(api.refusalWords(translator(messagesZhCN) as never, "zh-CN", moved), "继续之后当前已有变化，未撤销。");
  const unsynced = { code: api.DESIGN_TREE_UNSYNCED, detail: "" } as never;
  assert.equal(api.refusalWords(translator(messagesEn) as never, "en", unsynced), messagesEn["designTree.outcome.unsynced"]);
});

test("options are named once: a lone option has no letter and a label that carries one gets no second", async (t) => {
  const api = await harness(t);
  const fixture = api.createDesignTreeFixture();
  fixture.state.candidates.find((row) => row.run === "run-facade-a")!.label = "A Brick piers";
  const tree = api.buildGrowthTree(sourceOf(fixture));
  const words = api.treeWords(translator(messagesEn) as never, tree);
  const name = (run: string) => words.optionName(tree.nodes.get(`candidate:${run}`)!);
  assert.equal(tree.nodes.get("candidate:run-entrance-a")!.letter, null, "the only option of its Study needs no letter");
  assert.equal(name("run-entrance-a"), "Courtyard gate on the south bar");
  assert.equal(name("run-facade-a"), "A Brick piers", "no \"A · A …\"");
  assert.equal(name("run-facade-b"), "B · Deep timber fins", "options of a Study keep their letters");
  assert.deepEqual([...tree.studies.get("study-massing")!.members].map((id) => tree.nodes.get(id)!.letter), ["A", "B", "C", "D", "E"]);
});

test("only the option that is a Stage's accepted run reads accepted; the option it grew from says so", async (t) => {
  const api = await harness(t);
  const fixture = api.createDesignTreeFixture();
  let tree = api.buildGrowthTree(sourceOf(fixture));
  for (const [catalog, grew] of [[messagesEn, "Continued · S1 · Massing grew from this"], [messagesZhCN, "已继续 · S1 · Massing 由此发展"]] as const) {
    const words = api.treeWords(translator(catalog) as never, tree);
    assert.equal(words.status(tree.nodes.get("candidate:run-massing-c")!), grew,
      "S1's accepted run is a later run: the option it grew from is not what was accepted");
  }
  fixture.selectWorkingDraft({ projectId: "riverside-library", runId: "run-entrance-a", baseRevisionSha256: fixture.workingDraft().revisionSha256 ?? null });
  tree = api.buildGrowthTree(sourceOf(fixture));
  fixture.accept(tree.accept.candidateId!, { projectId: "riverside-library", branchId: tree.accept.branchId!, expectedHeadStageRef: tree.accept.expectedHeadStageRef! });
  tree = api.buildGrowthTree(sourceOf(fixture));
  assert.equal(api.treeWords(translator(messagesEn) as never, tree).status(tree.nodes.get("candidate:run-entrance-a")!), "Continued · accepted as S3");
  assert.equal(api.treeWords(translator(messagesZhCN) as never, tree).status(tree.nodes.get("candidate:run-entrance-a")!), "已继续 · 已接受为 S3");
});

test("an admission recorded in a later review reads as retroactive; others keep their actor", async (t) => {
  const api = await harness(t);
  const source = sourceOf(api.createDesignTreeFixture());
  const retroactive = source.history.candidates!.find((row) => row.candidateId === "run-facade-c")!;
  retroactive.admittedBy = { actorId: "studio:explicit-user-action", authenticated: false, origin: "retroactive" };
  const tree = api.buildGrowthTree(source);
  const facts = (run: string) => tree.nodes.get(`candidate:${run}`)!.candidate!;
  const en = api.treeWords(translator(messagesEn) as never, tree), zh = api.treeWords(translator(messagesZhCN) as never, tree);
  assert.deepEqual([en.admitter(facts("run-facade-c")), zh.admitter(facts("run-facade-c"))], ["You (retroactive review)", "你（补录）"]);
  assert.equal(en.admitter(facts("run-massing-a")), "Arch Agent");
});

test("a turned-down result cannot become a Stage: Accept says so first, and the runtime refuses it", async (t) => {
  const api = await harness(t);
  const fixture = api.createDesignTreeFixture();
  fixture.state.runs.set("run-entrance-x", { parent: "run-s2-layout", sourceStage: fixture.state.branchHead });
  fixture.state.rejected.add("run-entrance-x");
  fixture.selectWorkingDraft({ projectId: "riverside-library", runId: "run-entrance-x", baseRevisionSha256: fixture.workingDraft().revisionSha256 ?? null });
  const tree = api.buildGrowthTree(sourceOf(fixture));
  assert.equal(tree.nodes.has("candidate:run-entrance-x"), false, "a rejected result is never a Candidate");
  assert.equal(tree.nodes.get("current")!.parent, `stage:${fixture.state.branchHead}`);
  assert.equal(tree.nodes.get("current")!.current!.editsAfter, 1);
  assert.equal(tree.accept.allowed, false);
  assert.equal(tree.accept.block, "rejected");
  assert.throws(() => fixture.accept("run-entrance-x", { projectId: "riverside-library", branchId: "main", expectedHeadStageRef: fixture.state.branchHead }),
    (error: { code?: string }) => error.code === "CANDIDATE_REJECTED");
});

test("processed Candidates are hidden by default, retain their Study identity, and leave the head and acceptance unchanged", async (t) => {
  const api = await harness(t);
  const fixture = api.createDesignTreeFixture();
  const source = sourceOf(fixture);
  const rejected = source.history.candidates!.find((row) => row.candidateId === "run-massing-a")!;
  const archived = source.history.candidates!.find((row) => row.candidateId === "run-massing-b")!;
  rejected.review = { reviewRef: "review-a", disposition: "rejected", endorsed: false,
    actorId: "Review Architect", occurredAt: "2026-09-27T15:00:00Z", reason: "Does not meet brief", endorsedBy: null, endorsedAt: null };
  archived.review = { ...rejected.review, reviewRef: "review-b", disposition: "archived", actorId: "Archive Architect" };
  const accepted = source.history.candidates!.find((row) => row.candidateId === "run-entrance-a")!;
  accepted.acceptedStageRef = fixture.state.branchHead;
  accepted.review = { ...rejected.review, reviewRef: "review-accepted" };

  const normal = api.buildGrowthTree(source);
  assert.equal(normal.processedCount, 2);
  assert.equal(normal.nodes.has("candidate:run-massing-a"), false);
  assert.equal(normal.nodes.has("candidate:run-massing-b"), false);
  assert.equal(normal.nodes.has("candidate:run-entrance-a"), true, "an accepted Stage lineage is not filtered by a later review");
  assert.equal(normal.nodes.get("current")!.current!.headRunId, fixture.state.head, "filtering never moves Working Head");

  const shown = api.buildGrowthTree(source, true);
  assert.equal(shown.nodes.get("candidate:run-massing-a")!.letter, "A");
  assert.equal(shown.nodes.get("candidate:run-massing-b")!.letter, "B");
  assert.equal(shown.nodes.get("candidate:run-massing-a")!.candidate!.review!.actorId, "Review Architect");
  assert.equal(shown.nodes.get("candidate:run-massing-a")!.candidate!.review!.occurredAt, "2026-09-27T15:00:00Z");

  fixture.selectWorkingDraft({ projectId: "riverside-library", runId: "run-massing-a",
    baseRevisionSha256: fixture.workingDraft().revisionSha256 ?? null });
  const processedCurrent = api.buildGrowthTree({ ...source, workingSource: fixture.workingSource() });
  assert.equal(processedCurrent.nodes.get("current")!.current!.headRunId, "run-massing-a");
  assert.equal(processedCurrent.nodes.get("current")!.current!.sourceDisposition, "rejected");
  assert.deepEqual(processedCurrent.accept, api.buildGrowthTree(sourceOf(fixture)).accept,
    "review disposition does not introduce a new Stage acceptance rule");

  archived.review = { ...archived.review, disposition: "unreviewed" };
  const restored = api.buildGrowthTree({ ...source, workingSource: fixture.workingSource() });
  assert.equal(restored.nodes.has("candidate:run-massing-a"), false, "the other Candidate remains rejected");
  assert.equal(restored.nodes.has("candidate:run-massing-b"), true);
  assert.equal(restored.nodes.get("candidate:run-massing-b")!.letter, "B", "cancelling archive keeps the Study option identity");
  assert.equal(restored.nodes.get("current")!.current!.headRunId, fixture.state.head);
});

test("a runtime without the admission contract shows Stages, Current and running work only", async (t) => {
  const api = await harness(t);
  const fixture = api.createDesignTreeFixture();
  const { candidates, studies, ...history } = fixture.designHistory();
  assert.ok(candidates && studies);
  const tree = api.buildGrowthTree({ ...sourceOf(fixture), history });
  assert.equal([...tree.nodes.values()].filter((node) => node.kind === "candidate").length, 0);
  assert.deepEqual(tree.trunk.map((id) => tree.nodes.get(id)!.kind), ["stage", "stage", "stage", "current"]);
  assert.deepEqual(planarProblems(tree, api.layoutGrowthTree(tree)), []);
});

test("an unstaged project starts from the project root, and a Stage's own legacy admission is that Stage", async (t) => {
  const api = await harness(t);
  const head = { runId: "r3", stateDigest: "s", recordDigest: "d", sourceStageRef: null, branchId: null, accepted: false,
    origin: "working-position" as const, label: null, modelSource: null, lineage: ["r3", "r2", "r1"] };
  const tree = api.buildGrowthTree({
    projectId: "testmodel",
    history: { projectId: "testmodel", branches: [], branchId: "main", stages: [], candidates: [
      { candidateId: "r1", label: "Furniture band A", studyId: null },
      { candidateId: "r2", label: "Furniture band B", continuedFrom: "r1", studyId: null },
      { candidateId: "x1", label: "Unrelated option", studyId: null },
      { candidateId: "x2", label: "Rejected option", studyId: null, outcome: "rejected" },
    ] },
    workingSource: { projectId: "testmodel", workspace: "modeling", policy: "live", compatible: true, head },
    worktrees: null,
  });
  assert.equal(tree.nodes.has("candidate:x2"), false, "a retained rejection is never drawn");
  assert.equal(tree.root, "origin");
  assert.deepEqual(tree.trunk, ["origin", "candidate:r1", "candidate:r2", "current"]);
  assert.equal(tree.nodes.get("current")!.current!.editsAfter, 1);
  assert.equal(tree.accept.block, "no-stage");
  assert.deepEqual(planarProblems(tree, api.layoutGrowthTree(tree)), []);

  const fixture = api.createDesignTreeFixture();
  const history = fixture.designHistory();
  const folded = api.buildGrowthTree({ ...sourceOf(fixture), history: { ...history,
    candidates: [...history.candidates!, { candidateId: "run-s1-massing", legacy: "stage", acceptedStageRef: fixture.state.stages[1].ref }] } });
  assert.equal(folded.nodes.has("candidate:run-s1-massing"), false);
});

test("a long history stays planar at every fork", async (t) => {
  const api = await harness(t);
  const fixture = api.createDesignTreeFixture();
  const state = fixture.state;
  // Grow eight more rounds of five options, continuing a different letter each time.
  let base = state.head;
  let stageRef = state.branchHead;
  for (let round = 0; round < 8; round += 1) {
    const study = `study-round-${round}`;
    const ids = "ABCDE".split("").map((letter) => `run-r${round}-${letter}`);
    for (const id of ids) {
      state.runs.set(id, { parent: base, sourceStage: stageRef });
      state.candidates.push({ run: id, label: `Round ${round + 1} option`, summary: "Synthetic option.", studyId: study,
        admittedBy: "Arch Agent", admittedAt: "2026-09-25T00:00:00Z", continuedFrom: null, acceptedStage: null, blockedBy: [] });
    }
    state.studies.push({ id: study, label: `Round ${round + 1}`, baseRunId: base, baseStageRef: stageRef, candidateIds: ids });
    const chosen = ids[round % 5];
    state.head = chosen;
    if (round % 2 === 1) {
      fixture.accept(chosen, { projectId: "riverside-library", expectedHeadStageRef: stageRef });
      stageRef = state.branchHead;
    } else {
      // An abandoned sibling line with its own options, off a twig.
      const sibling = ids[(round + 2) % 5];
      const child = `run-r${round}-deep`;
      state.runs.set(child, { parent: sibling, sourceStage: stageRef });
      state.candidates.push({ run: child, label: "Deeper sibling", summary: "", studyId: null, admittedBy: "Kaiwen",
        admittedAt: "2026-09-25T00:00:00Z", continuedFrom: sibling, acceptedStage: null, blockedBy: [] });
    }
    base = chosen;
  }
  const tree = api.buildGrowthTree(sourceOf(fixture));
  assert.ok(tree.nodes.size > 60);
  assert.deepEqual(planarProblems(tree, api.layoutGrowthTree(tree)), []);
  // Continue from an early twig: a long abandoned future must stay planar too.
  state.head = "run-massing-e";
  const forked = api.buildGrowthTree(sourceOf(fixture));
  assert.deepEqual(planarProblems(forked, api.layoutGrowthTree(forked)), []);
});

test("the scene draws three levels of detail and hit targets for every node", async (t) => {
  const api = await harness(t);
  const fixture = api.createDesignTreeFixture();
  fixture.selectWorkingDraft({ projectId: "riverside-library", runId: "run-entrance-a", baseRevisionSha256: fixture.workingDraft().revisionSha256 ?? null });
  const tree = api.buildGrowthTree(sourceOf(fixture));
  const drawing = api.layoutGrowthTree(tree);
  const words = {
    current: "Current", origin: "Project start", name: (node: { label: string | null }) => node.label ?? "", pending: (status: string) => status,
    currentAt: "at Entrance A", accept: "Accept as S3", acceptBlocked: "Accept as next Stage", status: () => "Ready",
    fork: (fork: { options: number }) => [`${fork.options} options`, `${fork.options}`],
  };
  const scene = (level: "far" | "mid" | "close", textScale: number) =>
    api.buildTreeScene(tree, drawing, { level, textScale, selected: "candidate:run-massing-d", fontFamily: 2, words: words as never });
  const roles = (result: ReturnType<typeof scene>, role: string) => result.skeletons.filter((element) =>
    (element as unknown as { customData: { tree: { role: string } } }).customData.tree.role === role);
  const far = scene("far", 4), mid = scene("mid", 1.5), close = scene("close", 1);
  assert.equal(roles(far, "trunk").length, 1);
  assert.equal(roles(mid, "trunk").length, 1);
  assert.ok(roles(far, "dot").length > 10, "far shows options as dots");
  assert.ok(roles(far, "fork").length >= 3, "far shows counts");
  assert.equal(roles(far, "letter").length, 0);
  // #353: each option card carries its letter (a lone option has none), each running line its state.
  assert.equal(roles(mid, "letter").length, [...tree.nodes.values()].filter((node) => node.letter || node.kind === "pending").length, "middle shows letters");
  assert.ok(roles(mid, "letter").length > 8);
  assert.equal(roles(mid, "summary").filter((element) => (element as { id?: string }).id !== "current:summary").length, 0, "middle has no summaries");
  assert.ok(roles(close, "summary").length > 5, "close adds summaries");
  assert.ok(roles(close, "status").length > 5, "close adds status");
  assert.ok(mid.hits.some((hit) => hit.node === "current" && hit.action === "accept"));
  const ids = (result: ReturnType<typeof scene>) => result.skeletons.map((element) => (element as { id?: string }).id ?? "");
  assert.ok(ids(mid).includes("candidate:run-facade-c:review"), "an option admitted with review checks open carries a small mark");
  assert.equal(ids(far).some((id) => id.endsWith(":review")), false);
  assert.equal(roles(mid, "ring").length, 1, "the selection is drawn once");
  for (const id of tree.nodes.keys()) assert.ok(mid.hits.some((hit) => hit.node === id), `${id} can be clicked`);
  const massingD = drawing.nodes.get("candidate:run-massing-d")!;
  assert.equal(api.topmostAt(mid.hits, { x: massingD.card.x + 10, y: massingD.y })?.node, "candidate:run-massing-d");
  // #337: every colour is the palette's the canvas passes; left out, the light theme's.
  const palette = Object.fromEntries(Object.keys(api.LIGHT_TREE_PALETTE).map((name, index) =>
    [name, `#0000${index.toString(16).padStart(2, "0")}`])) as unknown as typeof api.LIGHT_TREE_PALETTE;
  const tinted = (level: "far" | "mid" | "close", textScale: number) => api.buildTreeScene(tree, drawing,
    { level, textScale, selected: "current", fontFamily: 2, words: words as never, palette });
  const byId = (result: ReturnType<typeof scene>, id: string) =>
    result.skeletons.find((element) => (element as { id?: string }).id === id) as unknown as { strokeColor?: string; backgroundColor?: string };
  assert.equal(byId(mid, "trunk").strokeColor, api.LIGHT_TREE_PALETTE.accent);
  const near = tinted("mid", 1.5);
  assert.equal(byId(near, "trunk").strokeColor, palette.accent);
  assert.equal(byId(near, "current:card").backgroundColor, palette.accentSoft);
  const stage = [...tree.nodes.values()].find((node) => node.kind === "stage")!;
  assert.equal(byId(near, `${stage.id}:card`).backgroundColor, palette.ink);
  assert.equal(byId(near, `${stage.id}:name`).strokeColor, palette.paper, "a Stage's name is paper on ink");
  const given = new Set(Object.values(palette));
  for (const element of [near, tinted("far", 4), tinted("close", 1)].flatMap((result) => result.skeletons)) {
    const { id, strokeColor, backgroundColor } = element as { id?: string; strokeColor?: string; backgroundColor?: string };
    for (const colour of [strokeColor, backgroundColor]) {
      if (colour && colour !== "transparent") assert.ok(given.has(colour), `${id} draws ${colour}, which is not the palette's`);
    }
  }
});

test("each Stage heads a column of what led to it, Current's work trails them, and a card's bar says its checks (#353)", async (t) => {
  const api = await harness(t);
  const fixture = api.createDesignTreeFixture();
  const source = sourceOf(fixture);
  source.history.candidates!.find((row) => row.candidateId === "run-massing-e")!.legacy = "working-copy";
  const tree = api.buildGrowthTree(source);
  const drawing = api.layoutGrowthTree(tree);
  const stage = (run: string) => `stage:${fixture.state.stages.find((row) => row.run === run)!.ref}`;
  assert.deepEqual(planarProblems(tree, drawing), []);
  // S0 holds itself; S1 the Massing Study that led to it; S2 the Facade Study; then Current with the Entrance
  // option and the running work, which grow towards S3.
  assert.deepEqual(drawing.columns.map((column) => column.node), [stage("run-site"), stage("run-s1-massing"), stage("run-s2-layout"), null]);
  assert.deepEqual(drawing.columns.map((column) => [column.options, column.pending]), [[0, 0], [5, 0], [4, 0], [1, 2]]);
  assert.equal(drawing.nodes.get("current")!.column, 3);
  assert.equal(drawing.columns[1].start, drawing.nodes.get("candidate:run-massing-c")!.x, "a header lines up with its column's first card");
  // Massing C stays on the trunk; A, B, D and E hang under it, level with it, in letter order, before S1.
  const chosen = drawing.nodes.get("candidate:run-massing-c")!;
  const hung = ["a", "b", "d", "e"].map((letter) => drawing.nodes.get(`candidate:run-massing-${letter}`)!);
  assert.ok(hung.every((node) => node.x === chosen.x && node.y > chosen.y && node.column === 1));
  assert.deepEqual(hung.map((node) => node.y), hung.map((node) => node.y).sort((a, b) => a - b));
  assert.ok(drawing.nodes.get(stage("run-s1-massing"))!.x > chosen.x + chosen.card.width);
  // Current stands level with the running work that waits beside it.
  assert.equal(drawing.nodes.get("pending:running:job-entrance-b")!.x, drawing.nodes.get("current")!.x);
  // A twig that was continued hands its row on: Facade A + 2 edits stands level with Facade A.
  assert.equal(drawing.nodes.get("candidate:run-facade-a2")!.y, drawing.nodes.get("candidate:run-facade-a")!.y);

  const node = (id: string) => tree.nodes.get(id)!;
  assert.deepEqual(["candidate:run-facade-b", "candidate:run-facade-c", "candidate:run-massing-e", "pending:running:job-entrance-b", "current"]
    .map((id) => api.checkOf(node(id))), ["held", "violated", "unchecked", "running", null]);
  const en = api.treeWords(translator(messagesEn) as never, tree), zh = api.treeWords(translator(messagesZhCN) as never, tree);
  assert.deepEqual([en.check(node("candidate:run-facade-c")), zh.check(node("candidate:run-facade-c"))], ["1 violated", "1 项违反"]);
  assert.deepEqual([en.check(node("candidate:run-facade-b")), zh.check(node("candidate:run-massing-e"))], ["Passed", "未查"]);
  assert.deepEqual(en.column(stage("run-s1-massing"), stage("run-site")), { id: "S1", name: "Massing", next: false });
  // The trailing column works towards S3, the Stage Accept would add after the line's newest.
  assert.deepEqual([en.column(null, stage("run-s2-layout")), zh.column(null, stage("run-s2-layout"))],
    [{ id: "S3", name: "Next Stage", next: true }, { id: "S3", name: "下一阶段", next: true }]);
  // Continued from an older Stage, the next Stage there is not S3: the header names no number.
  fixture.selectWorkingDraft({ projectId: "riverside-library", runId: "run-massing-d", baseRevisionSha256: fixture.workingDraft().revisionSha256 ?? null });
  const rerooted = api.buildGrowthTree(sourceOf(fixture));
  const rerootedDrawing = api.layoutGrowthTree(rerooted);
  assert.deepEqual(rerootedDrawing.columns.map((column) => column.node), [stage("run-site"), null]);
  assert.deepEqual(api.treeWords(translator(messagesEn) as never, rerooted).column(null, stage("run-site")), { id: "", name: "Next Stage", next: true });
  assert.equal(zh.scene.name(node("candidate:run-massing-c")), "Stepped courtyard block", "the card's letter stands beside its name, not in it");

  const palette = Object.fromEntries(Object.keys(api.LIGHT_TREE_PALETTE).map((name, index) =>
    [name, `#0001${index.toString(16).padStart(2, "0")}`])) as unknown as typeof api.LIGHT_TREE_PALETTE;
  const scene = api.buildTreeScene(tree, drawing, { level: "mid", textScale: 1.25, selected: null, fontFamily: 2, words: en.scene, palette });
  const bar = (id: string) => (scene.skeletons.find((element) => (element as { id?: string }).id === `${id}:bar`) as { backgroundColor?: string } | undefined)?.backgroundColor;
  assert.deepEqual(["candidate:run-facade-b", "candidate:run-facade-c", "candidate:run-massing-e", "pending:running:job-entrance-b"].map(bar),
    [palette.held, palette.violated, palette.unchecked, palette.running]);
  assert.equal(bar("current"), undefined, "Stages and Current carry no status bar");
  // Every word on a card stays inside it, and clear of its review mark, at every text scale the middle and close
  // levels use, in both languages.
  type Text = { id: string; type: string; x: number; y: number; text?: string; fontSize?: number; customData: { tree: { node?: string; role: string } } };
  const levels = (["mid", "close"] as const).flatMap((level) => (level === "mid" ? [1, 1.25, 1.5, 1.75, 2] : [1])
    .flatMap((textScale) => [en, zh].map((words) => api.buildTreeScene(tree, drawing, { level, textScale, selected: null, fontFamily: 2, words: words.scene, palette }))));
  const box = (element: Text) => {
    const lines = element.text!.split("\n");
    return { x: element.x, y: element.y, right: element.x + Math.max(...lines.map((line) => api.textWidth(line, element.fontSize!))),
      bottom: element.y + lines.length * element.fontSize! * 1.2 };
  };
  for (const result of [scene, ...levels]) {
    const texts = (result.skeletons as unknown as Text[]).filter((element) => element.type === "text" && element.customData.tree.role !== "fork");
    const marks = new Map(texts.filter((element) => element.id.endsWith(":review")).map((element) => [element.customData.tree.node!, box(element)]));
    for (const element of texts) {
      const placed = element.customData.tree.node ? drawing.nodes.get(element.customData.tree.node) : undefined;
      if (!placed) continue;
      const own = box(element);
      assert.ok(own.x >= placed.card.x && own.right <= placed.card.x + placed.card.width + 1e-6 && own.y >= placed.card.y
        && own.bottom <= placed.card.y + placed.card.height + 1e-6, `${placed.id}: "${element.text}" leaves its card`);
      const mark = marks.get(placed.id);
      if (mark && !element.id.endsWith(":review")) assert.ok(own.right <= mark.x || own.x >= mark.right || own.bottom <= mark.y || own.y >= mark.bottom,
        `${placed.id}: "${element.text}" runs into its review mark`);
    }
  }
  assert.ok(levels.some((result) => (result.skeletons as unknown as Text[]).some((element) => element.id === "candidate:run-facade-c:review")));
  // A long first line is wrapped short of the mark; the rest of the name keeps the card's width.
  const facadeC = (scene.skeletons as unknown as Text[]).filter((element) => element.id.startsWith("candidate:run-facade-c:name")).map((element) => element.text);
  assert.deepEqual(facadeC, ["Perforated", "terracotta screen"]);
});

test("text fits its box, and a turned rectangle keeps its own hit area", async (t) => {
  const api = await harness(t);
  assert.equal(api.clip("Stepped courtyard block", 12, 1000), "Stepped courtyard block");
  assert.ok(api.textWidth(api.clip("Stepped courtyard block", 12, 60), 12) <= 60);
  const rows = api.wrap("Courtyard gate on the south bar of the library", 12, 90, 2);
  assert.equal(rows.length, 2);
  assert.ok(rows[1].endsWith("…"));
  assert.ok(rows.every((row) => api.textWidth(row, 12) <= 90));
  assert.deepEqual(api.wrap("九格环庭", 12, 30, 2), ["九格", "环庭"]);
  // Only CJK, Hangul and full-width forms count double. The compatibility ideographs start at U+F900; the ranges
  // are written as escapes, so no editor's normalisation can move that start to U+8C48 and widen the rest.
  assert.deepEqual([api.textWidth("\uF900", 10), api.textWidth("\uFAFF", 10), api.textWidth("\uFF21", 10)], [10, 10, 10]);
  assert.ok(api.textWidth("\uE000", 10) < 10 && api.textWidth("\uA500", 10) < 10, "private use and Vai are not measured as CJK");
  assert.deepEqual(api.wrap("\uA500\uA501 \uE000", 10, 100, 1), ["\uA500\uA501 \uE000"], "and they wrap as words");
  const box = { x: 0, y: 0, width: 100, height: 20, angle: Math.PI / 2 };
  assert.equal(api.sceneBoxContains(box, { x: 50, y: 50 }), true, "rotated about its centre, the box stands upright");
  assert.equal(api.sceneBoxContains(box, { x: 90, y: 10 }), false);
  assert.equal(api.sceneBoxContains({ ...box, angle: 0 }, { x: 90, y: 10 }), true);
  assert.equal(api.sceneBoxContains({ x: Number.NaN, y: 0, width: 1, height: 1 }, { x: 0, y: 0 }), false);
});

test("close cards show the retained previews they have, and only close cards (#406)", async (t) => {
  const api = await harness(t);
  const fixture = api.createDesignTreeFixture();
  const source = sourceOf(fixture);
  const tree = api.buildGrowthTree(source);
  const drawing = api.layoutGrowthTree(tree);
  const words = {
    current: "Current", origin: "Project start", name: (node: { label: string | null }) => node.label ?? "", pending: (status: string) => status,
    currentAt: "at S2", accept: "Accept as S3", acceptBlocked: "Accept as next Stage", status: () => "Ready",
    fork: (fork: { options: number }) => [`${fork.options} options`],
  };
  // Each node that names a model can show it; running lines and the project start have none.
  const models = [...tree.nodes.values()].filter((node) => api.nodeModelSource(source, node));
  assert.deepEqual(new Set(models.map((node) => node.kind)), new Set(["stage", "candidate", "current"]));
  assert.equal([...tree.nodes.values()].filter((node) => node.kind === "pending").some((node) => api.nodeModelSource(source, node)), false);
  const S1 = [...tree.nodes.values()].find((node) => node.kind === "stage" && node.stage!.number === 1)!.id;
  const shown = ["candidate:run-massing-b", "candidate:run-facade-c", S1, "current"];
  const previews = new Map(shown.map((id, index) => [id, { fileId: `file-${index}`, width: 160 + index * 40, height: 90 }]));
  const scene = (level: "far" | "mid" | "close") =>
    api.buildTreeScene(tree, drawing, { level, textScale: level === "far" ? 4 : 1, selected: null, fontFamily: 2, words: words as never, previews });
  type Element = { id: string; type: string; x: number; y: number; width: number; height: number; fileId?: string; customData: { tree: { role: string; node?: string } } };
  const images = (level: "far" | "mid" | "close") => (scene(level).skeletons as unknown as Element[]).filter((element) => element.type === "image");
  assert.deepEqual(images("close").map((element) => element.customData.tree.node).sort(), [...shown].sort(), "one image for each node that has one");
  assert.deepEqual(images("mid"), [], "the middle level draws no images");
  assert.deepEqual(images("far"), [], "the far level draws no images");
  const close = scene("close").skeletons as unknown as Element[];
  for (const image of images("close")) {
    const card = drawing.nodes.get(image.customData.tree.node!)!.card;
    assert.ok(image.x >= card.x && image.y >= card.y && image.x + image.width <= card.x + card.width + 1e-6 && image.y + image.height <= card.y + card.height + 1e-6,
      `${image.id} stays inside its card`);
    const preview = previews.get(image.customData.tree.node!)!;
    assert.ok(Math.abs(image.width / image.height - preview.width / preview.height) < 1e-6, `${image.id} keeps its proportions`);
    assert.equal(image.fileId, preview.fileId);
  }
  // The image card keeps its letter, name and status, and its words stay clear of the image.
  const massingB = close.filter((element) => element.customData.tree.node === "candidate:run-massing-b");
  const picture = massingB.find((element) => element.type === "image")!;
  assert.equal(massingB.find((element) => element.id === "candidate:run-massing-b:letter")?.type, "text");
  for (const role of ["name", "status"]) {
    const words = massingB.filter((element) => element.type === "text" && element.customData.tree.role === role);
    assert.ok(words.length > 0 && words.every((element) => element.x >= picture.x + picture.width), `${role} beside the image`);
  }
  assert.equal(massingB.some((element) => element.customData.tree.role === "summary"), false, "the image takes the summary's place");
  // A card without a preview is the close card it was, and every node can still be clicked.
  const withoutPreviews = api.buildTreeScene(tree, drawing, { level: "close", textScale: 1, selected: null, fontFamily: 2, words: words as never });
  const plain = (result: { skeletons: unknown[] }) => (result.skeletons as Element[]).filter((element) => element.customData.tree.node === "candidate:run-massing-a");
  assert.deepEqual(plain(scene("close")), plain(withoutPreviews));
  for (const id of tree.nodes.keys()) assert.ok(scene("close").hits.some((hit) => hit.node === id && hit.action === "select"), `${id} can be clicked`);
});

test("only cards in view at the close level want their previews", async (t) => {
  const api = await harness(t);
  const drawing = api.layoutGrowthTree(api.buildGrowthTree(sourceOf(api.createDesignTreeFixture())));
  const current = drawing.nodes.get("current")!.card;
  // A 400 × 300 view centred on Current at 150 %.
  const view = { zoom: 1.5, width: 400, height: 300, scrollX: 200 / 1.5 - (current.x + current.width / 2), scrollY: 150 / 1.5 - (current.y + current.height / 2) };
  const near = api.nodesWantingPreviews(drawing, view, "close");
  assert.ok(near.includes("current"));
  assert.ok(near.length < drawing.nodes.size / 2, `a view shows part of the tree: ${near}`);
  const left = -view.scrollX, right = left + view.width / view.zoom;
  for (const id of near) {
    const card = drawing.nodes.get(id)!.card;
    assert.ok(card.x < right && card.x + card.width > left, `${id} is in view`);
  }
  assert.deepEqual(api.nodesWantingPreviews(drawing, view, "mid"), [], "nothing is wanted at the middle level");
  assert.deepEqual(api.nodesWantingPreviews(drawing, view, "far"), []);
  assert.deepEqual(api.nodesWantingPreviews(drawing, { ...view, scrollX: view.scrollX + 1e6 }, "close"), [], "nothing off screen");
});

test("the preview loader reads each key once, a few at a time, only while it is wanted", async (t) => {
  const api = await harness(t);
  const calls: string[] = [];
  const pending = new Map<string, (value: string | null) => void>();
  let changes = 0;
  const loader = api.createPreviewLoader<string>((key) => {
    calls.push(key);
    if (key === "broken") return Promise.reject(new Error("unreadable"));
    return new Promise((resolve) => pending.set(key, resolve));
  }, { limit: 2, onChange: () => { changes += 1; } });
  const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
  loader.want(["a", "b", "c", "d"]);
  await settle();
  assert.deepEqual(calls, ["a", "b"], "at most two at a time");
  // The view moved on: c and d are no longer wanted, so they are never read.
  loader.want(["a", "e"]);
  pending.get("a")!("image-a");
  pending.get("b")!(null);
  await settle();
  assert.deepEqual(calls, ["a", "b", "e"]);
  assert.equal(loader.get("a"), "image-a");
  assert.equal(loader.get("b"), null, "no preview is an answer, not an error");
  assert.equal(loader.get("c"), undefined);
  // Wanted again, a key already read is not read twice.
  loader.want(["a", "b", "a"]);
  await settle();
  assert.deepEqual(calls, ["a", "b", "e"]);
  // A failed read is no image.
  loader.want(["broken"]);
  await settle();
  assert.equal(loader.get("broken"), null);
  // Nothing wanted, nothing read; a disposed loader reads nothing more.
  const before = calls.length;
  loader.want([]);
  await settle();
  assert.equal(calls.length, before);
  loader.dispose();
  loader.want(["x"]);
  await settle();
  assert.equal(calls.length, before);
  assert.ok(changes >= 3);
});

test("a failed read is read again when its key comes back, after a while, or when a projection lands", async (t) => {
  const api = await harness(t);
  const calls: string[] = [];
  let clock = 1000, answer: string | null = null;
  const loader = api.createPreviewLoader<string>(async (key) => { calls.push(key); return answer; },
    { retryMs: 30_000, now: () => clock, onChange: () => undefined });
  const settle = async () => { for (let turn = 0; turn < 4; turn += 1) await new Promise((resolve) => setTimeout(resolve, 0)); };
  loader.want(["lost"]);
  await settle();
  assert.equal(loader.get("lost"), null, "the blob could not be read: no image");
  loader.want(["lost"]);
  await settle();
  assert.deepEqual(calls, ["lost"], "still wanted: not read again at once");
  // The store dropped the model's blob while it is drawn again, then names it again.
  loader.want([]);
  loader.want(["lost"]);
  await settle();
  assert.deepEqual(calls, ["lost", "lost"], "wanted again after it left: read again");
  clock += 29_999;
  loader.want(["lost"]);
  await settle();
  assert.equal(calls.length, 2);
  clock += 1;
  loader.want(["lost"]);
  await settle();
  assert.equal(calls.length, 3, "still wanted: read again after the retry interval");
  answer = "image";
  loader.retry();
  await settle();
  assert.equal(calls.length, 4, "a projection landed: read again at once");
  assert.equal(loader.get("lost"), "image");
  loader.retry();
  loader.want(["lost"]);
  await settle();
  assert.equal(calls.length, 4, "an image read is never read again");
  loader.dispose();
});

test("the preview loader keeps a bounded number of images, never one the view shows", async (t) => {
  const api = await harness(t);
  const calls: string[] = [];
  const loader = api.createPreviewLoader<string>(async (key) => { calls.push(key); return `image-${key}`; },
    { limit: 4, keep: 2, onChange: () => undefined });
  const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
  loader.want(["a", "b", "c"]);
  for (let turn = 0; turn < 4; turn += 1) await settle();
  assert.deepEqual(["a", "b", "c"].map((key) => loader.get(key)), ["image-a", "image-b", "image-c"], "all three are shown");
  loader.want(["d"]);
  for (let turn = 0; turn < 4; turn += 1) await settle();
  assert.equal(["a", "b", "c", "d"].filter((key) => loader.get(key) !== undefined).length, 2, "two kept");
  assert.equal(loader.get("d"), "image-d");
  assert.equal(loader.get("c"), "image-c", "the most recently shown of the rest stays");
  loader.want(["a"]);
  for (let turn = 0; turn < 4; turn += 1) await settle();
  assert.deepEqual(calls, ["a", "b", "c", "d", "a"], "an image let go is read again when shown again");
  loader.dispose();
});

test("a model's thumbnail is the store's newest done projection, and a model without one is asked for once", async (t) => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const thumbnails = await vite.ssrLoadModule("/src/features/artifacts/modelThumbnails.ts") as
    typeof import("../src/features/artifacts/modelThumbnails.ts");
  const asset = "a".repeat(64);
  const entity = (key: string, rev: number, blob: string, size = 256) => [`projections:${key}`, { id: `projections:${key}`,
    domain: "projections", rev, body: { key, inputSha256: asset, kind: "model-line-view",
      recipe: { view: "axon", size, style: "lines" }, renderer: `r${rev}`, blobSha256: blob } }] as const;
  const state = (entries: readonly (readonly [string, unknown])[]) => ({ status: "ready", epoch: "e", revision: 9,
    byId: new Map(entries), domains: { projections: 9 }, moved: new Map() }) as never;
  const held = state([entity("old", 3, "1".repeat(64)), entity("new", 7, "2".repeat(64)), entity("big", 8, "3".repeat(64), 1024),
    ["run:r", { id: "run:r", domain: "run", rev: 1, body: {} }]]);
  assert.equal(thumbnails.thumbnailBlobs(held).get(asset), "2".repeat(64), "the newer renderer's picture, at the tree's size");
  assert.equal(thumbnails.thumbnailBlobs(held), thumbnails.thumbnailBlobs(held), "worked out once per store state");
  assert.equal(thumbnails.thumbnailsMoved(held), "e:9");
  const asked: unknown[] = [];
  let answer: { status: string; blobSha256?: string | null } = { status: "pending" };
  const studio = { projection: async (source: unknown, size: number) => { asked.push([source, size]); return answer; },
    projectionBlob: async () => new Blob() } as never;
  const source = { runId: "r", stateDigest: "b".repeat(64), assetSha256: asset };
  assert.equal(await thumbnails.askThumbnail(studio, source, 1000), null, "pending: the placeholder shows");
  assert.equal(await thumbnails.askThumbnail(studio, source, 2000), null);
  assert.deepEqual(asked, [[source, 256]], "asked once while the answer stands");
  answer = { status: "done", blobSha256: "4".repeat(64) };
  assert.equal(await thumbnails.askThumbnail(studio, source, 1000 + 31_000), "4".repeat(64), "asked again once it is old");
  assert.equal(await thumbnails.askThumbnail(studio, source, 10_000_000), "4".repeat(64));
  assert.equal(asked.length, 2, "a done answer stands for good");
  // The blob could not be read (the server draws it again): the answer that named it is forgotten.
  thumbnails.forgetThumbnailBlob(studio, "4".repeat(64));
  answer = { status: "pending" };
  assert.equal(await thumbnails.askThumbnail(studio, source, 10_000_001), null);
  assert.equal(asked.length, 3, "asked again");
});

test("a refused or failed ask is kept, and waits longer each time it fails again", async (t) => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const thumbnails = await vite.ssrLoadModule("/src/features/artifacts/modelThumbnails.ts") as
    typeof import("../src/features/artifacts/modelThumbnails.ts");
  let asks = 0, fail = true;
  const studio = { projection: async () => { asks += 1; if (fail) throw new Error("409 MODEL_SOURCE_MISMATCH"); return { status: "pending" }; },
    projectionBlob: async () => new Blob() } as never;
  const source = { runId: "r", stateDigest: "b".repeat(64), assetSha256: "a".repeat(64) };
  const wait = thumbnails.ASK_AGAIN_MS;
  let now = 1000;
  assert.equal(await thumbnails.askThumbnail(studio, source, now), null);
  for (let scroll = 1; scroll < 50; scroll += 1) await thumbnails.askThumbnail(studio, source, now + scroll * 10);
  assert.equal(asks, 1, "every scroll and commit meanwhile is answered by the kept failure");
  await thumbnails.askThumbnail(studio, source, now + wait - 1);
  assert.equal(asks, 1, "never sooner than the pending wait");
  now += wait;
  await thumbnails.askThumbnail(studio, source, now);
  assert.equal(asks, 2);
  await thumbnails.askThumbnail(studio, source, now + wait);
  assert.equal(asks, 2, "a second failure in a row waits twice as long");
  now += 2 * wait;
  fail = false;
  await thumbnails.askThumbnail(studio, source, now);
  assert.equal(asks, 3);
  await thumbnails.askThumbnail(studio, source, now + wait);
  assert.equal(asks, 4, "an answer ends the backoff: pending waits the usual time");
});

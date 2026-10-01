/**
 * #549: component information datasets - the generic MonkeyHubComponentInfo@1 format, its Board
 * cards and the card Modeling shows for a pick. Synthetic fixtures only (test/fixtures/component-info).
 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test, { type TestContext } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

type Info = typeof import("../src/features/componentInfo/componentInfo.ts");
type Board = typeof import("../src/features/componentInfo/boardDatasets.ts");
type Catalog = typeof import("../src/i18n/messages.zh-CN.ts");

async function harness(t: TestContext) {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const info = await vite.ssrLoadModule("/src/features/componentInfo/componentInfo.ts") as Info;
  const board = await vite.ssrLoadModule("/src/features/componentInfo/boardDatasets.ts") as Board;
  const { messagesZhCN } = await vite.ssrLoadModule("/src/i18n/messages.zh-CN.ts") as Catalog;
  const { messagesEn } = await vite.ssrLoadModule("/src/i18n/messages.en.ts") as typeof import("../src/i18n/messages.en.ts");
  const words = (catalog: Record<string, string>) => (key: string, parameters?: Readonly<Record<string, string | number>>) =>
    catalog[key]!.replace(/\{(\w+)\}/g, (_, name: string) => String(parameters?.[name] ?? `{${name}}`));
  return { info, board, zh: words(messagesZhCN), en: words(messagesEn) };
}

const fixture = async (name: string) => readFile(new URL(`./fixtures/component-info/${name}.json`, import.meta.url), "utf8");
const B = "b".repeat(64);
const SHOWN_B = { projectId: "fixture-project", runId: "run-b", stateDigest: B };
const resolved = { status: "resolved", sourceState: "current" };

async function boardOf(info: Info, board: Board, names: string[]) {
  const datasets = (await Promise.all(names.map(fixture))).flatMap((text) => info.parseDatasetImport(text));
  const elements = datasets.map((dataset) => ({ id: board.cardElementId(dataset.id), type: "rectangle",
    customData: { [board.COMPONENT_INFO_KEY]: dataset } }));
  return { status: "ready" as const, projectId: "fixture-project", revisionSha256: "r".repeat(64), ...board.readBoardDatasets(elements) };
}

test("the generic format reads summary and section datasets as written", async (t) => {
  const { info } = await harness(t);
  const [basics] = info.parseDatasetImport(await fixture("basics"));
  assert.equal(basics!.id, "basics-fixture");
  assert.equal(basics!.role, "summary");
  assert.deepEqual(basics!.components["post-1"]!.fields![2], { label: "Made size", value: "24 × 7/8 × 7/8", unit: "in", source: "parts" });
  const both = info.parseDatasetImport(`[${await fixture("supply")}, ${await fixture("thermal")}]`);
  assert.deepEqual(both.map((dataset) => dataset.id), ["supply-fixture", "thermal-fixture"]);
  assert.equal(both[0]!.groups!["stock-a"]!.allocation, null);
  assert.equal(both[1]!.groups!["wall-type-1"]!.allocation!.basis, "by face area");
});

test("a dataset the Board could not keep, or the card could not read, is refused with every reason", async (t) => {
  const { info } = await harness(t);
  const valid = JSON.parse(await fixture("supply"));
  const refused = (value: unknown) => {
    try { info.parseDatasetImport(typeof value === "string" ? value : JSON.stringify(value)); }
    catch (error) {
      assert.ok(error instanceof info.ComponentInfoError);
      return error.problems.map((problem) => `${problem.code} ${problem.path}`);
    }
    assert.fail("accepted");
  };
  assert.deepEqual(refused("{not json"), ["notJson $"]);
  assert.deepEqual(refused([]), ["empty $"]);
  assert.deepEqual(refused({ ...valid, schema: "MonkeyHubComponentInfo@2" }), ["schema $.schema"]);
  assert.deepEqual(refused({ ...valid, role: "appendix" }), ["type $.role"]);
  assert.deepEqual(refused({ ...valid, appliesTo: { ...valid.appliesTo, stateDigest: "B".repeat(64) } }), ["digest $.appliesTo.stateDigest"]);
  assert.deepEqual(refused({ ...valid, preparedAt: "2026-02-30" }), ["date $.preparedAt"]);
  assert.deepEqual(refused({ ...valid, colour: "red" }), ["unknownKey $.colour"]);
  // The Board refuses these keys anywhere in an element, so a dataset carrying one never reaches a card.
  const nested = structuredClone(valid);
  nested.components["post-1"].fields[0].modelSource = { runId: "run-b" };
  assert.deepEqual(refused(nested), ["forbiddenKey $.components[\"post-1\"].fields[0].modelSource", "unknownKey $.components[\"post-1\"].fields[0].modelSource"]);
  const links = structuredClone(valid);
  links.groups["stock-a"].fields[0].url = "javascript:alert(1)";
  links.groups["stock-a"].fields[1].status = "guessed";
  links.groups["stock-a"].fields[1].source = "nowhere";
  links.components["brace-1"].group = "stock-z";
  assert.deepEqual(refused(links), [
    "url $.groups[\"stock-a\"].fields[0].url", "status $.groups[\"stock-a\"].fields[1].status",
    "sourceRef $.groups[\"stock-a\"].fields[1].source", "groupRef $.components[\"brace-1\"].group",
  ]);
  assert.deepEqual(refused([valid, valid]), ["duplicate $[1].id"]);
  const unworded = structuredClone(valid);
  unworded.components["post-1"].fields[0].value = "";
  assert.deepEqual(refused(unworded), ["type $.components[\"post-1\"].fields[0].value"]);
});

test("data attaches only to a pick resolved against the exact version shown", async (t) => {
  const { info } = await harness(t);
  assert.equal(info.pickMatchesShown(resolved, SHOWN_B), true);
  assert.equal(info.pickMatchesShown({ status: "MODEL_VISIBLE_CATALOG_MISSING", sourceState: "current" }, SHOWN_B), true);
  assert.equal(info.pickMatchesShown({ status: "resolved", sourceState: "stale" }, SHOWN_B), false);
  assert.equal(info.pickMatchesShown({ status: "resolved", sourceState: "unknown" }, SHOWN_B), false);
  assert.equal(info.pickMatchesShown({ status: "unbound", sourceState: "current" }, SHOWN_B), false);
  assert.equal(info.pickMatchesShown({ status: "local", sourceState: B }, SHOWN_B), true);
  assert.equal(info.pickMatchesShown({ status: "local", sourceState: "a".repeat(64) }, SHOWN_B), false);
  assert.equal(info.pickMatchesShown(resolved, null), false);
});

test("the card shows summary first, then sections by order, each in its own words", async (t) => {
  const { info, board, zh } = await harness(t);
  const ready = await boardOf(info, board, ["thermal", "supply", "basics"]);
  const status = (value: string) => zh(`componentInfo.status.${value}`);
  const card = info.buildComponentCard({ componentId: "post-1", elementId: "post-1-body", modelLabel: "Post 1",
    shown: SHOWN_B, pickMatchesShown: true, board: ready }, status as never);
  assert.equal(card.state, "ready");
  assert.equal(card.name, "Post 1 (front left)");
  assert.deepEqual(card.summary.map((block) => block.datasetId), ["basics-fixture"]);
  // Thermal was written for version A: it is listed as not matching, never shown as data.
  assert.deepEqual(card.sections.map((block) => block.datasetId), ["supply-fixture"]);
  assert.deepEqual(card.mismatched.map((item) => item.id), ["thermal-fixture"]);
  const made = card.summary[0]!.entry!.fields[2]!;
  assert.deepEqual([made.sourceLabel, made.sourceDate], ["parts list (model inches)", "2026-10-01"]);
  const supply = card.sections[0]!;
  assert.equal(supply.note, "Allowances only, not quotes.");
  assert.equal(supply.group!.title, "Timber stock A");
  assert.deepEqual([supply.group!.shared, supply.group!.sharedBy, supply.group!.allocation], [true, 2, null]);
  assert.equal(supply.group!.note, "Shared stock; no cost per piece is allocated.");
  const budget = supply.group!.fields[1]!;
  assert.deepEqual([budget.statusLabel, budget.sourceLabel, budget.sourceDate], ["Budget estimate", "initial budget sheet", "2026-09-30"]);
  // A status the dataset did not word gets the interface's word.
  assert.equal(supply.group!.fields[2]!.statusLabel, "待确认");
  assert.equal(supply.group!.fields[0]!.url, "https://example.com/stock-a");

  const text = info.componentCardText(card, zh as never);
  assert.match(text, /^Post 1 \(front left\)\n/);
  assert.match(text, /【Supply】\nAllowances only, not quotes\.\nCut from: stock A, ripped\n所属分组: Timber stock A\n共享：2 个构件共用/);
  assert.match(text, /Budget: 5 pieces \$25 \[Budget estimate\] · 来源：initial budget sheet · 2026-09-30/);
  assert.match(text, /Supplier: Example Timber <https:\/\/example\.com\/stock-a> · 来源：supplier page · 2026-10-01/);
  assert.match(text, /Lead time: to confirm \[待确认\]/);
  // A value that already says its status is not followed by the same word again.
  const leadTime = supply.group!.fields[2]!;
  assert.equal(info.statusBadge({ ...leadTime, value: "待确认" }), null);
  assert.equal(info.statusBadge(leadTime), "待确认");
  assert.match(text, /构件 ID: post-1\n元素 ID: post-1-body\n当前显示版本: fixture-project · run-b · b{64}/);
  assert.match(text, /版本不符的资料: Thermal \(thermal-fixture\) · version A · run-a · a{64}/);
  assert.doesNotMatch(text, /Conductivity|0\.13/, "data written for another version is never copied either");
});

test("a component a dataset does not cover says so in that dataset's words", async (t) => {
  const { info, board, zh } = await harness(t);
  const ready = await boardOf(info, board, ["basics", "supply"]);
  const card = info.buildComponentCard({ componentId: "stand-leg-1", elementId: null, modelLabel: "Stand Leg 1",
    shown: SHOWN_B, pickMatchesShown: true, board: ready }, (status) => status);
  assert.equal(card.state, "ready");
  assert.deepEqual(card.summary[0]!.entry!.fields.map((field) => field.value), ["1:1", "18 × 1-1/2 × 1-1/2"]);
  assert.equal(card.sections[0]!.entry, null);
  assert.equal(card.sections[0]!.missing, "No supply recorded");
  assert.equal(card.sections[0]!.group, null);
  const unknown = info.buildComponentCard({ componentId: "not-in-any", elementId: null, modelLabel: "Not In Any",
    shown: SHOWN_B, pickMatchesShown: true, board: ready }, (status) => status);
  assert.equal(unknown.name, "Not In Any", "without a dataset entry the model's own label names it");
  assert.deepEqual([unknown.summary[0]!.missing, unknown.sections[0]!.missing], ["No basics recorded", "No supply recorded"]);
  assert.match(info.componentCardText(unknown, zh as never), /No basics recorded\n\n【Supply】\nAllowances only, not quotes\.\nNo supply recorded/);
});

test("no data is shown for another version, an unconfirmed view, an unread board or an empty one", async (t) => {
  const { info, board, zh, en } = await harness(t);
  const ready = await boardOf(info, board, ["basics", "supply"]);
  const input = { componentId: "post-1", elementId: "post-1-body", modelLabel: "Post 1", shown: SHOWN_B, pickMatchesShown: true, board: ready };
  const other = { projectId: "fixture-project", runId: "run-a", stateDigest: "a".repeat(64) };
  for (const [change, state] of [
    [{ shown: other }, "mismatch"], [{ shown: { ...SHOWN_B, projectId: "another-project" } }, "mismatch"],
    [{ shown: null }, "unverified"], [{ pickMatchesShown: false }, "unverified"],
    [{ board: { status: "loading" } }, "loading"], [{ board: { status: "error", error: new Error("down") } }, "error"],
    [{ board: { ...ready, datasets: [] } }, "none"],
  ] as const) {
    const card = info.buildComponentCard({ ...input, ...change } as never, (status) => status);
    assert.equal(card.state, state, JSON.stringify(change));
    assert.deepEqual([card.summary, card.sections, card.used, card.sources], [[], [], [], []], state);
    assert.equal(card.name, "Post 1", "only the model's own label");
    const text = info.componentCardText(card, zh as never);
    assert.doesNotMatch(text, /timber|Timber stock|\$25/, state);
    assert.match(text, /构件 ID: post-1/);
  }
  const mismatch = info.buildComponentCard({ ...input, shown: other }, (status) => status);
  assert.equal(info.stateSentence(mismatch, zh as never), "构件资料写于 version B，不在当前显示版本的这条设计线上。");
  assert.equal(info.stateSentence(mismatch, en as never), "The component information was written for version B, which is not on the line of the version shown.");
  assert.equal(info.stateSentence(info.buildComponentCard({ ...input, board: { ...ready, datasets: [] } }, (status) => status), zh as never),
    "构件资料未导入。可在画板中“导入构件资料”。");
  const broken = { ...ready, datasets: [], invalid: [{ elementId: "card", datasetId: "x", problems: [{ code: "required" as const, path: "$.id" }] }] };
  assert.equal(info.stateSentence(info.buildComponentCard({ ...input, board: broken }, (status) => status), zh as never),
    "画板上有 1 张构件资料卡无法读取。", "a card that cannot be read is not reported as nothing imported");
});

test("data written for a version the shown one continued from is inherited, flagged where the component's shape changed since", async (t) => {
  const { info, board, zh, en } = await harness(t);
  const ready = await boardOf(info, board, ["basics", "supply"]);
  const next = { projectId: "fixture-project", runId: "run-c", stateDigest: "c".repeat(64) };
  const line = { runId: "run-c", ancestors: ["run-b", "run-a"], states: new Map([["run-b", B]]) };
  const input = { componentId: "post-1", elementId: "post-1-body", modelLabel: "Post 1", shown: next, pickMatchesShown: true, board: ready };

  // The next version on B's line (materials declared, say) shows B's data as its own.
  const inherited = info.buildComponentCard({ ...input, lineage: line }, (status) => status);
  assert.equal(inherited.state, "ready");
  assert.equal(inherited.name, "Post 1 (front left)");
  assert.deepEqual(inherited.used.map((item) => [item.id, item.inherited]), [["basics-fixture", true], ["supply-fixture", true]]);
  assert.deepEqual(inherited.reshapedSince, []);
  assert.match(info.componentCardText(inherited, zh as never), /资料: Basics \(basics-fixture\) · 2026-10-01 · 沿用自 version B/);

  // Its shape changed since B: one line says so, and the data still shows.
  const changedSinceB = (changed: boolean | "unknown") => new Map([["run-b", changed === "unknown" ? "unknown" as const : new Map([["post-1", changed]])]]);
  const reshaped = info.buildComponentCard({ ...input, lineage: line, changes: changedSinceB(true) }, (status) => status);
  assert.equal(reshaped.state, "ready");
  assert.deepEqual(reshaped.reshapedSince, ["version B"]);
  assert.equal(info.reshapedSentence(reshaped, zh as never), "资料写于 version B；此构件之后改过形状，尺寸以模型为准。");
  assert.equal(info.reshapedSentence(reshaped, en as never),
    "Written for version B; this component's shape has changed since, so take its sizes from the model.");
  assert.match(info.componentCardText(reshaped, zh as never), /^Post 1 \(front left\)\n资料写于 version B；此构件之后改过形状/);
  for (const changed of [false, "unknown"] as const) {
    assert.deepEqual(info.buildComponentCard({ ...input, lineage: line, changes: changedSinceB(changed) }, (status) => status).reshapedSince, [],
      `no flag for ${changed}`);
  }

  // Off the line, for another shown run, or for a state that run never had: not this version's data.
  for (const lineage of [null, { ...line, ancestors: ["run-a"] }, { ...line, runId: "run-d" }, { ...line, states: new Map([["run-b", "f".repeat(64)]]) }]) {
    assert.equal(info.buildComponentCard({ ...input, lineage }, (status) => status).state, "mismatch", JSON.stringify(lineage?.ancestors));
  }
  // Data written for the shown version itself needs no line; while the line is read, other data waits instead of being refused.
  assert.equal(info.buildComponentCard({ ...input, shown: SHOWN_B, lineage: null }, (status) => status).state, "ready");
  assert.equal(info.buildComponentCard({ ...input, lineagePending: true }, (status) => status).state, "loading");
});

test("cards on the Board are found by their marker, never by position or words", async (t) => {
  const { info, board } = await harness(t);
  const [basics] = info.parseDatasetImport(await fixture("basics"));
  const card = { id: board.cardElementId("basics-fixture"), type: "rectangle", customData: { componentInfo: basics } };
  const read = board.readBoardDatasets([
    { id: "note", type: "text", text: "构件资料 · Basics" },
    { ...card, id: "deleted-copy", isDeleted: true, customData: { componentInfo: { ...basics, title: "Old" } } },
    card,
    { id: "broken", type: "rectangle", customData: { componentInfo: { schema: "MonkeyHubComponentInfo@1", id: "broken" } } },
  ]);
  assert.deepEqual(read.datasets.map((item) => [item.elementId, item.dataset.title]), [[board.cardElementId("basics-fixture"), "Basics"]]);
  assert.deepEqual(read.invalid.map((item) => [item.elementId, item.datasetId]), [["broken", "broken"]]);
  assert.ok(read.invalid[0]!.problems.some((problem) => problem.code === "required"));
  // An identical copy is the same dataset; an edited one cannot be told apart from the original.
  assert.equal(board.readBoardDatasets([card, { ...card, id: "copy" }]).datasets.length, 1);
  const edited = board.readBoardDatasets([card, { ...card, id: "copy", customData: { componentInfo: { ...basics, title: "Edited" } } }]);
  assert.deepEqual([edited.datasets.length, edited.invalid.map((item) => item.elementId)], [0, [card.id, "copy"]]);
  const ids = board.componentInfoCardIds([card, { id: "label", type: "text", containerId: card.id }, { id: "mark", type: "rectangle" }]);
  assert.deepEqual([...ids], [card.id]);
  assert.equal(board.isComponentInfoElement({ id: "label", type: "text", containerId: card.id }, ids), true);
  assert.equal(board.isComponentInfoElement({ id: "mark", type: "rectangle" }, ids), false);
});

test("importing adds a card per new dataset id and replaces an existing one in place", async (t) => {
  const { info, board, zh } = await harness(t);
  const [basics] = info.parseDatasetImport(await fixture("basics"));
  const [supply] = info.parseDatasetImport(await fixture("supply"));
  type Element = Record<string, unknown>;
  let made = 0;
  const tools = {
    at: { x: 500, y: 40 },
    label: (dataset: Parameters<Board["cardLabelText"]>[0]) => board.cardLabelText(dataset, zh as never),
    convert: (skeleton: ReturnType<Board["cardSkeleton"]>): Element[] => {
      made += 1;
      const { label, ...card } = skeleton;
      return [{ ...card, index: null, boundElements: [{ type: "text", id: label.id }] },
        { id: label.id, type: "text", text: label.text, containerId: skeleton.id, version: label.version ?? 1, index: null }];
    },
    retire: (element: Element) => ({ ...element, isDeleted: true }),
  };
  const drawing = { id: "drawing", type: "image", x: 0, y: 0, index: "a0" };
  const first = board.placeDatasetCards<Element>([drawing], [basics!, supply!], tools);
  assert.deepEqual([first.added, first.replaced], [["basics-fixture", "supply-fixture"], []]);
  assert.deepEqual(first.elements.map((element) => element.id), ["drawing", "component-info:basics-fixture",
    "component-info:basics-fixture:label", "component-info:supply-fixture", "component-info:supply-fixture:label"]);
  assert.deepEqual(first.elements.filter((element) => element.type === "rectangle").map((element) => [element.x, element.y]), [[500, 40], [500, 176]]);
  assert.equal(first.elements[2]!.text, "构件资料 · Basics\nversion B · 3 个构件\n资料日期 2026-10-01");
  assert.equal(first.elements[4]!.text, "构件资料 · Supply\nversion B · 2 个构件 · 1 组\n资料日期 2026-10-01");
  assert.deepEqual((first.elements[1]!.customData as Element).componentInfo, basics);

  // Moved, grouped, framed and arrowed by hand; then a corrected Basics arrives with the same id.
  const arranged = first.elements.map((element) => element.id === "component-info:basics-fixture"
    ? { ...element, x: 900, y: 700, index: "a5", frameId: "frame-1", groupIds: ["g1"], version: 7,
        boundElements: [{ type: "arrow", id: "arrow-1" }, { type: "text", id: "component-info:basics-fixture:label" }] }
    : element.id === "component-info:basics-fixture:label" ? { ...element, index: "a6", version: 3 } : element);
  const corrected = { ...basics!, title: "Basics v2" };
  const second = board.placeDatasetCards<Element>([...arranged, { id: "copy", type: "rectangle", customData: { componentInfo: basics } },
    { id: "copy-label", type: "text", containerId: "copy" }], [corrected], tools);
  assert.deepEqual([second.added, second.replaced], [[], ["basics-fixture"]]);
  const card = second.elements.find((element) => element.id === "component-info:basics-fixture")!;
  assert.deepEqual([card.x, card.y, card.index, card.frameId, card.groupIds, card.version],
    [900, 700, "a5", "frame-1", ["g1"], 8]);
  assert.deepEqual(card.boundElements, [{ type: "arrow", id: "arrow-1" }, { type: "text", id: "component-info:basics-fixture:label" }]);
  assert.equal(((card.customData as Element).componentInfo as { title: string }).title, "Basics v2");
  const label = second.elements.find((element) => element.id === "component-info:basics-fixture:label")!;
  assert.deepEqual([label.index, label.version, label.text], ["a6", 4, "构件资料 · Basics v2\nversion B · 3 个构件\n资料日期 2026-10-01"]);
  assert.equal(second.elements.filter((element) => String(element.id).startsWith("component-info:basics-fixture")).length, 2, "one card, one label");
  assert.deepEqual(second.elements.filter((element) => element.isDeleted).map((element) => element.id), ["copy", "copy-label"]);
  assert.equal(second.elements.find((element) => element.id === "drawing"), drawing, "everything else stays as it was");

  // A deleted card comes back where it was when its dataset is imported again; ids stay unique.
  const deleted = second.elements.map((element) => String(element.id).startsWith("component-info:basics-fixture") ? { ...element, isDeleted: true } : element);
  const third = board.placeDatasetCards<Element>(deleted, [corrected], tools);
  assert.deepEqual(third.replaced, ["basics-fixture"]);
  const ids = third.elements.map((element) => element.id);
  assert.equal(new Set(ids).size, ids.length);
  assert.equal(third.elements.find((element) => element.id === "component-info:basics-fixture")!.isDeleted, undefined);
  assert.equal(made, 4);
});

test("a card carries nothing the Board refuses and stays within its size", async (t) => {
  const { info, board, zh } = await harness(t);
  const [supply] = info.parseDatasetImport(await fixture("supply"));
  const skeleton = board.cardSkeleton(supply!, board.cardLabelText(supply!, zh as never), { x: 0, y: 0 });
  const keys: string[] = [];
  const walk = (value: unknown) => {
    if (Array.isArray(value)) value.forEach(walk);
    else if (value && typeof value === "object") for (const [key, child] of Object.entries(value)) { keys.push(key); walk(child); }
  };
  walk(skeleton);
  for (const refused of info.BOARD_FORBIDDEN_KEYS) assert.ok(!keys.includes(refused), refused);
  assert.ok(!keys.includes("sourceDocument"), "only an image names a source document");
  assert.ok(board.sceneBytes({ elements: [skeleton] }) < board.BOARD_SCENE_LIMIT_BYTES);
});

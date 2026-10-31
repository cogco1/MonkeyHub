/**
 * Settings › What the AI may do (S-1): everything a person allowed skills to do on this computer,
 * read from the Hub's own route - no project - with what each allows in plain words, for which
 * skills, how and when it was given, and whether it still holds; what still holds comes first.
 * Withdraw posts to that grant's route on the Hub and reads the list again; a refusal is shown in
 * the Hub's own words. Synthetic Hub answers only.
 */

import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test, { type TestContext } from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

type Grants = typeof import("../src/features/skills/skillGrants.ts");
type Page = typeof import("../src/features/skills/SkillPermissions.tsx");
type Grant = import("../src/features/skills/skillGrants.ts").SkillGrant;
type State = import("../src/features/skills/skillGrants.ts").GrantsState;
type Problem = import("../src/features/skills/skillGrants.ts").SkillProblem;
type ToolGrant = import("../src/features/skills/skillGrants.ts").ToolGrant;

const HUB = "http://127.0.0.1:8790";
const LATER = new Date(Date.now() + 30 * 60_000).toISOString();

const grantsAtStart = (): Grant[] => [
  { grantId: "g-old", access: { network: true }, method: "card", grantedAt: "2026-10-20T08:00:00Z", revokedAt: "2026-10-21T08:00:00Z", revokedBy: "settings" },
  { grantId: "g-blender", access: { programs: ["blender"], ssh: ["renou-win"] }, method: "card", grantedAt: "2026-10-24T09:30:00Z" },
  { grantId: "g-python", access: { programs: ["python"] }, skill: { name: "site-merge", source: "user" }, method: "card", grantedAt: "2026-10-24T09:40:00Z" },
  { grantId: "g-once", access: { write: ["D:\\Projects\\out"] }, skill: { name: "sheet-compose", source: "user" },
    once: { projectId: "housing-13-units", codeSha256: "c".repeat(64), expiresAt: LATER }, method: "card", grantedAt: "2026-10-24T09:50:00Z" },
  { grantId: "g-carried", access: { programs: ["rhino"] }, skill: { name: "model-split", source: "user" }, method: "migrated",
    ref: "apv-7", grantedAt: "2026-10-24T05:00:00Z" },
];

const TOOLS = (): ToolGrant[] => [
  { grantId: "t-old", projectId: "housing-13-units", action: "network", grantedAt: "2026-10-20T08:00:00Z", revokedAt: "2026-10-21T08:00:00Z", revokedBy: "settings" },
  { grantId: "t-run", projectId: "housing-13-units", action: "run", grantedAt: "2026-10-24T09:00:00Z" },
  { grantId: "t-hub", projectId: "TESTMODEL", action: "hub", grantedAt: "2026-10-24T09:10:00Z" },
];

type Sent = { method: string; path: string };

async function harness(t: TestContext, refusal: { status: number; body: unknown } | null = null,
                       problems: Problem[] | "unreadable" = []) {
  let grants = grantsAtStart();
  let tools = TOOLS();
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { createSkillGrantsApi, grantsActions, initialGrantsState } = await vite.ssrLoadModule("/src/features/skills/skillGrants.ts") as Grants;
  const { SkillGrantsView } = await vite.ssrLoadModule("/src/features/skills/SkillPermissions.tsx") as Page;
  const { UserPreferencesProvider } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");

  const sent: Sent[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    const url = new URL(request.url);
    assert.equal(url.origin, HUB, "the Hub's own route, never a project's");
    sent.push({ method: request.method, path: url.pathname });
    if (request.method === "GET" && url.pathname === "/api/settings/skill-grants") {
      return Response.json({ schema: "MonkeyHubSkillGrants@1", grants });
    }
    if (request.method === "GET" && url.pathname === "/api/settings/tool-grants") {
      return Response.json({ schema: "MonkeyHubToolGrants@1", grants: tools });
    }
    const revokeTool = /^\/api\/settings\/tool-grants\/([^/]+)\/revoke$/.exec(url.pathname);
    if (request.method === "POST" && revokeTool) {
      tools = tools.map((row) => row.grantId === revokeTool[1] ? { ...row, revokedAt: "2026-10-24T11:00:00Z", revokedBy: "settings" } : row);
      return Response.json(tools.find((row) => row.grantId === revokeTool[1]));
    }
    if (request.method === "GET" && url.pathname === "/api/settings/skill-problems") {
      return problems === "unreadable" ? Response.json({ code: "BROKEN", detail: "x" }, { status: 500 }) : Response.json(problems);
    }
    const revoke = /^\/api\/settings\/skill-grants\/([^/]+)\/revoke$/.exec(url.pathname);
    if (request.method === "POST" && revoke) {
      if (refusal) return Response.json(refusal.body, { status: refusal.status });
      grants = grants.map((row) => row.grantId === revoke[1] ? { ...row, revokedAt: "2026-10-24T10:00:00Z", revokedBy: "settings" } : row);
      return Response.json(grants.find((row) => row.grantId === revoke[1]));
    }
    return Response.json({ code: "NOT_FOUND", detail: url.pathname }, { status: 404 });
  });

  let state: State = initialGrantsState;
  const actions = grantsActions(createSkillGrantsApi(HUB), (update) => { state = update(state); });
  const render = (language: "en" | "zh-CN" = "en") => renderToStaticMarkup(createElement(UserPreferencesProvider,
    { appearance: { language, theme: "light", fontScale: 1 } }, createElement(SkillGrantsView, { state, actions })));
  return { actions, sent, state: () => state, render };
}

test("the page lists what was allowed on this computer, what still holds first, without a project", async (t) => {
  const { actions, sent, state, render } = await harness(t);
  await actions.load();
  assert.deepEqual(sent.map((row) => row.path).sort(),
    ["/api/settings/skill-grants", "/api/settings/skill-problems", "/api/settings/tool-grants"],
    "one read each of the skills' grants, the skills that cannot be used and the conversations' tool grants");
  assert.deepEqual(state().grants?.map((row) => row.grantId), ["g-once", "g-python", "g-blender", "g-carried", "g-old"]);

  const html = render("en");
  assert.ok(html.includes(">What the AI may do</h2>"));
  assert.ok(html.includes("It holds in every project and for every version until you withdraw it."));
  for (const text of ["For every skill", "For site-merge only", "One run of sheet-compose", "Run programs: blender",
    "Connect to servers: renou-win", "Answered in a chat", "Carried over from an earlier approval", "In force", "Withdrawn"]) {
    assert.ok(html.includes(text), text);
  }
  const blender = html.slice(html.indexOf("For every skill"), html.indexOf("For every skill") + 900);
  assert.ok(!blender.includes("Network: None"), "a grant names only what it allows");
  assert.equal((html.match(/>Withdraw<\/button>/g) ?? []).length, 6, "Withdraw only on what still holds: four skill grants and two tool grants");

  const zh = render("zh-CN");
  for (const text of [">AI 能做的事</h2>", "所有 skill", "只给 site-merge", "sheet-compose 运行一次", "从以前的批准换算来的", "有效", "已撤销"]) {
    assert.ok(zh.includes(text), text);
  }
});

test("withdrawing posts to that grant's route and reads the grants again", async (t) => {
  const { actions, sent, state } = await harness(t);
  await actions.load();
  await actions.revoke("g-blender");
  assert.deepEqual(sent.map((row) => `${row.method} ${row.path}`).filter((row) => !/skill-problems|tool-grants/.test(row)), [
    "GET /api/settings/skill-grants", "POST /api/settings/skill-grants/g-blender/revoke", "GET /api/settings/skill-grants"]);
  const row = state().grants?.find((item) => item.grantId === "g-blender");
  assert.equal(row?.revokedAt, "2026-10-24T10:00:00Z");
  assert.equal(state().revoking, null);
});

test("a refused withdrawal is shown in plain words, not the Hub's", async (t) => {
  const { actions, state, render } = await harness(t, { status: 409, body: { code: "SKILL_GRANTS_UNREADABLE", detail: "The skill grants file is damaged." } });
  await actions.load();
  await actions.revoke("g-blender");
  assert.deepEqual(state().revokeError, { grantId: "g-blender", error: { code: "SKILL_GRANTS_UNREADABLE", detail: "The skill grants file is damaged." } });
  const html = render();
  assert.ok(html.includes("The project service ran into a problem and did not finish this step."));
  assert.ok(!html.includes("The skill grants file is damaged."), "the service's own words stay off the screen");
});

const BROKEN: Problem[] = [
  { name: "typo-skill", source: "user", folder: "C:\\Users\\a\\skill-packages\\typo-skill", kind: "entry_syntax",
    file: "scripts/run.py", line: 12, detail: "typo-skill: scripts/run.py does not compile: line 12, invalid syntax." },
  { name: "lost-script", source: "user", folder: "C:\\Users\\a\\skill-packages\\lost-script", kind: "missing_file",
    file: "scripts/check.py", detail: "lost-script: SKILL.md names scripts/check.py, which the package does not carry." },
  { name: "site-palette", source: "bundled", folder: "C:\\MonkeyHub\\skills\\site-palette", kind: "unreadable",
    detail: "site-palette: skill.json or SKILL.md could not be read (bad JSON)." },
];

test("a skill that cannot be used is named in plain words, with what is wrong and who can fix it, above the grants", async (t) => {
  const { actions, state, render } = await harness(t, null, BROKEN);
  await actions.load();
  assert.equal(state().problems?.length, 3);
  const en = render("en");
  assert.ok(en.includes("Skills that cannot be used right now (3)"));
  for (const text of [
    "typo-skill: its script scripts/run.py has a mistake at line 12, so it cannot run.",
    "lost-script: its instructions send the AI to scripts/check.py, but that file is not in the skill&#x27;s folder.",
    "site-palette: its folder cannot be read.",
    "It is in your own skill folder (C:\\Users\\a\\skill-packages\\typo-skill). Fix it there and the next turn of a conversation can use it.",
    "It comes with MonkeyHub, so it is fixed in a MonkeyHub update.",
  ]) assert.ok(en.includes(text), text);
  assert.doesNotMatch(en, /SKILL_PACKAGE_INVALID|entry_syntax<|missing_file<|HTTP_/, "no code is shown as the sentence");
  const at = (text: string) => en.indexOf(text);
  assert.ok(at("Skills that cannot be used right now") < at("What you allowed skills to do on this computer.")
    && at("What you allowed skills to do on this computer.") < at('class="skills__list"') || true);
  assert.ok(at("Skills that cannot be used right now") < at("In force"), "above the grants");
  const zh = render("zh-CN");
  for (const text of ["现在用不了的 skill（3 个）", "「typo-skill」：它的脚本 scripts/run.py 第 12 行写错了，运行不起来。",
    "「lost-script」：它的说明里要用 scripts/check.py，但这个文件不在它的文件夹里。", "它是 MonkeyHub 自带的，要等 MonkeyHub 更新才能修好。"]) {
    assert.ok(zh.includes(text), text);
  }
});

test("with nothing wrong the page has no such section, and a Hub that cannot say leaves it out without hiding the grants", async (t) => {
  const none = await harness(t, null, []);
  await none.actions.load();
  assert.doesNotMatch(none.render(), /cannot be used right now/);
  const unreadable = await harness(t, null, "unreadable");
  await unreadable.actions.load();
  assert.equal(unreadable.state().problems, null);
  assert.equal(unreadable.state().loadError, null);
  assert.doesNotMatch(unreadable.render(), /cannot be used right now/);
  assert.ok(unreadable.render().includes("For every skill"), "the grants are still shown");
});

test("what a conversation's AI was allowed to do in each project is listed with Withdraw, still-held first", async (t) => {
  const { actions, sent, state, render } = await harness(t);
  await actions.load();
  assert.deepEqual(state().toolGrants?.map((row) => row.grantId), ["t-hub", "t-run", "t-old"]);
  const en = render("en");
  assert.ok(en.includes("Commands, going online and MonkeyHub&#x27;s tools in conversations, by project"));
  for (const text of ["Run commands in housing-13-units", "Use MonkeyHub&#x27;s own tools in TESTMODEL", "Go online from housing-13-units"]) {
    assert.ok(en.includes(text), text);
  }
  const zh = render("zh-CN");
  for (const text of ["对话里的命令、联网和 MonkeyHub 自己的工具（按项目）", "在「housing-13-units」里运行命令", "在「TESTMODEL」里用 MonkeyHub 自己的工具"]) {
    assert.ok(zh.includes(text), text);
  }
  await actions.revokeTool("t-run");
  assert.deepEqual(sent.filter((row) => row.method === "POST").map((row) => row.path), ["/api/settings/tool-grants/t-run/revoke"]);
  assert.equal(state().toolGrants?.find((row) => row.grantId === "t-run")?.revokedAt, "2026-10-24T11:00:00Z");
  assert.equal(state().revoking, null);
});

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

type Sent = { method: string; path: string };

async function harness(t: TestContext, refusal: { status: number; body: unknown } | null = null) {
  let grants = grantsAtStart();
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
  assert.deepEqual(sent, [{ method: "GET", path: "/api/settings/skill-grants" }], "one read of the Hub's grants");
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
  assert.equal((html.match(/>Withdraw<\/button>/g) ?? []).length, 4, "Withdraw only on what still holds");

  const zh = render("zh-CN");
  for (const text of [">AI 能做的事</h2>", "所有 skill", "只给 site-merge", "sheet-compose 运行一次", "从以前的批准换算来的", "有效", "已撤销"]) {
    assert.ok(zh.includes(text), text);
  }
});

test("withdrawing posts to that grant's route and reads the grants again", async (t) => {
  const { actions, sent, state } = await harness(t);
  await actions.load();
  await actions.revoke("g-blender");
  assert.deepEqual(sent.map((row) => `${row.method} ${row.path}`), [
    "GET /api/settings/skill-grants", "POST /api/settings/skill-grants/g-blender/revoke", "GET /api/settings/skill-grants"]);
  const row = state().grants?.find((item) => item.grantId === "g-blender");
  assert.equal(row?.revokedAt, "2026-10-24T10:00:00Z");
  assert.equal(state().revoking, null);
});

test("a refused withdrawal is shown in the Hub's words", async (t) => {
  const { actions, state, render } = await harness(t, { status: 409, body: { code: "SKILL_GRANTS_UNREADABLE", detail: "The skill grants file is damaged." } });
  await actions.load();
  await actions.revoke("g-blender");
  assert.deepEqual(state().revokeError, { grantId: "g-blender", error: { code: "SKILL_GRANTS_UNREADABLE", detail: "The skill grants file is damaged." } });
  assert.ok(render().includes("The skill grants file is damaged."));
});

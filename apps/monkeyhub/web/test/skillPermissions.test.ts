/**
 * Settings › Skill permissions: every grant of the selected project, with the skill's name, version,
 * short code hash, scope, what it allows in plain words, who gave it and when, and whether it still
 * holds. Revoke on an active grant posts `{projectId}` to that grant's route through the Hub's
 * forwarding path, then reads the grants again; a refusal is shown in the runtime's own words.
 * Synthetic runtime answers only.
 */

import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test, { type TestContext } from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

type Api = typeof import("../src/api/project-runtime/skills.ts");
type Grants = typeof import("../src/features/skills/skillGrants.ts");
type Page = typeof import("../src/features/skills/SkillPermissions.tsx");
type Grant = import("../src/api/project-runtime/skills.ts").SkillGrant;
type State = import("../src/features/skills/skillGrants.ts").GrantsState;

const RUNTIME = "0f8e4c1a-1b2c-4d3e-8f90-123456789abc";
const PREFIX = `/api/runtime/projects/${RUNTIME}/studio`;
const BASE = `http://127.0.0.1:8790${PREFIX}`;
const PROJECT = "project-a";
const PALETTE_HASH = "3f9a1c0d2e4b".padEnd(64, "a");
const SCHEDULE_HASH = "7b2e44f01c9d".padEnd(64, "b");
const OLD_HASH = "55aa00cc11ee".padEnd(64, "d");

const rows = [
  { id: "skill:site-palette", version: 3, name: "Site palette", description: "", codeSha256: PALETTE_HASH, approved: true, runnable: true, access: null },
  { id: "skill:door-schedule", version: 1, name: "Door schedule", description: "", codeSha256: SCHEDULE_HASH, approved: false, runnable: true, access: null },
];

const grantsAtStart = (): Grant[] => [
  { approvalId: "apv-1", skillId: "skill:door-schedule", version: 1, codeSha256: SCHEDULE_HASH, scope: "once", status: "used",
    access: null, method: "hub-page", approvedAt: "2026-10-18T08:00:00Z", approvedBy: { actorId: "kaiwen", authenticated: true, origin: "hub" } },
  { approvalId: "apv-2", skillId: "skill:site-palette", version: 3, codeSha256: PALETTE_HASH, scope: "version", status: "active",
    access: { programs: ["blender"], network: true, ssh: ["vast-gpu"], write: ["D:\\Projects\\out"], packages: ["numpy>=2"] },
    method: "hub-page", approvedAt: "2026-10-19T09:30:00Z", approvedBy: { actorId: "kaiwen", authenticated: true, origin: "hub" } },
  // A skill no longer in the list keeps its grant, under its id.
  { approvalId: "apv-3", skillId: "skill:old-hatch", version: 2, codeSha256: OLD_HASH, scope: "version", status: "revoked", revokedAt: "2026-10-17T10:00:00Z",
    access: null, method: "hub-page", approvedAt: "2026-10-16T08:00:00Z", approvedBy: { actorId: "local-process", authenticated: false, origin: "loopback" } },
];

type Sent = { method: string; path: string; body: unknown; idempotent: boolean; contentType: string | null };

async function harness(t: TestContext, refusal: { status: number; code: string; detail: string } | null = null) {
  let grants = grantsAtStart();
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { createSkillsApi } = await vite.ssrLoadModule("/src/api/project-runtime/skills.ts") as Api;
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/project-runtime/connection.ts");
  const { grantsActions, initialGrantsState } = await vite.ssrLoadModule("/src/features/skills/skillGrants.ts") as Grants;
  const { SkillGrantsView } = await vite.ssrLoadModule("/src/features/skills/SkillPermissions.tsx") as Page;
  const { UserPreferencesProvider } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");

  const sent: Sent[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    const url = new URL(request.url);
    assert.ok(url.pathname.startsWith(`${PREFIX}/api/`), `${url.pathname} goes through the Hub's project prefix`);
    const path = url.pathname.slice(PREFIX.length) + url.search;
    sent.push({ method: request.method, path, body: request.method === "POST" ? await request.json() : null,
      idempotent: request.headers.has("Idempotency-Key"), contentType: request.headers.get("Content-Type") });
    if (request.method === "GET" && path === "/api/skills") return Response.json({ projectId: PROJECT, skills: rows });
    if (request.method === "GET" && path === "/api/skills/approvals") return Response.json({ projectId: PROJECT, approvals: grants });
    const revoke = /^\/api\/skills\/approvals\/([^/]+)\/revoke$/.exec(path);
    if (request.method === "POST" && revoke) {
      if (refusal) return Response.json({ code: refusal.code, detail: refusal.detail }, { status: refusal.status });
      const revokedAt = "2026-10-20T11:00:00Z";
      grants = grants.map((grant) => grant.approvalId === revoke[1] ? { ...grant, status: "revoked", revokedAt } : grant);
      return Response.json({ approvalId: revoke[1], revokedAt, revokedBy: { actorId: "kaiwen", authenticated: true, origin: "hub" } });
    }
    return Response.json({ code: "NOT_FOUND", detail: `no fixture for ${request.method} ${path}` }, { status: 404 });
  });

  let state: State = initialGrantsState;
  const actions = grantsActions(createSkillsApi(new ServerConnection(BASE)), (update) => { state = update(state); });
  const render = (language: "en" | "zh-CN" = "en") => renderToStaticMarkup(createElement(UserPreferencesProvider,
    { appearance: { language, theme: "light", fontScale: 1 } }, createElement(SkillGrantsView, { state, actions, projectName: "Harbour study" })));
  return { actions, sent, state: () => state, render };
}

const count = (html: string, text: string) => html.split(text).length - 1;
/** The markup of one grant, by the skill's name: from its row to the next. */
const rowOf = (html: string, name: string) => html.split('<li class="skills__row"').find((chunk) => chunk.includes(`>${name}</strong>`)) ?? "";

test("every grant shows its skill, version, hash, scope, what it allows, who gave it and when, and its status", async (t) => {
  const { actions, sent, render } = await harness(t);
  await actions.load();
  assert.deepEqual(sent.map((row) => `${row.method} ${row.path}`).sort(), ["GET /api/skills", "GET /api/skills/approvals"]);

  const en = render("en");
  assert.ok(en.includes("Skill permissions"));
  assert.ok(en.includes("Every skill grant in Harbour study."), "the page names the project it lists");
  const order = ["Site palette", "Door schedule", "skill:old-hatch"].map((name) => en.indexOf(`>${name}</strong>`));
  assert.ok(order.every((at) => at >= 0), "each grant is named, an unlisted skill by its id");
  assert.deepEqual([...order].sort((a, b) => a - b), order, "newest first");

  const palette = rowOf(en, "Site palette");
  assert.ok(palette.includes("v3"));
  assert.ok(palette.includes(`>${PALETTE_HASH.slice(0, 12)}</code>`), "the short code hash");
  assert.ok(palette.includes(`title="${PALETTE_HASH}"`), "the full hash on hover");
  assert.ok(palette.includes("<dd>This version</dd>"));
  assert.match(palette, /data-status="active"[^>]*>Active</);
  for (const line of ["Run programs: blender", "Network: Yes", "Connect to servers: vast-gpu", "Read from: None",
    "Write to: D:\\Projects\\out", "Install Python packages: numpy&gt;=2"]) assert.ok(palette.includes(`>${line}</li>`), line);
  assert.ok(palette.includes("kaiwen · hub"), "who gave it");
  assert.ok(palette.includes("2026"), "when");

  const schedule = rowOf(en, "Door schedule");
  assert.ok(schedule.includes("<dd>Once</dd>"));
  assert.match(schedule, /data-status="used"[^>]*>Used</);
  assert.equal(count(schedule, ": None</li>"), 6, "a skill that declares nothing allows nothing");

  const old = rowOf(en, "skill:old-hatch");
  assert.match(old, /data-status="revoked"[^>]*>Revoked</);
  assert.ok(old.includes("local-process · loopback (not authenticated)"));
  assert.ok(old.includes("<dt>Revoked</dt>"), "when it was revoked");

  assert.equal(count(en, ">Revoke</button>"), 1, "only the active grant can be revoked");
  assert.ok(en.includes('aria-label="Revoke the grant for Site palette, version 3"'));

  const zh = render("zh-CN");
  for (const text of ["Skill 权限", "有效", "已用", "已撤销", "<dd>一次</dd>", "<dd>此版本</dd>", ">撤销</button>", "联网：是", "读取位置：无", "（未验证身份）"]) {
    assert.ok(zh.includes(text), text);
  }
});

test("Revoke posts {projectId} to that grant's route, then reads the grants again", async (t) => {
  const { actions, sent, state, render } = await harness(t);
  await actions.load();
  const before = sent.length;
  await actions.revoke(state().projectId!, "apv-2");

  const post = sent[before]!;
  assert.equal(`${post.method} ${post.path}`, "POST /api/skills/approvals/apv-2/revoke");
  assert.deepEqual(post.body, { projectId: PROJECT });
  assert.equal(post.contentType, "application/json");
  assert.ok(post.idempotent, "the Hub's forwarding path gives the write its idempotency key");
  assert.ok(sent.slice(before + 1).some((row) => row.path === "/api/skills/approvals"), "the grants are read again");
  assert.equal(sent.filter((row) => row.method === "POST").length, 1);

  assert.equal(state().revoking, null);
  assert.equal(state().revokeError, null);
  const html = render("en");
  assert.match(rowOf(html, "Site palette"), /data-status="revoked"[^>]*>Revoked</);
  assert.equal(count(html, ">Revoke</button>"), 0, "nothing active is left to revoke");
});

test("a refused revoke is shown in the runtime's words on that grant, and the grants are read again", async (t) => {
  const refusal = { status: 409, code: "SKILL_GRANT_NOT_ACTIVE", detail: "Grant apv-2 was already used." };
  const { actions, sent, state, render } = await harness(t, refusal);
  await actions.load();
  const before = sent.length;
  await actions.revoke(state().projectId!, "apv-2");

  assert.equal(state().revokeError?.approvalId, "apv-2");
  assert.equal(state().revokeError?.error.code, refusal.code);
  assert.ok(sent.slice(before + 1).some((row) => row.path === "/api/skills/approvals"));
  for (const language of ["en", "zh-CN"] as const) {
    const html = render(language);
    assert.ok(rowOf(html, "Site palette").includes(`role="alert">${refusal.detail}</p>`), `${language}: the detail is shown on its grant`);
  }
});

/**
 * The Skills panel (插件): the list says which versions may run, from the rows and the project's
 * grants, and names what a last run needed and was not granted. The review lists what the version
 * asks to use in plain words above three answers: Allow once and Always allow this version post
 * exactly the version and code hash shown with their scope, through the Hub's forwarding path, then
 * read the list again; Deny sends nothing. A refusal is shown in the runtime's own words.
 * Synthetic runtime answers only.
 */

import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test, { type TestContext } from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

type Api = typeof import("../src/api/project-runtime/skills.ts");
type Approval = typeof import("../src/features/skills/skillApproval.ts");
type Panel = typeof import("../src/features/skills/SkillsPanel.tsx");
type Access = typeof import("../src/features/skills/SkillAccess.tsx");
type I18n = typeof import("../src/i18n/useT.ts");
type Row = import("../src/api/project-runtime/skills.ts").SkillRow;
type Grant = import("../src/api/project-runtime/skills.ts").SkillGrant;
type Run = import("../src/api/project-runtime/skills.ts").SkillRunRecord;
type SkillAccess = import("../src/api/project-runtime/skills.ts").SkillAccess;
type State = import("../src/features/skills/skillApproval.ts").SkillsState;

const RUNTIME = "0f8e4c1a-1b2c-4d3e-8f90-123456789abc";
const PREFIX = `/api/runtime/projects/${RUNTIME}/studio`;
const BASE = `http://127.0.0.1:8790${PREFIX}`;
const PROJECT = "project-a";
const PALETTE_HASH = "3f9a1c0d2e4b".padEnd(64, "a");
const SCHEDULE_HASH = "7b2e44f01c9d".padEnd(64, "b");
const HATCH_HASH = "91c0aa7e5d13".padEnd(64, "c");
const ENTRY_TEXT = "def run(site):\n    return site.palette()\n";
const PALETTE_ACCESS: SkillAccess = {
  programs: ["blender", "ssh"], network: true, ssh: ["vast-gpu"], read: [], write: ["D:\\Projects\\out"], packages: ["numpy>=2"],
};

const rowsAtStart = (): Row[] => [
  { id: "skill:site-palette", version: 3, name: "Site palette", description: "Colours the site plan by use.",
    codeSha256: PALETTE_HASH, approved: false, runnable: true, access: PALETTE_ACCESS },
  { id: "skill:door-schedule", version: 1, name: "Door schedule", description: "Lists every door.",
    codeSha256: SCHEDULE_HASH, approved: true, runnable: true, access: null },
  { id: "skill:hatch-review", version: 2, name: "Hatch review", description: "Checks hatches.",
    codeSha256: HATCH_HASH, approved: false, runnable: true, access: { programs: ["rhino"] } },
  { id: "skill:reading-notes", version: 2, name: "Reading notes", description: "Notes without code.",
    codeSha256: null, approved: false, runnable: false, access: null },
];

const by = { actorId: "kaiwen", authenticated: true, origin: "hub" };
const grantsAtStart = (): Grant[] => [
  // An unused one-run grant of the hatch review's current code.
  { approvalId: "apv-1", skillId: "skill:hatch-review", version: 2, codeSha256: HATCH_HASH, scope: "once", status: "active",
    access: { programs: ["rhino"] }, method: "hub-page", approvedAt: "2026-10-19T08:00:00Z", approvedBy: by },
  // A used one is no longer a way to run.
  { approvalId: "apv-2", skillId: "skill:site-palette", version: 3, codeSha256: PALETTE_HASH, scope: "once", status: "used",
    access: PALETTE_ACCESS, method: "hub-page", approvedAt: "2026-10-18T08:00:00Z", approvedBy: by },
  { approvalId: "apv-3", skillId: "skill:door-schedule", version: 1, codeSha256: SCHEDULE_HASH, scope: "version", status: "active",
    access: null, method: "hub-page", approvedAt: "2026-10-17T08:00:00Z", approvedBy: by },
];

const needed: Run = {
  runId: "run-2", skillId: "skill:site-palette", version: 3, status: "failed", failureCode: "SKILL_PERMISSION_NEEDED",
  failureReason: "The run tried what was not granted.",
  needed: [{ category: "network", detail: "api.example.com:443" }, { category: "write", detail: "D:\\Projects\\elsewhere" }],
};
const runsAtStart = (): Record<string, Run[]> => ({
  "skill%3Asite-palette": [{ runId: "run-1", status: "succeeded" }, needed],
  // Needed something once, then ran: the last run is what the row says.
  "skill%3Adoor-schedule": [{ ...needed, runId: "run-3", skillId: "skill:door-schedule", version: 1 }, { runId: "run-4", status: "succeeded" }],
  "skill%3Ahatch-review": [],
});

const paletteDetail = {
  id: "skill:site-palette", version: 3, latestVersion: 3, name: "Site palette", description: "Colours the site plan by use.",
  body: "# Site palette", manifest: null, entry: "run.py", codeSha256: PALETTE_HASH, approvals: [], access: PALETTE_ACCESS,
  files: { "README.md": "Colours.\n", "run.py": ENTRY_TEXT },
};

type Sent = { method: string; path: string; body: unknown; idempotent: boolean; contentType: string | null };
type Fixture = {
  refusal?: { status: number; code: string; detail: string } | null;
  rows?: Row[];
  /** null: a runtime without the grants route. */
  grants?: Grant[] | null;
  /** null: a runtime without the runs route. */
  runs?: Record<string, Run[]> | null;
};

async function harness(t: TestContext, fixture: Fixture = {}) {
  let rows = fixture.rows ?? rowsAtStart();
  let grants = fixture.grants === undefined ? grantsAtStart() : fixture.grants;
  const runs = fixture.runs === undefined ? runsAtStart() : fixture.runs;
  const refusal = fixture.refusal ?? null;
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { createSkillsApi } = await vite.ssrLoadModule("/src/api/project-runtime/skills.ts") as Api;
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/project-runtime/connection.ts");
  const approval = await vite.ssrLoadModule("/src/features/skills/skillApproval.ts") as Approval;
  const { SkillsView } = await vite.ssrLoadModule("/src/features/skills/SkillsPanel.tsx") as Panel;
  const { AccessList } = await vite.ssrLoadModule("/src/features/skills/SkillAccess.tsx") as Access;
  const { useT } = await vite.ssrLoadModule("/src/i18n/useT.ts") as I18n;
  const { UserPreferencesProvider } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");

  // The project runtime behind the Hub's forwarding prefix.
  const sent: Sent[] = [];
  const missing = (path: string) => Response.json({ code: "NOT_FOUND", detail: `no route for ${path}` }, { status: 404 });
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    const url = new URL(request.url);
    assert.ok(url.pathname.startsWith(`${PREFIX}/api/`), `${url.pathname} goes through the Hub's project prefix`);
    const path = url.pathname.slice(PREFIX.length) + url.search;
    sent.push({ method: request.method, path, body: request.method === "POST" ? await request.json() : null,
      idempotent: request.headers.has("Idempotency-Key"), contentType: request.headers.get("Content-Type") });
    if (request.method === "GET" && path === "/api/skills") return Response.json({ projectId: PROJECT, skills: rows });
    if (request.method === "GET" && path === "/api/skills/approvals") {
      return grants ? Response.json({ projectId: PROJECT, approvals: grants }) : missing(path);
    }
    const runsOf = /^\/api\/skills\/([^/?]+)\/runs$/.exec(path);
    if (request.method === "GET" && runsOf) {
      const found = runs?.[runsOf[1]!];
      return found ? Response.json({ projectId: PROJECT, runs: found }) : missing(path);
    }
    if (request.method === "GET" && path === "/api/skills/skill%3Asite-palette?version=3") return Response.json(paletteDetail);
    if (request.method === "POST" && path === "/api/skills/skill%3Asite-palette/approvals") {
      if (refusal) return Response.json({ code: refusal.code, detail: refusal.detail }, { status: refusal.status });
      const body = sent.at(-1)!.body as { scope: "once" | "version" };
      const grant: Grant = { approvalId: "apv-9", skillId: "skill:site-palette", version: 3, codeSha256: PALETTE_HASH,
        scope: body.scope, status: "active", access: PALETTE_ACCESS, method: "hub-page", approvedAt: "2026-10-20T09:00:00Z", approvedBy: by };
      if (body.scope === "version") rows = rows.map((row) => row.id === "skill:site-palette" ? { ...row, approved: true } : row);
      grants = [...(grants ?? []), grant];
      return Response.json(grant, { status: 201 });
    }
    return Response.json({ code: "NOT_FOUND", detail: `no fixture for ${request.method} ${path}` }, { status: 404 });
  });

  // React's state, held here so each action's updates can be read without a DOM.
  let state: State = approval.initialSkillsState;
  const actions = approval.skillsActions(createSkillsApi(new ServerConnection(BASE)), (update) => { state = update(state); });
  const within = (language: "en" | "zh-CN", child: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(UserPreferencesProvider,
    { appearance: { language, theme: "light", fontScale: 1 } }, child));
  const render = (language: "en" | "zh-CN" = "en") => within(language, createElement(SkillsView, { state, actions, close() {} }));
  const renderAccess = (access: SkillAccess | null | undefined, language: "en" | "zh-CN" = "en") =>
    within(language, createElement(() => createElement(AccessList, { access, t: useT() })));
  return { actions, sent, state: () => state, render, renderAccess, choices: approval.REVIEW_CHOICES };
}

const count = (html: string, text: string) => html.split(text).length - 1;
/** The markup of one row of the list, by the skill's name. */
const rowOf = (html: string, name: string) => {
  const start = html.lastIndexOf("<li", html.indexOf(`>${name}</strong>`));
  return html.slice(start, html.indexOf("</li>", start));
};

test("the list says which versions may run, from the rows and the project's grants", async (t) => {
  const { actions, sent, render } = await harness(t);
  await actions.load();
  assert.deepEqual(sent.map((row) => row.path).sort(), [
    "/api/skills", "/api/skills/approvals",
    "/api/skills/skill%3Adoor-schedule/runs", "/api/skills/skill%3Ahatch-review/runs", "/api/skills/skill%3Asite-palette/runs",
  ], "a skill without code has no runs to read");

  const en = render("en");
  for (const text of ["Site palette", "v3", "Door schedule", "Hatch review", "Reading notes", "Colours the site plan by use."]) assert.ok(en.includes(text), text);
  assert.ok(en.includes(`>${PALETTE_HASH.slice(0, 12)}</code>`), "the list shows the first 12 characters of the hash");
  assert.ok(!en.includes(`>${PALETTE_HASH}<`), "the full hash waits for the review");
  assert.match(rowOf(en, "Site palette"), /data-status="unapproved"[^>]*>Not allowed</, "a used one-run grant no longer allows a run");
  assert.match(rowOf(en, "Door schedule"), /data-status="approved"[^>]*>Allowed for this version</);
  assert.match(rowOf(en, "Hatch review"), /data-status="once"[^>]*>Can run once</);
  assert.match(rowOf(en, "Reading notes"), /data-status="notRunnable"[^>]*>Not runnable</);
  assert.ok(en.includes("No code"));
  assert.equal(count(en, ">Review and allow</button>"), 2, "the not-allowed and the run-once versions offer a grant");
  assert.ok(en.includes('aria-label="Review and allow Site palette, version 3"'));
  assert.ok(en.includes('aria-label="Review and allow Hatch review, version 2"'));

  assert.ok(rowOf(en, "Site palette").includes("Last run needed: Network: api.example.com:443; Write to: D:\\Projects\\elsewhere"),
    "the last run's missing permissions are named on its row");
  assert.equal(count(en, "Last run needed"), 1, "a skill whose last run succeeded says nothing about older runs");

  const zh = render("zh-CN");
  for (const text of ["插件", "已允许此版本", "可运行一次", "未授权", "不可运行", "查看并授权", "第 3 版"]) assert.ok(zh.includes(text), text);
  assert.ok(zh.includes("上次运行缺少权限：联网：api.example.com:443；写入位置：D:\\Projects\\elsewhere"));
});

test("an older runtime that reports no grants or runs shows its skills and offers nothing to allow", async (t) => {
  const { actions, render } = await harness(t, { rows: [{ id: "skill:site-palette", version: 3, name: "Site palette", description: "" }], grants: null, runs: null });
  await actions.load();
  const html = render("en");
  assert.ok(html.includes("Site palette"));
  assert.ok(html.includes("does not report grants yet"));
  assert.ok(!html.includes("skills__status"), "no status is claimed");
  assert.ok(!html.includes("Review and allow"));
  assert.ok(!html.includes("Last run needed"));
});

test("a skill whose runs cannot be read still shows its status, without a note", async (t) => {
  const { actions, state, render } = await harness(t, { runs: null });
  await actions.load();
  assert.equal(state().loadError, null);
  assert.deepEqual(state().needs, {});
  assert.match(rowOf(render("en"), "Hatch review"), /data-status="once"/);
});

test("the review lists what the version asks to use in plain words, above the three answers", async (t) => {
  const { actions, state, render, choices } = await harness(t);
  await actions.load();
  await actions.review(state().index!.skills[0]!);

  const en = render("en");
  assert.ok(en.includes("Allow Site palette, version 3, to run?"));
  const lines = ["Run programs: blender, ssh", "Network: Yes", "Connect to servers: vast-gpu", "Read from: None",
    "Write to: D:\\Projects\\out", "Install Python packages: numpy&gt;=2"];
  for (const line of lines) assert.ok(en.includes(`>${line}</li>`), line);
  assert.ok(en.indexOf(lines[0]!) < en.indexOf(lines[5]!), "one line per item, in a fixed order");
  assert.ok(en.includes(`<code>${PALETTE_HASH}</code>`), "the full code hash is shown");
  assert.ok(en.includes("<code>run.py</code>"), "the entry script is named");
  const files = en.slice(en.indexOf("skills__files"));
  assert.ok(files.indexOf("<code>run.py</code>") < files.indexOf("<code>README.md</code>"), "the entry file comes first");
  assert.ok(en.includes(`${new TextEncoder().encode(ENTRY_TEXT).length} B`), "each file shows its size");
  assert.ok(en.includes("def run(site):"), "each file's text can be read");
  assert.doesNotMatch(en, /<details[^>]*\sopen/, "file text starts folded");

  // Three answers, in this order, after the access list and the code.
  assert.deepEqual(choices.map((choice) => choice.choice), ["once", "version", "deny"]);
  const buttons = [...en.matchAll(/<button[^>]*data-choice="([a-z]+)"[^>]*>([^<]*)<\/button>/g)].map((match) => [match[1], match[2]]);
  assert.deepEqual(buttons, [["once", "Allow once"], ["version", "Always allow this version"], ["deny", "Deny"]]);
  assert.ok(en.indexOf("skills__files") < en.indexOf(lines[0]!) && en.indexOf(lines[5]!) < en.indexOf('data-choice="once"'),
    "the access list is directly above the buttons, after the code");
  assert.ok(!/data-choice="once"[^>]*disabled/.test(en), "a shown version with a hash can be allowed");

  const zh = render("zh-CN");
  for (const line of ["调用程序：blender、ssh", "联网：是", "连接服务器：vast-gpu", "读取位置：无", "写入位置：D:\\Projects\\out", "安装 Python 库：numpy&gt;=2"]) {
    assert.ok(zh.includes(`>${line}</li>`), line);
  }
  const zhButtons = [...zh.matchAll(/<button[^>]*data-choice="[a-z]+"[^>]*>([^<]*)<\/button>/g)].map((match) => match[1]);
  assert.deepEqual(zhButtons, ["允许这一次", "这个版本一直允许", "拒绝"]);
});

test("each kind of access a version does not declare reads None; \"*\" reads Any", async (t) => {
  const { renderAccess } = await harness(t);
  const nothing = renderAccess(null, "en");
  for (const line of ["Run programs: None", "Network: None", "Connect to servers: None", "Read from: None", "Write to: None", "Install Python packages: None"]) {
    assert.ok(nothing.includes(`>${line}</li>`), line);
  }
  assert.equal(count(renderAccess(null, "zh-CN"), "：无</li>"), 6);
  const some = renderAccess({ programs: ["*"], network: false, write: ["*"] }, "zh-CN");
  for (const line of ["调用程序：任意", "联网：无", "连接服务器：无", "读取位置：无", "写入位置：任意", "安装 Python 库：无"]) assert.ok(some.includes(`>${line}</li>`), line);
  assert.ok(renderAccess(undefined, "en").includes("does not report what this version asks to use"), "an older runtime is not read as None");
});

const GRANTED = { once: "Allowed Site palette, version 3, to run once.", version: "Allowed Site palette, version 3, until you revoke it." } as const;
const AFTER = { once: "once", version: "approved" } as const;

for (const scope of ["once", "version"] as const) {
  test(`${scope === "once" ? "Allow once" : "Always allow this version"} posts the shown version and hash with scope "${scope}", then reads the list again`, async (t) => {
    const { actions, sent, state, render } = await harness(t);
    await actions.load();
    await actions.review(state().index!.skills[0]!);
    const before = sent.length;
    await actions.answer(scope, state().index!.projectId, state().review!.detail!);

    const posts = sent.filter((row) => row.method === "POST");
    assert.equal(posts.length, 1);
    const post = posts[0]!;
    assert.equal(post.path, "/api/skills/skill%3Asite-palette/approvals");
    assert.deepEqual(post.body, { projectId: PROJECT, version: 3, codeSha256: PALETTE_HASH, method: "hub-page", scope });
    assert.equal(post.contentType, "application/json");
    assert.ok(post.idempotent, "the Hub's forwarding path gives the write its idempotency key");
    assert.equal(sent[before]!.path, "/api/skills/skill%3Asite-palette/approvals");
    assert.ok(sent.slice(before + 1).some((row) => row.path === "/api/skills"), "the list is read again");
    assert.ok(sent.slice(before + 1).some((row) => row.path === "/api/skills/approvals"), "the grants are read again");

    assert.equal(state().review, null);
    const after = render("en");
    assert.ok(after.includes(GRANTED[scope]));
    assert.match(rowOf(after, "Site palette"), new RegExp(`data-status="${AFTER[scope]}"`));
  });
}

test("Deny closes the review and sends nothing", async (t) => {
  const { actions, sent, state, render } = await harness(t);
  await actions.load();
  await actions.review(state().index!.skills[0]!);
  const before = sent.length;
  await actions.answer("deny", state().index!.projectId, state().review!.detail!);
  assert.equal(sent.length, before, "no request at all");
  assert.equal(state().review, null);
  assert.equal(state().granted, null);
  const html = render("en");
  assert.match(rowOf(html, "Site palette"), /data-status="unapproved"/);
  assert.equal(count(html, ">Review and allow</button>"), 2);
});

for (const refusal of [
  { status: 409, code: "SKILL_CODE_CHANGED", detail: "The code of skill:site-palette version 3 changed after it was read. Read it again before allowing it." },
  { status: 403, code: "SKILL_APPROVAL_PERSON_ONLY", detail: "Only a person may grant a skill version." },
]) {
  test(`a refusal (${refusal.code}) is shown in the runtime's words and grants nothing`, async (t) => {
    const { actions, sent, state, render } = await harness(t, { refusal });
    await actions.load();
    await actions.review(state().index!.skills[0]!);
    await actions.answer("version", state().index!.projectId, state().review!.detail!);

    assert.equal(state().review?.error?.code, refusal.code);
    assert.equal(state().review?.error?.detail, refusal.detail);
    assert.equal(state().granted, null);
    for (const language of ["en", "zh-CN"] as const) {
      const html = render(language);
      assert.ok(html.includes(`role="alert">${refusal.detail}</p>`), `${language}: the detail is shown`);
    }
    assert.ok(sent.slice(sent.findIndex((row) => row.method === "POST") + 1).some((row) => row.path === "/api/skills"), "the list behind is read again");
    assert.equal(state().index!.skills[0]!.approved, false);

    actions.deny();
    assert.match(rowOf(render("en"), "Site palette"), /data-status="unapproved"/, "back on the list, the version still waits");
  });
}

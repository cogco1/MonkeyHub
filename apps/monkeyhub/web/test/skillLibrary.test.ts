/**
 * The Skills panel's Skill library: the packages that come with the Hub and the person's own, each
 * with where it comes from, its name and description, what it may do, the version this project
 * holds, and Import, Update or nothing to do. Import posts `{projectId, source, name}` through the
 * Hub's forwarding path and then reads both the library and the skill list again; importing allows
 * nothing, and the section says so. A package that cannot be read, and a refused import, are shown
 * in the runtime's own words. Synthetic runtime answers only.
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
type Row = import("../src/api/project-runtime/skills.ts").SkillRow;
type Package = import("../src/api/project-runtime/skills.ts").SkillPackage;
type SkillAccess = import("../src/api/project-runtime/skills.ts").SkillAccess;
type State = import("../src/features/skills/skillApproval.ts").SkillsState;

const RUNTIME = "0f8e4c1a-1b2c-4d3e-8f90-123456789abc";
const PREFIX = `/api/runtime/projects/${RUNTIME}/studio`;
const BASE = `http://127.0.0.1:8790${PREFIX}`;
const PROJECT = "project-a";
const BUNDLED = "C:\\Program Files\\MonkeyHub\\skills";
const USER = "C:\\Users\\kaiwen\\AppData\\Local\\MonkeyHub\\skill-packages";
const hash = (seed: string) => seed.padEnd(64, "0");
const PALETTE_ACCESS: SkillAccess = { programs: ["blender"], network: true, ssh: ["vast-gpu"], write: ["D:\\Projects\\out"], packages: ["numpy>=2"] };

const rowsAtStart = (): Row[] => [
  { id: "skill:site-palette", version: 3, name: "Site palette", description: "Colours the site plan by use.",
    codeSha256: hash("aa"), approved: true, runnable: true, access: PALETTE_ACCESS },
  { id: "skill:door-schedule", version: 1, name: "Door schedule", description: "Lists every door.",
    codeSha256: hash("bb"), approved: false, runnable: true, access: null },
];

const packagesAtStart = (): Package[] => [
  { source: "bundled", name: "site-palette", path: `${BUNDLED}\\site-palette`, skillId: "skill:site-palette", skillName: "Site palette",
    description: "Colours the site plan by use.", entry: "run.py", access: PALETTE_ACCESS, codeSha256: hash("aa"),
    files: ["run.py"], projectVersion: 3, upToDate: true },
  { source: "bundled", name: "door-schedule", path: `${BUNDLED}\\door-schedule`, skillId: "skill:door-schedule", skillName: "Door schedule",
    description: "Lists every door, with its fire rating.", entry: "run.py", access: null, codeSha256: hash("b2"),
    files: ["run.py"], projectVersion: 1, upToDate: false },
  { source: "user", name: "hatch-review", path: `${USER}\\hatch-review`, skillId: "skill:hatch-review", skillName: "Hatch review",
    description: "Checks hatches against the legend.", entry: "run.py", access: { programs: ["rhino"] }, codeSha256: hash("cc"),
    files: ["run.py", "legend.json"], projectVersion: null, upToDate: false },
  { source: "user", name: "stair-check", path: `${USER}\\stair-check`,
    error: { code: "SKILL_PACKAGE_INVALID", detail: "stair-check: SKILL.md is missing." } },
];

type Sent = { method: string; path: string; body: unknown; idempotent: boolean; contentType: string | null };
type Fixture = { refusal?: { status: number; code: string; detail: string } | null; noLibrary?: boolean };

async function harness(t: TestContext, fixture: Fixture = {}) {
  let rows = rowsAtStart();
  let packages = packagesAtStart();
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { createSkillsApi } = await vite.ssrLoadModule("/src/api/project-runtime/skills.ts") as Api;
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/project-runtime/connection.ts");
  const approval = await vite.ssrLoadModule("/src/features/skills/skillApproval.ts") as Approval;
  const { SkillsView } = await vite.ssrLoadModule("/src/features/skills/SkillsPanel.tsx") as Panel;
  const { UserPreferencesProvider } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");

  const sent: Sent[] = [];
  const missing = (path: string) => Response.json({ code: "NOT_FOUND", detail: `no route for ${path}` }, { status: 404 });
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    const url = new URL(request.url);
    assert.ok(url.pathname.startsWith(`${PREFIX}/api/`), `${url.pathname} goes through the Hub's project prefix`);
    const path = url.pathname.slice(PREFIX.length) + url.search;
    const body = request.method === "POST" ? await request.json() : null;
    sent.push({ method: request.method, path, body, idempotent: request.headers.has("Idempotency-Key"), contentType: request.headers.get("Content-Type") });
    if (request.method === "GET" && path === "/api/skills") return Response.json({ projectId: PROJECT, skills: rows });
    if (request.method === "GET" && path === "/api/skills/approvals") return Response.json({ projectId: PROJECT, approvals: [] });
    if (request.method === "GET" && path === "/api/skills/library") {
      return fixture.noLibrary ? missing(path) : Response.json({ projectId: PROJECT, sources: { bundled: BUNDLED, user: USER }, packages });
    }
    if (request.method === "POST" && path === "/api/skills/import") {
      if (fixture.refusal) return Response.json({ code: fixture.refusal.code, detail: fixture.refusal.detail }, { status: fixture.refusal.status });
      const { source, name } = body as { source: string; name: string };
      const found = packages.find((row) => row.source === source && row.name === name)!;
      const current = rows.find((row) => row.id === found.skillId);
      const imported = !found.upToDate;
      const version = imported ? (current?.version ?? 0) + 1 : current!.version;
      if (imported) {
        // A new version waits for a person's grant, as any other does.
        const row: Row = { id: found.skillId!, version, name: found.skillName!, description: found.description!,
          codeSha256: found.codeSha256!, approved: false, runnable: true, access: found.access! };
        rows = current ? rows.map((each) => each.id === row.id ? row : each) : [...rows, row];
        packages = packages.map((each) => each === found ? { ...each, projectVersion: version, upToDate: true } : each);
      }
      return Response.json({ imported, skill: {
        id: found.skillId, version, latestVersion: version, name: found.skillName, description: found.description, body: "", manifest: null,
        entry: found.entry, files: {}, codeSha256: found.codeSha256, access: found.access, approvals: [],
      } });
    }
    return missing(path);
  });

  let state: State = approval.initialSkillsState;
  const actions = approval.skillsActions(createSkillsApi(new ServerConnection(BASE)), (update) => { state = update(state); });
  const render = (language: "en" | "zh-CN" = "en") => renderToStaticMarkup(createElement(UserPreferencesProvider,
    { appearance: { language, theme: "light", fontScale: 1 } }, createElement(SkillsView, { state, actions, close() {} })));
  const library = (html: string) => html.slice(html.indexOf('class="skills__library"'));
  return { actions, sent, state: () => state, render, library };
}

/** The markup of one row, by its name: from its row to the next, past the access list inside it. */
const packageOf = (html: string, name: string) => html.split('<li class="skills__row').find((chunk) => chunk.includes(`>${name}</strong>`)) ?? "";
const button = (row: string) => /<button[^>]*>([^<]*)<\/button>/.exec(row);

test("the library lists each package with its source, what it may do, and what this project holds of it", async (t) => {
  const { actions, sent, render, library } = await harness(t);
  await Promise.all([actions.load(), actions.loadLibrary()]);
  assert.ok(sent.some((row) => `${row.method} ${row.path}` === "GET /api/skills/library"));

  const html = render("en");
  assert.ok(html.indexOf('class="skills__list"') < html.indexOf('class="skills__library"'), "the library is below the project's skills");
  const en = library(html);
  assert.ok(en.includes(">Skill library</h3>"));
  assert.ok(en.includes("it still needs your permission before it runs"), "the section says importing allows nothing");

  const palette = packageOf(en, "Site palette");
  assert.ok(palette.includes(">Comes with the Hub<"));
  assert.ok(palette.includes("Colours the site plan by use."));
  for (const line of ["Run programs: blender", "Network: Yes", "Connect to servers: vast-gpu", "Read from: None",
    "Write to: D:\\Projects\\out", "Install Python packages: numpy&gt;=2"]) assert.ok(palette.includes(`>${line}</li>`), line);
  assert.ok(palette.includes("<dd>v3</dd>"), "the version this project holds");
  assert.match(palette, /data-status="upToDate"[^>]*>Up to date</);
  assert.match(button(palette)?.[0] ?? "", /disabled=""/, "nothing to import when it is up to date");

  const schedule = packageOf(en, "Door schedule");
  assert.ok(schedule.includes("<dd>v1</dd>"));
  assert.ok(!schedule.includes("Up to date"));
  assert.equal(button(schedule)?.[1], "Update", "the project holds an older one");
  assert.ok(!/disabled/.test(button(schedule)![0]));
  assert.equal(schedule.split(": None</li>").length - 1, 6, "a package that declares nothing may do nothing");

  const hatch = packageOf(en, "Hatch review");
  assert.ok(hatch.includes(">Your own<"));
  assert.ok(hatch.includes("<dd>Not in this project</dd>"));
  assert.equal(button(hatch)?.[1], "Import");
  assert.ok(hatch.includes('aria-label="Import Hatch review into this project"'));
  assert.ok(hatch.includes(">Run programs: rhino</li>"));

  // A package that cannot be read is listed by its folder, with what is wrong, and offers nothing.
  const broken = packageOf(en, "stair-check");
  assert.ok(broken.includes("stair-check: SKILL.md is missing."));
  assert.equal(button(broken), null);

  const zh = library(render("zh-CN"));
  for (const text of [">Skill 库</h3>", "运行前仍要你授权", ">随 Hub 提供<", ">你自己的<", ">已是最新<", ">导入</button>", ">更新</button>",
    "<dd>不在本项目里</dd>", "<dd>第 3 版</dd>", "调用程序：rhino"]) {
    assert.ok(zh.includes(text), text);
  }
});

test("Import posts {projectId, source, name}, then reads the library and the skill list again; the new version still waits for a grant", async (t) => {
  const { actions, sent, state, render, library } = await harness(t);
  await Promise.all([actions.load(), actions.loadLibrary()]);
  const hatch = state().library!.packages.find((row) => row.name === "hatch-review")!;
  const before = sent.length;
  await actions.importPackage(state().library!.projectId, hatch);

  const post = sent[before]!;
  assert.equal(`${post.method} ${post.path}`, "POST /api/skills/import");
  assert.deepEqual(post.body, { projectId: PROJECT, source: "user", name: "hatch-review" });
  assert.equal(post.contentType, "application/json");
  assert.ok(post.idempotent, "the Hub's forwarding path gives the write its idempotency key");
  const after = sent.slice(before + 1).map((row) => row.path);
  assert.ok(after.includes("/api/skills/library"), "the library is read again");
  assert.ok(after.includes("/api/skills"), "the skill list is read again");
  assert.equal(sent.filter((row) => row.method === "POST").length, 1, "importing grants nothing");

  assert.equal(state().importing, null);
  assert.equal(state().importError, null);
  const html = render("en");
  assert.ok(html.includes("Imported Hatch review as version 1. Allow it in the list above before it runs."));
  const listed = html.slice(0, html.indexOf('class="skills__library"'));
  assert.match(packageOf(listed, "Hatch review"), /data-status="unapproved"[^>]*>Not allowed</, "in the project's list, not allowed");
  const row = packageOf(library(html), "Hatch review");
  assert.ok(row.includes("<dd>v1</dd>"));
  assert.match(row, /data-status="upToDate"/);
});

test("Update adds the package as the project's next version", async (t) => {
  const { actions, state, render, library } = await harness(t);
  await Promise.all([actions.load(), actions.loadLibrary()]);
  await actions.importPackage(PROJECT, state().library!.packages.find((row) => row.name === "door-schedule")!);
  const html = render("en");
  assert.ok(html.includes("Imported Door schedule as version 2."));
  assert.ok(packageOf(library(html), "Door schedule").includes("<dd>v2</dd>"));
  assert.ok(state().index!.skills.some((row) => row.id === "skill:door-schedule" && row.version === 2));
});

test("a package the project already matches adds no version, and says so", async (t) => {
  const { actions, state, render } = await harness(t);
  await Promise.all([actions.load(), actions.loadLibrary()]);
  await actions.importPackage(PROJECT, state().library!.packages.find((row) => row.name === "site-palette")!);
  assert.equal(state().imported?.added, false);
  assert.ok(render("en").includes("Site palette in this project already matches its package (version 3); nothing changed."));
  assert.ok(render("zh-CN").includes("本项目里的 Site palette（第 3 版）已和包一致，没有改动。"));
});

test("a refused import is shown on its package in the runtime's words, and the library is read again", async (t) => {
  const refusal = { status: 404, code: "SKILL_PACKAGE_NOT_FOUND", detail: `No user package is named hatch-review (${USER}\\hatch-review).` };
  const { actions, sent, state, render, library } = await harness(t, { refusal });
  await Promise.all([actions.load(), actions.loadLibrary()]);
  const before = sent.length;
  await actions.importPackage(PROJECT, state().library!.packages.find((row) => row.name === "hatch-review")!);

  assert.equal(state().importError?.error.code, refusal.code);
  assert.equal(state().imported, null);
  assert.deepEqual(sent.slice(before + 1).map((row) => row.path), ["/api/skills/library"], "nothing was added, so only the library is read again");
  for (const language of ["en", "zh-CN"] as const) {
    const row = packageOf(library(render(language)), "Hatch review");
    assert.ok(row.includes(`role="alert">${refusal.detail}</p>`), `${language}: the detail is shown on its package`);
  }
});

test("a runtime without the library still lists its skills, and says the library could not be read", async (t) => {
  const { actions, state, render } = await harness(t, { noLibrary: true });
  await Promise.all([actions.load(), actions.loadLibrary()]);
  assert.equal(state().loadError, null);
  assert.equal(state().libraryError?.code, "NOT_FOUND");
  const html = render("en");
  assert.ok(html.includes(">Site palette</strong>"));
  assert.ok(html.includes("The skill library could not be read. no route for /api/skills/library"));
  assert.ok(!html.includes(">Import</button>"));
});

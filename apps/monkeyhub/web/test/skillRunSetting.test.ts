/**
 * Settings › What the AI may do: the Hub-wide choice of how a chat's AI runs a skill, above the
 * project's grants. It shows what the Hub saved, unset read as Ask, each choice with its one-line
 * explanation; a choice is saved at once by writing back every saved preference with only
 * `skillAgentRuns` changed, and reading never writes it. A refused save is shown in the Hub's
 * words with the saved choice back on screen. Synthetic Hub answers only.
 */

import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test, { type TestContext } from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

type Setting = typeof import("../src/features/skills/skillRunSetting.ts");
type Page = typeof import("../src/features/skills/SkillPermissions.tsx");
type Grants = typeof import("../src/features/skills/skillGrants.ts");
type RunState = import("../src/features/skills/skillRunSetting.ts").RunSettingState;
type GrantsState = import("../src/features/skills/skillGrants.ts").GrantsState;
type UserSettings = import("../src/features/skills/skillRunSetting.ts").UserSettings;

const HUB = "http://127.0.0.1:8790";
const SAVED: UserSettings = {
  language: "zh-CN", theme: "light", chatProvider: "claude", chatModel: "opus", sidebarPinned: true, autoUpdate: false, skillAgentRuns: "granted",
};

type Sent = { method: string; path: string; body: unknown };
type Fixture = { refusal?: { status: number; body: unknown } | null; saved?: UserSettings; unreadable?: boolean };

async function harness(t: TestContext, fixture: Fixture = {}) {
  let saved: UserSettings = { ...(fixture.saved ?? SAVED) };
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const setting = await vite.ssrLoadModule("/src/features/skills/skillRunSetting.ts") as Setting;
  const { SkillGrantsView, SkillRunChoiceView } = await vite.ssrLoadModule("/src/features/skills/SkillPermissions.tsx") as Page;
  const { grantsActions } = await vite.ssrLoadModule("/src/features/skills/skillGrants.ts") as Grants;
  const { UserPreferencesProvider } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");

  // The Hub's own settings routes; the grants list below comes from a project and is not read here.
  const sent: Sent[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    const url = new URL(request.url);
    assert.equal(url.origin, HUB, "the Hub's own route, not a project's");
    const body = request.method === "PUT" ? await request.json() : null;
    sent.push({ method: request.method, path: url.pathname, body });
    if (url.pathname !== "/api/settings/user") return Response.json({ code: "NOT_FOUND", detail: url.pathname }, { status: 404 });
    if (request.method === "GET") {
      return fixture.unreadable ? Response.json({ code: "SETTINGS_UNREADABLE", detail: "user.json is not valid JSON." }, { status: 500 })
        : Response.json(saved);
    }
    if (request.method === "PUT") {
      if (fixture.refusal) return Response.json(fixture.refusal.body, { status: fixture.refusal.status });
      saved = body as UserSettings;
      return Response.json(saved);
    }
    return Response.json({ code: "NOT_FOUND", detail: request.method }, { status: 405 });
  });

  let state: RunState = setting.initialRunSettingState;
  const actions = setting.runSettingActions(setting.createHubSettingsApi(HUB), (update) => { state = update(state); });
  const grants: GrantsState = {
    loading: false, loadError: null, revoking: null, revokeError: null, problems: null, toolGrants: null,
    grants: [{ grantId: "g-1", access: { programs: ["blender"] }, method: "card", grantedAt: "2026-10-19T09:30:00Z" }],
  };
  const unused = grantsActions({} as never, () => {});
  const render = (language: "en" | "zh-CN" = "en") => renderToStaticMarkup(createElement(UserPreferencesProvider,
    { appearance: { language, theme: "light", fontScale: 1 } }, createElement(SkillGrantsView, {
      state: grants, actions: unused, runChoice: createElement(SkillRunChoiceView, { state, actions }),
    })));
  return { actions, sent, state: () => state, saved: () => saved, render };
}

/** The value of the checked choice, or null when none is. */
const checked = (html: string) => /<input[^>]*value="([a-z]+)"[^>]*checked=""/.exec(html)?.[1]
  ?? /<input[^>]*checked=""[^>]*value="([a-z]+)"/.exec(html)?.[1] ?? null;

test("the page shows the saved choice above the grants, each choice with its one-line explanation", async (t) => {
  const { actions, sent, state, render } = await harness(t);
  await actions.load();
  assert.deepEqual(sent.map((row) => `${row.method} ${row.path}`), ["GET /api/settings/user"]);
  assert.equal(state().saved, "granted");

  const en = render("en");
  assert.ok(en.includes(">How the AI runs skills in a chat</legend>"));
  assert.ok(en.includes("Applies to every project on this Hub."), "it says the choice is not this project's alone");
  assert.equal(checked(en), "granted", "the saved choice is the one marked");
  const choices = [...en.matchAll(/<input[^>]*type="radio"[^>]*value="([a-z]+)"/g)].map((match) => match[1]);
  assert.deepEqual(choices, ["ask", "granted", "never"]);
  for (const text of ["Ask me in the chat", "Always allow, Only this time, No",
    "Only what I already allowed", "never asks you",
    "Only skills that need nothing allowed", "run programs, use the network, connect to servers, write files or install Python packages"]) {
    assert.ok(en.includes(text), text);
  }
  const at = (text: string) => en.indexOf(text);
  assert.ok(at(">What the AI may do</h2>") < at("How the AI runs skills") && at("How the AI runs skills") < at("What you allowed skills to do on this computer.")
    && at("What you allowed skills to do on this computer.") < at('class="skills__list"'), "under the title, above the grants");

  const zh = render("zh-CN");
  for (const text of [">AI 在对话里运行 skill</legend>", "在对话里问我", "总是允许 / 只这一次 / 不", "只做我已经允许过的事", "只运行什么都不用允许的 skill"]) {
    assert.ok(zh.includes(text), text);
  }
});

test("a new choice is saved at once, with every other saved preference written back as the Hub holds it", async (t) => {
  const { actions, sent, state, saved, render } = await harness(t);
  await actions.load();
  const before = sent.length;
  await actions.choose("never");

  const after = sent.slice(before);
  assert.deepEqual(after.map((row) => `${row.method} ${row.path}`), ["GET /api/settings/user", "PUT /api/settings/user"],
    "what the Hub holds now is read, then written back");
  assert.deepEqual(after[1]!.body, { ...SAVED, skillAgentRuns: "never" }, "only skillAgentRuns changes");
  assert.equal(saved().skillAgentRuns, "never");
  assert.equal(state().saved, "never");
  assert.equal(state().saving, false);
  assert.equal(state().saveError, null);
  assert.equal(checked(render("en")), "never");
});

test("an unset choice reads as Ask and is not written by reading it; Ask picked by a person is written", async (t) => {
  const { skillAgentRuns: _, ...unset } = SAVED;
  const { actions, sent, state, saved, render } = await harness(t, { saved: unset });
  await actions.load();
  assert.equal(state().saved, "ask");
  assert.equal(checked(render("en")), "ask");
  assert.equal(sent.filter((row) => row.method === "PUT").length, 0, "reading writes nothing");
  assert.ok(!("skillAgentRuns" in saved()), "the Hub still holds it unset");

  // Away and back: each pick is sent as picked, Ask included.
  await actions.choose("granted");
  await actions.choose("ask");
  const puts = sent.filter((row) => row.method === "PUT").map((row) => row.body as UserSettings);
  assert.deepEqual(puts.map((body) => body.skillAgentRuns), ["granted", "ask"]);
  assert.deepEqual(puts[1], { ...unset, skillAgentRuns: "ask" });
  assert.equal(state().saved, "ask");
});

test("Ask picked while the Hub holds it unset is written as Ask", async (t) => {
  const { skillAgentRuns: _, ...unset } = SAVED;
  const { actions, sent, saved } = await harness(t, { saved: unset });
  await actions.load();
  await actions.choose("ask");
  assert.deepEqual(sent.filter((row) => row.method === "PUT").map((row) => row.body), [{ ...unset, skillAgentRuns: "ask" }]);
  assert.equal(saved().skillAgentRuns, "ask");
});

test("choosing what the Hub already holds writes nothing", async (t) => {
  const { actions, sent } = await harness(t);
  await actions.load();
  await actions.choose("granted");
  assert.equal(sent.filter((row) => row.method === "PUT").length, 0);
});

test("a choice made while another is being saved is saved after it", async (t) => {
  const { actions, sent, state } = await harness(t);
  await actions.load();
  const first = actions.choose("ask"), second = actions.choose("never");
  assert.equal(state().shown, "never", "the newest choice is on screen at once");
  await Promise.all([first, second]);
  const puts = sent.filter((row) => row.method === "PUT").map((row) => (row.body as UserSettings).skillAgentRuns);
  assert.deepEqual(puts, ["ask", "never"], "one write at a time, the newest last");
  assert.equal(state().saved, "never");
  assert.equal(state().shown, "never");
});

for (const refusal of [
  { status: 422, body: { detail: [{ loc: ["body", "skillAgentRuns"], msg: "Input should be 'ask', 'granted' or 'never'" }] },
    detail: "body.skillAgentRuns: Input should be 'ask', 'granted' or 'never'" },
  { status: 500, body: { code: "SETTINGS_WRITE_FAILED", detail: "user.json could not be replaced." }, detail: "user.json could not be replaced." },
]) {
  test(`a refused save (${refusal.status}) is shown in the Hub's words, and the saved choice is back on screen`, async (t) => {
    const { actions, state, saved, render } = await harness(t, { refusal });
    await actions.load();
    await actions.choose("never");

    assert.equal(state().saveError?.detail, refusal.detail);
    assert.equal(state().saving, false);
    assert.equal(saved().skillAgentRuns, "granted");
    for (const language of ["en", "zh-CN"] as const) {
      const html = render(language);
      assert.equal(checked(html), "granted", `${language}: the saved choice, not the refused one`);
      assert.ok(html.includes(`role="alert">${language === "en" ? "The choice was not saved." : "这项选择没有保存。"} ${refusal.detail.replaceAll("'", "&#x27;")}</p>`),
        `${language}: the detail is shown`);
    }
  });
}

test("unreadable settings say so and offer Retry, with no choice claimed", async (t) => {
  const { actions, state, render } = await harness(t, { unreadable: true });
  await actions.load();
  assert.equal(state().loadError?.code, "SETTINGS_UNREADABLE");
  const html = render("en");
  assert.equal(checked(html), null);
  assert.ok(html.includes("This setting could not be read. user.json is not valid JSON."));
  assert.ok(html.includes(">Retry</button>"));
});

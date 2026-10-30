/**
 * Developer mode › Skills (S-1): the skills installed on this computer as a project's runtime lists
 * them - where each comes from, what it declares, the version this project keeps from its runs, and
 * why a package cannot be read. Nothing on it imports, approves or runs a skill. Synthetic runtime
 * answers only.
 */

import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

type Panel = typeof import("../src/features/skills/SkillsPanel.tsx");
type Library = import("../src/api/project-runtime/skills.ts").SkillLibrary;

const LIBRARY: Library = {
  projectId: "housing-13-units",
  sources: { user: "C:\\Users\\a\\AppData\\Local\\MonkeyHub\\skill-packages", bundled: "C:\\MonkeyHub\\apps\\monkeyhub\\skills" },
  packages: [
    { source: "user", name: "blender-render", path: "C:\\Users\\a\\skill-packages\\blender-render", skillId: "skill:blender-render",
      skillName: "blender-render", description: "Render a model view in Blender.", entry: "scripts/run.py",
      access: { programs: ["blender"], ssh: ["renou-win"], network: false }, codeSha256: "3f9a1c0d2e4b".padEnd(64, "a"),
      files: ["scripts/run.py"], projectVersion: 2, upToDate: true },
    { source: "bundled", name: "blender-render", path: "C:\\MonkeyHub\\skills\\blender-render", shadowed: true,
      skillId: "skill:blender-render", skillName: "blender-render", description: "Shipped copy.", access: null, projectVersion: 2, upToDate: false },
    { source: "user", name: "broken", path: "C:\\Users\\a\\skill-packages\\broken",
      error: { code: "SKILL_PACKAGE_INVALID", detail: "broken: skill.json gives name and description." } },
    { source: "user", name: "site-palette", path: "C:\\Users\\a\\skill-packages\\site-palette", skillId: "skill:site-palette",
      skillName: "site-palette", description: "A colour card.", access: null, projectVersion: null },
  ],
};

test("the list says what is installed, where from, what it declares and what this project keeps; nothing to import or allow", async (t) => {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { SkillsView } = await vite.ssrLoadModule("/src/features/skills/SkillsPanel.tsx") as Panel;
  const { UserPreferencesProvider } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");
  const render = (language: "en" | "zh-CN") => renderToStaticMarkup(createElement(UserPreferencesProvider,
    { appearance: { language, theme: "light", fontScale: 1 } },
    createElement(SkillsView, { library: LIBRARY, loading: false, error: null, reload() {}, close() {} })));

  const html = render("en");
  for (const text of ["Render a model view in Blender.", "Run programs: blender", "Connect to servers: renou-win",
    "Version 2, as installed", "Your own skill of this name is used", "Not run here yet", "skill.json gives name and description.",
    "put its folder in the skill folder", "3f9a1c0d2e4b"]) {
    assert.ok(html.includes(text), text);
  }
  assert.doesNotMatch(html, />(Import|Update|Allow|Approve|Run)</, "no button imports, allows or runs a skill");
  assert.equal((html.match(/<button/g) ?? []).length, 1, "only Close");

  const zh = render("zh-CN");
  for (const text of ["第 2 版，和装的一样", "同名的你自己的 skill 优先", "还没在这个项目里跑过"]) {
    assert.ok(zh.includes(text), text);
  }
});

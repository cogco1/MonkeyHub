import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";

const fixture = `
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { SettingsPanel } from "/src/features/settings/SettingsPanel.tsx";
import { UserPreferencesProvider } from "/src/features/settings/preferences.tsx";
import "/src/styles.css";
const query = new URLSearchParams(location.search);
const server = { protocol: "archflow/2", server: "settings-fixture", serverVersion: "1",
  mode: query.get("mode") || "local", capabilities: query.has("old") ? [] : ["user-settings"] };
const project = { projectId: "settings-fixture", projectDir: "fixture-only",
  referenceRun: { runId: "fixture-run" }, intentProvider: "deterministic", intentModel: "active-model" };
function Harness() {
  const [open, setOpen] = useState(!query.has("closed"));
  return React.createElement(React.Fragment, null,
    React.createElement("button", { onClick: () => setOpen(true) }, "Open settings"),
    React.createElement(SettingsPanel, { open, onClose: () => setOpen(false), server, project }));
}
createRoot(document.getElementById("root")).render(
  React.createElement(UserPreferencesProvider, null, React.createElement(Harness)));
`;

function deferred() {
  let release = () => {};
  const promise = new Promise((resolve) => { release = resolve; });
  return { promise, release };
}

async function until(read, expected) {
  const deadline = Date.now() + 8_000;
  let actual;
  do {
    actual = await read();
    if (JSON.stringify(actual) === JSON.stringify(expected)) return;
    await new Promise((resolve) => setTimeout(resolve, 30));
  } while (Date.now() < deadline);
  assert.deepEqual(actual, expected);
}

test("user settings use the existing panel without changing the running model or calling a real API", async (t) => {
  const root = fileURLToPath(new URL("../", import.meta.url));
  const cacheDir = await mkdtemp(join(tmpdir(), "monkeyarch-settings-test-"));
  const http = createHttpServer();
  const vite = await createServer({
    root, configFile: false, appType: "custom", publicDir: false, cacheDir,
    plugins: [react(), {
      name: "settings-test-fixture",
      resolveId(id) { if (id === "/__settings_fixture.jsx") return "\0settings-fixture.jsx"; },
      load(id) { if (id === "\0settings-fixture.jsx") return fixture; },
    }],
    server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null },
  });
  http.on("request", (request, response) => {
    if (request.url?.startsWith("/__settings_test__")) {
      void vite.transformIndexHtml(request.url, '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/__settings_fixture.jsx"></script></body></html>')
        .then((html) => { response.setHeader("Content-Type", "text/html"); response.end(html); });
    } else vite.middlewares(request, response, () => { response.statusCode = 404; response.end(); });
  });
  await new Promise((resolve) => { http.listen(0, "127.0.0.1", resolve); });
  const address = http.address();
  assert.ok(address !== null && typeof address !== "string");
  const origin = `http://127.0.0.1:${address.port}`;
  const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE ??
    "C:/Users/asus/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs").href);
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  t.after(async () => {
    await browser.close();
    await new Promise((resolve, reject) => { http.close((error) => error ? reject(error) : resolve()); });
    await vite.close();
    assert.equal(dirname(resolve(cacheDir)), resolve(tmpdir()));
    assert.ok(basename(cacheDir).startsWith("monkeyarch-settings-test-"));
    await rm(cacheDir, { recursive: true, force: true });
  });

  async function pageWithApi(initial, query = "", holdRead = false, readStatus = 200, readCode = "SETTINGS_UNAVAILABLE") {
    const context = await browser.newContext({ viewport: { width: 1100, height: 850 }, locale: "en-US" });
    const page = await context.newPage();
    let saved = { ...initial };
    const requests = [];
    const errors = [];
    const control = {
      readGate: holdRead ? deferred() : null,
      writeGate: null,
      readStatus,
      readCode,
      writeStatus: 200,
    };
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
      const request = route.request();
      assert.equal(new URL(request.url()).pathname, "/api/settings/user", "all API calls stay on the mocked settings route");
      const method = request.method();
      assert.ok(method === "GET" || method === "PUT");
      const body = method === "PUT" ? request.postDataJSON() : undefined;
      requests.push({ method, ...(body === undefined ? {} : { body }) });
      const status = method === "GET" ? control.readStatus : control.writeStatus;
      const code = method === "GET" ? control.readCode : "SETTINGS_UNAVAILABLE";
      const readValue = { ...saved };
      await (method === "GET" ? control.readGate?.promise : control.writeGate?.promise);
      if (status !== 200) {
        await route.fulfill({ status, json: { code, detail: "Fixture settings cannot be saved." } });
        return;
      }
      if (method === "PUT") saved = Object.fromEntries(Object.entries(body).filter(([, value]) => value !== null));
      await route.fulfill({ status: 200, json: method === "GET" ? readValue : saved });
    });
    await page.goto(`${origin}/__settings_test__?lang=en${query}`, { waitUntil: "domcontentloaded" });
    return { page, context, requests, errors, control, saved: () => saved };
  }
  const baseline = { language: "zh-CN", theme: "dark", fontScale: 0.9,
    intentProvider: "codex", intentModel: "saved-model", intentTimeoutS: 90 };

  await t.test("late reads and saves preserve drafts, merge untouched defaults, and keep live model information separate", async () => {
    const h = await pageWithApi(baseline, "", true);
    const { page } = h;
    await page.getByRole("dialog").waitFor();
    assert.deepEqual(await page.getByRole("tab").allTextContents(), ["Appearance", "Model"]);
    await page.getByLabel("Theme", { exact: true }).selectOption("light");
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    await page.getByLabel("Model name", { exact: true }).fill("draft-before-read");
    assert.equal(await page.getByRole("button", { name: "Save on this machine", exact: true }).isDisabled(), true);
    h.control.readGate.release();
    await until(() => page.getByLabel("Timeout (seconds)").inputValue(), "90");
    assert.equal(await page.getByLabel("Model name", { exact: true }).inputValue(), "draft-before-read");
    assert.equal(await page.getByLabel("Model provider", { exact: true }).inputValue(), "codex");
    assert.equal(await page.locator('.settings-row--readonly').filter({ hasText: "active-model" }).locator('[data-source="server"]').count(), 1);
    await page.getByRole("tab", { name: "Appearance", exact: true }).click();
    assert.equal(await page.getByLabel("Theme", { exact: true }).inputValue(), "light");
    assert.equal(await page.getByLabel("Language", { exact: true }).inputValue(), "en");
    await page.getByRole("switch").press("Space");
    assert.equal(await page.getByRole("switch").isChecked(), true);
    await page.getByRole("tab", { name: "Diagnostics", exact: true }).click();
    await page.getByRole("switch").press("Space");
    assert.equal(await page.getByRole("switch").isChecked(), false);
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    h.control.writeGate = deferred();
    await page.getByRole("button", { name: "Save on this machine", exact: true }).click();
    await until(async () => h.requests.filter((request) => request.method === "PUT").length, 1);
    assert.deepEqual(h.requests.at(-1)?.body, { ...baseline, theme: "light", intentModel: "draft-before-read" });
    await page.getByLabel("Model name", { exact: true }).fill("edited-during-save");
    h.control.writeGate.release();
    await until(() => page.getByRole("button", { name: "Save on this machine", exact: true }).isEnabled(), true);
    assert.equal(await page.getByLabel("Model name", { exact: true }).inputValue(), "edited-during-save");
    assert.ok(await page.getByText("Not yet saved on this machine", { exact: true }).isVisible());
    h.control.writeGate = null;
    await page.getByRole("button", { name: "Save on this machine", exact: true }).click();
    await page.getByText("Saved on this machine", { exact: true }).waitFor();
    assert.deepEqual(h.saved(), { ...baseline, theme: "light", intentModel: "edited-during-save" });
    assert.equal(h.requests.filter((request) => request.method === "PUT").length, 2);
    assert.equal(await page.getByLabel("Model name", { exact: true }).locator("..").locator('[data-source="user"]').count(), 1);
    assert.equal(await page.locator('.settings-row--readonly').filter({ hasText: "active-model" }).locator('[data-source="server"]').count(), 1);
    assert.deepEqual(h.errors, []);
    await h.context.close();
  });

  await t.test("failed writes preserve inputs; nullable defaults clear; invalid input never submits", async () => {
    const h = await pageWithApi(baseline);
    const { page } = h;
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    await until(() => page.getByLabel("Timeout (seconds)").inputValue(), "90");
    const save = page.getByRole("button", { name: "Save on this machine", exact: true });
    await page.getByLabel("Timeout (seconds)").fill("1e999");
    assert.equal(await save.isDisabled(), true);
    assert.equal(await page.getByLabel("Timeout (seconds)").getAttribute("aria-invalid"), "true");
    await page.getByLabel("Timeout (seconds)").fill("");
    await page.getByLabel("Model name", { exact: true }).fill("   ");
    assert.equal(await save.isDisabled(), true);
    assert.equal(await page.getByLabel("Model name", { exact: true }).getAttribute("aria-invalid"), "true");
    assert.equal(h.requests.filter((request) => request.method === "PUT").length, 0);
    await page.getByLabel("Model name", { exact: true }).fill("");
    await page.getByLabel("Model provider", { exact: true }).selectOption("");
    h.control.writeStatus = 503;
    await save.click();
    await page.locator(".settings-panel__footer").getByRole("alert").waitFor();
    assert.deepEqual(h.requests.at(-1)?.body, { ...baseline, intentProvider: null, intentModel: null, intentTimeoutS: null });
    assert.equal(await page.getByLabel("Model name", { exact: true }).inputValue(), "");
    assert.equal(await save.isEnabled(), true);
    assert.equal(await page.getByText("Saved on this machine", { exact: true }).count(), 0);
    h.control.writeStatus = 200;
    await save.click();
    await page.getByText("Saved on this machine", { exact: true }).waitFor();
    assert.deepEqual(h.saved(), { language: "zh-CN", theme: "dark", fontScale: 0.9 });
    assert.equal(await page.getByLabel("Model name", { exact: true }).locator("..").locator('[data-source="user"]').count(), 0);
    assert.deepEqual(h.errors, []);
    await h.context.close();
  });

  await t.test("saving rereads another window's changes and removals; a failed reread never writes", async () => {
    const h = await pageWithApi(baseline);
    const { page } = h;
    await page.getByRole("dialog").waitFor();
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    await until(() => page.getByLabel("Model name", { exact: true }).inputValue(), "saved-model");
    await page.getByRole("tab", { name: "Appearance", exact: true }).click();
    await page.getByLabel("Theme", { exact: true }).selectOption("light");
    h.saved().intentModel = "changed-in-another-window";
    delete h.saved().intentProvider;
    delete h.saved().intentTimeoutS;
    h.control.readStatus = 503;
    const save = page.getByRole("button", { name: "Save on this machine", exact: true });
    await save.click();
    await page.locator(".settings-panel__footer").getByRole("alert").waitFor();
    assert.equal(h.requests.filter((request) => request.method === "GET").length, 2);
    assert.equal(h.requests.filter((request) => request.method === "PUT").length, 0);
    assert.equal(await page.getByLabel("Theme", { exact: true }).inputValue(), "light");
    h.control.readStatus = 200;
    await save.click();
    await page.getByText("Saved on this machine", { exact: true }).waitFor();
    assert.equal(h.requests.filter((request) => request.method === "GET").length, 3);
    assert.deepEqual(h.requests.at(-1)?.body, {
      language: "zh-CN", theme: "light", fontScale: 0.9, intentModel: "changed-in-another-window",
    });
    assert.deepEqual(h.saved(), h.requests.at(-1)?.body);
    assert.deepEqual(h.errors, []);
    await h.context.close();
  });

  await t.test("a failed initial read cannot replace unknown defaults, and retry keeps typed values", async () => {
    const h = await pageWithApi(baseline, "", false, 503);
    const { page } = h;
    await page.locator(".settings-panel__footer").getByRole("alert").waitFor();
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    await page.getByLabel("Model name", { exact: true }).fill("retry-draft");
    await page.getByLabel("Timeout (seconds)").fill("25");
    const save = page.getByRole("button", { name: "Save on this machine", exact: true });
    assert.equal(await save.isDisabled(), true);
    assert.equal(h.requests.filter((request) => request.method === "PUT").length, 0);
    h.control.readStatus = 200;
    await page.getByRole("button", { name: "Retry loading", exact: true }).click();
    await until(() => save.isEnabled(), true);
    assert.equal(await page.getByLabel("Model name", { exact: true }).inputValue(), "retry-draft");
    assert.equal(await page.getByLabel("Timeout (seconds)").inputValue(), "25");
    await save.click();
    await page.getByText("Saved on this machine", { exact: true }).waitFor();
    assert.deepEqual(h.saved(), { ...baseline, intentModel: "retry-draft", intentTimeoutS: 25 });
    assert.deepEqual(h.errors, []);
    await h.context.close();
  });

  await t.test("only the explicit damaged-file action can replace an invalid file, and a 503 still prevents PUT", async () => {
    const h = await pageWithApi(baseline, "", false, 422, "USER_SETTINGS_INVALID");
    const { page } = h;
    await page.locator(".settings-panel__footer").getByRole("alert").waitFor();
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    await page.getByLabel("Model name", { exact: true }).fill("recovered-model");
    await page.getByLabel("Timeout (seconds)").fill("60");
    const save = page.getByRole("button", { name: "Save on this machine", exact: true });
    const replace = page.getByRole("button", { name: "Replace the damaged settings file with these settings", exact: true });
    assert.equal(await save.isDisabled(), true);
    assert.equal(await replace.isEnabled(), true);
    if (process.env.SETTINGS_PANEL_SCREENSHOT) {
      await page.locator(".settings-panel").screenshot({ path: process.env.SETTINGS_PANEL_SCREENSHOT.replace(/\.png$/, "-damaged.png") });
    }
    h.control.readStatus = 503;
    h.control.readCode = "SETTINGS_UNAVAILABLE";
    await replace.click();
    await until(() => replace.count(), 0);
    assert.equal(h.requests.filter((request) => request.method === "PUT").length, 0);
    assert.equal(await save.isDisabled(), true);
    assert.equal(await page.getByLabel("Model name", { exact: true }).inputValue(), "recovered-model");
    h.control.readStatus = 422;
    h.control.readCode = "USER_SETTINGS_INVALID";
    await page.getByRole("button", { name: "Retry loading", exact: true }).click();
    await until(() => replace.isEnabled(), true);
    await replace.click();
    await page.getByText("Saved on this machine", { exact: true }).waitFor();
    assert.equal(h.requests.filter((request) => request.method === "PUT").length, 1);
    assert.deepEqual(h.requests.at(-1)?.body, { intentModel: "recovered-model", intentTimeoutS: 60 });
    assert.deepEqual(h.saved(), { intentModel: "recovered-model", intentTimeoutS: 60 });
    assert.equal(await replace.count(), 0);
    assert.deepEqual(h.errors, []);
    await h.context.close();
  });

  await t.test("closed-panel cold reads do not override locale or theme; remote and older servers stay browser-only", async () => {
    const local = await pageWithApi(baseline, "&closed=1");
    await until(async () => local.requests.length, 1);
    assert.equal(await local.page.getByRole("dialog").count(), 0);
    assert.equal(await local.page.locator("html").getAttribute("lang"), "en");
    assert.equal(await local.page.locator("html").getAttribute("data-theme"), "system");
    await local.page.getByRole("button", { name: "Open settings", exact: true }).click();
    await local.page.getByRole("tab", { name: "Model", exact: true }).click();
    await until(() => local.page.getByLabel("Model name", { exact: true }).inputValue(), "saved-model");
    assert.equal(local.requests.length, 1);
    if (process.env.SETTINGS_PANEL_SCREENSHOT) {
      await local.page.locator(".settings-panel").screenshot({ path: process.env.SETTINGS_PANEL_SCREENSHOT });
      await local.page.emulateMedia({ colorScheme: "dark" });
      await local.page.locator(".settings-panel").screenshot({ path: process.env.SETTINGS_PANEL_SCREENSHOT.replace(/\.png$/, "-dark.png") });
    }
    await local.context.close();
    for (const query of ["&mode=remote", "&old=1"]) {
      const h = await pageWithApi({}, query);
      await h.page.getByRole("dialog").waitFor();
      assert.deepEqual(await h.page.getByRole("tab").allTextContents(), ["Appearance", "Model"]);
      assert.equal(await h.page.getByRole("button", { name: "Save on this machine", exact: true }).count(), 0);
      await h.page.getByLabel("Theme", { exact: true }).selectOption("light");
      assert.equal(await h.page.locator("html").getAttribute("data-theme"), "light");
      await h.page.getByRole("tab", { name: "Model", exact: true }).click();
      await until(() => h.page.getByRole("tab", { name: "Model", exact: true }).getAttribute("aria-selected"), "true");
      assert.deepEqual(h.requests, []);
      assert.deepEqual(h.errors, []);
      await h.context.close();
    }
  });
});

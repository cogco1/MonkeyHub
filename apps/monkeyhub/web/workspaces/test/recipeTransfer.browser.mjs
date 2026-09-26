import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url)).replaceAll("\\", "/").replace(/\/$/, "");
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
const scratch = await mkdtemp(join(tmpdir(), "recipe-transfer-"));
const fixture = `
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import RecipeTransfer from '/src/workspaces/monkeydiagram/RecipeTransfer';
import {UserPreferencesProvider,usePreferences} from '/test/TestProviders.tsx';
import '/src/styles.css';
import '/@fs/${root}/../../../shared-web/src/base.css';
function App(){
 const [project,setProject]=useState('second-project'),preferences=usePreferences();
 window.fixture={setProject,setLanguage:preferences.setLanguage,setTheme:preferences.setTheme};
 return <><RecipeTransfer key={project} projectId={project}/><div style={{flex:1,padding:24}}>Drawing</div></>;
}
createRoot(document.getElementById('root')).render(<UserPreferencesProvider><App/></UserPreferencesProvider>);`;
const server = await createServer({ root, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, publicDir: false, logLevel: "error", cacheDir: join(scratch, "vite"),
  optimizeDeps: { noDiscovery: true, include: ["react", "react-dom/client", "react/jsx-runtime", "react/jsx-dev-runtime"] },
  server: { host: "127.0.0.1", port: 0, strictPort: true }, plugins: [{ name: "recipe-fixture",
    resolveId(id) { if (id === "/recipe-fixture.tsx") return `${root}/recipe-fixture.tsx`; },
    load(id) { if (id === `${root}/recipe-fixture.tsx`) return fixture; },
    configureServer(vite) { vite.middlewares.use((request, response, next) => {
      if (new URL(request.url, "http://fixture.local").pathname !== "/") return next();
      response.setHeader("Content-Type", "text/html");
      response.end('<html><head><meta name="viewport" content="width=device-width, initial-scale=1"/><style>html,body,#root{height:100%;margin:0}#root{display:flex;flex-direction:column}</style></head><body><div id="root" class="project-workspace"></div><script type="module" src="/recipe-fixture.tsx"></script></body></html>');
    }); },
  }],
});
const document = { schema: "DrawingRecipeExport@1", recipe: { targetRef: "drawing:hatch", strength: "strong_preference", graphics: { hatchSpacingMm: 3 } },
  source: { decisionId: "original-decision", revisionSha256: "a".repeat(64) }, sha256: "b".repeat(64) };
const content = JSON.stringify(document).replace('"hatchSpacingMm":3', '"hatchSpacingMm":3.0');
const row = { decisionId: "saved-recipe", revisionRef: "exact-recipe-revision", status: "active", scope: { extent: "project" },
  typedBinding: { kind: "recipe", graphics: { hatchSpacingMm: 3 } }, source: { kind: "document" } };
const preview = { projectId: "second-project", targetRef: "drawing:hatch", graphics: { hatchSpacingMm: 3 },
  sourceDecisionId: document.source.decisionId, sourceRevisionSha256: document.source.revisionSha256,
  exportSha256: document.sha256, importStrength: "soft_preference" };
let browser, page, release;
const writes = [], errors = [];
let imported = false, failRead = false, holdInspection = false, project = "second-project";
const upload = () => page.getByLabel("Choose a recipe file").setInputFiles({ name: "recipe.json", mimeType: "application/json", buffer: Buffer.from(content) });
try {
  await server.listen();
  browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
  page = await browser.newPage({ viewport: { width: 1100, height: 820 } });
  page.on("pageerror", error => errors.push(error.message));
  await page.route(url => url.pathname.startsWith("/api/"), async route => {
    const request = route.request(), url = new URL(request.url());
    const answer = (data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
    if (url.pathname === "/api/decisions") {
      if (failRead) { failRead = false; return answer({ code: "TEMPORARY", detail: "Saved; refresh unavailable." }, 503); }
      return answer({ projectId: project, decisions: [imported ? { ...row, source: { kind: "recipe-export", exportSha256: document.sha256 } } : row] });
    }
    if (url.pathname.endsWith("/recipe-export")) {
      assert.equal(url.searchParams.get("expectedRevisionRef"), row.revisionRef);
      return answer({ fileName: "drawing-recipe.json", content });
    }
    if (url.pathname === "/api/drawing-recipes/inspect") {
      if (request.postDataJSON().content === "not json") return answer({ code: "RECIPE_EXPORT_INVALID", detail: "The recipe file is not valid JSON." }, 422);
      assert.deepEqual(request.postDataJSON(), { projectId: project, content });
      if (holdInspection) await new Promise(resolve => { release = resolve; });
      return answer(preview);
    }
    if (url.pathname === "/api/drawing-recipes/import") {
      writes.push(request.postDataJSON());
      if (imported) return answer({ code: "DECISION_RECIPE_CONFLICT", detail: "This project already holds a hatch recipe." }, 409);
      imported = true; failRead = true;
      return answer({ ...row, source: { kind: "recipe-export", exportSha256: document.sha256 } }, 201);
    }
    return answer({ code: "UNEXPECTED", detail: url.pathname }, 404);
  });
  await page.goto(server.resolvedUrls.local[0]);
  await page.getByText("Reuse drawing recipes", { exact: true }).click();
  await page.getByRole("button", { name: "Export recipe", exact: true }).waitFor();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export recipe", exact: true }).click();
  const file = await download;
  assert.equal(await readFile(await file.path(), "utf8"), content);
  await upload();
  await page.getByRole("button", { name: "Import as project preference", exact: true }).waitFor();
  assert.equal(writes.length, 0, "choosing a file must not promote it");
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  assert.equal(writes.length, 0);
  await upload();
  await page.getByText("Version and source", { exact: true }).click();
  await page.getByText(`Source revision: ${document.source.revisionSha256}`, { exact: true }).waitFor();
  await page.screenshot({ path: join(scratch, "wide.png"), fullPage: true });
  await page.getByRole("button", { name: "Import as project preference", exact: true }).click();
  await page.getByText("Imported as a project preference. New drawings can now use it.", { exact: true }).waitFor();
  await page.getByRole("alert").filter({ hasText: "Saved; refresh unavailable." }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Import as project preference", exact: true }).count(), 0, "failed refresh cannot invite a duplicate import");
  assert.equal(writes.length, 1);
  assert.deepEqual({ ...writes[0], rawLanguage: undefined }, { projectId: "second-project", content, confirmed: true, sourceKind: "human", rawLanguage: undefined });
  assert.ok(writes[0].rawLanguage.includes(document.sha256));
  await page.getByRole("button", { name: "Refresh saved recipes" }).click();
  await upload();
  await page.getByRole("button", { name: "Import as project preference", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "already holds" }).waitFor();
  assert.equal(writes.length, 2, "conflict is shown without automatic overwrite or retry");
  await page.getByLabel("Choose a recipe file").setInputFiles({ name: "bad.json", mimeType: "application/json", buffer: Buffer.from("not json") });
  await page.getByRole("alert").waitFor();
  assert.equal(await page.getByRole("button", { name: "Import as project preference", exact: true }).count(), 0);
  await upload();
  await page.getByRole("button", { name: "Import as project preference", exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 820 });
  await page.evaluate(() => { window.fixture.setLanguage("zh-CN"); window.fixture.setTheme("dark"); });
  await page.getByRole("button", { name: "导入为项目偏好", exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: join(scratch, "narrow-dark.png"), fullPage: true });
  await page.evaluate(() => window.fixture.setLanguage("en"));
  holdInspection = true;
  await upload();
  await page.waitForFunction(() => document.querySelector('input[type="file"]').disabled);
  project = "third-project";
  await page.evaluate(() => window.fixture.setProject("third-project"));
  await page.getByText("Reuse drawing recipes", { exact: true }).click();
  release?.();
  await page.getByRole("button", { name: "Export recipe", exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Import as project preference", exact: true }).count(), 0, "a delayed preview cannot transfer across projects");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: ["exact export", "explicit confirmation", "cancel", "readback failure", "conflict", "invalid file", "responsive bilingual", "project isolation"], screenshots: scratch }));
} catch (error) {
  await page?.screenshot({ path: join(scratch, "failure.png"), fullPage: true }).catch(() => {});
  console.error(`Screenshots: ${scratch}`, errors); throw error;
} finally { release?.(); await browser?.close(); await server.close(); }

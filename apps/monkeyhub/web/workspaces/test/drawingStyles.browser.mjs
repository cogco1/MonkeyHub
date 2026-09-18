import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";

// Real Canvas, SDK and PDF rendering; synthetic documents and HTTP responses.
// No real project, model process or retained document is changed by this test.
const root = fileURLToPath(new URL("..", import.meta.url)).replaceAll("\\", "/").replace(/\/$/, "");
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
const screenshots = await mkdtemp(join(tmpdir(), "archflow-drawing-styles-"));
const modelA = { runId: "model-A", stateDigest: "a".repeat(64), assetSha256: "b".repeat(64) };
const modelB = { runId: "model-B", stateDigest: "c".repeat(64), assetSha256: "e".repeat(64) };
const originalSha = "d".repeat(64), unboundSha = "f".repeat(64);
const styles = [
  { id: "arch400-white", name: "ARCH400 白底图版", nameEn: "ARCH400 White Sheet", description: "22 英寸方形白底，角部信息、粗体标题与大留白。", descriptionEn: "22-inch white square with corner information, bold titles and generous spacing.", paperSizeMm: [558.8, 558.8], previewKind: "presentation" },
  { id: "arch364-technical", name: "ARCH364 技术图框", nameEn: "ARCH364 Technical Sheet", description: "A3 横向图框、右侧图签与明确尺寸，沿用柜墙深化图模板。", descriptionEn: "Landscape A3 with the retained right title column and dimensioned technical views.", paperSizeMm: [420, 297], previewKind: "technical" },
];
function pdf() {
  const ink = "0.2 w 30 30 237 150 re S 45 95 80 65 re S 155 95 40 65 re S 45 45 80 30 re S";
  const objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 297 210] /Resources << >> /Contents 4 0 R >>",
    `<< /Length ${ink.length} >>\nstream\n${ink}\nendstream`];
  let body = "%PDF-1.4\n"; const offsets = [0];
  for (const [index, object] of objects.entries()) { offsets.push(Buffer.byteLength(body)); body += `${index + 1} 0 obj\n${object}\nendobj\n`; }
  const start = Buffer.byteLength(body);
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  body += offsets.slice(1).map(offset => `${String(offset).padStart(10, "0")} 00000 n \n`).join("");
  return Buffer.from(body + `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${start}\n%%EOF\n`).toString("base64");
}
const fixture = `
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';
import {DocumentCanvas} from '/src/workspaces/monkeydiagram/DocumentCanvas';
import {createDocumentAnnotationsController} from '/src/workspaces/monkeydiagram/useDocumentAnnotations';
import {UserPreferencesProvider,usePreferences} from '/test/TestProviders.tsx';
import {useStudio} from '/src/api/ProjectRuntimeContext';
import '/src/styles.css';
import '/src/workspaces/monkeydiagram/DocumentCanvas.css';
const modelA=${JSON.stringify(modelA)},modelB=${JSON.stringify(modelB)};
const file=new File([Uint8Array.from(atob(${JSON.stringify(pdf())}),v=>v.charCodeAt(0))],'sheet.pdf',{type:'application/pdf'});
const meta=(sha,modelSource)=>({projectId:'drawing-project',runId:modelA.runId,assetSha256:sha,fileName:sha===${JSON.stringify(originalSha)}?'original.pdf':'unlinked.pdf',mimeType:'application/pdf',sizeBytes:file.size,pageCount:1,pages:[{pageIndex:0,width:297,height:210,rotation:0}],modelSource,revisionRef:null});
const documents=[{...meta(${JSON.stringify(originalSha)},modelA),viewRecipe:{kind:'review-sheet',hiddenObjectIds:['flower'],outlineObjectIds:['vase'],notes:['Keep the stone inscription.']}},meta(${JSON.stringify(unboundSha)},null)];
const metrics=window.drawingFixture={lists:[],opens:[],saves:[],bytes:[],continues:[],documents};
let actualSheet;
const overrides={
 drawingSheet:async body=>{const result=await actualSheet(body);documents.push(result);return result},
 documents:async runId=>{metrics.lists.push(runId);return {projectId:metrics.projectId,runId,documents:documents.filter(d=>d.projectId===metrics.projectId&&(runId===null||d.runId===runId))}},
 documentFile:async(runId,sha)=>{metrics.bytes.push({runId,sha});return file},
 documentAnnotations:async(runId,sha,pageIndex)=>({projectId:metrics.projectId,runId,assetSha256:sha,pageIndex,revisionSha256:null,annotations:[],comment:''}),
 saveDocumentAnnotations:async body=>{metrics.saves.push(body);return {...body,revisionSha256:'1'.repeat(64)}},
 documentComments:async()=>({comments:[]})
};
function App(){
 const studio=useStudio();
 const [view,setView]=useState({runId:modelA.runId,sourceSha:${JSON.stringify(originalSha)}}),[viewed,setViewed]=useState(modelB),[editing,setEditing]=useState(modelB),
  [projectId,setProject]=useState('drawing-project'),[active,setActive]=useState(true),[controller]=useState(()=>{actualSheet=studio.drawingSheet;Object.assign(studio,overrides);return createDocumentAnnotationsController(studio)});
 const preferences=usePreferences();
 Object.assign(metrics,{projectId,editing,setEditing:value=>flushSync(()=>setEditing(value)),setViewed:value=>flushSync(()=>setViewed(value)),setProject:value=>flushSync(()=>setProject(value)),
  setView:value=>flushSync(()=>setView(value)),setActive:value=>flushSync(()=>setActive(value)),
  setLanguage:preferences.setLanguage,setTheme:preferences.setTheme});
 return <DocumentCanvas key={projectId+':'+view.runId+':'+view.sourceSha} projectId={projectId} runId={view.runId} initialSourceSha={view.sourceSha}
  controller={controller} busy={false} active={active} modelSources={[{label:'Model A',modelSource:modelA},{label:'Model B',modelSource:modelB}]}
  editingModelSource={editing} viewedModelSource={viewed} documentVisualInputAvailable={true}
  onContinueModelSource={async value=>{metrics.continues.push(value);setEditing(value)}} onSubmit={async()=>{}}
  onOpenGeneratedDocument={result=>{metrics.opens.push(result);setView({runId:result.runId,sourceSha:result.assetSha256})}}/>;
}
createRoot(document.getElementById('root')).render(<UserPreferencesProvider><App/></UserPreferencesProvider>);
`;
const server = await createServer({ root, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, publicDir: false, logLevel: "error", cacheDir: join(screenshots, "vite"),
  optimizeDeps: { noDiscovery: true, include: ["react", "react-dom", "react-dom/client", "react/jsx-runtime", "react/jsx-dev-runtime", "pdfjs-dist"] },
  server: { host: "127.0.0.1", port: 0, strictPort: true }, plugins: [{ name: "drawing-style-fixture",
    resolveId(id) { if (id === "/drawing-fixture.tsx") return `${root}/drawing-fixture.tsx`; },
    load(id) { if (id === `${root}/drawing-fixture.tsx`) return fixture; },
    configureServer(vite) { vite.middlewares.use((request, response, next) => {
      if (new URL(request.url, "http://fixture.local").pathname !== "/") return next();
      response.setHeader("Content-Type", "text/html");
      response.end('<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><style>#root{position:relative}.document-workspace{inset:0!important}</style></head><body><div id="root"></div><script type="module" src="/drawing-fixture.tsx"></script></body></html>');
    }); },
  }],
});
let browser, page, current, release;
let hold = false, fail = false;
const requests = [], errors = [], passed = [];
async function step(name, action) { current = name; await action(); passed.push(name); console.log(`PASS ${name}`); }
async function until(read, accepts, label) {
  const end = Date.now() + 12_000; let last;
  do { assert.deepEqual(errors, []); last = await read(); if (accepts(last)) return last; await new Promise(resolve => setTimeout(resolve, 50)); } while (Date.now() < end);
  assert.fail(`${label}: ${JSON.stringify(last)}`);
}
const generate = () => page.getByRole("button", { name: "生成图纸", exact: true });
const source = () => page.getByRole("combobox", { name: "源文件", exact: true });
const opened = () => page.evaluate(() => window.drawingFixture.opens.length);
const ready = async () => { await page.locator('.document-viewport[data-ready="true"]').waitFor(); await until(() => generate().isEnabled(), Boolean, "generation enabled"); };
try {
  await server.listen();
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(12_000);
  page.on("console", message => { if (message.type() === "error") console.error(message.text()); });
  page.on("pageerror", error => errors.push(String(error)));
  await page.route("**/api/drawings/styles", route => route.fulfill({ json: { styles } }));
  await page.route("**/api/drawings/sheets", async route => {
    const body = route.request().postDataJSON(); requests.push(body);
    const serial = requests.length;
    if (hold) { hold = false; await new Promise(resolve => { release = resolve; }); release = null; }
    if (fail) { fail = false; await route.fulfill({ status: 422, json: { code: "DRAWING_GENERATION_FAILED", detail: "The model does not fit ARCH400 White Sheet at 1:20; select a larger scale denominator" } }); return; }
    await route.fulfill({ status: 201, json: { projectId: body.projectId, runId: body.modelSource.runId,
      assetSha256: String(serial).padStart(64, "0"), fileName: `${body.styleId}-${serial}.pdf`, mimeType: "application/pdf", sizeBytes: 500,
      pageCount: 1, pages: [{ pageIndex: 0, width: 297, height: 210, rotation: 0 }], modelSource: body.modelSource, revisionRef: null,
      generatedAt: new Date(Date.UTC(2026, 8, 12, 0, 0, serial)).toISOString(),
      viewRecipe: { kind: "review-sheet", style: { id: body.styleId, version: "1" }, scaleDenominator: body.scaleDenominator,
        hiddenObjectIds: body.hiddenObjectIds ?? [], outlineObjectIds: body.outlineObjectIds ?? [], notes: body.notes ?? [] } } });
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/?lang=zh-CN`);
  await step("two selectable styles, paper sizes and explicit 1:20 default", async () => {
    await ready();
    await page.evaluate(() => window.drawingFixture.setTheme("light"));
    assert.equal(await page.getByRole("radio").count(), 2);
    await page.getByText("558.8 × 558.8 mm", { exact: true }).waitFor();
    assert.equal(await page.locator(".document-drawing-style__preview").first().getAttribute("viewBox"), "0 0 56 56");
    assert.equal(await page.getByLabel("图纸比例", { exact: true }).inputValue(), "20");
    assert.equal(await page.getByRole("option", { name: /自动/ }).count(), 0);
    await page.getByRole("radio", { name: /ARCH364/ }).focus(); await page.keyboard.press("Space");
    assert.equal(await page.getByRole("radio", { name: /ARCH364/ }).isChecked(), true);
    await page.getByLabel("图纸比例", { exact: true }).selectOption("10");
    await page.screenshot({ path: join(screenshots, "styles-light.png") });
  });
  await step("one request uses document model and keeps filters, saves pending notes and opens new PDF", async () => {
    hold = true; await generate().click();
    await until(() => requests.length, value => value === 1, "one drawing request");
    assert.deepEqual(requests[0], { projectId: "drawing-project", modelSource: modelA, styleId: "arch364-technical", scaleDenominator: 10,
      hiddenObjectIds: ["flower"], outlineObjectIds: ["vase"], notes: ["Keep the stone inscription."] });
    assert.equal(await page.getByRole("button", { name: "正在生成图纸…", exact: true }).isDisabled(), true);
    assert.equal(await source().isEnabled(), true);
    await page.getByLabel("对此页的意见", { exact: true }).fill("Preserve this note while generating.");
    release(); await until(opened, value => value === 1, "new PDF opened"); await ready();
    assert.match(await source().locator("option:checked").textContent(), /arch364-technical-1\.pdf/);
    assert.equal(await source().locator("option").count(), 3);
    assert.equal(await page.evaluate(() => window.drawingFixture.saves.at(-1).comment), "Preserve this note while generating.");
    assert.equal(await page.getByRole("radio", { name: /ARCH364/ }).isChecked(), true);
    assert.equal(await page.getByLabel("图纸比例", { exact: true }).inputValue(), "10");
  });
  await step("opening a retained sheet restores its style and scale without overwriting later choices", async () => {
    await page.getByRole("radio", { name: /ARCH400/ }).check();
    await page.getByLabel("图纸比例", { exact: true }).selectOption("50");
    await page.getByLabel("对此页的意见", { exact: true }).fill("A later edit must not reset my next drawing choices.");
    assert.equal(await page.getByRole("radio", { name: /ARCH400/ }).isChecked(), true);
    assert.equal(await page.getByLabel("图纸比例", { exact: true }).inputValue(), "50");
    const generatedSha = await source().inputValue();
    await source().selectOption(originalSha);
    await source().selectOption(generatedSha); await ready();
    assert.equal(await page.getByRole("radio", { name: /ARCH364/ }).isChecked(), true);
    assert.equal(await page.getByLabel("图纸比例", { exact: true }).inputValue(), "10");
    const retained = await page.evaluate(sha => window.drawingFixture.documents.find(document => document.assetSha256 === sha), generatedSha);
    assert.deepEqual(retained.viewRecipe.hiddenObjectIds, ["flower"]);
    assert.deepEqual(retained.viewRecipe.outlineObjectIds, ["vase"]);
    assert.deepEqual(retained.viewRecipe.notes, ["Keep the stone inscription."]);
  });
  await step("unbound PDF uses the viewed model and opens its distinct run without old filters", async () => {
    await page.evaluate(({ runId, sourceSha }) => window.drawingFixture.setView({ runId, sourceSha }), { runId: modelA.runId, sourceSha: unboundSha });
    await ready(); await generate().click(); await until(opened, value => value === 2, "other model PDF opened"); await ready();
    assert.deepEqual(requests.at(-1).modelSource, modelB);
    assert.equal("hiddenObjectIds" in requests.at(-1), false);
    assert.equal(await page.evaluate(() => window.drawingFixture.lists.at(-1)), modelB.runId);
  });
  await step("fresh document list selects the newest exact-source PDF with no revisionRef", async () => {
    const newest = await page.evaluate(runId => {
      const fixture = window.drawingFixture;
      const retained = fixture.documents.find(item => item.runId === runId);
      fixture.documents.push({ ...retained, assetSha256: "8".repeat(64), fileName: "older-elevation.pdf",
        revisionRef: "drawings/older-revision", generatedAt: "2026-09-11T00:00:00Z" });
      fixture.documents.push({ ...fixture.documents.find(item => item.generatedAt && item.runId !== runId),
        assetSha256: "9".repeat(64), fileName: "newest-other-model.pdf", generatedAt: "2026-09-13T00:00:00Z" });
      fixture.setView({ runId, sourceSha: null });
      return retained.assetSha256;
    }, modelB.runId);
    await ready();
    assert.equal(await source().inputValue(), JSON.stringify([modelB.runId, newest, null]));
    assert.match(await source().locator("option:checked").textContent(), /arch400-white-2\.pdf/);
    assert.equal(await opened(), 2, "initial discovery does not need another generation callback");
  });
  await step("an unrelated editing model leaves the page unselected until the user chooses its exact source", async () => {
    const rootSource = { ...modelB, runId: "original-root", stateDigest: "0".repeat(64) };
    await page.evaluate(value => {
      window.drawingFixture.setEditing(value);
      window.drawingFixture.setView({ runId: value.runId, sourceSha: null });
    }, rootSource);
    await until(() => source().inputValue(), value => value === "", "An unrelated drawing must not become the selected source");
    assert.equal(await page.locator('.document-viewport[data-ready="true"]').count(), 0);
    await source().selectOption(JSON.stringify([modelA.runId, "9".repeat(64), null]));
    await ready();
    assert.match(await source().locator("option:checked").textContent(), /newest-other-model\.pdf/);
    assert.equal(await page.evaluate(() => window.drawingFixture.lists.at(-1)), null);
    assert.deepEqual(await page.evaluate(() => window.drawingFixture.bytes.at(-1)), { runId: modelA.runId, sha: "9".repeat(64) });
    assert.equal(await page.locator(".document-model-source strong").textContent(), "Model A");
    assert.equal(await page.locator(".document-model-source").getAttribute("data-model-source-status"), "mismatch");
    await page.getByLabel("对此页的意见", { exact: true }).fill("Review the latest drawing without changing my starting model.");
    assert.equal(await page.getByRole("button", { name: "提交本页意见", exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole("button", { name: "从此模型继续", exact: true }).isEnabled(), true);
    assert.deepEqual(await page.evaluate(() => window.drawingFixture.editing), rootSource);
    assert.deepEqual(await page.evaluate(() => window.drawingFixture.continues), []);
    assert.equal(await opened(), 2);
  });
  await step("an existing selection survives refreshed defaults and keeps its actual source", async () => {
    await source().selectOption(JSON.stringify([modelA.runId, originalSha, null]));
    await ready();
    const listCount = await page.evaluate(() => window.drawingFixture.lists.length);
    await page.evaluate(value => window.drawingFixture.setEditing(value), modelB);
    await until(() => page.evaluate(() => window.drawingFixture.lists.length), count => count > listCount, "list refreshed");
    await ready();
    assert.equal(await source().inputValue(), JSON.stringify([modelA.runId, originalSha, null]));
    assert.equal(await page.locator(".document-model-source strong").textContent(), "Model A");
    assert.deepEqual(await page.evaluate(() => window.drawingFixture.editing), modelB);
  });
  await step("an explicitly requested missing drawing never falls back to another PDF", async () => {
    const bytes = await page.evaluate(() => window.drawingFixture.bytes.length);
    const missingSha = "7".repeat(64);
    await page.evaluate(({ runId, sourceSha }) => window.drawingFixture.setView({ runId, sourceSha }), { runId: modelA.runId, sourceSha: missingSha });
    await page.locator(".document-empty [role='alert']").waitFor();
    assert.equal(await source().inputValue(), missingSha);
    assert.equal(await page.locator(".document-viewport").count(), 0);
    assert.equal(await page.evaluate(() => window.drawingFixture.bytes.length), bytes);
  });
  await step("missing source is explained and cannot generate from editing base", async () => {
    await page.evaluate(({ runId, sourceSha }) => { window.drawingFixture.setViewed(null); window.drawingFixture.setView({ runId, sourceSha }); }, { runId: modelA.runId, sourceSha: unboundSha });
    await page.locator('.document-viewport[data-ready="true"]').waitFor();
    assert.equal(await generate().isDisabled(), true);
    assert.match(await page.locator("#document-drawing-source").textContent(), /请先打开模型/);
    assert.equal(requests.length, 2);
  });
  await step("server size error preserves old document and supports a new explicit scale", async () => {
    await page.evaluate(value => window.drawingFixture.setViewed(value), modelA); await ready();
    const before = await source().inputValue(); fail = true;
    await generate().click(); await page.getByText("The model does not fit ARCH400 White Sheet at 1:20; select a larger scale denominator", { exact: true }).waitFor();
    assert.equal(await source().inputValue(), before);
    assert.equal(await opened(), 2);
    await page.getByLabel("图纸比例", { exact: true }).selectOption("50"); await generate().click();
    await until(opened, value => value === 3, "retry opens PDF"); await ready();
    assert.equal(requests.at(-1).scaleDenominator, 50);
  });
  for (const change of ["model", "workspace", "project"]) await step(`late ${change} response does not reopen an obsolete PDF`, async () => {
    const before = await opened(); hold = true; await generate().click();
    await until(() => Boolean(release), Boolean, "response held");
    if (change === "model") await page.evaluate(value => window.drawingFixture.setViewed(value), modelB);
    if (change === "workspace") await page.evaluate(() => window.drawingFixture.setActive(false));
    if (change === "project") await page.evaluate(() => window.drawingFixture.setProject("other-project"));
    release(); await until(() => release, value => value === null, "response released");
    await until(() => page.getByRole("button", { name: "正在生成图纸…", exact: true }).count(), value => value === 0, "request finished");
    assert.equal(await opened(), before);
    if (change === "workspace") await page.evaluate(() => window.drawingFixture.setActive(true));
    if (change === "project") await page.evaluate(() => window.drawingFixture.setProject("drawing-project"));
    await ready();
  });
  await step("English text, dark theme and narrow layout retain usable style controls", async () => {
    await page.evaluate(() => { window.drawingFixture.setLanguage("en"); window.drawingFixture.setTheme("dark"); });
    await page.getByRole("button", { name: "Generate drawing", exact: true }).waitFor();
    assert.match(await page.getByRole("radio").first().getAttribute("value"), /arch400-white/);
    await page.screenshot({ path: join(screenshots, "styles-dark-en.png") });
    await page.setViewportSize({ width: 375, height: 812 });
    await page.getByRole("button", { name: "Generate drawing", exact: true }).scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: join(screenshots, "styles-narrow.png") });
  });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: passed.length, requests: requests.length, screenshots }, null, 2));
} catch (error) { console.error(JSON.stringify({ current, passed, errors, requests,
  body: await page?.locator("body").innerText().catch(() => null) }, null, 2));
  await page?.screenshot({ path: join(screenshots, "failure.png") }); throw error; }
finally { release?.(); await browser?.close(); await server.close(); }

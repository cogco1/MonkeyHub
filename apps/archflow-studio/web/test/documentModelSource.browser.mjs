import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";

const playwrightModule = process.env.PLAYWRIGHT_MODULE?.trim();
if (!playwrightModule) {
  throw new Error("Set PLAYWRIGHT_MODULE to the Playwright module file path.");
}

// The real DocumentCanvas with contract-shaped, in-memory ports. This checks
// UI binding and callback behavior; backend ownership is covered by its tests.
const root = fileURLToPath(new URL("..", import.meta.url)).replaceAll("\\", "/").replace(/\/$/, "");
const { chromium } = await import(pathToFileURL(playwrightModule).href);
const modelA = { runId: "model-A", stateDigest: "a".repeat(64), assetSha256: "b".repeat(64) };
const modelB = { runId: "model-B", stateDigest: "c".repeat(64), assetSha256: "e".repeat(64) };
const storageRun = "document-storage-R";
const documentSha = "d".repeat(64);
const markedReferenceSha = "f".repeat(64);
const emptyReferenceSha = "6".repeat(64);
const pdfSha = "7".repeat(64);
const referenceLabel = (fileName, pageIndex = 0) => `${fileName} · 第 ${pageIndex + 1} 页`;
// Two synthetic pages: the second page's visible CropBox is 600 x 500 and
// rotates once to 500 x 600. The colored corners identify the crop and rotation.
function syntheticPdf() {
  const first = "q 1 1 1 rg 0 0 400 300 re f 1 0 0 rg 0 250 50 50 re f 0 1 0 rg 350 0 50 50 re f Q";
  const second = "q 1 0 1 rg 0 0 800 600 re f 0.956862745 0.960784314 0.964705882 rg 100 50 600 500 re f 1 0 0 rg 100 50 60 50 re f 0 1 0 rg 640 500 60 50 re f Q";
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >> /Contents 4 0 R >>",
    `<< /Length ${first.length} >>\nstream\n${first}\nendstream`,
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 800 600] /CropBox [100 50 700 550] /Rotate 90 /Resources << >> /Contents 6 0 R >>",
    `<< /Length ${second.length} >>\nstream\n${second}\nendstream`,
  ];
  let body = "%PDF-1.4\n";
  const offsets = [0];
  for (const [index, object] of objects.entries()) {
    offsets.push(Buffer.byteLength(body, "ascii"));
    body += `${index + 1} 0 obj\n${object}\nendobj\n`;
  }
  const start = Buffer.byteLength(body, "ascii");
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  body += offsets.slice(1).map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("");
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${start}\n%%EOF\n`;
  return Buffer.from(body, "ascii");
}
const fixture = `
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';
import {DocumentCanvas} from '/src/features/stage/DocumentCanvas.tsx';
import {createDocumentAnnotationsController} from '/src/features/stage/useDocumentAnnotations.ts';
import {UserPreferencesProvider} from '/src/features/settings/preferences.tsx';
import {studio} from '/src/api/client';
const modelA=${JSON.stringify(modelA)},modelB=${JSON.stringify(modelB)},storageRun=${JSON.stringify(storageRun)},documentSha=${JSON.stringify(documentSha)};
const markedReferenceSha=${JSON.stringify(markedReferenceSha)},emptyReferenceSha=${JSON.stringify(emptyReferenceSha)},pdfSha=${JSON.stringify(pdfSha)};
async function imageFile(name,width,height){
 const raster=document.createElement('canvas');raster.width=width;raster.height=height;
 const context=raster.getContext('2d');context.fillStyle='#f4f5f6';context.fillRect(0,0,width,height);
 context.fillStyle='#ef4444';context.fillRect(width*.04,height*.04,width*.16,height*.16);
 context.fillStyle='#16a34a';context.fillRect(width*.8,height*.8,width*.16,height*.16);
 const blob=await new Promise(resolve=>raster.toBlob(resolve,'image/png'));
 return new File([blob],name,{type:'image/png'});
}
const file=await imageFile('source.png',800,600);
const markedFile=await imageFile('reference-marked.png',2600,1800);
const emptyFile=await imageFile('reference-empty.png',640,480);
const pdfFile=new File([Uint8Array.from(atob(${JSON.stringify(syntheticPdf().toString("base64"))}),value=>value.charCodeAt(0))],'reference-pages.pdf',{type:'application/pdf'});
const files=new Map([[documentSha,file],[markedReferenceSha,markedFile],[emptyReferenceSha,emptyFile],[pdfSha,pdfFile]]);
let source={projectId:'ui-project',runId:storageRun,assetSha256:documentSha,fileName:file.name,mimeType:file.type,sizeBytes:file.size,pageCount:1,
 pages:[{pageIndex:0,width:800,height:600,rotation:0}],modelSource:null,modelSourceBindingRef:null};
function metadata(sha,file,pages){return {projectId:'ui-project',runId:storageRun,assetSha256:sha,fileName:file.name,mimeType:file.type,sizeBytes:file.size,
 pageCount:pages.length,pages,modelSource:modelA,modelSourceBindingRef:'fixture-existing-binding'}};
const otherSources=[metadata(markedReferenceSha,markedFile,[{pageIndex:0,width:2600,height:1800,rotation:0}]),
 metadata(emptyReferenceSha,emptyFile,[{pageIndex:0,width:640,height:480,rotation:0}]),
 metadata(pdfSha,pdfFile,[{pageIndex:0,width:400,height:300,rotation:0},{pageIndex:1,width:500,height:600,rotation:90}])];
const currentPages=new Map(),revisions=new Map();
const key=(value)=>JSON.stringify([value.projectId,value.runId,value.assetSha256,value.pageIndex]);
const historicalKey=(value,revision)=>JSON.stringify([key(value),revision]);
function retain(value){const copy=structuredClone(value);currentPages.set(key(copy),copy);if(copy.revisionSha256)revisions.set(historicalKey(copy,copy.revisionSha256),copy);return copy}
const line=(id)=>({id,kind:'line',points:[[.2,.3],[.7,.3]],color:'#2f80ed',lineWidth:.004});
function seed(sha,pageIndex,revision,annotations,comment){return retain({projectId:'ui-project',runId:storageRun,assetSha256:sha,pageIndex,
 revisionSha256:revision,annotations,comment})}
seed(documentSha,0,'1'.repeat(64),[line('kept-line')],'保留意见');
seed(markedReferenceSha,0,'2'.repeat(64),[line('old-reference-line')],'旧参照意见：不得作为本次修改意见');
seed(emptyReferenceSha,0,'3'.repeat(64),[],'旧参照文字，没有笔迹');
seed(pdfSha,0,'4'.repeat(64),[],'合成 PDF 第一页');
seed(pdfSha,1,'5'.repeat(64),[line('rotated-page-line')],'检查旋转裁切页');
const originalPage=()=>currentPages.get(key({projectId:'ui-project',runId:storageRun,assetSha256:documentSha,pageIndex:0}));
let revision=8,comments=[];
const metrics=window.modelFixture={reads:[],writes:[],bindings:[],continues:[],intents:[],fileLoads:0,holdBinding:true,releaseBinding:null,
 scope:{projectId:'ui-project',runId:storageRun},holdNext:null,pending:null,releasePending:null,failNext:null};
const matches=(rule,kind,details)=>rule?.kind===kind&&Object.entries(rule).every(([name,value])=>name==='kind'||name==='mode'||details[name]===value);
async function boundary(kind,details){
 if(matches(metrics.holdNext,kind,details)){
  metrics.holdNext=null;metrics.pending={kind,...details};
  await new Promise(resolve=>{metrics.releasePending=()=>{metrics.pending=null;metrics.releasePending=null;resolve()}});
 }
 if(matches(metrics.failNext,kind,details)){
  const failure=metrics.failNext;metrics.failNext=null;
  if(failure.mode==='corrupt')return 'corrupt';
  throw new Error('Deliberate visual fixture '+kind+' failure');
 }
 return null;
}
Object.assign(studio,{
 documents:async run=>{const projectId=metrics.scope.projectId;metrics.reads.push({kind:'list',runId:run});return {projectId,runId:run,
  documents:[source,...otherSources].map(value=>({...structuredClone(value),projectId,runId:run}))}},
 documentFile:async(run,sha)=>{
  const details={runId:run,sha};metrics.reads.push({kind:'bytes',...details});metrics.fileLoads++;
  const result=await boundary('bytes',details);
  if(result==='corrupt')return new File(['deliberately invalid image'],'broken.png',{type:'image/png'});
  const value=files.get(sha);if(!value)throw new Error('Unexpected synthetic document SHA '+sha);return value;
 },
 documentAnnotations:async(run,sha,page,revision)=>{
  const scope={projectId:metrics.scope.projectId,runId:run,assetSha256:sha,pageIndex:page};
  const details={runId:run,sha,page,revisionSha256:revision??null};metrics.reads.push({kind:'annotations',...details});
  const saved=revision?revisions.get(historicalKey(scope,revision)):currentPages.get(key(scope));
  if(revision&&!saved)throw new Error('Unknown requested retained page revision '+revision);
  await boundary('annotations',details);
  return structuredClone(saved??{...scope,revisionSha256:null,annotations:[],comment:''});
 },
 saveDocumentAnnotations:async body=>{
  metrics.writes.push(structuredClone(body));await boundary('save',{runId:body.runId,sha:body.assetSha256,page:body.pageIndex});
  const previous=currentPages.get(key(body));if((previous?.revisionSha256??null)!==body.baseRevisionSha256)throw new Error('Fixture CAS mismatch');
  return structuredClone(retain({...structuredClone(body),revisionSha256:String(++revision).padStart(64,'0')}));
 },
 bindDocumentModelSource:async(projectId,runId,sha,modelSource)=>{
  metrics.bindings.push({projectId,runId,sha,modelSource:structuredClone(modelSource)});
  if(metrics.holdBinding)await new Promise(resolve=>{metrics.releaseBinding=resolve});
  source={...source,modelSource:structuredClone(modelSource),modelSourceBindingRef:'binding-1'};
  return structuredClone(source);
 },
 documentComments:async run=>{metrics.reads.push({kind:'comments',runId:run});return {comments:structuredClone(comments)}}
});
function App(){
 const [editing,setEditing]=useState(modelB),[controller]=useState(createDocumentAnnotationsController),
  [scope,setScope]=useState({projectId:'ui-project',runId:storageRun}),[visualAvailable,setVisualAvailable]=useState(true);
 Object.assign(metrics,{scope,setEditing:next=>flushSync(()=>setEditing(next)),setScope:next=>flushSync(()=>setScope(next)),
  setVisualAvailable:next=>flushSync(()=>setVisualAvailable(next)),forceSelect:(selector,value)=>flushSync(()=>{
   const select=document.querySelector(selector);select.value=String(value);select.dispatchEvent(new Event('change',{bubbles:true}));
  }),state:()=>({source:structuredClone(source),saved:structuredClone(originalPage()),editing,scope,visualAvailable,
   pages:[...currentPages.values()].map(value=>structuredClone(value)),comments:structuredClone(comments)})});
 return <DocumentCanvas projectId={scope.projectId} runId={scope.runId} controller={controller} busy={false} documentVisualInputAvailable={visualAvailable}
  initialSourceSha={documentSha} modelSources={[{label:'模型 A',modelSource:modelA},{label:'模型 B',modelSource:modelB}]} editingModelSource={editing}
  onContinueModelSource={async source=>{metrics.continues.push(structuredClone(source));setEditing(source)}}
  onSubmit={async(utterance,refs,modelSource,documentVisuals)=>{
   metrics.intents.push({utterance,refs:structuredClone(refs),modelSource:structuredClone(modelSource),documentVisuals:structuredClone(documentVisuals)});
   comments.push({commentRef:'comment-'+metrics.intents.length,projectId:scope.projectId,sourceRunId:modelSource.runId,stateDigest:modelSource.stateDigest,utterance,
    documentAnnotations:structuredClone(refs),submittedAt:'2026-09-09T00:00:00Z'});
  }}/>
}
createRoot(document.getElementById('root')).render(<UserPreferencesProvider><App/></UserPreferencesProvider>);
`;
const server = await createServer({ root, configFile: false, logLevel: "error", publicDir: false,
  server: { host: "127.0.0.1", port: 0, strictPort: true }, plugins: [{ name: "document-model-source-fixture",
    resolveId(id) { if (id === "/model-source-fixture.tsx") return `${root}/model-source-fixture.tsx`; },
    load(id) { if (id === `${root}/model-source-fixture.tsx`) return fixture; },
    configureServer(vite) { vite.middlewares.use((request, response, next) => {
      if (request.url !== "/") return next();
      response.setHeader("Content-Type", "text/html");
      response.end('<html><head><meta charset="utf-8"/><style>body{margin:0;--line:#ccd1d8;--panel:#fff;--panel-2:#f1f3f5;--ground:#eee;--viewport:#cfd4d8;--ink:#1e293b;--muted:#5d6877;--accent:#2f80ed;--accent-ink:#fff;--accent-soft:#e7f0ff;--radius:6px;--radius-sm:4px}#root{position:relative;width:1300px;height:900px}.document-workspace{inset:0!important}.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}button,input,select,textarea{font:inherit}</style></head><body><div id="root"></div><script type="module" src="/model-source-fixture.tsx"></script></body></html>');
    }); },
  }],
});
let browser;
let page;
let current;
const passed = [];
const errors = [];
const apiRequests = [];
const status = () => page.locator(".document-model-source").getAttribute("data-model-source-status");
const snapshot = () => page.evaluate(() => ({ ...window.modelFixture.state(),
  reads: window.modelFixture.reads, writes: window.modelFixture.writes, bindings: window.modelFixture.bindings,
  continues: window.modelFixture.continues, intents: window.modelFixture.intents, fileLoads: window.modelFixture.fileLoads }));
const submitButton = () => page.getByRole("button", { name: "提交本页意见", exact: true });
const sourceSelect = () => page.locator(".document-header > select");
const pageSelect = () => page.locator(".document-pages select");
const referencePanel = () => page.locator(".document-references");
const referenceCheck = (fileName, pageIndex = 0) => referencePanel().getByRole("checkbox", {
  name: referenceLabel(fileName, pageIndex), exact: true,
});
const referenceInk = (fileName, pageIndex = 0) => referencePanel().getByRole("checkbox", {
  name: `附上已保存批注：${referenceLabel(fileName, pageIndex)}`, exact: true,
});
const referencePurpose = (fileName, pageIndex = 0) => referencePanel().getByRole("textbox", {
  name: `参照用途：${referenceLabel(fileName, pageIndex)}`, exact: true,
});
const pageSizes = new Map([
  [documentSha, [[800, 600]]], [markedReferenceSha, [[2600, 1800]]],
  [emptyReferenceSha, [[640, 480]]], [pdfSha, [[400, 300], [500, 600]]],
]);
async function until(read, accepts, label) {
  const end = Date.now() + 10_000;
  let last;
  do {
    assert.deepEqual(errors, [], "no browser application exceptions");
    last = await read(); if (accepts(last)) return last;
    await new Promise((resolve) => setTimeout(resolve, 40));
  } while (Date.now() < end);
  assert.fail(`${label}: ${JSON.stringify(last)}`);
}
async function at(point) {
  const rect = await page.locator(".document-page").boundingBox();
  return [rect.x + point[0] * rect.width, rect.y + point[1] * rect.height];
}
async function step(name, action) { current = name; await action(); passed.push(name); console.log(`PASS ${name}`); }
async function frames() {
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
async function pageReady(sha = documentSha, pageIndex = 0) {
  const [width, height] = pageSizes.get(sha)[pageIndex];
  await until(async () => ({ source: await sourceSelect().inputValue(), page: await pageSelect().inputValue(),
    ready: await page.locator(".document-viewport").getAttribute("data-ready"),
    viewBox: await page.locator(".document-page__ink").getAttribute("viewBox"),
  }), (value) => value.source === sha && value.page === String(pageIndex) && value.ready === "true" &&
    value.viewBox === `0 0 ${width} ${height}`, "the selected synthetic page is fully rendered");
  await page.locator(".document-save-state").filter({ hasText: "已保存" }).waitFor();
}
async function selectDocument(sha, pageIndex = 0) {
  if (await sourceSelect().inputValue() !== sha) await sourceSelect().selectOption(sha);
  if (await pageSelect().inputValue() !== String(pageIndex)) await pageSelect().selectOption(String(pageIndex));
  await pageReady(sha, pageIndex);
}
async function openReferences() {
  if (await referencePanel().getAttribute("open") === null) await referencePanel().locator("summary").click();
}
async function clearReferences() {
  await openReferences();
  const checked = referencePanel().locator("input[type=checkbox]:checked");
  while (await checked.count()) await checked.first().uncheck();
}
async function finishSubmission() {
  await until(() => sourceSelect().isDisabled(), (value) => !value, "the pending submission has finished");
}
async function submitVisuals() {
  const count = await page.evaluate(() => window.modelFixture.intents.length);
  await submitButton().click();
  await until(() => page.evaluate(() => window.modelFixture.intents.length), (value) => value === count + 1,
    "one visual-input callback was dispatched");
  await finishSubmission();
  return (await snapshot()).intents.at(-1);
}
function assertVisualLimits(visuals) {
  let total = 0;
  for (const visual of visuals) {
    assert.ok(["edit", "reference"].includes(visual.role));
    for (const image of [visual.pagePngBase64, visual.annotatedPngBase64]) {
      if (image === null) continue;
      assert.equal(typeof image, "string");
      assert.match(image, /^[A-Za-z0-9+/]+={0,2}$/, "PNG data is pure base64 without a data URL prefix");
      const bytes = Buffer.from(image, "base64");
      assert.deepEqual([...bytes.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
      assert.ok(bytes.byteLength <= 4 * 1024 * 1024, "each decoded PNG is at most 4 MiB");
      total += bytes.byteLength;
    }
  }
  assert.ok(total <= 16 * 1024 * 1024, "the complete visual request is at most 16 MiB decoded");
}
async function pngPixels(base64, points) {
  return page.evaluate(async ({ base64, points }) => {
    const bytes = Uint8Array.from(atob(base64), (value) => value.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width; canvas.height = bitmap.height;
    const context = canvas.getContext("2d"); context.drawImage(bitmap, 0, 0);
    const result = { width: canvas.width, height: canvas.height, pixels: points.map(([x, y]) =>
      [...context.getImageData(Math.min(canvas.width - 1, Math.floor(x * canvas.width)),
        Math.min(canvas.height - 1, Math.floor(y * canvas.height)), 1, 1).data]) };
    bitmap.close(); canvas.width = 0; canvas.height = 0;
    return result;
  }, { base64, points });
}
function color(actual, expected, tolerance = 8) {
  assert.ok(expected.every((value, index) => Math.abs(actual[index] - value) <= tolerance),
    `pixel ${JSON.stringify(actual)} must match ${JSON.stringify(expected)}`);
  assert.equal(actual[3], 255);
}
async function assertAlignedImages(visual, points) {
  const plain = await pngPixels(visual.pagePngBase64, points);
  assert.ok(plain.width > 0 && plain.height > 0 && Math.max(plain.width, plain.height) <= 2048);
  const marked = visual.annotatedPngBase64 === null ? null : await pngPixels(visual.annotatedPngBase64, points);
  if (marked) assert.deepEqual([marked.width, marked.height], [plain.width, plain.height]);
  return { plain, marked };
}

try {
  await server.listen();
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  page = await browser.newPage({ viewport: { width: 1320, height: 920 } });
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("request", (request) => { if (new URL(request.url()).pathname.startsWith("/api/")) apiRequests.push(request.url()); });
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => route.abort("blockedbyclient"));
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/`);
  await page.locator('.document-viewport[data-ready="true"]').waitFor();
  await page.waitForFunction(() => document.querySelector(".document-tools button")?.disabled === false);

  await step("an unknown drawing remains annotatable and saveable, but cannot submit a model change", async () => {
    assert.equal(await status(), "unknown");
    assert.equal(await submitButton().isDisabled(), true);
    assert.equal(await page.getByRole("button", { name: "保存本页", exact: true }).isEnabled(), true);
    await page.locator(".document-tools").getByRole("button", { name: "画笔", exact: true }).click();
    await page.mouse.move(...await at([0.2, 0.55])); await page.mouse.down();
    await page.mouse.move(...await at([0.4, 0.6])); await page.mouse.up();
    const saved = await until(snapshot, (value) => value.saved.annotations.length === 2, "unknown source stroke saved");
    assert.equal(saved.source.modelSource, null);
    assert.equal(saved.writes.length, 1);
    assert.equal(saved.writes[0].runId, storageRun);
    assert.equal(saved.writes[0].assetSha256, documentSha);
    assert.deepEqual(saved.bindings, []);
    assert.deepEqual(saved.intents, []);
  });

  await step("explicit model association preserves document bytes, retained revision and unfinished page text", async () => {
    await page.locator(".document-save-state").filter({ hasText: "已保存" }).waitFor();
    const before = await snapshot();
    await page.evaluate(() => { window.modelFixture.canvasBefore = document.querySelector(".document-page__raster"); });
    await page.locator(".document-tools").getByRole("button", { name: "文字", exact: true }).click();
    await page.mouse.click(...await at([0.45, 0.65]));
    await page.locator(".document-text-editor textarea").fill("未完成的页内文字");
    await page.locator(".document-model-source select").selectOption(JSON.stringify([modelA.runId, modelA.stateDigest, modelA.assetSha256]));
    await page.getByRole("button", { name: "关联所选模型", exact: true }).click();
    await page.waitForFunction(() => window.modelFixture.releaseBinding !== null);
    const pending = await snapshot();
    assert.equal(await status(), "unknown");
    assert.deepEqual(pending.bindings, [{ projectId: "ui-project", runId: storageRun, sha: documentSha, modelSource: modelA }]);
    await page.evaluate(() => window.modelFixture.releaseBinding());
    await until(status, (value) => value === "mismatch", "association metadata returned");
    const after = await snapshot();
    assert.deepEqual(after.saved, before.saved, "binding does not write or replace page annotations");
    assert.equal(after.fileLoads, before.fileLoads, "binding metadata does not refetch the same bytes");
    assert.equal(await page.evaluate(() => window.modelFixture.canvasBefore === document.querySelector(".document-page__raster")), true);
    assert.equal(await page.locator(".document-text-editor textarea").inputValue(), "未完成的页内文字");
    assert.equal(await page.locator(".document-header > select").inputValue(), documentSha);
    assert.equal(await page.locator(".document-model-source strong").textContent(), "模型 A");
    assert.equal(await page.locator(".document-model-source select").count(), 0, "a known source cannot be reassigned in this UI");
    assert.equal(await submitButton().isDisabled(), true);
    await page.locator(".document-text-editor textarea").press("Escape");
  });

  await step("Continue uses the linked model only on the explicit action and leaves document storage bound", async () => {
    const before = await snapshot();
    assert.deepEqual(before.continues, []);
    assert.deepEqual(before.editing, modelB);
    await page.getByRole("button", { name: "从此模型继续", exact: true }).click();
    await until(status, (value) => value === "ready", "explicit model continuation resolved");
    const after = await snapshot();
    assert.deepEqual(after.continues, [modelA]);
    assert.deepEqual(after.editing, modelA);
    assert.equal(await page.locator(".document-header > select").inputValue(), documentSha);
    assert.deepEqual(after.saved, before.saved);
    assert.ok(after.reads.every((read) => read.runId === storageRun));
  });

  await step("submission requires all three model identity fields to match", async () => {
    for (const mismatch of [{ ...modelA, runId: modelB.runId }, { ...modelA, stateDigest: modelB.stateDigest }, { ...modelA, assetSha256: modelB.assetSha256 }, null]) {
      await page.evaluate((source) => window.modelFixture.setEditing(source), mismatch);
      await until(status, (value) => value === "mismatch", "one differing identity field blocks submission");
      assert.equal(await submitButton().isDisabled(), true);
      assert.equal(await page.locator(".document-tools").getByRole("button", { name: "画笔", exact: true }).isEnabled(), true);
      assert.equal(await page.getByRole("button", { name: "保存本页", exact: true }).isEnabled(), true);
    }
    await page.evaluate((source) => window.modelFixture.setEditing(source), modelA);
    await until(status, (value) => value === "ready", "exact model identity matches");
    assert.equal(await submitButton().isEnabled(), true);
  });

  await step("submit carries the linked model and the exact page revision while comment lookup keeps the storage run", async () => {
    const before = await snapshot();
    await submitButton().click();
    const after = await until(snapshot, (value) => value.intents.length === 1 && value.comments.length === 1, "source-aware submission completed");
    assert.deepEqual(after.intents.map(({ documentVisuals: _visuals, ...intent }) => intent), [{ utterance: before.saved.comment, modelSource: modelA,
      refs: [{ runId: storageRun, assetSha256: documentSha, pageIndex: 0, revisionSha256: before.saved.revisionSha256 }] }]);
    assert.ok(after.reads.filter((read) => read.kind === "comments").every((read) => read.runId === storageRun));
    assert.ok(after.writes.every((write) => write.runId === storageRun && write.assetSha256 === documentSha));
    await page.getByText("本页意见已提交，处理结果见左侧对话。", { exact: true }).waitFor();
  });

  await step("edit visuals contain the original page and the exact saved marks, and require the visual-input capability", async () => {
    const before = await snapshot();
    const intent = before.intents[0];
    assert.equal(intent.documentVisuals.length, 1);
    const [visual] = intent.documentVisuals;
    assert.deepEqual({ role: visual.role, runId: visual.runId, assetSha256: visual.assetSha256,
      pageIndex: visual.pageIndex, revisionSha256: visual.revisionSha256, referenceNote: visual.referenceNote },
    { role: "edit", ...intent.refs[0], referenceNote: null });
    assert.ok(before.reads.some((read) => read.kind === "annotations" && read.runId === storageRun &&
      read.sha === documentSha && read.page === 0 && read.revisionSha256 === intent.refs[0].revisionSha256),
    "The image overlay must read the exact saved revision returned by save");
    assertVisualLimits(intent.documentVisuals);
    const { plain, marked } = await assertAlignedImages(visual, [[0.1, 0.1], [0.9, 0.9], [0.4, 0.3]]);
    assert.deepEqual([plain.width, plain.height], [800, 600]);
    color(plain.pixels[0], [239, 68, 68]); color(plain.pixels[1], [22, 163, 74]);
    color(plain.pixels[2], [244, 245, 246]);
    assert.ok(marked, "The retained blue line needs a separate annotated image");
    color(marked.pixels[0], [239, 68, 68]); color(marked.pixels[1], [22, 163, 74]);
    color(marked.pixels[2], [47, 128, 237]);
    assert.notEqual(visual.pagePngBase64, visual.annotatedPngBase64);
    await page.evaluate(() => window.modelFixture.setVisualAvailable(false));
    assert.equal(await submitButton().isDisabled(), true);
    await page.locator(".document-visual-unavailable").waitFor();
    assert.equal(await page.getByRole("button", { name: "保存本页", exact: true }).isEnabled(), true);
    assert.equal((await snapshot()).intents.length, before.intents.length);
    await page.evaluate(() => window.modelFixture.setVisualAvailable(true));
    assert.equal(await submitButton().isEnabled(), true);
  });

  await step("only selected reference pages travel as context and old marks require their own explicit choice", async () => {
    await openReferences();
    assert.equal(await referenceCheck("source.png").count(), 0, "The edit page is not also a reference choice");
    await referenceCheck("reference-marked.png").check();
    await referenceCheck("reference-empty.png").check();
    await referenceCheck("reference-pages.pdf", 1).check();
    assert.equal(await referenceCheck("reference-pages.pdf", 0).isDisabled(), true, "At most three references can be selected");
    await referencePurpose("reference-marked.png").fill("只比较材质，不改变几何");
    await referencePurpose("reference-pages.pdf", 1).fill("检查剖面比例");
    for (const [name, index] of [["reference-marked.png", 0], ["reference-empty.png", 0], ["reference-pages.pdf", 1]])
      assert.equal(await referenceInk(name, index).isChecked(), false);
    const before = await snapshot();
    const referenceShas = new Set([markedReferenceSha, emptyReferenceSha, pdfSha]);
    const referencePagesBefore = before.pages.filter((value) => referenceShas.has(value.assetSha256));
    const first = await submitVisuals();
    const after = await snapshot();
    assert.equal(first.utterance, before.saved.comment, "An old reference comment cannot become the new instruction");
    assert.deepEqual(first.modelSource, modelA);
    assert.equal(first.refs.length, 1);
    assert.equal(first.documentVisuals.length, 4);
    assert.equal(first.documentVisuals[0].role, "edit");
    const references = first.documentVisuals.filter((visual) => visual.role === "reference");
    assert.deepEqual(references.map(({ assetSha256, pageIndex }) => [assetSha256, pageIndex]).sort(),
      [[markedReferenceSha, 0], [emptyReferenceSha, 0], [pdfSha, 1]].sort());
    for (const visual of references) {
      assert.equal(visual.runId, storageRun);
      assert.equal(visual.revisionSha256, null);
      assert.equal(visual.annotatedPngBase64, null);
    }
    assert.equal(references.find((visual) => visual.assetSha256 === markedReferenceSha).referenceNote, "只比较材质，不改变几何");
    assert.equal(references.find((visual) => visual.assetSha256 === emptyReferenceSha).referenceNote, null);
    assert.equal(references.find((visual) => visual.assetSha256 === pdfSha).referenceNote, "检查剖面比例");
    assert.deepEqual(after.reads.slice(before.reads.length).filter((read) => read.kind === "annotations" && referenceShas.has(read.sha)), [],
      "Selecting references alone does not read their old annotations");
    assert.deepEqual(after.writes.slice(before.writes.length).filter((write) => referenceShas.has(write.assetSha256)), [],
      "Reference pages never acquire an empty saved revision");
    assert.deepEqual(after.pages.filter((value) => referenceShas.has(value.assetSha256)), referencePagesBefore);
    assertVisualLimits(first.documentVisuals);

    await referenceInk("reference-marked.png").check();
    const readStart = after.reads.length;
    const withMarks = await submitVisuals();
    const marked = withMarks.documentVisuals.find((visual) => visual.assetSha256 === markedReferenceSha);
    assert.equal(marked.revisionSha256, "2".repeat(64));
    assert.equal(marked.pagePngBase64, references.find((visual) => visual.assetSha256 === markedReferenceSha).pagePngBase64);
    assert.ok(marked.annotatedPngBase64);
    const pixels = await assertAlignedImages(marked, [[0.1, 0.1], [0.4, 0.3]]);
    assert.equal(Math.max(pixels.plain.width, pixels.plain.height), 2048, "A large source is bounded by the visual-input edge limit");
    color(pixels.plain.pixels[0], [239, 68, 68]); color(pixels.plain.pixels[1], [244, 245, 246]);
    color(pixels.marked.pixels[1], [47, 128, 237]);
    assert.deepEqual((await snapshot()).reads.slice(readStart).filter((read) => read.kind === "annotations" && referenceShas.has(read.sha)),
      [{ kind: "annotations", runId: storageRun, sha: markedReferenceSha, page: 0, revisionSha256: null }]);
    assert.equal(withMarks.utterance, before.saved.comment);
    assertVisualLimits(withMarks.documentVisuals);

    await referenceInk("reference-marked.png").uncheck();
    await referenceInk("reference-empty.png").check();
    const empty = (await submitVisuals()).documentVisuals.find((visual) => visual.assetSha256 === emptyReferenceSha);
    assert.equal(empty.revisionSha256, null, "A stored comment without marks is not an annotated reference revision");
    assert.equal(empty.annotatedPngBase64, null, "No marks means no duplicate image");
    const final = await snapshot();
    assert.deepEqual(final.pages.filter((value) => referenceShas.has(value.assetSha256)), referencePagesBefore);
    assert.deepEqual(final.writes.slice(before.writes.length).filter((write) => referenceShas.has(write.assetSha256)), []);
    assert.deepEqual(final.editing, before.editing);
    assert.deepEqual(final.continues, before.continues);
    await clearReferences();
  });

  await step("PDF CropBox and rotation are exported as the whole page independently of canvas zoom and pan", async () => {
    await selectDocument(pdfSha, 0);
    const unmarked = await submitVisuals();
    assert.equal(unmarked.documentVisuals.length, 1);
    assert.equal(unmarked.documentVisuals[0].role, "edit");
    assert.equal(unmarked.documentVisuals[0].revisionSha256, unmarked.refs[0].revisionSha256);
    assert.ok(unmarked.documentVisuals[0].revisionSha256, "An unmarked edit page still has its exact saved revision");
    assert.equal(unmarked.documentVisuals[0].annotatedPngBase64, null);
    await selectDocument(pdfSha, 1);
    const before = await submitVisuals();
    const [visual] = before.documentVisuals;
    assert.equal(visual.pageIndex, 1);
    const image = await assertAlignedImages(visual, [[0.05, 0.05], [0.95, 0.95], [0.4, 0.3]]);
    assert.ok(Math.abs(image.plain.width / image.plain.height - 5 / 6) < 0.001);
    color(image.plain.pixels[0], [255, 0, 0]); color(image.plain.pixels[1], [0, 255, 0]);
    color(image.plain.pixels[2], [244, 245, 246]); color(image.marked.pixels[2], [47, 128, 237]);
    const originalRect = await page.locator(".document-page").boundingBox();
    const viewport = await page.locator(".document-viewport").boundingBox();
    await page.mouse.move(viewport.x + viewport.width / 2, viewport.y + viewport.height / 2);
    await page.keyboard.down("Control"); await page.mouse.wheel(0, -500); await page.keyboard.up("Control");
    await until(() => page.locator(".document-page").boundingBox(), (value) => value.width > originalRect.width * 1.5, "the visible page is zoomed");
    await page.mouse.wheel(110, 150);
    await pageReady(pdfSha, 1);
    const movedRect = await page.locator(".document-page").boundingBox();
    assert.ok(movedRect.width > viewport.width || movedRect.height > viewport.height, "The editor now clips part of the zoomed page");
    assert.ok(movedRect.x !== originalRect.x || movedRect.y !== originalRect.y);
    const after = await submitVisuals();
    assert.deepEqual(after.refs, before.refs);
    assert.deepEqual(after.documentVisuals, before.documentVisuals,
      "Visual input is rendered from the saved whole page, not captured from the transformed editor viewport");
    assertVisualLimits(after.documentVisuals);
  });

  await step("delayed saves or file reads cannot submit after the base, project, run, source or page changes", async () => {
    const cases = [
      { name: "editing base changed and returned", sha: documentSha, pageIndex: 0, hold: "save", change: "base" },
      { name: "project", sha: documentSha, pageIndex: 0, hold: "bytes", change: "project" },
      { name: "storage run", sha: documentSha, pageIndex: 0, hold: "bytes", change: "run" },
      { name: "source", sha: documentSha, pageIndex: 0, hold: "bytes", change: "source" },
      { name: "page", sha: pdfSha, pageIndex: 1, hold: "bytes", change: "page" },
    ];
    for (const testCase of cases) {
      await selectDocument(testCase.sha, testCase.pageIndex);
      await page.evaluate(({ kind, sha }) => { window.modelFixture.holdNext = { kind, sha }; }, { kind: testCase.hold, sha: testCase.sha });
      if (testCase.hold === "save") await page.locator("#document-comment").fill("保存当前页意见，但取消已经换过来源的提交");
      const before = await snapshot();
      await submitButton().click();
      await until(() => page.evaluate(() => window.modelFixture.pending), Boolean, `${testCase.name}: the exact asynchronous boundary is held`);
      assert.equal(await sourceSelect().isDisabled(), true);
      assert.equal(await pageSelect().isDisabled(), true);
      assert.equal(await page.locator("#document-comment").isDisabled(), true);
      const history = page.locator(".document-submitted article button");
      assert.ok(await history.count() > 0);
      assert.ok((await history.evaluateAll((buttons) => buttons.map((button) => button.disabled))).every(Boolean),
        "Historical page entry points remain disabled during visual preparation");
      await page.evaluate(({ change, modelA, modelB, storageRun, markedReferenceSha }) => {
        const fixture = window.modelFixture;
        if (change === "base") { fixture.setEditing(modelB); fixture.setEditing(modelA); }
        if (change === "project") fixture.setScope({ projectId: "ui-project-next", runId: storageRun });
        if (change === "run") fixture.setScope({ projectId: "ui-project", runId: "document-storage-S" });
        // Normal UI navigation is disabled above. A programmatic change still
        // reaches React's real handlers and must invalidate an already captured scope.
        if (change === "source") fixture.forceSelect(".document-header > select", markedReferenceSha);
        if (change === "page") fixture.forceSelect(".document-pages select", 0);
      }, { change: testCase.change, modelA, modelB, storageRun, markedReferenceSha });
      const nextSha = testCase.change === "source" ? markedReferenceSha : testCase.sha;
      const nextPage = testCase.change === "page" ? 0 : testCase.pageIndex;
      if (testCase.hold !== "save") await pageReady(nextSha, nextPage);
      const text = await page.locator("#document-comment").inputValue();
      await page.evaluate(() => window.modelFixture.releasePending());
      await finishSubmission();
      await pageReady(nextSha, nextPage);
      assert.equal((await snapshot()).intents.length, before.intents.length, `${testCase.name}: the old request must not dispatch`);
      assert.equal(await page.locator("#document-comment").inputValue(), text, `${testCase.name}: late completion preserves the current page text`);
      assert.equal(await page.getByText("本页意见已提交，处理结果见左侧对话。", { exact: true }).count(), 0);
      await page.evaluate(({ modelA, storageRun }) => {
        window.modelFixture.setScope({ projectId: "ui-project", runId: storageRun });
        window.modelFixture.setEditing(modelA);
      }, { modelA, storageRun });
      await pageReady(nextSha, nextPage);
    }
    await selectDocument(documentSha);
  });

  await step("annotation-read and image-render failures cannot dispatch empty or stale visuals and allow a later retry", async () => {
    await clearReferences();
    await referenceCheck("reference-marked.png").check();
    for (const failure of [{ kind: "annotations", sha: documentSha }, { kind: "bytes", sha: markedReferenceSha, mode: "corrupt" }]) {
      const before = await snapshot();
      const comment = await page.locator("#document-comment").inputValue();
      await page.evaluate((failure) => { window.modelFixture.failNext = failure; }, failure);
      await submitButton().click();
      await until(() => page.evaluate(() => window.modelFixture.failNext), (value) => value === null, "the deliberate failure was consumed");
      await finishSubmission();
      await page.locator(".document-error").waitFor();
      const after = await snapshot();
      assert.equal(after.intents.length, before.intents.length, "Failed visual preparation must never submit a partial request");
      assert.equal(await page.locator("#document-comment").inputValue(), comment);
      assert.equal(await sourceSelect().inputValue(), documentSha);
      assert.equal(await pageSelect().inputValue(), "0");
      assert.deepEqual(after.pages, before.pages);
      assert.deepEqual(after.writes, before.writes);
    }
    const retry = await submitVisuals();
    assert.equal(retry.documentVisuals.length, 2);
    assert.equal(retry.documentVisuals[0].role, "edit");
    assert.equal(retry.documentVisuals[1].role, "reference");
    assert.equal(retry.documentVisuals[1].assetSha256, markedReferenceSha);
    assert.equal(retry.documentVisuals[1].annotatedPngBase64, null);
    assert.equal(await page.locator(".document-error").count(), 0);
    assertVisualLimits(retry.documentVisuals);
  });
  assert.deepEqual(errors, []);
  assert.deepEqual(apiRequests, [], "this fixture never uses a project API");
  console.log(JSON.stringify({ passed: passed.length, browserErrors: 0 }, null, 2));
} catch (error) {
  const state = page ? await snapshot().catch(() => null) : null;
  console.error(JSON.stringify({ failed: current, passed, browserErrors: errors, state: state && { ...state,
    intents: state.intents.map(({ documentVisuals, ...intent }) => ({ ...intent, documentVisuals: documentVisuals?.map(({ pagePngBase64, annotatedPngBase64, ...visual }) =>
      ({ ...visual, plainBase64Length: pagePngBase64?.length, markedBase64Length: annotatedPngBase64?.length })) })) } }, null, 2));
  throw error;
} finally {
  await browser?.close(); await server.close();
}

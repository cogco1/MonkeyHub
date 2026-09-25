import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";

// Actual Drawing component and generated SDK; disposable HTTP sources only.
const root = fileURLToPath(new URL("..", import.meta.url)).replaceAll("\\", "/").replace(/\/$/, "");
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
const screenshots = await mkdtemp(join(tmpdir(), "archflow-drawing-canvas-"));
const assets = JSON.parse(execFileSync(process.env.PYTHON ?? "python", ["-c", "import json; from monkeydiagram.drawing_svg import dressing_assets; print(json.dumps(dressing_assets()))"], { cwd: resolve(root, "../../../.."), encoding: "utf8" }));
const modelA = { runId: "model-A", stateDigest: "a".repeat(64), assetSha256: "b".repeat(64) };
const modelB = { runId: "model-B", stateDigest: "c".repeat(64), assetSha256: "d".repeat(64) };
const savedDimension = { id: "saved-door-width", entityRef: "entity:wall", openingId: "door", placement: { offsetMm: 8 } };
const legacyDocument = { projectId: "drawing-project", runId: modelA.runId, assetSha256: "0".repeat(64),
  fileName: "retained-floor-plan.png", mimeType: "image/png", sizeBytes: 200, pageCount: 1,
  pages: [{ pageIndex: 0, width: 600, height: 400, rotation: 0 }], modelSource: modelA, sourceStageRef: "stage-A",
  drawingId: "floor-plan", revisionRef: "revision-legacy", generatedAt: "2026-09-22T00:00:00Z",
  viewRecipe: { kind: "cut-plan", frame: { origin: [0, 0, 1.2], far_depth: 1.2, scale: "1:100", crop_uv: [0, 0, 10, 6] },
    graphics: { cutLineMm: .35, visibleLineMm: .18, hatchSpacingMm: 2 }, dimensions: [savedDimension] } };
const fixture = `
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';
import DrawingCanvas from '/src/workspaces/monkeydiagram/DrawingCanvas';
import {UserPreferencesProvider,useStudio,usePreferences} from '/test/TestProviders.tsx';
import '/src/styles.css';
import '/@fs/${root}/../../../shared-web/src/base.css';
const modelA=${JSON.stringify(modelA)},modelB=${JSON.stringify(modelB)};
const metrics=window.drawingFixture={requests:[],documents:[${JSON.stringify(legacyDocument)}],handoffs:[],head:'stage-A'};
const stages=[{stageRef:'stage-A',label:'Accepted A',branchId:'main',modelSource:modelA}, {stageRef:'stage-B',label:'Accepted B',branchId:'main',modelSource:modelB}];
function App(){
 const studio=useStudio(),[active,setActive]=useState(true),[projectId,setProjectId]=useState('drawing-project');
 const preferences=usePreferences();
 const [installed]=useState(()=>{
  studio.documents=async()=>({projectId:metrics.projectId,runId:null,documents:metrics.documents.filter(d=>d.projectId===metrics.projectId)});
  studio.designHistory=async()=>({projectId:metrics.projectId,branchId:'main',branches:[{branchId:'main',headStageRef:metrics.head}],stages});
  studio.documentFile=async(runId,sha,name,revisionRef)=>{
   metrics.requests.push({kind:'bytes',runId,sha,revisionRef});
   const canvas=document.createElement('canvas');canvas.width=600;canvas.height=400;
   const c=canvas.getContext('2d');c.fillStyle='white';c.fillRect(0,0,600,400);c.strokeStyle='#283440';c.lineWidth=4;
   c.strokeRect(55,70,480,260);c.clearRect(250,325,90,12);c.lineWidth=1;c.beginPath();c.moveTo(250,350);c.lineTo(340,350);c.stroke();
   c.font='16px sans-serif';c.fillStyle='#283440';c.fillText('900 mm',263,375);
   return new File([await new Promise(resolve=>canvas.toBlob(resolve,'image/png'))],name,{type:'image/png'});
  };return true;
 });
 Object.assign(metrics,{projectId,setActive:value=>flushSync(()=>setActive(value)),setProject:value=>flushSync(()=>setProjectId(value)),setLanguage:preferences.setLanguage,setTheme:preferences.setTheme});
 return <DrawingCanvas key={projectId} projectId={projectId} active={active} onDesignRequest={request=>metrics.handoffs.push(request)}/>;
}
createRoot(document.getElementById('root')).render(<UserPreferencesProvider><App/></UserPreferencesProvider>);
`;
const server = await createServer({ root, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, publicDir: false, logLevel: "error", cacheDir: join(screenshots, "vite"),
  optimizeDeps: { noDiscovery: true, include: ["react", "react-dom", "react-dom/client", "react/jsx-runtime", "react/jsx-dev-runtime", "pdfjs-dist"] },
  server: { host: "127.0.0.1", port: 0, strictPort: true }, plugins: [{ name: "drawing-fixture",
    resolveId(id) { if (id === "/drawing-fixture.tsx") return `${root}/drawing-fixture.tsx`; },
    load(id) { if (id === `${root}/drawing-fixture.tsx`) return fixture; },
    configureServer(vite) { vite.middlewares.use((request, response, next) => {
      if (new URL(request.url, "http://fixture.local").pathname !== "/") return next();
      response.setHeader("Content-Type", "text/html");
      response.end('<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><style>html,body,#root{height:100%;margin:0}#root{position:relative}</style></head><body><div id="root" class="project-workspace"></div><script type="module" src="/drawing-fixture.tsx"></script></body></html>');
    }); },
  }],
});
let browser, page, hold = false, release, broken = false, current;
const requests = [], statusRequests = [], drives = [], errors = [], passed = [], vectorBytes = new Map();
async function step(name, action) { current = name; await action(); passed.push(name); console.log(`PASS ${name}`); }
const revision = () => page.getByRole("combobox", { name: "Drawing revision", exact: true });
const source = () => page.getByRole("combobox", { name: "Model to draw", exact: true });
async function until(read, accepts, label) {
  const deadline = Date.now() + 12000; let value;
  do { assert.deepEqual(errors, []); value = await read(); if (accepts(value)) return value; await new Promise(resolve => setTimeout(resolve, 50)); } while (Date.now() < deadline);
  assert.fail(`${label}: ${JSON.stringify(value)}`);
}
try {
  await server.listen();
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.setDefaultTimeout(12000);
  page.on("pageerror", error => errors.push(String(error)));
  await page.route("**/api/drawings/plans/dimensions?*", route => route.fulfill({ json: { lengthUnit: "meter", dimensions: [
    { entityRef: "entity:wall", openingId: "door", label: "Entry wall / Door", parameterKey: "door-width", canDrive: true },
  ] } }));
  await page.route("**/api/drawings/plans", async route => {
    const body = route.request().postDataJSON(); requests.push(body);
    const serial = requests.length;
    if (hold) { hold = false; await new Promise(resolve => { release = resolve; }); release = null; }
    const result = { projectId: body.projectId, runId: body.modelSource.runId, assetSha256: String(serial).padStart(64, "0"),
      fileName: `floor-plan-${serial}.png`, mimeType: "image/png", sizeBytes: 200, pageCount: 1,
      pages: [{ pageIndex: 0, width: 600, height: 400, rotation: 0 }], modelSource: body.modelSource, sourceStageRef: body.sourceStageRef,
      drawingId: "floor-plan", revisionRef: `revision-${serial}`, generatedAt: `2026-09-23T00:00:0${serial}Z`,
      viewRecipe: { kind: "cut-plan", frame: { origin: [0, 0, body.cutHeight], far_depth: body.cutHeight - body.bottom,
        scale: `1:${body.scaleDenominator}`, crop_uv: body.cropUv ?? [0, 0, 10, 6] },
        graphics: { cutLineMm: body.cutLineMm, visibleLineMm: body.visibleLineMm, hatchSpacingMm: body.hatchSpacingMm },
        hiddenObjectIds: body.hiddenObjectIds ?? [], dimensions: body.dimensions, dressing: body.dressing ?? [] } };
    await page.evaluate(result => window.drawingFixture.documents.push(result), result);
    await route.fulfill({ status: 201, json: result });
  });
  await page.route("**/api/drawings/plans/vector?*", async route => {
    const revisionRef = new URL(route.request().url()).searchParams.get("revisionRef");
    const doc = await page.evaluate(ref => window.drawingFixture.documents.find(d => d.revisionRef === ref), revisionRef);
    const scale = Number(doc.viewRecipe.frame.scale.split(":")[1]);
    const dressing = (doc.viewRecipe.dressing ?? []).map(item => `<polyline data-dressing-id="${item.id}" points="1,1 2,2"/>`).join("");
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${10000 / scale}mm" height="${6000 / scale}mm" viewBox="0 0 10 6" data-unit="meter" data-scale="1:${scale}"><title>${doc.fileName}</title><polyline data-object="obj-wall" points="1,1 9,1 9,5 1,5 1,1" fill="none" stroke="black" stroke-width="0.04"/><g id="dressing">${dressing}</g></svg>`;
    vectorBytes.set(revisionRef, svg);
    return route.fulfill({ json: { svg, assets,
      anchors: [{ objectId: "obj-wall", positionUv: [5, 1] }] } });
  });
  await page.route("**/api/drawings/plans/status", async route => {
    const body = route.request().postDataJSON(); statusRequests.push(body);
    const sourceDocument = await page.evaluate(body => window.drawingFixture.documents.find(d => d.revisionRef === body.revisionRef), body);
    const targetStageRef = body.targetStageRef ?? await page.evaluate(() => window.drawingFixture.head);
    const targetModelSource = body.targetModelSource ?? (targetStageRef === "stage-B" ? modelB : modelA);
    const outdated = targetModelSource.runId !== sourceDocument.modelSource.runId;
    await route.fulfill({ json: { status: broken ? "partially-broken" : outdated ? "outdated" : "current",
      detail: broken ? "Door no longer exists." : outdated ? "Selected model differs from this drawing." : "Exact source retained.",
      targetModelSource, targetStageRef, lengthUnit: "meter",
      dimensions: sourceDocument.viewRecipe.dimensions.map(d => ({ ...d, status: broken === "outside-view" ? "outside-view" : broken ? "missing" : "resolved", value: .9, label: "900 mm",
        offsetMm: d.placement.offsetMm, canDrive: !outdated && !broken, parameterKey: "door-width", detail: broken ? "Door anchor is missing." : null })) } });
  });
  await page.route("**/api/drawings/plans/dimension-proposal", route => {
    const body = route.request().postDataJSON(); drives.push(body);
    return route.fulfill({ json: { proposalId: "dimension-proposal", baseStateDigest: body.targetModelSource.stateDigest } });
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/?lang=en`);
  await step("a new cut plan starts with model and appearance choices without a door-width experiment", async () => {
    await page.getByRole("button", { name: "Generate cut plan", exact: true }).waitFor();
    await until(() => source().inputValue(), value => value === "stage-A", "accepted source selected");
    assert.equal(await page.getByRole("combobox", { name: "Choose a door", exact: true }).count(), 0);
    assert.equal(await page.getByRole("button", { name: "Add dimension", exact: true }).count(), 0);
    assert.equal(await page.getByRole("group", { name: "Saved dimensions", exact: true }).count(), 0);
    await page.getByRole("button", { name: "Generate cut plan", exact: true }).click();
    await page.locator('.drawing-preview__viewport[data-ready="true"]').waitFor();
    assert.deepEqual(requests[0].modelSource, modelA); assert.equal(requests[0].sourceStageRef, "stage-A");
    assert.equal(requests[0].cutHeight, 1.2); assert.deepEqual(requests[0].dimensions, []);
  });
  let oldRevision;
  await step("appearance updates retain semantic dimension and previous revision without design calls", async () => {
    await revision().selectOption(JSON.stringify([legacyDocument.runId, legacyDocument.assetSha256, legacyDocument.revisionRef]));
    await page.getByText("900 mm", { exact: true }).waitFor();
    oldRevision = await revision().inputValue();
    await page.getByLabel("Scale denominator (1 : n)", { exact: true }).fill("50");
    await page.getByLabel("Label offset (paper mm)", { exact: true }).fill("16");
    await page.getByRole("button", { name: "Save appearance as a revision", exact: true }).click();
    await until(() => revision().inputValue(), value => value !== oldRevision, "new drawing revision opened");
    assert.equal(requests[1].previousRevisionRef, legacyDocument.revisionRef); assert.equal(requests[1].drawingId, "floor-plan");
    assert.equal(requests[1].dimensions[0].id, savedDimension.id); assert.equal(requests[1].dimensions[0].placement.offsetMm, 16);
    assert.equal(drives.length, 0);
    await revision().selectOption(oldRevision); assert.equal(await page.getByLabel("Label offset (paper mm)", { exact: true }).inputValue(), "8");
  });
  await step("returning after a new accepted Stage follows the source branch head unless explicitly pinned", async () => {
    const generationCount = requests.length;
    const selectedBefore = await revision().inputValue();
    const retainedBefore = await page.evaluate(() => structuredClone(window.drawingFixture.documents.find(document => document.revisionRef === "revision-legacy")));
    const exactPage = { runId: retainedBefore.runId, assetSha256: retainedBefore.assetSha256, revisionRef: retainedBefore.revisionRef };
    await page.evaluate(() => { window.drawingFixture.setActive(false); window.drawingFixture.head = "stage-B"; });
    await page.evaluate(() => window.drawingFixture.setActive(true));
    await page.locator('.drawing-status[data-status="outdated"]').waitFor();
    assert.equal(await source().inputValue(), "stage-B");
    assert.equal(statusRequests.at(-1).targetModelSource, undefined, "the backend resolves the exact drawing's branch, not a generic latest Stage");
    assert.deepEqual(statusRequests.at(-1), exactPage, "only the comparison target is refreshed; status still reads the original exact drawing");
    assert.equal(await revision().inputValue(), selectedBefore, "following the branch head never selects or creates another drawing revision");
    assert.equal(requests.length, generationCount, "returning to Drawing cannot generate or rebuild automatically");
    assert.deepEqual(await page.evaluate(() => window.drawingFixture.documents.find(document => document.revisionRef === "revision-legacy")), retainedBefore,
      "the original document's ModelSource, Stage, bytes and recipe remain unchanged");
    assert.deepEqual(await page.evaluate(() => window.drawingFixture.requests.filter(request => request.kind === "bytes").at(-1)),
      { kind: "bytes", runId: exactPage.runId, sha: exactPage.assetSha256, revisionRef: exactPage.revisionRef }, "the displayed file still comes from the original exact revision");
    assert.equal(await page.locator(".drawing-preview__tools span").innerText(), retainedBefore.fileName);
    await source().selectOption("stage-A");
    await page.locator('.drawing-status[data-status="current"]').waitFor();
    await page.evaluate(() => window.drawingFixture.setActive(false));
    await page.evaluate(() => window.drawingFixture.setActive(true));
    await page.locator('.drawing-status[data-status="current"]').waitFor();
    assert.equal(await source().inputValue(), "stage-A", "explicit historical source survives reactivation");
    assert.deepEqual(statusRequests.at(-1).targetModelSource, modelA);
  });
  await step("source change is explicit and rebuild preserves retained dimension intention", async () => {
    const generationCount = requests.length;
    const oldSelection = await revision().inputValue();
    await source().selectOption("stage-B");
    await page.locator('.drawing-status[data-status="outdated"]').waitFor();
    assert.equal(await page.getByText("Change design width", { exact: true }).count(), 0);
    assert.equal(await page.getByRole("button", { name: "Create design candidate", exact: true }).count(), 0);
    await page.getByRole("button", { name: "Rebuild on selected model", exact: true }).click();
    await page.locator('.drawing-status[data-status="current"]').waitFor();
    assert.equal(requests.length, generationCount + 1, "only the explicit rebuild creates the revision");
    assert.notEqual(await revision().inputValue(), oldSelection);
    assert.deepEqual(requests.at(-1).modelSource, modelB); assert.equal(requests.at(-1).dimensions[0].id, savedDimension.id);
  });
  await step("broken anchors remain visible and cannot drive, while prior revisions remain selectable", async () => {
    broken = true;
    await page.getByRole("button", { name: "Refresh sources", exact: true }).click();
    await page.locator('.drawing-status[data-status="partially-broken"]').waitFor();
    assert.equal(await page.getByText("Door anchor is missing.", { exact: true }).count(), 1);
    assert.equal(await page.getByRole("button", { name: "Create design candidate", exact: true }).count(), 0);
    assert.equal(await revision().locator("option").count(), 5);
    broken = "outside-view";
    await page.getByRole("button", { name: "Refresh sources", exact: true }).click();
    await page.getByText("Dimension falls outside the drawing. Adjust its paper offset and save appearance.", { exact: true }).waitFor();
    assert.equal(await page.getByLabel("Label offset (paper mm)", { exact: true }).isEnabled(), true);
    broken = false;
  });
  await step("a late generation response never changes another workspace selection", async () => {
    const before = await revision().inputValue(); hold = true;
    await page.getByRole("button", { name: "Save appearance as a revision", exact: true }).click();
    await until(() => Promise.resolve(Boolean(release)), Boolean, "held generation");
    await page.evaluate(() => window.drawingFixture.setActive(false)); release();
    await page.getByRole("button", { name: "Save appearance as a revision", exact: true }).waitFor();
    assert.equal(await revision().inputValue(), before);
    await page.evaluate(() => window.drawingFixture.setActive(true));
  });
  await step("an empty representation field cannot silently reuse old values during rebuild", async () => {
    await until(() => page.getByLabel("Cut height (meter)", { exact: true }).isEnabled(), Boolean, "active form");
    const before = requests.length;
    await page.getByLabel("Cut height (meter)", { exact: true }).fill("");
    await page.getByRole("button", { name: "Rebuild on selected model", exact: true }).click();
    assert.equal(requests.length, before);
    assert.equal(await page.getByLabel("Cut height (meter)", { exact: true }).evaluate(node => node.validity.valueMissing), true);
    await page.getByLabel("Cut height (meter)", { exact: true }).fill("1.2");
  });
  await step("SVG people and trees remain independently editable through drag, keyboard, mirror, save and reopen", async () => {
    await page.getByRole("button", { name: "Add person", exact: true }).click();
    const object = page.locator('[data-dressing-id]').first();
    await object.waitFor();
    const id = await object.getAttribute("data-dressing-id");
    await page.getByLabel("Symbol size (meter)", { exact: true }).fill("1.2");
    await page.getByRole("button", { name: "Mirror horizontally", exact: true }).click();
    await object.focus(); await page.keyboard.press("ArrowRight");
    assert.ok(Number(await page.getByLabel("Horizontal position / offset (meter)", { exact: true }).inputValue()) > 5);
    const bounds = await object.boundingBox();
    await page.mouse.move(bounds.x+bounds.width/2, bounds.y+bounds.height/2); await page.mouse.down();
    await page.mouse.move(bounds.x+bounds.width/2+35, bounds.y+bounds.height/2-20, { steps: 5 }); await page.mouse.up();
    assert.ok(Number(await page.getByLabel("Vertical position / offset (meter)", { exact: true }).inputValue()) > 3);
    await page.getByRole("button", { name: "Add tree", exact: true }).click();
    assert.equal(await page.locator('[data-dressing-id]').count(), 2);
    await page.getByLabel("Position follows", { exact: true }).selectOption("obj-wall");
    const old = await revision().inputValue();
    await page.getByRole("button", { name: "Save appearance as a revision", exact: true }).click();
    await until(() => revision().inputValue(), value => value !== old, "dressing revision");
    const newRevision = await revision().inputValue();
    assert.equal(requests.at(-1).dressing.length, 2);
    assert.equal(requests.at(-1).dressing[0].id, id); assert.equal(requests.at(-1).dressing[0].flipped, true);
    assert.equal(requests.at(-1).dressing[1].anchorObjectId, "obj-wall");
    await revision().selectOption(old); await until(() => page.locator('[data-dressing-id]').count(), value => value === 0, "old recipe remains intact");
    await revision().selectOption(newRevision); await until(() => page.locator('[data-dressing-id]').count(), value => value === 2, "reopened SVG objects");
    await page.getByLabel("Selected object", { exact: true }).selectOption(id);
    await page.getByRole("button", { name: "Delete object", exact: true }).click();
    assert.equal(await page.locator('[data-dressing-id]').count(), 1);
    await page.getByRole("button", { name: "Save appearance as a revision", exact: true }).click();
    await until(() => revision().inputValue(), value => value !== newRevision, "deletion retained");
    assert.equal(requests.at(-1).dressing.length, 1);
    await page.waitForFunction(() => { const image = document.querySelector(".drawing-vector-base"); return image?.complete && image.naturalWidth > 0; });
    assert.equal(drives.length, 0, "appearance never invokes a design proposal");
  });
  await step("SVG download preserves exact saved vector bytes, scale, source objects and entourage", async () => {
    const downloadButton = page.getByRole("button", { name: "Download SVG", exact: true });
    await until(() => downloadButton.isEnabled(), Boolean, "saved SVG available");
    const [, , revisionRef] = JSON.parse(await revision().inputValue());
    const [download] = await Promise.all([page.waitForEvent("download"), downloadButton.click()]);
    const svg = await readFile(await download.path(), "utf8");
    assert.equal(svg, vectorBytes.get(revisionRef), "download is the retained SVG, not the display with dressing removed");
    assert.match(download.suggestedFilename(), /\.svg$/);
    assert.match(svg, /width="[\d.]+mm"/); assert.match(svg, /data-scale="1:100"/);
    assert.match(svg, /data-object="obj-wall"/); assert.match(svg, /data-dressing-id=/);
    await page.getByRole("button", { name: "Add person", exact: true }).click();
    assert.equal(await downloadButton.isDisabled(), true, "unsaved appearance cannot be downloaded as a retained version");
    await page.getByText("Save appearance changes before downloading SVG.", { exact: true }).waitFor();
    await revision().selectOption(oldRevision);
    await until(() => downloadButton.isEnabled(), Boolean, "historical SVG available");
    const [oldDownload] = await Promise.all([page.waitForEvent("download"), downloadButton.click()]);
    const oldSvg = await readFile(await oldDownload.path(), "utf8");
    assert.equal(oldSvg, vectorBytes.get(legacyDocument.revisionRef));
    assert.doesNotMatch(oldSvg, /data-dressing-id=/, "historical download never inherits new or unsaved entourage");
  });
  await step("retained dimensions can be removed without rewriting their original revision", async () => {
    await page.getByRole("button", { name: "Remove dimension", exact: true }).click();
    assert.equal(await page.getByRole("group", { name: "Saved dimensions", exact: true }).count(), 0);
    await page.getByRole("button", { name: "Save appearance as a revision", exact: true }).click();
    await until(() => revision().inputValue(), value => value !== oldRevision, "dimension removal retained");
    assert.deepEqual(requests.at(-1).dimensions, []);
    assert.deepEqual(await page.evaluate(() => window.drawingFixture.documents.find(document => document.revisionRef === "revision-legacy").viewRecipe.dimensions), [savedDimension]);
    assert.equal(drives.length, 0);
  });
  await step("keyboard controls and English/Chinese narrow layouts keep the drawing usable", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => window.drawingFixture.setLanguage("zh-CN"));
    await page.getByRole("button", { name: "保存表达新版本", exact: true }).waitFor();
    await page.evaluate(() => { document.querySelector('.drawing-body').scrollTop = 0; document.querySelector('.drawing-controls__fields').scrollTop = 0; });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: join(screenshots, "drawing-narrow.png"), fullPage: true });
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.evaluate(() => { window.drawingFixture.setLanguage("en"); window.drawingFixture.setTheme("dark"); });
    await page.evaluate(() => { document.querySelector('.drawing-body').scrollTop = 0; document.querySelector('.drawing-controls__fields').scrollTop = 0; });
    const saveBounds = await page.getByRole("button", { name: "Save appearance as a revision", exact: true }).boundingBox();
    assert.ok(saveBounds && saveBounds.y >= 0 && saveBounds.y + saveBounds.height <= 900, "Save stays visible beside the drawing while settings scroll");
    await page.screenshot({ path: join(screenshots, "drawing-wide-dark.png"), fullPage: true });
    await source().focus(); await page.keyboard.press("ArrowUp"); await page.keyboard.press("Enter");
    assert.equal(await source().inputValue(), "stage-A");
  });
  await step("a response for a previous project never opens a document in the new project", async () => {
    hold = true;
    await page.getByRole("button", { name: "Save appearance as a revision", exact: true }).click();
    await until(() => Promise.resolve(Boolean(release)), Boolean, "held project generation");
    await page.evaluate(() => window.drawingFixture.setProject("another-project"));
    await page.getByRole("button", { name: "Generate cut plan", exact: true }).waitFor();
    release();
    await until(() => Promise.resolve(release === null), Boolean, "old response released");
    assert.equal(await revision().inputValue(), "");
    assert.equal(await revision().locator("option").count(), 1);
    assert.equal(await page.locator(".drawing-preview").count(), 0);
  });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed, screenshots, generationRequests: requests.length, dimensionProposals: drives.length }));
} catch (error) {
  if (page) await page.screenshot({ path: join(screenshots, "failure.png"), fullPage: true }).catch(() => {});
  console.error(`FAIL ${current}; screenshots: ${screenshots}`, errors); throw error;
} finally { if (release) release(); await browser?.close(); await server.close(); }

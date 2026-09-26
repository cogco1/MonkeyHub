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
// The runtime's dressing symbols, and the projection's own SVG for the retained revision: its groups, each
// line's component and material, and a poché cut. The fixture's later revisions are written in the same shape.
const [assets, retainedSvg] = JSON.parse(execFileSync(process.env.PYTHON ?? "python", ["-c", [
  "import json",
  "from archflow.adapters.occt_backend import OcctDrawingPolyline as Line, OcctDrawingRegion as Region",
  "from monkeydiagram.drawing_svg import dressing_assets, drawing_svg",
  "wall = ((1, 1), (9, 1), (9, 5), (1, 5), (1, 1))",
  "svg = drawing_svg([Line('obj-wall', 'section', wall), Line('obj-table', 'visible', ((7, 4), (8.5, 4), (8.5, 3), (7, 3), (7, 4)))],"
    + " crop_uv=(0, 0, 10, 6), unit='meter', scale_denominator=100, hidden_lines=False, title='retained-floor-plan',"
    + " regions=[Region('obj-wall', (wall, ((1.2, 1.2), (8.8, 1.2), (8.8, 4.8), (1.2, 4.8), (1.2, 1.2))))],"
    + " graphics={'cutLineMm': .35, 'visibleLineMm': .18, 'hatchSpacingMm': 2, 'hatch': {'byMaterial': {'Concrete': {'poche': True}}}},"
    + " semantics={'obj-wall': {'component': 'Wall', 'material': 'Concrete'}, 'obj-table': {'component': 'Table', 'material': 'Oak'}})",
  "print(json.dumps([dressing_assets(), svg.decode('utf-8')]))",
].join("; ")], { cwd: resolve(root, "../../../.."), encoding: "utf8" }));
const modelA = { runId: "model-A", stateDigest: "a".repeat(64), assetSha256: "b".repeat(64) };
const modelB = { runId: "model-B", stateDigest: "c".repeat(64), assetSha256: "d".repeat(64) };
const externalAsset = { runId: "imported-model", assetSha256: "e".repeat(64) };
const externalArtifact = { ...externalAsset, sha256: externalAsset.assetSha256, projectId: "drawing-project",
  artifactId: externalAsset.assetSha256, fileName: "imported-house.3dm", format: "3dm", representation: "external",
  modelSource: null, sourceStageRef: null, available: true,
  sourceImport: { sourceFileName: "imported-house.skp", sourceArtifact: { runId: "imported-model", assetSha256: "8".repeat(64) },
    conversion: { provider: "SketchUp C API", sourceFormat: "skp", targetFormat: "3dm", representation: "external",
      warnings: ["Layer Roof was skipped."] } } };
const unsupportedExact = { ...externalArtifact, runId: "seat-rhino-run", sha256: "9".repeat(64),
  artifactId: "9".repeat(64), fileName: "seat-rhino.3dm", representation: "exact" };
const savedDimension = { id: "saved-door-width", entityRef: "entity:wall", openingId: "door", placement: { offsetMm: 8 } };
// The projected objects' component and material, as a design-state model names them.
const projected = { "obj-wall": ["Wall", "Concrete"], "obj-table": ["Table", "Oak"] };
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
const metrics=window.drawingFixture={requests:[],documents:[${JSON.stringify(legacyDocument)}],artifacts:[${JSON.stringify(externalArtifact)},${JSON.stringify(unsupportedExact)}],uploads:[],handoffs:[],head:'stage-A',revision:0,headDrawable:true};
const stages=[{stageRef:'stage-A',label:'Accepted A',branchId:'main',modelSource:modelA}, {stageRef:'stage-B',label:'Accepted B',branchId:'main',modelSource:modelB}];
function App(){
 const studio=useStudio(),[active,setActive]=useState(true),[projectId,setProjectId]=useState('drawing-project');
 const preferences=usePreferences();
 const [installed]=useState(()=>{
  studio.documents=async()=>({projectId:metrics.projectId,runId:null,documents:metrics.documents.filter(d=>d.projectId===metrics.projectId)});
  studio.artifacts=async()=>({projectId:metrics.projectId,artifacts:metrics.artifacts.filter(a=>a.projectId===metrics.projectId),skippedRuns:[]});
  studio.uploadModel=async(projectId,file)=>{ metrics.uploads.push(file.name); const artifact={...metrics.artifacts[0],projectId,fileName:'house.3dm',runId:'uploaded-skp',sha256:'f'.repeat(64),sourceImport:{...metrics.artifacts[0].sourceImport,sourceFileName:file.name}}; metrics.artifacts.push(artifact); return artifact; };
  studio.designHistory=async()=>({projectId:metrics.projectId,branchId:'main',branches:[{branchId:'main',headStageRef:metrics.head}],stages});
  studio.workingSource=async(workspace)=>{
   const model=metrics.head==='stage-B'?modelB:modelA;
   const head={runId:model.runId,stateDigest:model.stateDigest,recordDigest:'r',sourceStageRef:metrics.head,branchId:'main',accepted:true,origin:'working-position',label:null,modelSource:model,lineage:[model.runId]};
   return metrics.headDrawable?{projectId:metrics.projectId,workspace,policy:'live',revisionSha256:String(metrics.revision),head,compatible:true,source:model,stageRef:metrics.head,reason:null,warnings:[]}
    :{projectId:metrics.projectId,workspace,policy:'live',revisionSha256:String(metrics.revision),head,compatible:false,source:null,stageRef:null,reason:'The current working version has no complete model to draw from yet.',warnings:[]};
  };
  studio.workingRevision=async()=>({projectId:metrics.projectId,revisionSha256:String(metrics.revision)});
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
let browser, page, hold = false, release, broken = false, refuseNext = false, current;

const requests = [], statusRequests = [], dimensionQueries = [], drives = [], errors = [], passed = [], vectorBytes = new Map();
const sectionRequests = [], planOnlyCalls = [];

async function step(name, action) { current = name; await action(); passed.push(name); console.log(`PASS ${name}`); }
const revision = () => page.getByRole("combobox", { name: "Drawing", exact: true });
const source = () => page.getByRole("combobox", { name: "Version to draw", exact: true });
// GH-302: appearance saves itself; the only save button left is Retry after a refusal.
const saveButton = () => page.getByRole("button", { name: "Retry saving", exact: true });
const objectChoice = () => page.getByRole("combobox", { name: "Projected object", exact: true });
// The page's own drawing of a projected object, and where a plan point is on screen.
const drawn = object => page.locator(`svg.drawing-vector-base .drawing-plan__lines [data-object="${object}"]`);
const planPoint = (x, y) => page.evaluate(([x, y]) => {
  const matrix = document.querySelector("svg.drawing-vector-base").getScreenCTM();
  return { x: matrix.a * x + matrix.c * y + matrix.e, y: matrix.b * x + matrix.d * y + matrix.f };
}, [x, y]);
const legacyKey = JSON.stringify([legacyDocument.runId, legacyDocument.assetSha256, legacyDocument.revisionRef]);
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
  await page.route("**/api/drawings/plans/dimensions?*", route => {
    dimensionQueries.push(Object.fromEntries(new URL(route.request().url()).searchParams));
    return route.fulfill({ json: { lengthUnit: "meter", dimensions: [
    { entityRef: "entity:wall", openingId: "door", label: "Entry wall / Door", parameterKey: "door-width", canDrive: true },
  ] } });
  });
  await page.route("**/api/drawings/plans", async route => {
    const body = route.request().postDataJSON(); requests.push(body);
    const serial = requests.length;
    if (hold) { hold = false; await new Promise(resolve => { release = resolve; }); release = null; }
    if (refuseNext) { refuseNext = false; return route.fulfill({ status: 503, json: { code: "RUNTIME_BUSY", detail: "Fixture refused this drawing revision." } }); }
    // Like the runtime: a chosen version is kept on later revisions until one follows again.
    const previous = body.previousRevisionRef ? await page.evaluate(ref => window.drawingFixture.documents.find(d => d.revisionRef === ref) ?? null, body.previousRevisionRef) : null;
    const follow = body.follow ?? previous?.viewRecipe?.follow;
    const result = { projectId: body.projectId, runId: body.modelSource?.runId ?? body.sourceAsset.runId, assetSha256: String(serial).padStart(64, "0"),
      fileName: `floor-plan-${serial}.png`, mimeType: "image/png", sizeBytes: 200, pageCount: 1,
      pages: [{ pageIndex: 0, width: 600, height: 400, rotation: 0 }], modelSource: body.modelSource ?? null, sourceStageRef: body.sourceStageRef ?? null,
      drawingId: "floor-plan", revisionRef: `revision-${serial}`, generatedAt: `2026-09-23T00:00:0${serial}Z`,
      viewRecipe: { kind: "cut-plan", frame: { origin: [0, 0, body.cutHeight], far_depth: body.cutHeight - body.bottom,
        scale: `1:${body.scaleDenominator}`, crop_uv: body.cropUv ?? [0, 0, 10, 6] },
        graphics: { cutLineMm: body.cutLineMm, visibleLineMm: body.visibleLineMm, hatchSpacingMm: body.hatchSpacingMm },
        hiddenObjectIds: body.hiddenObjectIds ?? previous?.viewRecipe?.hiddenObjectIds ?? [], dimensions: body.dimensions, dressing: body.dressing ?? [],
        ...(body.sourceAsset ? { sourceAsset: body.sourceAsset } : {}),
        ...(follow === "frozen" ? { follow: "frozen" } : {}) } };
    await page.evaluate(result => window.drawingFixture.documents.push(result), result);
    await route.fulfill({ status: 201, json: result });
  });
  await page.route("**/api/drawings/section-perspectives", async route => {
    const body = route.request().postDataJSON(); sectionRequests.push(body);
    const serial = sectionRequests.length;
    const result = { projectId: body.projectId, runId: body.modelSource?.runId ?? body.sourceAsset.runId, assetSha256: `e${String(serial).padStart(63, "0")}`,
      fileName: "section-perspective.png", mimeType: "image/png", sizeBytes: 200, pageCount: 1,
      pages: [{ pageIndex: 0, width: 600, height: 400, rotation: 0 }], modelSource: body.modelSource ?? null, sourceStageRef: body.sourceStageRef ?? null,
      drawingId: "section-perspective", revisionRef: `section-revision-${serial}`, generatedAt: `2026-09-24T00:00:0${serial}Z`,
      viewRecipe: { kind: "section-perspective", request: { section: body.section, camera: body.camera },
        ...(body.sourceAsset ? { sourceAsset: body.sourceAsset, follow: "frozen" } : {}), crop_uv: [-0.3, -0.6, 6.3, 3.6], scale: "1:100" } };
    await page.evaluate(result => window.drawingFixture.documents.push(result), result);
    await route.fulfill({ status: 201, json: result });
  });
  await page.route("**/api/drawings/plans/vector?*", async route => {
    const revisionRef = new URL(route.request().url()).searchParams.get("revisionRef");
    const doc = await page.evaluate(ref => window.drawingFixture.documents.find(d => d.revisionRef === ref), revisionRef);
    if (doc.viewRecipe.kind !== "cut-plan") { planOnlyCalls.push(["vector", revisionRef]); return route.fulfill({ status: 422, json: { code: "DRAWING_PLAN_REQUIRED", detail: "Not a cut plan." } }); }
    const scale = Number(doc.viewRecipe.frame.scale.split(":")[1]);
    const dressing = (doc.viewRecipe.dressing ?? []).map(item => `<g data-dressing="${item.id}" data-asset="${item.assetId}"><polyline points="1,1 2,2"/></g>`).join("");
    // Like the projection: a line names its object, component and material, its group is its role, and a hidden object is not drawn.
    const hidden = new Set(doc.viewRecipe.hiddenObjectIds ?? []);
    const line = (id, tag, points, paint = "") => hidden.has(id) ? ""
      : `<${tag} data-object="${id}" data-component="${projected[id][0]}" data-material="${projected[id][1]}"${paint} points="${points}"/>`;
    // A plan past the inline line limit: many short lines of one object.
    const dense = () => `<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 10 6" data-unit="meter" data-scale="1:100"><g id="visible" fill="none" stroke="#000" stroke-width="0.018">`
      + Array.from({ length: 20001 }, (_, index) => { const x = index % 100 / 10, y = Math.floor(index / 100) / 40;
        return `<polyline data-object="obj-screen" data-component="Screen" data-material="Steel" points="${x},${y} ${x + .05},${y}"/>`; }).join("")
      + `</g><g id="dressing"></g></svg>`;
    const svg = revisionRef === "revision-dense" ? dense() : revisionRef === legacyDocument.revisionRef ? retainedSvg
      : `<svg xmlns="http://www.w3.org/2000/svg" width="${10000 / scale}mm" height="${6000 / scale}mm" viewBox="0 0 10 6" data-unit="meter" data-scale="1:${scale}"><title>${doc.fileName}</title>`
      + `<g id="visible" fill="none" stroke="#000" stroke-width="0.018">${line("obj-table", "polyline", "7,2 8.5,2 8.5,3 7,3 7,2")}</g>`
      + `<g id="section-hatch" fill="none" stroke="#000" stroke-width="0.01">${line("obj-wall", "polygon", "1,1 9,1 9,1.2 1,1.2", ' fill="#000" fill-rule="evenodd" stroke="none"')}</g>`
      + `<g id="section" fill="none" stroke="#000" stroke-width="0.035">${line("obj-wall", "polyline", "1,1 9,1 9,5 1,5 1,1")}</g>`
      + `<g id="dressing">${dressing}</g></svg>`;
    vectorBytes.set(revisionRef, svg);
    return route.fulfill({ json: { svg, assets,
      anchors: [{ objectId: "obj-wall", positionUv: [5, 1] }] } });
  });
  await page.route("**/api/drawings/plans/status", async route => {
    const body = route.request().postDataJSON(); statusRequests.push(body);
    const sourceDocument = await page.evaluate(body => window.drawingFixture.documents.find(d => d.revisionRef === body.revisionRef), body);

    if (sourceDocument.viewRecipe.kind !== "cut-plan") { planOnlyCalls.push(["status", body.revisionRef]); return route.fulfill({ status: 422, json: { code: "DRAWING_PLAN_REQUIRED", detail: "Not a cut plan." } }); }

    if (sourceDocument.viewRecipe.sourceAsset) return route.fulfill({ json: { status: "current", detail: "Original object geometry retained.",
      targetModelSource: null, targetStageRef: null, lengthUnit: "meter", bindingChanged: false, dimensions: [] } });

    const kept = sourceDocument.viewRecipe.follow === "frozen" && !body.targetModelSource && !body.targetStageRef;
    if (!kept && !body.targetModelSource && !body.targetStageRef && !await page.evaluate(() => window.drawingFixture.headDrawable))
      return route.fulfill({ json: { status: "outdated", detail: "The current model cannot be drawn yet: no exact STEP.", targetModelSource: null,
        targetStageRef: null, lengthUnit: null, bindingChanged: false, dimensions: [] } });
    const targetStageRef = body.targetStageRef ?? (kept ? sourceDocument.sourceStageRef : await page.evaluate(() => window.drawingFixture.head));
    const targetModelSource = body.targetModelSource ?? (kept ? sourceDocument.modelSource : targetStageRef === "stage-B" ? modelB : modelA);
    const outdated = targetModelSource.runId !== sourceDocument.modelSource.runId;
    await route.fulfill({ json: { status: broken ? "partially-broken" : outdated ? "outdated" : "current",
      detail: broken ? "Door no longer exists." : outdated ? "Selected model differs from this drawing." : "Exact source retained.",
      targetModelSource, targetStageRef, lengthUnit: "meter", bindingChanged: outdated,
      dimensions: sourceDocument.viewRecipe.dimensions.map(d => ({ ...d, status: broken === "outside-view" ? "outside-view" : broken ? "missing" : "resolved", value: .9, label: "900 mm",
        offsetMm: d.placement.offsetMm, canDrive: !outdated && !broken, parameterKey: "door-width", detail: broken ? "Door anchor is missing." : null })) } });
  });
  await page.route("**/api/drawings/plans/dimension-proposal", route => {
    const body = route.request().postDataJSON(); drives.push(body);
    return route.fulfill({ json: { proposalId: "dimension-proposal", baseStateDigest: body.targetModelSource.stateDigest } });
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/?lang=en`);
  await step("the newest drawing opens LIVE on the Working Head and a new cut plan draws the head", async () => {
    await until(() => revision().inputValue(), value => value === legacyKey, "newest drawing opened without choosing a revision");
    await page.locator('.drawing-status[data-follow="live"][data-status="current"]').waitFor();
    await page.getByText("Current with the project model", { exact: true }).waitFor();
    assert.equal(requests.length, 0, "a drawing already reading the head is not rebuilt");
    assert.equal(statusRequests.at(-1).targetModelSource, undefined, "LIVE status asks the runtime for the Working Head");
    assert.equal(await page.getByRole("combobox", { name: "Choose a door", exact: true }).count(), 0);
    assert.equal(await page.getByRole("button", { name: "Add dimension", exact: true }).count(), 0);
    await revision().selectOption("");
    await page.getByRole("button", { name: "Generate cut plan", exact: true }).waitFor();
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
    assert.equal(await saveButton().count(), 0, "appearance has no Save button, only a retry after a refusal");
    await page.getByLabel("Scale denominator (1 : n)", { exact: true }).fill("50");
    await page.getByLabel("Label offset (paper mm)", { exact: true }).fill("16");
    // GH-302: an open drawing's autosave says it is saving its appearance.
    await page.getByText("Saving appearance…", { exact: true }).waitFor();
    await until(() => revision().inputValue(), value => value !== oldRevision, "the edits saved themselves as a new revision");
    assert.equal(requests.length, 2, "two edits inside one pause make one revision");
    assert.equal(requests[1].previousRevisionRef, legacyDocument.revisionRef); assert.equal(requests[1].drawingId, "floor-plan");
    assert.equal(requests[1].dimensions[0].id, savedDimension.id); assert.equal(requests[1].dimensions[0].placement.offsetMm, 16);
    assert.equal(drives.length, 0);
    await revision().selectOption(oldRevision); assert.equal(await page.getByLabel("Label offset (paper mm)", { exact: true }).inputValue(), "8");
  });
  await step("when the Working Head moves the LIVE drawing rebuilds once on it, while an earlier revision stays frozen", async () => {
    const generationCount = requests.length;
    assert.equal(await revision().inputValue(), legacyKey);
    await page.locator('.drawing-status[data-follow="frozen"]').waitFor();
    await page.getByText("Earlier revision · not updated automatically", { exact: true }).waitFor();
    const retainedBefore = await page.evaluate(() => structuredClone(window.drawingFixture.documents.find(document => document.revisionRef === "revision-legacy")));
    await page.evaluate(() => { window.drawingFixture.setActive(false); window.drawingFixture.head = "stage-B"; window.drawingFixture.revision += 1; });
    await page.evaluate(() => window.drawingFixture.setActive(true));
    await page.locator('.drawing-status[data-follow="frozen"][data-status="outdated"]').waitFor();
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.equal(requests.length, generationCount, "an earlier revision is never rebuilt automatically");
    assert.deepEqual(await page.evaluate(() => window.drawingFixture.documents.find(document => document.revisionRef === "revision-legacy")), retainedBefore,
      "the original document's ModelSource, Stage, bytes and recipe remain unchanged");
    await page.getByRole("button", { name: "Open the current revision", exact: true }).click();
    await until(() => Promise.resolve(requests.length), value => value === generationCount + 1, "one automatic rebuild on the moved head");
    assert.deepEqual(requests.at(-1).modelSource, modelB); assert.equal(requests.at(-1).sourceStageRef, "stage-B");
    assert.equal(requests.at(-1).dimensions[0].id, savedDimension.id, "rebuilding keeps the retained dimension intention");
    await page.locator('.drawing-status[data-follow="live"][data-status="current"]').waitFor();
    await page.evaluate(() => window.drawingFixture.setActive(false));
    await page.evaluate(() => window.drawingFixture.setActive(true));
    await page.locator('.drawing-status[data-follow="live"][data-status="current"]').waitFor();
    assert.equal(requests.length, generationCount + 1, "the same head is never rebuilt twice");
  });
  await step("a Working Head that cannot be drawn is stated, not silently rebound", async () => {
    const generationCount = requests.length;
    await page.evaluate(() => { window.drawingFixture.setActive(false); window.drawingFixture.headDrawable = false; window.drawingFixture.revision += 1; });
    await page.evaluate(() => window.drawingFixture.setActive(true));
    await page.getByText("The current model cannot be drawn yet: no exact STEP.", { exact: true }).waitFor();
    await page.getByText("Not updated", { exact: true }).waitFor();
    assert.equal(requests.length, generationCount);
    await page.evaluate(() => { window.drawingFixture.setActive(false); window.drawingFixture.headDrawable = true; window.drawingFixture.revision += 1; });
    await page.evaluate(() => window.drawingFixture.setActive(true));
    await page.locator('.drawing-status[data-follow="live"][data-status="current"]').waitFor();
    assert.equal(requests.length, generationCount);
  });
  await step("drawing another version is explicit, never rebuilds by itself and preserves dimension intention", async () => {
    const generationCount = requests.length;
    const oldSelection = await revision().inputValue();
    await page.getByText("Draw another version", { exact: true }).click();
    await source().selectOption("stage-A");
    await page.locator('.drawing-status[data-follow="frozen"][data-status="outdated"]').waitFor();
    await page.getByText("Drawn from a chosen version · not updated automatically", { exact: true }).waitFor();
    assert.deepEqual(statusRequests.at(-1).targetModelSource, modelA);
    assert.equal(requests.length, generationCount, "choosing a version never rebuilds by itself");
    assert.equal(await page.getByText("Change design width", { exact: true }).count(), 0);
    await page.getByRole("button", { name: "Rebuild on this version", exact: true }).click();
    await until(() => Promise.resolve(requests.length), value => value === generationCount + 1, "explicit rebuild");
    await page.locator('.drawing-status[data-status="current"]').waitFor();
    assert.notEqual(await revision().inputValue(), oldSelection);
    assert.deepEqual(requests.at(-1).modelSource, modelA); assert.equal(requests.at(-1).dimensions[0].id, savedDimension.id);
    assert.equal(requests.at(-1).follow, "frozen", "the chosen version is recorded with the revision");
  });
  await step("a drawing made from a chosen version stays on it after reopening until it follows again", async () => {
    const generationCount = requests.length;
    const kept = await revision().inputValue();
    await revision().selectOption(legacyKey);
    await revision().selectOption(kept);
    await page.locator('.drawing-status[data-follow="frozen"][data-status="current"]').waitFor();
    await page.getByText("Drawn from a chosen version · not updated automatically", { exact: true }).waitFor();
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.equal(requests.length, generationCount, "reopening never rebuilds a kept drawing on the head");
    await page.getByRole("button", { name: "Follow the current model again", exact: true }).click();
    await until(() => Promise.resolve(requests.length), value => value === generationCount + 1, "explicit return to LIVE");
    assert.equal(requests.at(-1).follow, "live"); assert.deepEqual(requests.at(-1).modelSource, modelB);
    await page.locator('.drawing-status[data-follow="live"][data-status="current"]').waitFor();
  });
  await step("broken anchors remain visible and cannot drive, while prior revisions remain selectable", async () => {
    broken = true;
    await page.getByRole("button", { name: "Refresh sources", exact: true }).click();
    await page.locator('.drawing-status[data-status="partially-broken"]').waitFor();
    assert.equal(await page.getByText("Door anchor is missing.", { exact: true }).count(), 1);
    assert.equal(await page.getByRole("button", { name: "Create design candidate", exact: true }).count(), 0);
    assert.equal(await revision().locator("option").count(),
      1 + await page.evaluate(() => window.drawingFixture.documents.filter(document => document.projectId === "drawing-project").length));
    broken = "outside-view";
    await page.getByRole("button", { name: "Refresh sources", exact: true }).click();
    await page.getByText("Dimension falls outside the drawing. Adjust its paper offset.", { exact: true }).waitFor();
    assert.equal(await page.getByLabel("Label offset (paper mm)", { exact: true }).isEnabled(), true);
    broken = false;
  });
  await step("a late generation response never changes another workspace selection", async () => {
    const before = await revision().inputValue(); hold = true;
    const scale = page.getByLabel("Scale denominator (1 : n)", { exact: true });
    await scale.fill(await scale.inputValue() === "75" ? "80" : "75");
    await until(() => Promise.resolve(Boolean(release)), Boolean, "held generation");
    await page.evaluate(() => window.drawingFixture.setActive(false)); release();
    await until(() => revision().isEnabled(), Boolean, "the held answer is settled");
    assert.equal(await revision().inputValue(), before);
    await page.evaluate(() => window.drawingFixture.setActive(true));
    await until(() => revision().inputValue(), value => value !== before, "the edit kept while hidden saves itself on return");
  });
  await step("an empty representation field cannot silently reuse old values during rebuild", async () => {
    await until(() => page.getByLabel("Cut height (meter)", { exact: true }).isEnabled(), Boolean, "active form");
    // Choose a version again: the previous step returned this drawing to LIVE.
    const another = page.locator("details.drawing-another");
    if (!await another.evaluate(node => node.open)) await another.locator("summary").click();
    await source().selectOption("stage-A");
    const before = requests.length;
    await page.getByLabel("Cut height (meter)", { exact: true }).fill("");
    await page.getByRole("button", { name: "Rebuild on this version", exact: true }).click();
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
    await until(() => Promise.resolve(requests.at(-1).dressing?.[1]?.anchorObjectId === "obj-wall"), Boolean, "dressing revision");
    await until(() => page.getByText("Saving appearance…", { exact: true }).count(), (value) => value === 0, "dressing revision opened");
    const old = legacyKey;
    const newRevision = await revision().inputValue();
    assert.equal(requests.at(-1).dressing.length, 2);
    assert.equal(requests.at(-1).dressing[0].id, id); assert.equal(requests.at(-1).dressing[0].flipped, true);
    assert.equal(requests.at(-1).dressing[1].anchorObjectId, "obj-wall");
    await revision().selectOption(old); await until(() => page.locator('[data-dressing-id]').count(), value => value === 0, "old recipe remains intact");
    await revision().selectOption(newRevision); await until(() => page.locator('[data-dressing-id]').count(), value => value === 2, "reopened SVG objects");
    await page.getByLabel("Selected object", { exact: true }).selectOption(id);
    await page.getByRole("button", { name: "Delete object", exact: true }).click();
    assert.equal(await page.locator('[data-dressing-id]').count(), 1);
    await until(() => revision().inputValue(), value => value !== newRevision, "deletion retained");
    assert.equal(requests.at(-1).dressing.length, 1);
    await drawn("obj-wall").first().waitFor({ state: "attached" });
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
    const savedScale = await page.evaluate(ref => window.drawingFixture.documents.find(d => d.revisionRef === ref).viewRecipe.frame.scale, revisionRef);
    assert.match(svg, /width="[\d.]+mm"/); assert.ok(svg.includes(`data-scale="${savedScale}"`), "the download keeps the saved revision's scale");
    assert.match(svg, /data-object="obj-wall"/); assert.match(svg, /data-dressing=/);
    const beforeAdd = await revision().inputValue();
    await page.getByRole("button", { name: "Add person", exact: true }).click();
    assert.equal(await downloadButton.isDisabled(), true, "unsaved appearance cannot be downloaded as a retained version");
    await until(() => revision().inputValue(), value => value !== beforeAdd, "the added person saved itself");
    await until(() => downloadButton.isEnabled(), Boolean, "the saved revision downloads again");
    await revision().selectOption(oldRevision);
    await until(() => downloadButton.isEnabled(), Boolean, "historical SVG available");
    const [oldDownload] = await Promise.all([page.waitForEvent("download"), downloadButton.click()]);
    const oldSvg = await readFile(await oldDownload.path(), "utf8");
    assert.equal(oldSvg, vectorBytes.get(legacyDocument.revisionRef));
    assert.doesNotMatch(oldSvg, /data-dressing=/, "historical download never inherits new or unsaved entourage");
  });
  await step("retained dimensions can be removed without rewriting their original revision", async () => {
    await page.getByRole("button", { name: "Remove dimension", exact: true }).click();
    assert.equal(await page.getByRole("group", { name: "Saved dimensions", exact: true }).count(), 0);
    await until(() => revision().inputValue(), value => value !== oldRevision, "dimension removal retained");
    assert.deepEqual(requests.at(-1).dimensions, []);
    assert.deepEqual(await page.evaluate(() => window.drawingFixture.documents.find(document => document.revisionRef === "revision-legacy").viewRecipe.dimensions), [savedDimension]);
    assert.equal(drives.length, 0);
  });
  await step("a refused appearance save keeps its edits, says so and can be retried", async () => {
    const before = await revision().inputValue(), sent = requests.length;
    refuseNext = true;
    await page.getByLabel("Cut height (meter)", { exact: true }).fill("1.4");
    await page.locator(".drawing-actions .error-panel").waitFor();
    await page.getByText("Appearance changes are not saved yet; fix the marked field or retry.", { exact: true }).waitFor();
    assert.equal(await page.getByLabel("Cut height (meter)", { exact: true }).inputValue(), "1.4", "the refused edit stays in its field");
    await new Promise(resolve => setTimeout(resolve, 1200));
    assert.equal(requests.length, sent + 1, "a refusal is not retried in a loop");
    await saveButton().click();
    await until(() => revision().inputValue(), value => value !== before, "retry saves the kept edit");
    assert.equal(requests.at(-1).cutHeight, 1.4);
    assert.equal(await saveButton().count(), 0);
  });
  await step("keyboard controls and English/Chinese narrow layouts keep the drawing usable", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => window.drawingFixture.setLanguage("zh-CN"));
    await page.getByRole("button", { name: "下载 SVG", exact: true }).waitFor();
    await page.evaluate(() => { document.querySelector('.drawing-body').scrollTop = 0; document.querySelector('.drawing-controls__fields').scrollTop = 0; });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: join(screenshots, "drawing-narrow.png"), fullPage: true });
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.evaluate(() => { window.drawingFixture.setLanguage("en"); window.drawingFixture.setTheme("dark"); });
    await page.evaluate(() => { document.querySelector('.drawing-body').scrollTop = 0; document.querySelector('.drawing-controls__fields').scrollTop = 0; });
    const actionBounds = await page.getByRole("button", { name: "Download SVG", exact: true }).boundingBox();
    assert.ok(actionBounds && actionBounds.y >= 0 && actionBounds.y + actionBounds.height <= 900, "the drawing's actions stay visible beside it while settings scroll");
    await page.screenshot({ path: join(screenshots, "drawing-wide-dark.png"), fullPage: true });
    // Choosing another version is a disclosed, explicit action; the LIVE drawing needs no choice.
    if (!await source().isVisible()) await page.getByText("Draw another version", { exact: true }).click();
    assert.equal(await source().locator("option").filter({ hasText: /^seat-rhino\.3dm$/ }).count(), 0);
    assert.equal(await source().inputValue(), "");
    await source().focus(); await page.keyboard.press("ArrowDown"); await page.keyboard.press("Enter");
    assert.equal(await source().inputValue(), "stage-A");
  });
  await step("an imported 3DM draws from its retained asset and reopens without following HEAD", async () => {
    await revision().selectOption("");
    if (!await source().isVisible()) await page.getByText("Draw another version", { exact: true }).click();
    await source().selectOption(`asset:${externalAsset.runId}:${externalAsset.assetSha256}`);
    assert.equal(await source().locator("option").filter({ hasText: /^imported-house\.skp$/ }).count(), 1);
    await page.getByText("Layer Roof was skipped.", { exact: true }).waitFor();
    await until(() => Promise.resolve(dimensionQueries.at(-1)), value => value?.sourceAssetRunId === externalAsset.runId, "external model units queried");
    assert.equal(dimensionQueries.at(-1).sourceAssetSha256, externalAsset.assetSha256);
    assert.equal(dimensionQueries.at(-1).stateDigest, undefined);
    await page.getByRole("button", { name: "Generate cut plan", exact: true }).click();
    await until(() => revision().inputValue(), value => value !== "", "external drawing saved");
    assert.deepEqual(requests.at(-1).sourceAsset, externalAsset);
    assert.equal(requests.at(-1).modelSource, undefined);
    assert.equal(requests.at(-1).sourceStageRef, undefined);
    assert.equal(requests.at(-1).follow, "frozen");
    const saved = await revision().inputValue(), count = requests.length;
    await revision().selectOption("");
    await revision().selectOption(saved);
    await page.getByRole("region", { name: "Source status" }).getByText("imported-house.skp", { exact: true }).waitFor();
    await page.getByRole("region", { name: "Source status" }).getByText("Layer Roof was skipped.", { exact: true }).waitFor();
    await page.locator('.drawing-status[data-follow="frozen"][data-status="current"]').waitFor();
    await page.evaluate(() => { window.drawingFixture.setActive(false); window.drawingFixture.head = "stage-B"; window.drawingFixture.revision += 1; });
    await page.evaluate(() => window.drawingFixture.setActive(true));
    await page.locator('.drawing-status[data-follow="frozen"][data-status="current"]').waitFor();
    assert.equal(requests.length, count, "reopening an external drawing never rebinds the Working Head");
    assert.equal(await page.getByText("Follow the current model again", { exact: true }).count(), 0);
    await page.getByLabel("Scale denominator (1 : n)", { exact: true }).fill("75");
    await until(() => revision().inputValue(), value => value !== saved, "external appearance revision saved");
    assert.deepEqual(requests.at(-1).sourceAsset, externalAsset);
    assert.equal(requests.at(-1).modelSource, undefined);
    assert.equal(requests.at(-1).follow, "frozen");
  });
  await step("a SketchUp upload selects the server-returned 3DM for drawing", async () => {
    await revision().selectOption("");
    await page.locator('input[type="file"][accept=".3dm,.skp"]').setInputFiles({ name: "house.skp", mimeType: "application/octet-stream", buffer: Buffer.from("skp") });
    await until(() => source().inputValue(), value => value === `asset:uploaded-skp:${"f".repeat(64)}`, "uploaded model selected");
    assert.deepEqual(await page.evaluate(() => window.drawingFixture.uploads), ["house.skp"]);
    assert.equal(await source().locator("option").filter({ hasText: /^house\.skp$/ }).count(), 1);
    await page.getByRole("button", { name: "Generate cut plan", exact: true }).click();
    await until(() => revision().inputValue(), value => value !== "", "uploaded model drawing saved");
    assert.deepEqual(requests.at(-1).sourceAsset, { runId: "uploaded-skp", assetSha256: "f".repeat(64) });
    assert.equal(requests.at(-1).modelSource, undefined);
  });
  await step("a response for a previous project never opens a document in the new project", async () => {
    hold = true;
    const scale = page.getByLabel("Scale denominator (1 : n)", { exact: true });
    await scale.fill(await scale.inputValue() === "60" ? "65" : "60");
    await until(() => Promise.resolve(Boolean(release)), Boolean, "held project generation");
    await page.evaluate(() => window.drawingFixture.setProject("another-project"));
    await page.getByRole("button", { name: "Generate cut plan", exact: true }).waitFor();
    release();
    await until(() => Promise.resolve(release === null), Boolean, "old response released");
    assert.equal(await revision().inputValue(), "");
    assert.equal(await revision().locator("option").count(), 1);
    assert.equal(await page.locator(".drawing-preview").count(), 0);
  });
  await step("a section perspective is generated on the drawing's target from its own form and opens as a drawing", async () => {
    const generations = requests.length;
    await page.locator("details.drawing-section > summary").click();
    const eyeHeight = page.getByLabel("Eye height above the lowest cut point (meter)", { exact: true });
    await until(() => eyeHeight.isEnabled(), Boolean, "the section form knows the model unit");
    assert.equal(await eyeHeight.inputValue(), "1.6", "the default eye is 1.6 m above the lowest cut point, in the model unit");
    assert.equal(await page.getByLabel("Field of view (degrees)", { exact: true }).inputValue(), "55");
    await page.getByRole("combobox", { name: "Cut plane", exact: true }).selectOption("x");
    await page.getByLabel("Position (meter)", { exact: true }).fill("2.5");
    await page.getByRole("combobox", { name: "Look toward", exact: true }).selectOption("-");
    await eyeHeight.fill("");
    await page.getByRole("button", { name: "Generate section perspective", exact: true }).click();
    assert.equal(sectionRequests.length, 0, "an empty section field is marked, never sent");
    assert.equal(await eyeHeight.evaluate(node => node.validity.valueMissing), true);
    await eyeHeight.fill("1.5");
    await page.getByLabel("Field of view (degrees)", { exact: true }).fill("60");
    await page.getByRole("button", { name: "Generate section perspective", exact: true }).click();
    await until(() => Promise.resolve(sectionRequests.length), value => value === 1, "one section perspective request");
    const head = await page.evaluate(() => window.drawingFixture.head);
    assert.deepEqual(sectionRequests[0].modelSource, head === "stage-B" ? modelB : modelA);
    assert.equal(sectionRequests[0].sourceStageRef, head);
    assert.deepEqual(sectionRequests[0].section, { line: [[2.5, 0], [2.5, 1]], keep: "left" }, "looking toward -X keeps x < 2.5");
    assert.deepEqual(sectionRequests[0].camera, { eyeHeight: 1.5, fovDeg: 60 });
    await until(() => revision().inputValue(), value => value.includes("section-revision-1"), "the new drawing opened");
    await page.getByText("Section perspective · true to scale at the cut plane", { exact: true }).waitFor();
    await page.locator('.drawing-preview__viewport[data-ready="true"]').waitFor();
    await page.screenshot({ path: join(screenshots, "drawing-section-perspective.png"), fullPage: true });
    assert.equal(await page.getByLabel("Cut height (meter)", { exact: true }).count(), 0, "cut-plan appearance is not offered for it");
    assert.equal(await page.getByRole("button", { name: "Download SVG", exact: true }).count(), 0);
    assert.deepEqual(planOnlyCalls, [], "no cut-plan status or vector is asked for a section perspective");
    assert.equal(requests.length, generations, "no cut plan was generated");
    await page.evaluate(() => window.drawingFixture.setLanguage("zh-CN"));
    await page.getByRole("button", { name: "生成剖透视", exact: true }).waitFor();
    await page.evaluate(() => window.drawingFixture.setLanguage("en"));
  });
  await step("a section perspective from an imported SKP keeps its exact asset and warning on reopen", async () => {
    await page.evaluate(() => window.drawingFixture.setProject("drawing-project"));
    await revision().selectOption("");
    if (!await source().isVisible()) await page.getByText("Draw another version", { exact: true }).click();
    await source().selectOption(`asset:${externalAsset.runId}:${externalAsset.assetSha256}`);
    await page.getByText("Layer Roof was skipped.", { exact: true }).waitFor();
    const before = sectionRequests.length;
    const button = page.getByRole("button", { name: "Generate section perspective", exact: true });
    if (!await button.isVisible()) await page.locator("details.drawing-section > summary").click();
    await until(() => button.isEnabled(), Boolean, "imported model units resolved for section");
    await button.click();
    await until(() => Promise.resolve(sectionRequests.length), value => value === before + 1, "imported section saved");
    assert.deepEqual(sectionRequests.at(-1).sourceAsset, externalAsset);
    assert.equal(sectionRequests.at(-1).modelSource, undefined);
    assert.equal(sectionRequests.at(-1).sourceStageRef, undefined);
    await until(() => revision().inputValue(), value => value.includes(`section-revision-${before + 1}`), "imported section opened");
    const saved = await revision().inputValue();
    await revision().selectOption("");
    await revision().selectOption(saved);
    await page.getByRole("region", { name: "Source status" }).getByText("imported-house.skp", { exact: true }).waitFor();
    await page.getByRole("region", { name: "Source status" }).getByText("Layer Roof was skipped.", { exact: true }).waitFor();
    assert.equal(sectionRequests.length, before + 1, "reopening never redraws the imported source");
    assert.deepEqual(planOnlyCalls, []);
  });
  await step("a projected line says which object it draws: its component, material and role", async () => {
    // The retained revision's vector is the projection's own SVG.
    await revision().selectOption(legacyKey);
    await drawn("obj-table").waitFor();
    assert.equal(await page.locator("img.drawing-vector-base").count(), 0, "the plan is drawn inline, not as an image");
    assert.deepEqual(await objectChoice().locator("option").allTextContents(), ["Choose an object", "Table · Oak · beyond", "Wall · Concrete · cut"]);
    const table = await planPoint(7.75, 2), wall = await planPoint(5, 1);
    await page.mouse.move(table.x, table.y);
    await until(() => page.getByRole("tooltip").textContent(), value => value === "Table · Oak · beyond", "a line beyond the cut names its object");
    await page.mouse.move(wall.x, wall.y);
    await until(() => page.getByRole("tooltip").textContent(), value => value === "Wall · Concrete · cut", "the cut names its wall");
    await page.mouse.click(wall.x, wall.y);
    assert.equal(await objectChoice().inputValue(), "obj-wall");
    assert.equal(await page.locator(".drawing-object-picked > strong").textContent(), "Wall · Concrete · cut");
    assert.equal(await page.locator('.drawing-plan__highlight > .is-picked').count(), 2, "the wall's cut and poché are lit");
    await page.screenshot({ path: join(screenshots, "drawing-object-picked.png"), fullPage: true });
    await page.evaluate(() => window.drawingFixture.setLanguage("zh-CN"));
    await until(() => page.locator(".drawing-object-picked > strong").textContent(), value => value === "Wall · Concrete · 剖切", "the role in Chinese");
    await page.evaluate(() => window.drawingFixture.setLanguage("en"));
    await page.keyboard.press("Escape");
    assert.equal(await objectChoice().inputValue(), "", "Escape lets the object go");
    await page.mouse.move(0, 0);
    await page.getByRole("tooltip").waitFor({ state: "detached" });
  });
  await step("a hidden object stays hidden through its rebuild, and no design request is made", async () => {
    const table = await planPoint(7.75, 2);
    await page.mouse.click(table.x, table.y);
    assert.equal(await objectChoice().inputValue(), "obj-table");
    const before = await revision().inputValue(), sent = requests.length;
    await page.getByRole("button", { name: "Hide this object", exact: true }).click();
    assert.equal(await drawn("obj-table").count(), 0, "the hidden object leaves the page at once");
    assert.equal(requests.length, sent, "before its revision is saved");
    await until(() => revision().inputValue(), value => value !== before, "hiding saved itself as a revision");
    assert.equal(requests.length, sent + 1); assert.deepEqual(requests.at(-1).hiddenObjectIds, ["obj-table"]);
    assert.equal(requests.at(-1).previousRevisionRef, legacyDocument.revisionRef);
    await page.getByRole("button", { name: "Show again: Table · Oak", exact: true }).waitFor();
    const another = page.locator("details.drawing-another");
    if (!await another.evaluate(node => node.open)) await another.locator("summary").click();
    await source().selectOption("stage-B");
    const hiddenRevision = await revision().inputValue();
    await page.getByRole("button", { name: "Rebuild on this version", exact: true }).click();
    await until(() => revision().inputValue(), value => value !== hiddenRevision, "the rebuild opened");
    assert.equal(requests.length, sent + 2); assert.deepEqual(requests.at(-1).modelSource, modelB);
    assert.deepEqual(requests.at(-1).hiddenObjectIds, ["obj-table"], "the rebuild asks for the object to stay hidden");
    const [, , rebuiltRef] = JSON.parse(await revision().inputValue());
    await until(() => Promise.resolve(vectorBytes.get(rebuiltRef)), Boolean, "the rebuilt vector read");
    assert.doesNotMatch(vectorBytes.get(rebuiltRef), /obj-table/);
    await drawn("obj-wall").first().waitFor();
    assert.equal(await drawn("obj-table").count(), 0, "the rebuilt drawing still leaves it out");
    assert.equal(await objectChoice().locator("option", { hasText: "Table" }).count(), 0);
    assert.equal(drives.length, 0, "hiding never proposes a design change");
    assert.deepEqual(await page.evaluate(() => window.drawingFixture.handoffs), []);
    await page.getByRole("button", { name: "Show again: Table · Oak", exact: true }).click();
    await until(() => Promise.resolve(requests.length), value => value === sent + 3, "showing it again saved itself");
    assert.deepEqual(requests.at(-1).hiddenObjectIds, []);
    await drawn("obj-table").waitFor();
    await until(() => page.getByText("Saving appearance…", { exact: true }).count(), value => value === 0, "the shown object saved");
  });
  await step("a plan past the inline line limit is an image whose objects can still be hidden from the list", async () => {
    // Kept on its chosen version, so the moved Working Head does not rebuild it.
    const dense = { ...legacyDocument, assetSha256: "7".repeat(64), fileName: "dense-plan.png", drawingId: "dense-plan", revisionRef: "revision-dense",
      generatedAt: "2026-09-21T00:00:00Z", viewRecipe: { ...legacyDocument.viewRecipe, dimensions: [], follow: "frozen" } };
    await page.evaluate(document => window.drawingFixture.documents.push(document), dense);
    await page.getByRole("button", { name: "Refresh sources", exact: true }).click();
    await revision().selectOption(JSON.stringify([dense.runId, dense.assetSha256, dense.revisionRef]));
    await page.locator("img.drawing-vector-base").waitFor();
    await page.getByText("This drawing has 20001 lines, so it is shown as an image; choose its objects from the list.", { exact: true }).waitFor();
    assert.equal(await page.locator("svg.drawing-vector-base").count(), 0);
    const sent = requests.length;
    await objectChoice().selectOption("obj-screen");
    assert.equal(await page.locator(".drawing-object-picked > strong").textContent(), "Screen · Steel · beyond");
    await page.getByRole("button", { name: "Hide this object", exact: true }).click();
    await until(() => Promise.resolve(requests.length), value => value === sent + 1, "hiding from the list saved a revision");
    assert.deepEqual(requests.at(-1).hiddenObjectIds, ["obj-screen"]); assert.equal(requests.at(-1).previousRevisionRef, dense.revisionRef);
    await until(() => page.getByText("Saving appearance…", { exact: true }).count(), value => value === 0, "the hidden object saved");
  });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed, screenshots, generationRequests: requests.length, dimensionProposals: drives.length }));
} catch (error) {
  if (page) await page.screenshot({ path: join(screenshots, "failure.png"), fullPage: true }).catch(() => {});
  console.error(`FAIL ${current}; screenshots: ${screenshots}`, errors); throw error;
} finally { if (release) release(); await browser?.close(); await server.close(); }

/** Real Stage face picks, signed normal previews and one existing draft commit. */
import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const cacheDir = await mkdtemp(path.join(tmpdir(), "signed-normal-"));
const http = createHttpServer(), errors = [], projectCalls = [];
let browser, vite, page;
const html = `<!doctype html><html><head><style>
#root, .stage { height: 100vh; } #root .viewport-state { display: none; }
</style></head><body><div id="root" class="project-workspace"></div><script type="module">
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Box3, Mesh, Vector3 } from "three";
import { Stage } from "/src/features/stage/Stage.tsx";
import { UserPreferencesProvider } from "/test/TestProviders.tsx";
import { applyDraftCommand, createModelDraft, currentDraft, drawnShapeFromSpec, undoDraft, redoDraft } from "/src/features/stage/modelDraft.ts";
import { constrainedTranslation } from "/src/workspaces/monkeyarch/viewer/translationGizmo.ts";
import "/src/styles.css";
import "/@fs/${fileURLToPath(new URL("../../src/styles.css", import.meta.url)).replaceAll("\\", "/")}";
const noop = () => {};
const initial = createModelDraft([{ elementId: "source", componentId: "fixture", created: true, originalObjectNames: [],
  spec: { profile: [[0,0],[4,0],[4,3],[0,3]], base: 0, height: 3 } }]);
window.actions = []; window.picks = [];
function Harness() {
  const viewport = useRef(null);
  const [history, setHistory] = useState(initial), [selected, select] = useState(null);
  const [tool, setTool] = useState(null), [base, setBase] = useState("base-1"), [blocked, setBlocked] = useState(false);
  const [width, setWidth] = useState("100%");
  const snapshot = currentDraft(history), object = snapshot.objects.get(selected);
  const target = useMemo(() => object?.spec ? { elementId: object.elementId,
    shape: drawnShapeFromSpec(object.spec, object.parameterBoundFields) } : null, [object]);
  window.viewport = viewport; window.setBase = setBase; window.setBlocked = setBlocked;
  window.clearSelection = () => select(null); window.setWidth = setWidth;
  window.arm = setTool; window.selection = selected; window.selectWithoutFace = () => select("source");
  window.reset = () => { setTool(null); select(null); setHistory(initial); setBase("base-1"); setBlocked(false); window.actions.length = 0; };
  window.snapshot = () => ({ index: history.index, commands: snapshot.commands,
    objects: [...snapshot.objects.values()].map(o => ({ id:o.elementId, spec:o.spec })), initial: [...initial.snapshots[0].objects.values()] });
  useEffect(() => { viewport.current?.draftPreview({ objects: [...snapshot.objects.values()].filter(o => o.spec && !o.deleted)
    .map(o => ({ elementId: o.elementId, spec:o.spec })), hiddenObjectNames: [] }); }, [snapshot]);
  const apply = action => {
    window.actions.push(action);
    setHistory(value => applyDraftCommand(value, { kind: "direct", elementId: action.target.elementId, action,
      ...(action.kind === "copy" ? { copyElementId: "copy-" + window.actions.length } : {}) }));
  };
  const pick = value => { window.picks.push(value); select(value?.draftElementId ?? null); };
  return React.createElement("div", { style: { width } }, React.createElement(Stage, {
    viewportRef: viewport, embedded:true, status:"ready", message:"", picked:null,
    versions:[], workingCopies:[], loadedShas:[], loadingSha:null, designHistory:null,
    documentView:{open:false,mounted:false}, displayMode:"model", tool:null,
    gestures:[], home:null, hasModel:true, editingBaseRunId:base, loadedRunId:base,
    modelAnnotations:null, annotationsReady:false, documentModelSources:[],
    editingModelSource:null, viewedModelSource:null, artifactError:null, baseError:null,
    blend:null, captureState:"idle", onTool:noop, onInspection:noop, onStatus:noop,
    onRequestFile:noop, onOpenFile:noop, onSource:noop, onPick:pick,
    model:{ onDelete:noop, canDelete:!!selected, deleting:false, subject:selected,
      onUndo:() => setHistory(undoDraft), canUndo:history.index > 0,
      onRedo:() => setHistory(redoDraft), canRedo:history.index+1 < history.snapshots.length,
      onClearSelection:() => select(null), hasSelection:!!selected,
      onTool:value => setTool(value === "select" ? null : value), directTool:tool, onApply:apply,
      pushPullTarget:target, busy:false, interactionBlocked:blocked }
  }));
}
createRoot(document.getElementById("root")).render(React.createElement(UserPreferencesProvider,null,React.createElement(Harness)));
window.projectPoint = point => {
  const r = document.querySelector("canvas").getBoundingClientRect(), c = window.readRuntime().camera;
  c.updateMatrixWorld(); const p = new Vector3(...point).project(c);
  return [r.left+(p.x+1)*r.width/2, r.top+(1-p.y)*r.height/2];
};
</script></body></html>`;

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
try {
  vite = await createServer({ root:webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, cacheDir, publicDir:"../.generated/public", logLevel:"silent",
    plugins:[{ name:"read-only-viewport-probe", enforce:"pre", transform(source,id) {
      if (id.replaceAll("\\", "/").endsWith("/viewer/normalDrag.ts")) {
        return {code:source.replace("matches(next, nextRect) {", "matches(next, nextRect) { (window as any).__normalCapture = {rect, nextRect, world, currentWorld:next.matrixWorld.toArray(), projection, currentProjection:next.projectionMatrix.toArray()};"),map:null};
      }
      if (!id.replaceAll("\\", "/").endsWith("/viewer/ThreeDmViewport.tsx")) return;
      const marker = "  const pickAt = useCallback(";
      assert.equal(source.split(marker).length,2);
      return {code:source.replace(marker,"  (window as any).readRuntime = () => runtimeRef.current;\n"+marker),map:null};
    }},react()], server:{middlewareMode:true,hmr:false,ws:{server:http},watch:null} });
  http.on("request", async (request,response) => {
    if (request.url.startsWith("/normal-test")) { response.setHeader("Content-Type","text/html"); response.end(await vite.transformIndexHtml(request.url,html)); }
    else if (request.url.startsWith("/api/")) { projectCalls.push(request.url); response.writeHead(500).end(); }
    else vite.middlewares(request,response);
  });
  await new Promise(resolve=>http.listen(0,"127.0.0.1",resolve));
  browser = await chromium.launch({headless:true, ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath:process.env.CHROMIUM_EXECUTABLE} : {}), args:["--enable-unsafe-swiftshader","--no-sandbox"]});
  page = await browser.newPage({viewport:{width:1280,height:850},locale:"en-US"});
  page.on("pageerror",error=>errors.push(error.message)); page.setDefaultTimeout(12000);
  await page.goto(`http://127.0.0.1:${http.address().port}/normal-test?lang=en`);
  await page.waitForFunction(()=>window.readRuntime?.()?.draftObjects.size===1);
  const count = () => page.evaluate(()=>window.actions.length);
  const snapshot = () => page.evaluate(()=>window.snapshot());
  const camera = () => page.evaluate(()=>window.viewport.current.camera());
  const field = () => page.getByRole("spinbutton",{name:"Distance m",exact:true});
  const setView = async (ortho,position) => {
    await page.evaluate(({ortho,position})=>{
      window.viewport.current.standardView(ortho ? "top" : "perspective");
      const r = window.readRuntime(); r.camera.position.fromArray(position); r.camera.up.set(0,0,1);
      // Leave screen space for signed excursions after changing a fitted Top into an oblique view.
      if (ortho) { r.camera.zoom = .2; r.camera.updateProjectionMatrix(); }
      r.controls.target.set(2,1.5,1.5); r.camera.lookAt(r.controls.target); r.controls.update(); r.camera.updateMatrixWorld(); r.render();
    }, {ortho,position});
  };
  const pickAndArm = async point => {
    const p = await page.evaluate(point=>window.projectPoint(point), point);
    await page.mouse.click(...p); await page.waitForFunction(()=>window.selection === "source");
    await page.locator('button[data-model-tool="pushPull"]').click();
    await page.locator(".stage-pushpull").waitFor();
  };
  const reset = async () => { await page.evaluate(()=>window.reset()); await page.waitForFunction(()=>window.selection === null && window.snapshot().index === 0); };
  const cases = [
    {point:[2,1.5,3],normal:[0,0,1],position:[12,-15,10]},
    {point:[2,1.5,0],normal:[0,0,-1],position:[12,-15,-10]},
    {point:[4,1.5,1.5],normal:[1,0,0],position:[12,-15,10]},
    {point:[2,0,1.5],normal:[0,-1,0],position:[12,-15,10]},
  ];
  for (const ortho of [false,true]) for (const c of cases) {
    await reset(); await setView(ortho,c.position);
    await pickAndArm(c.point);
    const stable = await camera(), initial = await snapshot();
    assert.equal(await page.locator(".stage-pushpull svg line").count(),1);
    for (const distance of [.6,-.4,.3]) {
      const to = await page.evaluate(point=>window.projectPoint(point),c.point.map((x,i)=>x+distance*c.normal[i]));
      const box = page.viewportSize();
      assert.ok(to[0]>20 && to[0]<box.width-20 && to[1]>20 && to[1]<box.height-130,
        `The synthetic pointer target must stay inside the unobstructed viewport: ${to}`);
      await page.mouse.move(...to,{steps:5});
      await page.waitForFunction(expected=>Math.abs(Number(document.querySelector('.model-edit-panel input').value)-expected)<.015,distance);
      assert.equal(await count(),0); assert.deepEqual(await snapshot(),initial);
    }
    await field().fill("-0.333333333");
    await page.mouse.move(650,340);
    assert.equal(await field().inputValue(),"-0.333333333","pointer cannot replace numeric input");
    await field().press("Enter"); await page.waitForFunction(()=>window.actions.length===1);
    await page.keyboard.press("Enter"); await page.mouse.up(); assert.equal(await count(),1);
    const action = await page.evaluate(()=>window.actions[0]);
    assert.equal(action.kind,"pushPull"); assert.equal(action.distance,-0.333333333);
    action.normal.forEach((value,i)=>assert.ok(Math.abs(value-c.normal[i])<1e-8));
    assert.deepEqual(await camera(),stable,"no automatic camera fit after a face edit");
    await page.keyboard.press("Escape");
    await page.keyboard.press("Control+z"); await page.waitForFunction(()=>window.snapshot().index===0);
    await page.keyboard.press("Control+Shift+z"); await page.waitForFunction(()=>window.snapshot().index===1);
    console.log(`PASS ${ortho?'orthographic':'perspective'} face ${c.normal}: signed pointer, exact override, one commit, undo/redo`);
  }
  await reset(); await setView(true,[2,1.5,20]); await pickAndArm([2,1.5,3]);
  assert.equal(await page.locator(".stage-pushpull svg line").count(),0,"end-on direction has no fake screen handle");
  await page.mouse.move(650,340); assert.equal(Number(await field().inputValue()),0);
  await field().fill("1.25"); await field().press("Enter"); await page.waitForFunction(()=>window.actions.length===1);
  assert.equal(await page.evaluate(()=>window.actions[0].distance),1.25);
  console.log("PASS end-on face refuses pointer ambiguity but keeps signed numeric input");
  for (const cancellation of ["escape","blur","pointercancel","base","blocked","view","resize"]) {
    await reset(); await setView(false,[12,-15,10]); await pickAndArm([2,1.5,3]); await field().fill("0.8");
    if (cancellation === "escape") await page.keyboard.press("Escape");
    else if (cancellation === "blur") await page.evaluate(()=>window.dispatchEvent(new Event("blur")));
    else if (cancellation === "pointercancel") await page.locator(".stage-pushpull").dispatchEvent("pointercancel");
    else if (cancellation === "base") await page.evaluate(()=>window.setBase("base-2"));
    else if (cancellation === "blocked") await page.evaluate(()=>window.setBlocked(true));
    else if (cancellation === "view") { await setView(true,[12,-15,10]); await field().press("Enter"); }
    else { await page.setViewportSize({width:1180,height:800}); await field().press("Enter"); }
    await page.keyboard.press("Enter"); await page.mouse.up(); assert.equal(await count(),0,`${cancellation} cannot commit an old gesture`);
    await page.setViewportSize({width:1280,height:850});
  }
  console.log("PASS all seven cancellation paths leave no typed action");
  await reset(); await setView(false,[12,-15,10]);
  // Clear the viewport pick through a real empty-space click before selecting
  // the object without a face, as an object-tree selection would do.
  await page.mouse.click(24,24);
  assert.equal(await page.evaluate(()=>window.viewport.current.workPlaneFromSelection()),null);
  await page.evaluate(()=>window.selectWithoutFace());
  await page.waitForFunction(()=>window.selection === "source");
  await page.evaluate(()=>window.arm("pushPull"));
  assert.equal(await page.locator('.model-edit-panel button[type="submit"]').isDisabled(),true);
  assert.equal(await count(),0,"missing face cannot fall back to an unqualified push/pull");
  assert.deepEqual(projectCalls,[]); assert.deepEqual(errors,[]);
  if (process.env.EVIDENCE_DIR) {
    await mkdir(process.env.EVIDENCE_DIR,{recursive:true});
    await reset(); await setView(false,[12,-15,10]); await pickAndArm([2,1.5,3]);
    await page.screenshot({path:path.join(process.env.EVIDENCE_DIR,"pushpull.png")});
    await writeFile(path.join(process.env.EVIDENCE_DIR,"pushpull-report.json"),JSON.stringify({cases:8,cancellations:7,projectCalls,errors},null,2));
  }
  console.log("PASS cancellations, missing-face refusal, zero project requests and zero JavaScript errors");
} catch (error) {
  const diagnostic = await page?.evaluate(() => ({ capture:window.__normalCapture, selection:window.selection,
    camera:window.viewport?.current?.camera(), panel:document.querySelector(".model-edit-panel")?.outerHTML,
    stage:document.querySelector(".stage")?.textContent, actions:window.actions })).catch(() => null);
  console.error("BROWSER_DIAGNOSTIC", JSON.stringify({diagnostic,errors,projectCalls}));
  if (process.env.EVIDENCE_DIR && page) {
    await mkdir(process.env.EVIDENCE_DIR,{recursive:true});
    await page.screenshot({path:path.join(process.env.EVIDENCE_DIR,"pushpull-failure.png")}).catch(() => {});
  }
  throw error;
} finally {
  await browser?.close(); await vite?.close(); await new Promise(resolve=>http.close(resolve)); await rm(cacheDir,{recursive:true,force:true});
}

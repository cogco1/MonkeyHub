/** Real Stage pointer/form events use the retained local-draft commands. */
import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const cacheDir = await mkdtemp(path.join(tmpdir(), "translation-gizmo-"));
const http = createHttpServer(), errors = [], projectCalls = [];
let browser, vite, page;
const html = `<!doctype html><html><head><style>
#root, .stage { height: 100vh; } #root .viewport-state { display: none; }
</style></head><body><div id="root"></div><script type="module">
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Box3, Mesh, Vector3 } from "three";
import { Stage } from "/src/features/stage/Stage.tsx";
import { UserPreferencesProvider } from "/src/features/settings/preferences.tsx";
import { applyDraftCommand, createModelDraft, currentDraft, drawnShapeFromSpec, undoDraft, redoDraft } from "/src/features/stage/modelDraft.ts";
import { constrainedTranslation } from "/src/workspaces/monkeyarch/viewer/translationGizmo.ts";
import "/src/styles.css";
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
  window.arm = setTool;
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
window.handlePoint = axis => {
  const h = window.readRuntime().translation, points = [], r = document.querySelector("canvas").getBoundingClientRect();
  h.controls.getHelper().updateMatrixWorld(true);
  h.controls.getHelper().traverse(object => {
    if (!(object instanceof Mesh) || object.name !== axis || !object.visible || !object.parent?.visible || !object.layers.mask) return;
    const centre = new Box3().setFromObject(object).getCenter(new Vector3());
    const relative = constrainedTranslation(centre.sub(h.proxy.position).toArray(), axis);
    const world = new Vector3(...relative).add(h.proxy.position).toArray(), screen = window.projectPoint(world);
    const hit = h.hover({ x:(screen[0]-r.left)/r.width*2-1, y:1-(screen[1]-r.top)/r.height*2, button:0 });
    if (hit === axis) points.push({ world, screen });
  });
  if (!points.length) throw new Error("No visible " + axis + " handle");
  return points[0];
};
</script></body></html>`;
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
try {
  vite = await createServer({ root:webRoot, configFile:false, cacheDir, publicDir:".generated/public", logLevel:"silent",
    plugins:[{ name:"read-only-viewport-probe", enforce:"pre", transform(source,id) {
      if (!id.replaceAll("\\", "/").endsWith("/viewer/ThreeDmViewport.tsx")) return;
      const marker = "  const pickAt = useCallback(";
      assert.equal(source.split(marker).length,2);
      return {code:source.replace(marker,"  (window as any).readRuntime = () => runtimeRef.current;\n"+marker),map:null};
    }},react()], server:{middlewareMode:true,hmr:false,ws:{server:http},watch:null} });
  http.on("request", async (request,response) => {
    if (request.url.startsWith("/gizmo-test")) { response.setHeader("Content-Type","text/html"); response.end(await vite.transformIndexHtml(request.url,html)); }
    else if (request.url.startsWith("/api/")) { projectCalls.push(request.url); response.writeHead(500).end(); }
    else vite.middlewares(request,response);
  });
  await new Promise(resolve=>http.listen(0,"127.0.0.1",resolve));
  browser = await chromium.launch({headless:true, ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath:process.env.CHROMIUM_EXECUTABLE} : {}), args:["--enable-unsafe-swiftshader","--no-sandbox"]});
  page = await browser.newPage({viewport:{width:1280,height:850},locale:"en-US"});
  page.on("pageerror",error=>errors.push(error.message)); page.setDefaultTimeout(12000);
  await page.goto(`http://127.0.0.1:${http.address().port}/gizmo-test?lang=en`);
  await page.waitForFunction(()=>window.readRuntime?.()?.draftObjects.size===1);
  await page.evaluate(()=>window.viewport.current.fitView());
  const camera = () => page.evaluate(()=>window.viewport.current.camera());
  const count = () => page.evaluate(()=>window.actions.length);
  const snapshot = () => page.evaluate(()=>window.snapshot());
  const selectSource = async () => {
    const point = await page.evaluate(()=>window.projectPoint([2,1.5,3]));
    await page.mouse.click(...point);
    await page.waitForFunction(()=>window.picks.at(-1)?.draftElementId === "source");
  };
  const arm = async (tool="move") => {
    await page.locator(`button[data-model-tool="${tool}"]`).click();
    await page.locator(".stage-move").waitFor();
    await page.waitForFunction(()=>!!window.readRuntime().translation);
  };
  const drag = async (axis,delta,release=true) => {
    const from = await page.evaluate(a=>window.handlePoint(a),axis);
    const to = await page.evaluate(({world,delta})=>window.projectPoint(world.map((n,i)=>n+delta[i])),{world:from.world,delta});
    await page.mouse.move(...from.screen); await page.mouse.down(); await page.mouse.move(...to,{steps:6});
    if (release) await page.mouse.up();
    await page.waitForTimeout(30);
  };
  const apply = () => page.locator(".model-edit-panel--move button[type=submit]").click();
  const axis = () => page.getByRole("combobox",{name:"World constraint"});
  const field = name => page.getByRole("spinbutton",{name:`${name} m`,exact:true});
  const near = (actual,expected) => actual.forEach((value,i)=>assert.ok(Math.abs(value-expected[i])<0.002,`${actual} != ${expected}`));
  const deltas = {X:[0.75,0,0],Y:[0,-0.65,0],Z:[0,0,0.6],XY:[0.4,-0.35,0],XZ:[0.4,0,0.3],YZ:[0,-0.35,0.3]};
  await selectSource();
  const initial = await snapshot(), stableCamera = await camera();
  for (const [handle,delta] of Object.entries(deltas)) {
    await arm(); assert.deepEqual(await camera(),stableCamera,"arming must not refit");
    await drag(handle,delta);
    assert.equal(await axis().inputValue(),handle);
    near(await Promise.all(["X","Y","Z"].map(n=>field(n).inputValue().then(Number))),delta);
    for (const n of "XYZ") assert.equal(await field(n).isDisabled(),!handle.includes(n));
    assert.equal(await count(),0,"drag/release remains a preview, not a hidden commit");
    assert.deepEqual(await snapshot(),initial,"preview does not change retained local draft");
    assert.deepEqual(await camera(),stableCamera,"drag cannot orbit/refit");
    await page.keyboard.press("Escape");
    await page.waitForFunction(()=>window.readRuntime().translation===null);
  }
  // Exact signed override and form Enter (not rounded displayed pointer numbers).
  await arm(); await drag("X",[0.6,0,0]);
  await field("X").fill("-1.23456789"); await field("X").press("Enter");
  await page.waitForFunction(()=>window.actions.length===1);
  await page.keyboard.press("Enter"); await page.mouse.up();
  assert.equal(await count(),1,"duplicate completion events do not submit twice");
  assert.deepEqual((await snapshot()).objects[0].spec.plane.origin,[-1.23456789,0,0]);
  assert.deepEqual((await snapshot()).initial,initial.initial,"original snapshot remains immutable");
  await arm("copy"); await drag("Y",[0,0.8,0]); await field("Y").fill("2.5"); await apply();
  await page.waitForFunction(()=>window.actions.length===2);
  const copied = await snapshot();
  assert.deepEqual(copied.objects.map(o=>o.id),["source","copy-2"]);
  assert.deepEqual(copied.objects[0].spec.plane.origin,[-1.23456789,0,0]);
  // The retained transform recomputes a point relative to its pivot. IEEE-754
  // cancellation may change a final bit; typed actions and untouched source
  // snapshots still compare exactly, derived coordinates use 1e-12 metres.
  copied.objects[1].spec.plane.origin.forEach((value,i) => assert.ok(Math.abs(value-[-1.23456789,2.5,0][i]) < 1e-12));
  assert.deepEqual(await page.evaluate(()=>window.actions.map(a=>a.translation)),[[-1.23456789,0,0],[0,2.5,0]]);
  await page.keyboard.press("Control+z"); await page.waitForFunction(()=>window.snapshot().index===1);
  assert.equal((await snapshot()).objects.length,1);
  await page.keyboard.press("Control+Shift+z"); await page.waitForFunction(()=>window.snapshot().index===2);
  assert.deepEqual(await snapshot(),copied,"undo/redo preserves the same copied identity");
  // Starting another drag never implies confirmation; cancellation removes all handles.
  for (const cancellation of ["Escape","pointercancel","lostpointercapture","blur"]) {
    await arm(); await drag("Z",[0,0,0.5],false);
    if (cancellation === "Escape") await page.keyboard.press("Escape");
    else if (cancellation === "blur") await page.evaluate(()=>window.dispatchEvent(new Event("blur")));
    else await page.evaluate(kind=>document.querySelector(".stage-move").dispatchEvent(new PointerEvent(kind,{bubbles:true,pointerId:1})),cancellation);
    await page.mouse.up(); await page.waitForFunction(()=>window.readRuntime().translation===null);
    assert.equal(await count(),2); assert.deepEqual(await snapshot(),copied);
  }
  // A stale selection/base cannot complete a preview built against the old source.
  await arm(); await drag("X",[0.7,0,0],false);
  await page.evaluate(()=>window.setBase("base-2")); await page.mouse.up();
  await page.keyboard.press("Enter"); assert.equal(await count(),2);
  await page.keyboard.press("Escape");
  await arm(); await axis().selectOption("XZ"); await field("X").fill("3");
  await axis().selectOption("Y");
  assert.equal(await field("X").inputValue(),"0","new constraint resets prior preview coordinates");
  await field("Y").fill(""); await apply(); assert.equal(await count(),2,"blank does not silently become zero");
  await page.keyboard.press("Escape");
  await arm(); await drag("X",[0.2,0,0],false);
  await page.evaluate(()=>window.setBlocked(true)); await page.mouse.up();
  await page.keyboard.press("Enter"); assert.equal(await count(),2);
  await page.evaluate(()=>{window.arm(null);window.setBlocked(false);});
  await page.waitForFunction(()=>window.readRuntime().translation===null);
  // Gizmo and numeric panel visibility never undo the previous camera-stability repair.
  await arm(); const beforeResize=await camera();
  await page.evaluate(()=>window.setWidth("900px")); await page.waitForTimeout(100);
  assert.deepEqual(await camera(),beforeResize);
  await axis().selectOption("Z"); await field("Z").fill("1.125");
  assert.deepEqual(errors,[]); assert.deepEqual(projectCalls,[]);
  if (process.env.BROWSER_OUTPUT) {
    await mkdir(process.env.BROWSER_OUTPUT,{recursive:true});
    await page.screenshot({path:path.join(process.env.BROWSER_OUTPUT,"translation-gizmo.png")});
    await writeFile(path.join(process.env.BROWSER_OUTPUT,"translation-report.json"),JSON.stringify({
      source:process.env.SOURCE_SHA,assertions:["six actual axis/plane drags","zero per-pointer project calls","numeric signed override",
        "one explicit commit","copy identity/source preservation","undo/redo","Esc/pointercancel/lostcapture/blur","stale base and blocked interaction",
        "constraint switch and blank refusal","unchanged camera on drag and resize"],actions:await page.evaluate(()=>window.actions),errors,projectCalls,
      boundary:"Real Chromium/WebGL, Stage and local-draft command history. Synthetic editable geometry; not a live Agent or API/OCCT Sync acceptance."
    },null,2));
  }
  console.log("Stage Move/Copy gizmos, exact entry, cancellation, draft/undo identity and camera stability: PASS");
} catch (error) {
  if (page && process.env.BROWSER_OUTPUT) {
    await mkdir(process.env.BROWSER_OUTPUT,{recursive:true});
    await page.screenshot({path:path.join(process.env.BROWSER_OUTPUT,"translation-failure.png")}).catch(()=>{});
    console.error("Browser errors", errors, "project calls", projectCalls);
  }
  throw error;
} finally {
  await browser?.close(); await vite?.close();
  await new Promise(resolve=>http.close(resolve)); await rm(cacheDir,{recursive:true,force:true});
}

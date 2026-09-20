/** Real pointers, scene visibility and constrained snap coordinates in the existing Stage. */
import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import rhino3dm from "rhino3dm";

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const cacheDir = await mkdtemp(path.join(tmpdir(), "scene-snapping-"));
const http = createHttpServer(), errors = [], projectCalls = [];
let browser, vite, page;
const rhino = await rhino3dm();
const model = new rhino.File3dm();
for (let row = 0; row < 20; row++) for (let column = 0; column < 20; column++) {
  const mesh = new rhino.Mesh();
  const x = column * 3, y = row * 3, height = 2 + (row + column) % 5;
  for (const point of [[x,y,0], [x+2,y,0], [x+2,y+2,0], [x,y+2,0],
    [x,y,height], [x+2,y,height], [x+2,y+2,height], [x,y+2,height]]) mesh.vertices().add(...point);
  for (const face of [[0,3,2,1], [4,5,6,7], [0,1,5,4], [1,2,6,5], [2,3,7,6], [3,0,4,7]]) mesh.faces().addQuadFace(...face);
  mesh.normals().computeNormals();
  const attributes = new rhino.ObjectAttributes();
  attributes.name = `mass-${row}-${column}`;
  attributes.colorSource = rhino.ObjectColorSource.ColorFromObject;
  attributes.objectColor = { r: 180, g: 184, b: 191, a: 255 };
  attributes.setUserString("archflow:component", "fixture-massing");
  attributes.setUserString("archflow:object_ref", `cad-object:mass-${row}-${column}`);
  model.objects().add(mesh, attributes);
  attributes.delete();
  mesh.delete();
}
const modelBytes = Buffer.from(model.toByteArray());
model.delete();
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
let initial = createModelDraft([{ elementId: "source", componentId: "fixture", created: true, originalObjectNames: [],
  spec: { profile: [[0,0],[4,0],[4,3],[0,3]], base: 0, height: 3 } }]);
window.actions = []; window.picks = []; window.sketches = [];
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
  window.fixture = entries => {
    viewport.current?.draftPreview(null);
    initial = createModelDraft(entries.map(([elementId,spec]) => ({ elementId, spec, componentId:"fixture", created:true, originalObjectNames:[] })));
    setHistory(initial); select(null); setTool(null); window.actions.length=0; window.sketches.length=0;
  };
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
    onRequestFile:noop, onOpenFile:noop, onSource:noop, onPick:pick, onSketch:async action => { window.sketches.push(action); },
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
    // An oblique view can overlap handle centres. Sample the plane's interior
    // too, keeping actual Three handle picking as the acceptance condition.
    for(const u of [1,.75,1.25]) for(const v of [1,.75,1.25]) {
      const world = new Vector3(relative[0]*u,relative[1]*v,relative[2]).add(h.proxy.position).toArray(), screen = window.projectPoint(world);
      const hit = h.hover({ x:(screen[0]-r.left)/r.width*2-1, y:1-(screen[1]-r.top)/r.height*2, button:0 });
      if (hit === axis) points.push({ world, screen });
    }
  });
  if (!points.length) throw new Error("No visible " + axis + " handle");
  return points[0];
};
</script></body></html>`;
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
try {
  vite = await createServer({ root:webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, cacheDir, publicDir:"../.generated/public", logLevel:"silent",
    plugins:[{ name:"read-only-viewport-probe", enforce:"pre", transform(source,id) {
      if (id.replaceAll("\\", "/").endsWith("/viewer/featureEdges.ts")) {
        return {code:source.replace("  const { positions } = soup;", "  (window as any).featureBuilds = ((window as any).featureBuilds ?? 0) + 1;\n  const { positions } = soup;"),map:null};
      }
      if (!id.replaceAll("\\", "/").endsWith("/viewer/ThreeDmViewport.tsx")) return;
      const marker = "  const pickAt = useCallback(";
      assert.equal(source.split(marker).length,2);
      return {code:source.replace(marker,"  (window as any).readRuntime = () => runtimeRef.current; (window as any).readInteraction = () => interaction.current;\n"+marker),map:null};
    }},react()], server:{middlewareMode:true,hmr:false,ws:{server:http},watch:null} });
  http.on("request", async (request,response) => {
    if (request.url.startsWith("/scene-snap-test")) { response.setHeader("Content-Type","text/html"); response.end(await vite.transformIndexHtml(request.url,html)); }
    else if (request.url === "/fixture.3dm") { response.end(modelBytes); }
    else if (request.url.startsWith("/api/")) { projectCalls.push(request.url); response.writeHead(500).end(); }
    else vite.middlewares(request,response);
  });
  await new Promise(resolve=>http.listen(0,"127.0.0.1",resolve));
  browser = await chromium.launch({headless:true, ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath:process.env.CHROMIUM_EXECUTABLE} : {}), args:["--enable-unsafe-swiftshader","--no-sandbox"]});
  page = await browser.newPage({viewport:{width:1280,height:850},locale:"en-US"});
  page.on("pageerror",error=>errors.push(error.message)); page.setDefaultTimeout(12000);
  await page.goto(`http://127.0.0.1:${http.address().port}/scene-snap-test?lang=en`);
  await page.waitForFunction(()=>window.readRuntime?.()?.draftObjects.size===1);
  await page.evaluate(()=>window.viewport.current.fitView());
  const solid = (x, y, base = 0, width = 4, depth = 3, height = 3) => ({ profile:[[x,y],[x+width,y],[x+width,y+depth],[x,y+depth]], base, height });
  const source = ["source", solid(0,0)], neighbour = ["neighbour", solid(6,0)];
  const load = async (entries = [source, neighbour]) => {
    await page.keyboard.press("Escape"); await page.keyboard.press("Escape");
    await page.evaluate(entries => window.fixture(entries), entries);
    await page.waitForFunction(count=>window.readRuntime().draftObjects.size===count,entries.length);
  };
  const setView = async (ortho = true, oblique = false, zoom = 1) => {
    await page.evaluate(({ortho,oblique,zoom})=>{
      window.viewport.current.standardView(ortho ? "top" : "perspective");
      const r=window.readRuntime(), c=r.camera;
      c.position.fromArray(oblique ? [14,-18,14] : [4,1.5,24]); c.up.set(0,oblique ? 0 : 1,oblique ? 1 : 0);
      r.controls.target.set(4,1.5,1.5); c.lookAt(r.controls.target);
      if(ortho) { c.left=-8; c.right=8; c.top=5; c.bottom=-5; }
      c.zoom=zoom; c.updateProjectionMatrix(); r.controls.update(); c.updateMatrixWorld(); r.render();
    },{ortho,oblique,zoom});
  };
  const screen = point => page.evaluate(point=>window.projectPoint(point),point);
  const move = async (point, offset=[0,0]) => {
    const p=await screen(point); await page.mouse.move(p[0]+offset[0],p[1]+offset[1]); return [p[0]+offset[0],p[1]+offset[1]];
  };
  const marker = () => page.locator(".viewport-snap");
  const near = (actual,expected) => assert.ok(actual.every((v,i)=>Math.abs(v-expected[i])<1e-6),`${actual} != ${expected}`);
  const markerAt = async point => {
    const expected=await screen(point), actual=await marker().boundingBox();
    assert.ok(actual); assert.ok(Math.hypot(actual.x+4-expected[0],actual.y+4-expected[1])<1, `the marker is at the committed world point: ${JSON.stringify({point,expected,actual})}`);
  };
  const camera = () => page.evaluate(()=>window.viewport.current.camera());
  for(const ortho of [true,false]) for(const zoom of [.7,1.4]) {
    await load(); await setView(ortho,false,zoom);
    await page.keyboard.press("t");
    const stable=await camera(), cursor=await move([6,3,3],[-9,-9]);
    await page.waitForFunction(()=>document.querySelector(".viewport-snap")?.textContent.trim()==="End");
    assert.equal(await page.evaluate(([x,y])=>window.viewport.current.sampleAt(x,y),cursor),null,"acquire without a first hit");
    await markerAt([6,3,3]);
    for(const offset of [[-9.2,-9],[-8.8,-9],[-9.1,-9]]) { await move([6,3,3],offset); await markerAt([6,3,3]); }
    await page.evaluate(()=>window.viewport.current.clearSnap()); await move([6,1.5,3],[3,0]);
    await page.waitForFunction(()=>document.querySelector(".viewport-snap")?.textContent.trim()==="Mid"); await markerAt([6,1.5,3]);
    await page.evaluate(()=>window.viewport.current.clearSnap()); await move([6,.6,3],[-5,0]);
    await page.waitForFunction(()=>document.querySelector(".viewport-snap")?.textContent.trim()==="Edge");
    await page.evaluate(()=>window.viewport.current.clearSnap()); await move([7,1,3]);
    await page.waitForFunction(()=>document.querySelector(".viewport-snap")?.textContent.trim()==="Face");
    assert.deepEqual(await camera(),stable);
    console.log(`PASS ${ortho?"orthographic":"perspective"} zoom ${zoom}: End/Mid/Edge/Face, empty pixel, marker and stability`);
  }

  // The first hit can be a broad lower object while B's silhouette remains visible.
  await load([source,neighbour,["floor",solid(-2,-2,-2,14,8,1)]]); await setView();
  let cursor=await move([6,3,3],[-9,-9]);
  await page.waitForTimeout(40);
  assert.equal(await marker().count(),0,"ordinary selection does not offer B's marker when clicking would pick the floor");
  await page.mouse.click(...cursor);
  await page.waitForFunction(()=>window.picks.at(-1)?.draftElementId==="floor");
  await page.keyboard.press("t");
  cursor=await move([6,3,3],[-9,-9]);
  assert.equal(await page.evaluate(([x,y])=>window.viewport.current.sampleAt(x,y)?.objectName,cursor),"draft:floor");
  await page.waitForFunction(()=>document.querySelector(".viewport-snap")?.textContent.trim()==="End"); await markerAt([6,3,3]);
  // Cover the held target without touching the camera or cursor.
  await page.evaluate(()=>{
    const r=window.readRuntime(); r.draftObjects.get("floor").object.visible=false;
    r.draftObjects.get("neighbour").object.visible=false;
  });
  await move([6,3,3],[-9.1,-9]); await page.waitForFunction(()=>!document.querySelector(".viewport-snap"));
  await load([source,neighbour,["cover",solid(5,2,5,4,3,1)]]); await setView();
  await page.keyboard.press("t"); await move([6,3,3],[-9,-9]);
  await page.waitForFunction(()=>document.querySelector(".viewport-snap")?.textContent.trim()==="Face");
  assert.equal(await page.evaluate(()=>window.readInteraction().modelSnap),null,"an occluded endpoint is not retained");
  console.log("PASS adjacent first-hit object, hidden target and actual mesh occlusion");

  // A line starts on A and ends on B. XY remains authoritative over their roof Z.
  await load(); await setView();
  await page.getByRole("button",{name:"Line",exact:true}).click();
  cursor=await move([4,3,3],[9,-9]); await page.mouse.click(...cursor);
  cursor=await move([6,3,3],[-9,-9]);
  await page.waitForFunction(()=>document.querySelector(".viewport-snap")?.textContent.trim()==="End · projected");
  await markerAt([6,3,0]);
  near(await page.evaluate(()=>window.readInteraction().sketch.cursor),[6,3]);
  if(process.env.BROWSER_OUTPUT) {
    await mkdir(process.env.BROWSER_OUTPUT,{recursive:true});
    await page.screenshot({path:path.join(process.env.BROWSER_OUTPUT,"scene-snapping-line.png")});
  }
  await page.mouse.click(...cursor); await page.keyboard.press("Enter");
  await page.waitForFunction(()=>window.sketches.length===1);
  const line=await page.evaluate(()=>window.sketches[0]);
  assert.deepEqual(line.profile,[[4,3],[6,3]]); assert.equal(line.base,0);
  console.log("PASS cross-object line commits the same projected point shown by the marker");

  await load([source,["neighbour",solid(6,1)]]); await setView();
  await page.getByRole("button",{name:"Line",exact:true}).click();
  cursor=await move([4,3,3]); await page.mouse.click(...cursor);
  await move([6,4,3]);
  near(await page.evaluate(()=>window.readInteraction().sketch.cursor),[6,4]);
  await page.keyboard.down("Shift"); await page.mouse.down(); await page.mouse.up(); await page.keyboard.up("Shift");
  near(await page.evaluate(()=>window.readInteraction().sketch.vertices.at(-1)),[6,3]);
  await page.keyboard.press("Enter"); await page.waitForFunction(()=>window.sketches.length===1);
  assert.deepEqual((await page.evaluate(()=>window.sketches[0])).profile,[[4,3],[6,3]]);
  console.log("PASS Shift pressed without pointer motion re-constrains the clicked endpoint");

  for(const [tool,axis,target,expected] of [["move","X",[9,1.5,1.5],[7,0,0]],
    ["copy","X",[9,1.5,1.5],[7,0,0]],["move","XY",[9,3.5,1.5],[7,2,0]]]) {
    // The chosen visible face/edge is compatible with the locked axis/plane.
    await load([source,["neighbour",solid(6,-.5,0,3,4,3)]]); await setView(true,true);
    await page.mouse.click(...await screen([2,1.5,3]));
    await page.waitForFunction(()=>window.picks.at(-1)?.draftElementId==="source");
    if(tool==="copy") await page.evaluate(()=>window.arm("copy"));
    else await page.locator('button[data-model-tool="move"]').click();
    await page.locator(".stage-move").waitFor();
    const handle=await page.evaluate(axis=>window.handlePoint(axis),axis);
    const stable=await camera();
    await page.mouse.move(...handle.screen); await page.mouse.down(); await move(target,[1,-1]);
    await marker().waitFor();
    const translation=await page.evaluate(()=>window.readInteraction().move.translation);
    near(translation,expected); await markerAt(target);
    await page.mouse.up(); await page.keyboard.press("Enter");
    await page.waitForFunction(()=>window.actions.length===1);
    near(await page.evaluate(()=>window.actions[0].translation),expected);
    assert.deepEqual(await camera(),stable);
    await page.keyboard.press("Control+z"); await page.waitForFunction(()=>window.snapshot().index===0);
    await page.keyboard.press("Control+Shift+z"); await page.waitForFunction(()=>window.snapshot().index===1);
    console.log(`PASS ${tool}: shared scene point, ${axis} authority, explicit commit, undo/redo and fixed camera`);
  }

  // A neighbouring authored roof edge supplies a signed normal distance.
  await load([source,["neighbour",solid(2,1.5,5,3,3,1)]]); await setView(true,true);
  await page.mouse.click(...await screen([2,1.5,3]));
  await page.waitForFunction(()=>window.picks.at(-1)?.draftElementId==="source");
  await page.locator('button[data-model-tool="pushPull"]').click(); await page.locator(".stage-pushpull").waitFor();
  await move([2,1.5,5],[2,1]); await marker().waitFor();
  near([await page.evaluate(()=>window.readInteraction().pushPull.distance)],[2]); await markerAt([2,1.5,5]);
  const field=page.getByRole("spinbutton",{name:"Distance m",exact:true}); await field.fill("1.125");
  await page.waitForFunction(()=>!document.querySelector(".viewport-snap")); await field.press("Enter");
  await page.waitForFunction(()=>window.actions.length===1);
  assert.equal(await page.evaluate(()=>window.actions[0].distance),1.125);
  console.log("PASS normal snapping and exact numeric override clear the inferred marker");
  await load([]);
  const beforeLoaded=await page.evaluate(()=>window.featureBuilds);
  await page.evaluate(async()=>{
    const blob=await (await fetch("/fixture.3dm")).blob();
    await window.viewport.current.openFile(new File([blob],"fixture.3dm"));
    window.viewport.current.standardView("top"); window.viewport.current.fitView();
  });
  await page.keyboard.press("t");
  await move([29,29,5],[4,-4]);
  await page.waitForFunction(()=>document.querySelector(".viewport-snap")?.textContent.trim()==="End");
  await markerAt([29,29,5]);
  const measured=await page.evaluate(beforeLoaded=>{
    const v=window.viewport.current, p=window.projectPoint([29,29,5]), builds=window.featureBuilds, times=[];
    for(let i=0;i<600;i++) {
      v.clearSnap(); const begin=performance.now(); v.snapOnModel(p[0]+4,p[1]-4);
      if(i>=100) times.push(performance.now()-begin);
    }
    times.sort((a,b)=>a-b);
    return {fixture:"400 separate loaded 3DM meshes / Top / 1280x850",queries:600,geometryBuildsForLoad:builds-beforeLoaded,geometryBuildsBefore:builds,
      geometryBuildsAfter:window.featureBuilds,queryMs:{median:times[250],p95:times[475]}};
  },beforeLoaded);
  assert.equal(measured.geometryBuildsAfter,measured.geometryBuildsBefore);
  await page.evaluate(()=>window.viewport.current.setLayerVisibility(0,false));
  await move([29,29,5],[4.1,-4]); await page.waitForFunction(()=>!document.querySelector(".viewport-snap"));
  await page.evaluate(()=>window.viewport.current.setLayerVisibility(0,true));
  await move([29,29,5],[4,-4]); await marker().waitFor();
  await page.evaluate(()=>window.viewport.current.clear());
  await move([29,29,5],[4.1,-4]); await page.waitForFunction(()=>!document.querySelector(".viewport-snap"));
  console.log("PASS loaded 3DM scene, geometry cache reuse, layer hide/show and clear",JSON.stringify(measured));
  assert.deepEqual(errors,[]); assert.deepEqual(projectCalls,[]);
  if(process.env.BROWSER_OUTPUT) {
    await mkdir(process.env.BROWSER_OUTPUT,{recursive:true});
    await page.screenshot({path:path.join(process.env.BROWSER_OUTPUT,"scene-snapping.png")});
    await writeFile(path.join(process.env.BROWSER_OUTPUT,"scene-snapping.json"),JSON.stringify({source:process.env.SOURCE_SHA,
      boundary:"Real Chromium/WebGL, loaded 3DM and Stage/local draft commands; synthetic geometry, no project API/save acceptance claim.",measured,errors,projectCalls},null,2));
  }
} catch(error) {
  if(page && process.env.BROWSER_OUTPUT) {
    await mkdir(process.env.BROWSER_OUTPUT,{recursive:true}); await page.screenshot({path:path.join(process.env.BROWSER_OUTPUT,"scene-snapping-failure.png")});
    console.error(await page.evaluate(()=>({marker:document.querySelector(".viewport-snap")?.textContent,
      sketch:window.readInteraction?.().sketch, move:window.readInteraction?.().move?.translation, actions:window.actions, errors:[]})),errors);
  }
  throw error;
} finally {
  await browser?.close(); await vite?.close(); await new Promise(resolve=>http.close(resolve)); await rm(cacheDir,{recursive:true,force:true});
}

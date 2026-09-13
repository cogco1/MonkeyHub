/** Real Studio/OCCT: local edits remain in memory until explicit Sync. */
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer as createHttpServer, request as httpRequest } from "node:http";
import { mkdir, mkdtemp, readdir, rm, stat } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import rhino3dm from "rhino3dm";

const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const repoRoot = path.resolve(webRoot, "../../..");
const apiRoot = path.resolve(webRoot, "../api");
const python = process.env.PYTHON ?? "python";
const rhino = await rhino3dm();
const root = await mkdtemp(path.join(tmpdir(), "monkeyarch-model-sync-"));
const projectDir = path.join(root, "demo-project");
const errors = [];
let api, vite, browser, page, closing = false;
const http = createHttpServer();
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function within(promise, milliseconds, label) {
  let timer;
  try { return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(label)),milliseconds);})]); }
  finally { clearTimeout(timer); }
}

function fail(message) {
  errors.push(message);
  return false;
}

// ---- the project, built by the API's own fixture so nothing here invents one

try {
const build = spawnSync(python, ["-c", `
import sys
sys.path.insert(0, r"${apiRoot.replaceAll("\\", "/")}")
from tests.support import make_project
make_project(r"${root.replaceAll("\\", "/")}")
print("built")
`], { cwd: apiRoot, encoding: "utf8" });
assert.equal(build.status, 0, `fixture project failed: ${build.stderr || build.stdout}`);

// ---- the real Studio API, on a port of its own, exporting real geometry

const apiPort = await new Promise((resolve) => {
  const probe = createHttpServer();
  probe.listen(0, "127.0.0.1", () => {
    const { port } = probe.address();
    probe.close(() => resolve(port));
  });
});
api = spawn(python, ["-m", "archflow_studio_api.main", "--port", String(apiPort), "--project-dir", projectDir], {
  cwd: apiRoot,
  env: { ...process.env, ARCHFLOW_STUDIO_CAD_EXPORT: "occt", PYTHONUTF8: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});
api.stderr.on("data", (chunk) => { const line = String(chunk); if (line.includes("Traceback")) errors.push(line); });
const apiOrigin = `http://127.0.0.1:${apiPort}`;

async function apiReady() {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    try {
      const answer = await fetch(`${apiOrigin}/api/health`);
      if (answer.ok) return true;
    } catch { /* not listening yet */ }
    await delay(100);
  }
  return false;
}
assert.ok(await apiReady(), "the Studio API never started");

async function call(method, route, body) {
  const answer = await fetch(apiOrigin + route, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await answer.text();
  const value = text === "" ? null : JSON.parse(text);
  if (!answer.ok) throw new Error(`${method} ${route} -> ${answer.status} ${text}`);
  return value;
}

async function finished(jobId) {
  for (let attempt = 0; attempt < 3000; attempt += 1) {
    const job = await call("GET", `/api/jobs/${jobId}`);
    if (job.status === "succeeded" || job.status === "failed") return job;
    await delay(100);
  }
  throw new Error(`job ${jobId} never finished`);
}

/** Every named object of one run's exported model, with the box it occupies. */
async function exported(runId) {
  const runDir = path.join(projectDir, "runs", runId);
  const found = new Map();
  const walk = async (directory) => {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const full = path.join(directory, entry.name);
      if (entry.isDirectory()) await walk(full);
      else if (entry.name.endsWith(".3dm")) {
        const document = rhino.File3dm.fromByteArray(readFileSync(full));
        const objects = document.objects();
        for (let index = 0; index < objects.count; index += 1) {
          const item = objects.get(index);
          const name = item.attributes().name;
          if (!name) continue;
          const box = item.geometry().getBoundingBox();
          found.set(name, {
            x: Number((box.max[0] - box.min[0]).toFixed(3)),
            y: Number((box.max[1] - box.min[1]).toFixed(3)),
            z: Number((box.max[2] - box.min[2]).toFixed(3)),
            min: box.min.map((value) => Number(value.toFixed(3))),
            max: box.max.map((value) => Number(value.toFixed(3))),
          });
        }
        document.delete();
      }
    }
  };
  await stat(runDir);
  await walk(runDir);
  return found;
}

async function runIds() {
  return (await readdir(path.join(projectDir, "runs"), { withFileTypes: true }))
    .filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort();
}

// ---- one candidate ahead of the page, so the embed opens on a run the
//      project's default projection is not on.

const home = await call("GET", "/api/state");
const seedProposal = await call("POST", "/api/proposals/sketch", {
  stateDigest: home.stateDigest, componentId: "portico", elementId: "seed-block",
  profile: [[10, 0], [12, 0], [12, 2], [10, 2]], height: 1.5, baseLevel: "level-ground",
});
const seedRun = (await finished((await call("POST", `/api/proposals/${seedProposal.proposalId}/candidate`)).jobId)).candidateId;
assert.ok((await exported(seedRun)).has("obj-seed-block"), "the seed candidate exported nothing");

// ---- the app, served by vite, talking to that API through this origin

vite = await createServer({ root: webRoot, configFile: false, logLevel: "error", publicDir: ".generated/public",
  define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") }, cacheDir: path.join(root, "vite-cache"),
  plugins: [{ name: "manual-sync-probe", enforce: "pre", transform(source, id) {
    const module = id.split("?")[0].replaceAll("\\", "/");
    let marker, insert;
    if (module.endsWith("/features/stage/Stage.tsx")) {
      marker = "  const interaction = useRef(createInteractionSession());";
      insert = `(window as any).__gesture = () => ({ phase: interaction.current.sketch.phase,
        tool: interaction.current.sketch.tool, pushPull: interaction.current.pushPull });`;
    } else if (module.endsWith("/app/App.tsx")) {
      marker = '  const booting = !canOpenDocuments && (session.status === "idle" || session.status === "loading");';
      insert = `(window as any).__app = () => ({ loaded: loadedArtifact?.runId, base: projection?.referenceRun.runId,
        status: viewerStatus, busy: modelNavigationBusy, picked: picked?.elementId, pickedStatus: picked?.status, interactionEpoch: modelInteractionEpoch.current,
        selection: selection?.elementId, canDelete: canDeleteModel, directTool, error: localModel?.error,
        syncBusy: localModel?.busy, dirty: localModel ? !snapshotsEquivalent(currentDraft(localModel.history), localModel.synced) : false,
        index: localModel?.history.index, commands: draftSnapshot?.commands, ink: gestures.length, documentOpen: documentView.open,
        openDocuments: (open:boolean) => setDocumentView(current=>({...current,mounted:true,open})),
        candidates: transcript.entries.filter((entry) => entry.kind === "candidate").map((entry) => entry.candidateId) });`;
    } else if (module.endsWith("/viewer/ThreeDmViewport.tsx")) {
      source = source.replace("      const started = performance.now();", `
        const __parseTiming = { file: names, bytes: totalSize, start: performance.timeOrigin + performance.now(), end: null as number | null };
        ((window as any).__parseTimes ??= []).push(__parseTiming);
        const started = performance.now();`);
      const readyMarker = '      const fallbackWarning = nurbsFallbackWarning(model);';
      assert.equal(source.split(readyMarker).length,2);
      source = source.replace(readyMarker, '__parseTiming.end = performance.timeOrigin + performance.now();\n' + readyMarker);
      marker = "  const pickAt = useCallback(";
      insert = `(window as any).__view = {
        project: (point: number[]) => {
          const r = runtimeRef.current!, rect = r.renderer.domElement.getBoundingClientRect();
          const p = new Vector3(...point as [number,number,number]).project(r.camera);
          return { x: rect.left + (p.x + 1) * rect.width / 2, y: rect.top + (1 - p.y) * rect.height / 2 };
        },
        blank: () => {
          const r = runtimeRef.current!, rect = r.renderer.domElement.getBoundingClientRect();
          for (let y=rect.top+100;y<rect.bottom-160;y+=45) for(let x=rect.left+100;x<rect.right-100;x+=45) {
            const ray = rayAt(x,y), point = new Vector3();
            if (document.elementFromPoint(x,y) === r.renderer.domElement && !hitAt(x,y) &&
                ray?.ray.intersectPlane(new Plane(new Vector3(0,0,1),0), point) && point.length()<60) return {x,y};
          }
          return null;
        },
        face: (id: string, top = false) => {
          const r = runtimeRef.current!, object = r.draftObjects.get(id)?.object ?? r.model?.getObjectByName('obj-'+id);
          if (!object) return null;
          const b = new Box3().setFromObject(object), min=b.min.toArray(), max=b.max.toArray();
          for(const axis of top?[2]:[2,0,1]) for(const side of top?[1]:[1,0]) for(const u of [.5,.3,.7]) for(const v of [.5,.3,.7]) {
            const p = min.map((n,i)=>n+(max[i]!-n)*.5); p[axis]=side?max[axis]!:min[axis]!;
            p[(axis+1)%3]=min[(axis+1)%3]!+(max[(axis+1)%3]!-min[(axis+1)%3]!)*u;
            p[(axis+2)%3]=min[(axis+2)%3]!+(max[(axis+2)%3]!-min[(axis+2)%3]!)*v;
            const screen=(window as any).__view.project(p), hit=hitAt(screen.x,screen.y);
            if(hit && (hit.draftElementId===id || hit.objectName==='obj-'+id) && (!top || (hit.normal?.z ?? 0)>.99)) return {...screen, normal:hit.normal?.toArray(), point:hit.point.toArray()};
          }
          return null;
        },
        state: () => {
          const r=runtimeRef.current; if(!r)return null;
          const bounds=(o: Object3D) => {const b=new Box3().setFromObject(o);return {min:b.min.toArray(),max:b.max.toArray()};};
          return { highlighted:r.highlighted.length, camera:r.camera.position.toArray(), drafts:[...r.draftObjects].map(([id,{object,spec}])=>({id,spec,...bounds(object),
            ids:[object.uuid,...object.children.flatMap((o:any)=>[o.uuid,o.geometry.uuid,o.material.uuid])],
            visible:object.children.map(o=>o.visible)})),
            hidden:[...r.draftHidden].map(([o,v])=>({name:o.name,visible:o.visible,original:v})),
            preview:r.sketch?.visible?bounds(r.sketch):null };
        }
      };`;
      assert.equal(source.split(marker).length, 2);
      return { code:source.replace(marker, insert+"\n"+marker), map:null };
    } else return;
    assert.equal(source.split(marker).length, 2);
    return { code:source.replace(marker, marker+"\n"+insert), map:null };
  } }, react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
const sent=[], requests=[];
http.on("request", (request,response)=>{
  if(!request.url?.startsWith('/api/')) { vite.middlewares(request,response);return; }
  requests.push({method:request.method,path:request.url});
  if(['POST','PUT','DELETE'].includes(request.method)) {
    const chunks=[]; request.on('data',chunk=>chunks.push(chunk)); request.on('end',()=>{
      const body=Buffer.concat(chunks).toString('utf8'); sent.push({path:request.url,body:body?JSON.parse(body):null});
    });
  }
  const proxy=httpRequest({host:'127.0.0.1',port:apiPort,path:request.url,method:request.method,
    headers:{...request.headers,host:`127.0.0.1:${apiPort}`}},answer=>{
      response.writeHead(answer.statusCode??502,answer.headers);answer.pipe(response);
    });
  proxy.on('error',error=>{if(!closing)errors.push(error.message);if(!response.headersSent)response.writeHead(502);response.end();});
  request.pipe(proxy);
});
await new Promise(resolve=>http.listen(0,'127.0.0.1',resolve));
const {chromium}=await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE ??
  'C:/Users/asus/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs').href);
browser=await chromium.launch({headless:true,channel:'chrome'});
const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'en-US'});
await context.addInitScript(()=>{
  localStorage.setItem('archflow-studio.user-preferences',JSON.stringify({version:1,language:'en',theme:'light',fontScale:1,eventStreamVisible:false,developerMode:true,editingBases:{}}));
  window.EventSource=class {constructor(){queueMicrotask(()=>this.onopen?.());}addEventListener(){}removeEventListener(){}close(){}};
});
page=await context.newPage(); page.setDefaultTimeout(15000);
page.on('pageerror',error=>{errors.push(error.message);console.error('PAGE',error.message);});
const networkTimings=[],timedRequests=new Map(),timingReads=[];
page.on('request',request=>{
  if(!new URL(request.url()).pathname.startsWith('/api/'))return;
  const row={path:new URL(request.url()).pathname,method:request.method(),start:Date.now()};
  networkTimings.push(row);timedRequests.set(request,row);
});
page.on('response',response=>{
  const row=timedRequests.get(response.request());if(!row)return;
  timingReads.push((async()=>{
    await response.finished();row.end=Date.now();row.status=response.status();
    if(row.path.endsWith('/bytes'))row.bytes=(await response.body()).length;
    if(row.path.startsWith('/api/jobs/')&&response.ok())row.jobStatus=(await response.json()).status;
  })().catch(()=>{}));
});
async function stages(t0,label) {
  await Promise.allSettled(timingReads);
  const rows=networkTimings.filter(row=>row.start>=t0);
  const stages=rows.filter(row=>!row.path.startsWith('/api/jobs/')||row.jobStatus==='succeeded');
  const parses=await page.evaluate(()=>window.__parseTimes??[]);
  console.log('STAGES '+label,JSON.stringify({clock:'wall ms from Sync click; HTTP/parse overlap, not additive; fixture startup excluded',
    requests:stages.map(row=>({method:row.method,path:row.path,at:row.start-t0,ms:row.end-row.start,status:row.status,bytes:row.bytes,jobStatus:row.jobStatus})),
    parse:parses.filter(row=>row.start>=t0).map(row=>({at:Math.round(row.start-t0),ms:Math.round(row.end-row.start),bytes:row.bytes})),
    readyAt:Date.now()-t0}));
}
const snap=()=>page.evaluate(()=>({...window.__app?.(),view:window.__view?.state(),gesture:window.__gesture?.()}));
async function wait(predicate,label,timeout=15000) {
  const end=Date.now()+timeout;let state;
  while(Date.now()<end){state=await snap();if(predicate(state))return state;await delay(100);}
  throw new Error(`${label}: ${JSON.stringify(state)}`);
}
const button=name=>page.getByRole('button',{name,exact:true});
const localTimings=[];
async function deselect(){await button('Select').click();await page.locator('body').click({position:{x:5,y:5}});}
async function blank(){await deselect();const p=await page.evaluate(()=>window.__view.blank());assert.ok(p,'no usable ground-plane point');return p;}
async function rectangle(side=2,height=1) {
  const before=await snap(),p=await blank(); await button('Rectangle').click();await page.mouse.click(p.x,p.y);
  const input=page.locator('.sketch-entry input');await input.fill(String(side));await input.press('Enter');await input.fill(String(height));
  const start=performance.now();await input.press('Enter');
  const state=await wait(s=>s.view.drafts.length===before.view.drafts.length+1,'rectangle not retained');
  const obj=state.view.drafts.find(o=>!before.view.drafts.some(old=>old.id===o.id));
  localTimings.push({action:'rectangle Enter → retained/highlighted',ms:Math.round(performance.now()-start)});
  assert.equal(state.picked,obj.id);assert.ok(state.view.highlighted>0,'new drawing must be highlighted');await deselect();return obj.id;
}
async function line(){
  const before=await snap(),p=await blank();await button('Line').click();await page.mouse.click(p.x,p.y);await page.mouse.click(p.x+75,p.y+30);await page.keyboard.press('Enter');
  const state=await wait(s=>s.view.drafts.length===before.view.drafts.length+1,'line not retained');await deselect();return state.view.drafts.find(o=>!before.view.drafts.some(old=>old.id===o.id)).id;
}
async function pick(id,top=false){
  const p=await page.evaluate(({id,top})=>window.__view.face(id,top),{id,top});assert.ok(p,`no visible face of ${id}`);
  await page.mouse.click(p.x,p.y);await wait(s=>s.picked===id,'local pick');return p;
}
const candidateCalls=()=>sent.filter(row=>/^\/api\/proposals\/[^/]+\/candidate$/.test(row.path));
await page.goto(`http://127.0.0.1:${http.address().port}/?embedded=tool&candidate=${seedRun}`);
await wait(s=>s.status==='ready'&&s.loaded===seedRun&&s.base===seedRun&&!s.busy,'initial model',120000);
const originalRuns=await runIds(), beforeWrites=sent.length;
console.log('1 · completed rectangles/lines stay local and independently pickable');
const block=await rectangle(2,1), curve=await line();
let state=await snap();assert.equal(state.view.drafts.find(o=>o.id===curve).spec.closed,false);
assert.deepEqual(state.view.drafts.find(o=>o.id===curve).visible,[true,false,false]);
await pick(block,true);assert.equal((await snap()).pickedStatus,'local');
console.log('2 · two successive P gestures re-pick the moved face, with latest-pointer and numeric override');
await button('Push/Pull').click();await wait(s=>s.gesture.pushPull,'P did not start');
const first=await snap(),g=first.gesture.pushPull,original=first.view.drafts.find(o=>o.id===block);
const end=await page.evaluate(p=>window.__view.project(p),g.face.origin.map((v,i)=>v+g.face.normal[i]*.625));
const pStart=performance.now();await page.evaluate(({x,y})=>{
  const overlay=document.querySelector('.stage-pushpull');
  if(!overlay)throw new Error('missing P overlay');
  for(let i=0;i<10;i++)overlay.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:x-i,clientY:y,pointerId:1}));
  overlay.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:x,clientY:y,pointerId:1}));
  overlay.dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:x,clientY:y}));
},end);
state=await wait(s=>!s.gesture.pushPull&&s.index>first.index,'first pointer P not applied');
const pulled=state.view.drafts.find(o=>o.id===block);assert.ok(Math.abs(pulled.max[2]-original.max[2]-.625)<.01);
localTimings.push({action:'P click → updated geometry',ms:Math.round(performance.now()-pStart)});
assert.deepEqual(pulled.min,original.min);assert.deepEqual(pulled.ids,original.ids,'P updates the existing completed object');
await pick(block,true);state=await wait(s=>s.gesture.pushPull,'second face pick did not arm P');
assert.ok(Math.abs(state.gesture.pushPull.face.origin[2]-pulled.max[2])<1e-5,'second P starts on the moved face');
const pInput=page.locator('.model-edit-panel input').first();await pInput.fill('0.375');
await page.mouse.move(end.x+35,end.y+20);await pInput.press('Enter');
state=await wait(s=>!s.gesture.pushPull&&s.index>first.index+1,'numeric P not applied');
assert.ok(Math.abs(state.view.drafts.find(o=>o.id===block).max[2]-original.max[2]-1)<1e-5);
await deselect();
console.log('3 · delete, undo and redo hide and restore only local/base objects without a request');
await pick(block);await page.keyboard.press('Delete');await wait(s=>!s.view.drafts.some(o=>o.id===block),'local delete');
await page.keyboard.press('Control+z');await wait(s=>s.view.drafts.some(o=>o.id===block),'local undo');
await page.keyboard.press('Control+y');await wait(s=>!s.view.drafts.some(o=>o.id===block),'local redo');
await page.keyboard.press('Control+z');await wait(s=>s.view.drafts.some(o=>o.id===block),'local second undo');
await pick('seed-block');await page.keyboard.press('Delete');await wait(s=>s.view.hidden.some(o=>o.name==='obj-seed-block'&&!o.visible),'base hide');
await page.keyboard.press('Control+z');await wait(s=>!s.view.hidden.some(o=>o.name==='obj-seed-block'),'base restore');
assert.equal(sent.length,beforeWrites,'local modeling wrote to the API');assert.deepEqual(await runIds(),originalRuns);
console.log('3b · selection, text, repeated keys and camera gestures retain their original owners');
await pick(block);await page.keyboard.press('Escape');await wait(s=>!s.picked&&!s.selection&&s.view.highlighted===0,'Esc selection');
await pick(block);const empty=await page.evaluate(()=>window.__view.blank());assert.ok(empty);await page.mouse.click(empty.x,empty.y);
await wait(s=>!s.picked&&!s.selection&&s.view.highlighted===0,'blank selection');
await pick(block);const selected=await snap();
for(const mouseButton of ['right','middle'])await page.mouse.click(empty.x,empty.y,{button:mouseButton});
state=await snap();assert.equal(state.picked,block);assert.equal(state.selection,block);
await page.mouse.move(empty.x,empty.y);await page.mouse.down();await page.mouse.move(empty.x+40,empty.y+25,{steps:8});await page.mouse.up();
state=await snap();assert.notDeepEqual(state.view.camera,selected.view.camera);assert.equal(state.picked,block);
await button('Fit').click();
const typing=await blank();await button('Rectangle').click();await page.mouse.click(typing.x,typing.y);
const textEntry=page.locator('.sketch-entry input'),beforeText=(await snap()).index;
await textEntry.fill('12');await textEntry.press('Delete');await textEntry.press('Backspace');await textEntry.press('Control+z');
assert.equal((await snap()).index,beforeText);await page.keyboard.press('Escape');await deselect();
await pick(block);const repeatedBefore=(await snap()).index;
await page.evaluate(()=>{
  for(const repeat of [false,true,true])window.dispatchEvent(new KeyboardEvent('keydown',{key:'Delete',repeat,bubbles:true}));
});
await wait(s=>s.index===repeatedBefore+1&&!s.view.drafts.some(o=>o.id===block),'held Delete');
await page.keyboard.press('Control+z');await wait(s=>s.index===repeatedBefore,'held Delete undo');
assert.equal(sent.length,beforeWrites);assert.deepEqual(await runIds(),originalRuns);
console.log('4 · one delayed Sync captures its snapshot while later local edits remain usable');
let release,heldResolve,heldAt;const released=new Promise(resolve=>release=resolve),held=new Promise(resolve=>heldResolve=resolve);
const hold=async route=>{const response=await route.fetch();assert.equal(response.status(),201);heldAt=performance.now();heldResolve();await released;await route.fulfill({response});};
await page.route('**/api/proposals/sketch',hold,{times:1});const syncStart=performance.now(),heldSyncWall=Date.now();await button('Sync').click();await within(held,15000,'sketch response was not held');
state=await snap();assert.equal(state.syncBusy,true);assert.equal(state.busy,false);assert.equal(await button('Sync').isDisabled(),true);
const later=await rectangle(1.1,.8);await pick(later);await page.keyboard.press('Delete');await wait(s=>!s.view.drafts.some(o=>o.id===later),'delete during Sync');
await page.keyboard.press('Control+z');await wait(s=>s.view.drafts.some(o=>o.id===later),'undo during Sync');
const artificialHoldMs=performance.now()-heldAt;release();await wait(s=>!s.syncBusy&&s.candidates.length===1,'first Sync did not finish',120000);
console.log('TIMING Sync with later edits',JSON.stringify({clickToSucceededMs:Math.round(performance.now()-syncStart),artificialHoldMs:Math.round(artificialHoldMs),excludingHoldMs:Math.round(performance.now()-syncStart-artificialHoldMs)}));
await stages(heldSyncWall,'continued editing');
state=await snap();assert.equal(state.loaded,seedRun);assert.equal(state.base,seedRun);assert.equal(state.dirty,true);assert.ok(state.view.drafts.some(o=>o.id===later));
assert.equal(candidateCalls().length,1);const saved=await exported(state.candidates[0]);
assert.ok(saved.has('obj-'+block));assert.ok(saved.has('obj-'+curve));assert.ok(!saved.has('obj-'+later),'late object leaked into frozen Sync');
assert.equal(saved.get('obj-'+block).z,2);
console.log('5 · without further input Sync shows its completed candidate automatically');
const quietSyncStart=performance.now(),quietSyncWall=Date.now();await button('Sync').click();await wait(s=>!s.syncBusy&&!s.dirty&&s.candidates.length===2&&s.loaded===s.candidates[1]&&s.base===s.candidates[1],
  'second Sync did not show the completed model',120000);
console.log('TIMING quiet Sync click → visible candidate',Math.round(performance.now()-quietSyncStart),'ms (no artificial hold)');
await stages(quietSyncWall,'quiet auto display');
state=await snap();assert.equal(candidateCalls().length,2);assert.ok((await exported(state.candidates[1])).has('obj-'+later));
assert.equal(state.view.drafts.length,0,'the completed batch must not overlap its saved model');
console.log('6 · a delayed 422 after Undo releases Sync; corrected geometry replaces the failed snapshot');
const failedObject=await rectangle(.9,.9), failedBase=(await snap()).base;
let releaseFailure, failureReady;
const failureReleased=new Promise(resolve=>releaseFailure=resolve), failureHeld=new Promise(resolve=>failureReady=resolve);
await page.route('**/api/proposals/sketch',async route=>{
  failureReady();await failureReleased;
  await route.fulfill({status:422,contentType:'application/json',body:JSON.stringify({code:'INVALID_SKETCH',detail:'Fixture rejected the captured drawing.'})});
},{times:1});
await button('Sync').click();await within(failureHeld,15000,'failure was not held');await page.keyboard.press('Control+z');
await wait(s=>!s.view.drafts.some(o=>o.id===failedObject),'Undo while rejected request is held');releaseFailure();
await wait(s=>!s.syncBusy&&Boolean(s.error),'422 did not release Sync');
assert.equal(candidateCalls().length,2,'a refused snapshot cannot start a candidate');
const corrected=await rectangle(1.2,.7);await button('Sync').click();
await wait(s=>!s.syncBusy&&s.candidates.length===3&&s.loaded===s.candidates[2]&&s.base===s.candidates[2],'corrected Sync did not finish',120000);
state=await snap();const correctedExport=await exported(state.candidates[2]);
assert.ok(correctedExport.has('obj-'+corrected));assert.ok(!correctedExport.has('obj-'+failedObject));
assert.notEqual(state.base,failedBase);assert.equal(candidateCalls().length,3);
console.log('7 · annotation and document keys cannot act on the local model');
const inkObject=await rectangle(.8,.6);await pick(inkObject);const inkModelIndex=(await snap()).index;
const annotate=page.locator('button[aria-controls="annotation-tools"]');await annotate.click();
const inkTool=page.locator('#annotation-tools').getByRole('button',{name:'╱ Line',exact:true});await inkTool.click();
const inkBox=await page.locator('canvas.annotate[data-armed="true"]').boundingBox();assert.ok(inkBox);
await page.mouse.move(inkBox.x+inkBox.width*.4,inkBox.y+inkBox.height*.55);await page.mouse.down();
await page.mouse.move(inkBox.x+inkBox.width*.6,inkBox.y+inkBox.height*.55,{steps:8});await page.mouse.up();
await wait(s=>s.ink>0,'annotation stroke');await page.keyboard.press('Control+z');await wait(s=>s.ink===0,'annotation undo');
await page.keyboard.press('Control+z');await page.keyboard.press('Delete');assert.equal((await snap()).index,inkModelIndex);
await inkTool.click();await annotate.click();await page.keyboard.press('Control+z');await wait(s=>s.index===inkModelIndex-1,'model owns undo again');
await page.keyboard.press('Control+y');await wait(s=>s.index===inkModelIndex,'model redo after annotation');
await page.evaluate(()=>window.__app().openDocuments(true));await wait(s=>s.documentOpen,'documents open');
await page.keyboard.press('Control+z');await page.keyboard.press('Control+y');await page.keyboard.press('Delete');
assert.equal((await snap()).index,inkModelIndex);await page.evaluate(()=>window.__app().openDocuments(false));await wait(s=>!s.documentOpen,'documents close');
assert.equal(candidateCalls().length,3);
console.log('8 · drawing during background model download cancels replacement without losing the unfinished gesture');
const beforeDownload=await snap();let releaseDownload,downloadReady,downloadDone;
const downloadReleased=new Promise(resolve=>releaseDownload=resolve),downloadHeld=new Promise(resolve=>downloadReady=resolve),downloadDelivered=new Promise(resolve=>downloadDone=resolve);
await page.route('**/api/artifacts/*/bytes',async route=>{
  const response=await route.fetch();assert.equal(response.status(),200);downloadReady();await downloadReleased;
  await route.fulfill({response});downloadDone();
},{times:1});
await button('Sync').click();await within(downloadHeld,120000,'background download did not start');
state=await snap();assert.equal(state.loaded,beforeDownload.loaded);assert.equal(state.status,'ready');assert.equal(state.busy,false);
await page.evaluate(()=>document.activeElement?.blur());const bodyKeyEpoch=(await snap()).interactionEpoch;await page.keyboard.press('l');
assert.equal((await snap()).interactionEpoch,bodyKeyEpoch+1,'global shortcut after Sync lost focus must invalidate background adoption');
const nextStart=await blank();await button('Line').click();await page.mouse.click(nextStart.x,nextStart.y);
await wait(s=>s.gesture.phase==='profile','new gesture while background download is held');releaseDownload();await downloadDelivered;await delay(400);
state=await snap();assert.equal(state.loaded,beforeDownload.loaded);assert.equal(state.base,beforeDownload.base);assert.equal(state.gesture.phase,'profile');
await page.mouse.click(nextStart.x+65,nextStart.y+25);await page.keyboard.press('Enter');
await wait(s=>s.view.drafts.length===beforeDownload.view.drafts.length+1,'unfinished gesture lost on background response');await deselect();
assert.equal(candidateCalls().length,4);
console.log('9 · failed job reads release Sync; reconnect polls the retained job without another candidate');
let failedReads=0;
await page.route('**/api/jobs/*',async route=>{
  failedReads++;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({code:'TEMPORARY_READ_FAILURE',detail:'Fixture job status is temporarily unavailable.'})});
});
await button('Sync').click();await wait(s=>!s.syncBusy&&Boolean(s.error),'job-read failure did not release Sync',60000);
assert.ok(failedReads>=3);assert.equal(candidateCalls().length,5);const retryCalls=sent.length;
await page.unroute('**/api/jobs/*');await button('Sync').click();await wait(s=>!s.syncBusy&&!s.error,'job reconnect did not settle',60000);
assert.equal(candidateCalls().length,5);assert.equal(sent.length,retryCalls,'job reconnect wrote another proposal/candidate');
console.log('TIMING local actions (automation observation, 100 ms polling maximum)',JSON.stringify(localTimings));
const qa=path.join(tmpdir(),'monkeyarch-curves-qa');await mkdir(qa,{recursive:true});await deselect();await button('Line').hover();await delay(400);
await page.screenshot({path:path.join(qa,'manual-sync-toolbar.png')});
console.log('PASS manual Sync: local draw/P/delete/undo/redo zero writes; delayed Sync permits continued editing; one candidate per snapshot; exact OCCT export');
} finally {
  closing=true;await browser?.close().catch(()=>{});await vite?.close().catch(()=>{});
  if(http.listening)await new Promise(resolve=>http.close(resolve));api?.kill();await delay(300);await rm(root,{recursive:true,force:true}).catch(()=>{});
}
assert.deepEqual(errors,[]);

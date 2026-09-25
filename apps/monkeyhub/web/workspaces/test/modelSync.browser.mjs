import { workspaceFixture } from "./workspaceFixture.mjs";
/** Real Studio/OCCT: local recovery persists edits; only explicit Sync builds candidates. */
// Run normally for the existing gesture/Sync regression, or set
// MONKEYARCH_AUTOSAVE=1 for recovery, concurrency and milestone coverage.
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
const repoRoot = path.resolve(webRoot, "../../../..");
const apiRoot = path.resolve(repoRoot, "apps/archflow-studio/api");
const python = process.env.PYTHON ?? "python";
const rhino = await rhino3dm();
const root = await mkdtemp(path.join(tmpdir(), "monkeyarch-model-sync-"));
const projectDir = path.join(root, "demo-project");
const errors = [];
// Authored-only modes reuse the same real fixture/service/browser setup.
const authoredContinue = process.env.MONKEYARCH_AUTHORED_ONLY === "continue";
const authoredUndo = process.env.MONKEYARCH_AUTHORED_ONLY === "undo";
const authoredInput = authoredContinue || authoredUndo;
const authoredOnly = process.env.MONKEYARCH_AUTHORED_ONLY === "1" || authoredInput;
const moveCopyOnly = process.env.MONKEYARCH_MOVE_COPY === "1";
const rotateOnly = process.env.MONKEYARCH_ROTATE === "1";
const scaleOnly = process.env.MONKEYARCH_SCALE === "1";
const autosaveOnly = process.env.MONKEYARCH_AUTOSAVE === "1";
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
from tests.support import ${authoredOnly ? "make_empty_project" : "make_project"}
${authoredOnly ? "make_empty_project" : "make_project"}(r"${root.replaceAll("\\", "/")}")
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
  // Importing the API with OCCT can take most of a minute on a busy machine.
  for (let attempt = 0; attempt < 900; attempt += 1) {
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
  // Recovery commands have their own P036 storage; this counts geometry runs.
  return (await readdir(path.join(projectDir, "runs"), { withFileTypes: true }))
    .filter((entry) => entry.isDirectory() && entry.name !== "studio-working-draft").map((entry) => entry.name).sort();
}

// ---- one candidate ahead of the page, so the embed opens on a run the
//      project's default projection is not on.

const home = await call("GET", "/api/state");
let seedRun = null;
if (!authoredOnly) {
const seedProposal = await call("POST", "/api/proposals/sketch", {
  stateDigest: home.stateDigest, componentId: "portico", elementId: "seed-block",
  profile: rotateOnly || scaleOnly ? [[10, 0], [13, 0], [10.5, 2]] : [[10, 0], [12, 0], [12, 2], [10, 2]], height: 1.5, baseLevel: "level-ground",
});
const seedJob = await finished((await call("POST", `/api/proposals/${seedProposal.proposalId}/candidate`)).jobId);
assert.equal(seedJob.status,"succeeded",`seed candidate failed: ${seedJob.error}`);
seedRun = seedJob.candidateId;
assert.ok((await exported(seedRun)).has("obj-seed-block"), "the seed candidate exported nothing");
}

// ---- the app, served by vite, talking to that API through this origin

vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, logLevel: "error", publicDir: "../.generated/public",
  define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") }, cacheDir: path.join(root, "vite-cache"),
  plugins: [workspaceFixture(), { name: "manual-sync-probe", enforce: "pre", transform(source, id) {
    const module = id.split("?")[0].replaceAll("\\", "/");
    let marker, insert;
    if (module.endsWith("/features/stage/Stage.tsx")) {
      marker = "  const interaction = useRef(createInteractionSession());";
      insert = `useEffect(() => { (window as any).__stageCommits = ((window as any).__stageCommits ?? 0) + 1; });
        (window as any).__gesture = () => ({ phase: interaction.current.sketch.phase,
        tool: interaction.current.sketch.tool, modelSnap: interaction.current.modelSnap?.snap ?? null, pushPull: interaction.current.pushPull, move: interaction.current.move, rotate: interaction.current.rotate, scale: interaction.current.scale });`;
    } else if (module.endsWith("/app/App.tsx")) {
      marker = '  const booting = !canOpenDocuments && (session.status === "idle" || session.status === "loading");';
      insert = `(window as any).__app = () => ({ loaded: loadedArtifact?.runId, base: projection?.referenceRun.runId,
        sessionStatus: session.status, stateDigest: projection?.stateDigest, sourceRunId, sourceLabel,
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
        vertices: (id: string) => {
          const r=runtimeRef.current!, object=id==='preview'?r.sketch:r.draftObjects.get(id)?.object??r.model?.getObjectByName('obj-'+id);
          const points:number[][]=[];
          object?.updateWorldMatrix(true,true);
          object?.traverse((child:any)=>{
            if(!child.isMesh||!child.visible)return;
            const attribute=child.geometry.getAttribute('position');
            const count=Math.min(attribute.count,child.geometry.drawRange.count);
            for(let i=0;i<count;i++) {
              const point=new Vector3().fromBufferAttribute(attribute,i).applyMatrix4(child.matrixWorld).toArray();
              if(!points.some(p=>p.every((v,j)=>Math.abs(v-point[j]!)<1e-6)))points.push(point);
            }
          });
          return points;
        },
        previewIdentity: () => {
          const object=runtimeRef.current?.sketch;
          return object ? [object,...object.children.flatMap((child:any)=>[child,child.geometry,child.material,child.geometry.getAttribute('position')])] : [];
        },
        project: (point: number[]) => {
          const r = runtimeRef.current!, rect = r.renderer.domElement.getBoundingClientRect();
          const p = new Vector3(...point as [number,number,number]).project(r.camera);
          return { x: rect.left + (p.x + 1) * rect.width / 2, y: rect.top + (1 - p.y) * rect.height / 2 };
        },
        blank: (skip=0) => {
          const r = runtimeRef.current!, rect = r.renderer.domElement.getBoundingClientRect();
          for (let y=rect.top+100;y<rect.bottom-160;y+=45) for(let x=rect.left+100;x<rect.right-100;x+=45) {
            const ray = rayAt(x,y), point = new Vector3();
            if (document.elementFromPoint(x,y) === r.renderer.domElement && !hitAt(x,y) &&
                ray?.ray.intersectPlane(new Plane(new Vector3(0,0,1),0), point) && point.length()<60 && skip--<=0) return {x,y};
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
          return { hasBaseModel:r.model!==null, highlighted:r.highlighted.length, hover:r.preselection?.group.visible??false,
            camera:r.camera.position.toArray(), drafts:[...r.draftObjects].map(([id,{object,spec}])=>({id,spec,...bounds(object),
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
// Existing gesture assertions count model writes; recovery persistence is
// checked separately without treating a local snapshot as a generated model.
const sent=[], requests=[], draftWrites=[];
http.on("request", (request,response)=>{
  if(!request.url?.startsWith('/api/')) { vite.middlewares(request,response);return; }
  requests.push({method:request.method,path:request.url});
  if(['POST','PUT','DELETE'].includes(request.method)) {
    const chunks=[]; request.on('data',chunk=>chunks.push(chunk)); request.on('end',()=>{
      const body=Buffer.concat(chunks).toString('utf8');
      const destination=request.url.startsWith('/api/working-draft')?draftWrites:sent;
      destination.push({path:request.url,body:body?JSON.parse(body):null});
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
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
browser=await chromium.launch({headless:true,channel:'chrome'});
const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'en-US'});
await context.addInitScript(()=>{
  localStorage.setItem('archflow-studio.user-preferences',JSON.stringify({version:1,language:'en',theme:'light',fontScale:1,eventStreamVisible:false,developerMode:true,editingBases:{}}));
  window.EventSource=class {constructor(){queueMicrotask(()=>this.onopen?.());}addEventListener(){}removeEventListener(){}close(){}};
});
page=await context.newPage(); page.setDefaultTimeout(15000);
// Only navigation waits longer: the first one also waits out Vite's dependency scan.
page.setDefaultNavigationTimeout(180000);
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
// #302: Record coming or going moves no tool; Select stands for the fixed tools.
const toolAt=async()=>{const box=await button('Select').boundingBox();assert.ok(box,'Select is not on screen');return {x:box.x,y:box.y};};
async function steadyTools(before,label){
  // A layout change lands within a frame or two; give it the chance to show.
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal((await toolAt()).x,before.x,`${label} moved the tools sideways`);
}
/** Where the row has no room beside the tools, Record takes a line above them and still covers none. */
async function narrowRecord(narrowBefore){
  const size=page.viewportSize();
  await page.setViewportSize({width:700,height:size.height});
  await page.locator('.model-tools__sync--above').waitFor();
  await steadyTools(narrowBefore,'Record above the tools');
  assert.equal((await toolAt()).y,narrowBefore.y,'Record above the tools moved them down or up');
  const record=await button('Record').boundingBox(),tools=await page.locator('.model-tools button.model-tool-button:not([data-tool-icon="sync"])').evaluateAll(nodes=>
    nodes.map(node=>node.getBoundingClientRect()).filter(box=>box.width>0).map(({x,y,width,height})=>({x,y,width,height})));
  assert.ok(record&&record.x>=0&&record.x+record.width<=700,'Record stays on screen at a narrow width');
  for(const box of tools)assert.ok(record.x+record.width<=box.x||box.x+box.width<=record.x||record.y+record.height<=box.y||box.y+box.height<=record.y,
    `Record covers a tool at ${JSON.stringify(box)}`);
  await page.setViewportSize(size);
  await page.locator('.model-tools__sync--above').waitFor({state:'detached'});
}
async function narrowToolAt(){
  const size=page.viewportSize();
  await page.setViewportSize({width:700,height:size.height});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const at=await toolAt();
  await page.setViewportSize(size);
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  return at;
}
// GH-234 Q1/Q2: a generated seed never becomes the saved base on its own; the
// architect's explicit choice of it is what opening the page restores.
async function openSeed(label){
  const listed=await call('GET','/api/working-draft');
  if(listed.current?.runId!==seedRun)await call('PUT','/api/working-draft',{projectId:listed.projectId,runId:seedRun,baseRevisionSha256:listed.revisionSha256});
  await page.goto(`http://127.0.0.1:${http.address().port}/?embedded=tool&candidate=${seedRun}`);
  await wait(s=>s.status==='ready'&&s.loaded===seedRun&&s.base===seedRun&&!s.busy,label,120000);
}
const localTimings=[];
async function deselect(){await button('Select').click();await page.locator('body').click({position:{x:5,y:5}});}
async function blank(){await deselect();const p=await page.evaluate(()=>window.__view.blank());assert.ok(p,'no usable ground-plane point');return p;}
async function rectangle(side=2,height=1,from=null) {
  const before=await snap(),p=from??await blank(); await button('Rectangle').click();await page.mouse.click(p.x,p.y);
  const input=page.locator('.sketch-entry input');await input.fill(String(side));await input.press('Enter');await input.fill(String(height));
  const start=performance.now();await input.press('Enter');
  const state=await wait(s=>s.view.drafts.some(o=>o.id===s.picked&&!before.view.drafts.some(old=>old.id===o.id)),'rectangle not retained');
  const obj=state.view.drafts.find(o=>o.id===state.picked);
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
if (autosaveOnly) {
  const readDraft=()=>call('GET','/api/working-draft');
  async function savedDraft(predicate,label) {
    const end=Date.now()+15000;let draft;
    while(Date.now()<end){draft=await readDraft();if(predicate(draft))return draft;await delay(100);}
    throw new Error(`${label}: ${JSON.stringify(draft)}`);
  }
  // GH-234 Q2: the seed is a generated candidate, so it is only listed; the
  // architect's explicit choice of it is what reopening restores.
  const listed=await readDraft();
  assert.equal(listed.current,null,'a generated candidate never becomes the saved base on its own');
  assert.ok(listed.recovery.some(row=>row.runId===seedRun),'the generated seed is listed for recovery');
  await call('PUT','/api/working-draft',{projectId:listed.projectId,runId:seedRun,baseRevisionSha256:listed.revisionSha256});
  await page.goto(`http://127.0.0.1:${http.address().port}/?embedded=tool`);
  await wait(s=>s.status==='ready'&&s.loaded===seedRun&&s.base===seedRun&&!s.busy,'restored current source',120000);
  const initial=await snap(),originalRuns=await runIds(),steady=await toolAt();
  const primaryPage=page,otherPage=await context.newPage();
  page=otherPage;
  await page.goto(primaryPage.url());
  await wait(s=>s.status==='ready'&&s.loaded===seedRun&&!s.busy,'second editor current source',120000);
  page=primaryPage;
  // GH-234: edits whose autosave is still being written are not held yet, so
  // they are unsaved; once the working draft holds them they are only unsynced
  // and would survive a restart. Chat needs a Sync either way.
  const reason=()=>page.evaluate(()=>window.__workspaceDesignContext?.unavailableReason);
  let releaseFirstSave,firstSaveSent;
  const firstSaveReleased=new Promise(resolve=>releaseFirstSave=resolve),firstSaveHeld=new Promise(resolve=>firstSaveSent=resolve);
  await page.route('**/api/working-draft/local',async route=>{firstSaveSent();await firstSaveReleased;await route.continue();},{times:1});
  const object=await rectangle(2,1.25);
  await button('Record').waitFor();await steadyTools(steady,'Record appearing');
  await within(firstSaveHeld,15000,'the first autosave was never written');
  assert.equal(await reason(),'unsaved','an autosave still being written leaves its edits unsaved');
  assert.equal(await page.evaluate(()=>window.__workspaceDesignContext.designContext),null);
  releaseFirstSave();
  const saved=await savedDraft(d=>d.localDraft?.commands.length===1,'local commands were not retained');
  await page.waitForFunction(()=>window.__workspaceDesignContext?.unavailableReason==='unsynced');
  assert.equal(await page.evaluate(()=>window.__workspaceDesignContext.designContext),null,'held local edits still need a Sync before chat');
  assert.equal(saved.localDraft.source.sourceRunId,seedRun);
  assert.equal(saved.localDraft.source.stateDigest,initial.stateDigest);
  assert.equal(candidateCalls().length,0,'ordinary edits never create a candidate');
  assert.deepEqual(await runIds(),originalRuns,'autosave creates no geometry run');
  assert.ok(draftWrites.some(row=>row.path==='/api/working-draft/local'&&row.body.draft?.commands.length===1));
  try {
    page=otherPage;
    await rectangle(1,.75);
    await wait(s=>s.error?.includes('另一窗口'),'older editor silently replaced the newer recovery');
    assert.equal(await reason(),'unsaved','a refused autosave leaves its edits unsaved');
    assert.deepEqual((await readDraft()).localDraft.commands,saved.localDraft.commands,'a stale editor cannot overwrite another window');
  } finally {page=primaryPage;await otherPage.close();}

  // Cold restoration can learn the state before the artifact list arrives.
  // The original model and the exact local preview must both survive that order.
  await page.route(/\/api\/artifacts(?:\?.*)?$/,async route=>{await delay(700);await route.continue();},{times:1});
  await page.reload();
  await wait(s=>s.status==='ready'&&s.loaded===seedRun&&s.base===seedRun&&s.view?.hasBaseModel&&
    s.view.drafts.some(row=>row.id===object),'cold restoration lost the base model or local preview',30000);
  assert.deepEqual((await snap()).commands,saved.localDraft.commands);
  assert.equal(candidateCalls().length,0,'restoring local commands never starts Sync');
  await page.waitForFunction(()=>window.__workspaceDesignContext?.unavailableReason==='unsynced');
  assert.equal(await page.evaluate(()=>window.__workspaceDesignContext.designContext),null,'a restored draft still asks chat for a Sync');
  await deselect();await page.keyboard.press('Control+z');
  await wait(s=>!s.dirty&&!s.view.drafts.some(row=>row.id===object),'Undo did not return to the original model');
  await savedDraft(d=>d.localDraft===null,'Undo to baseline did not clear recovery');
  await button('Record').waitFor({state:'detached'});await steadyTools(steady,'Record leaving with Undo');
  await page.keyboard.press('Control+y');
  await wait(s=>s.dirty&&s.view.drafts.some(row=>row.id===object),'Redo did not recover the local object');
  await savedDraft(d=>d.localDraft?.commands.length===1,'Redo was not retained');
  // SS-5: with its edits autosaved, Record is back with its status line.
  await page.locator('.model-tools__sync-status').filter({hasText:'Unrecorded edits · saved automatically'}).waitFor();
  assert.equal(await button('Record').isEnabled(),true);await steadyTools(steady,'Record returning with Redo');

  let delayedClear=false;
  const delayClear=async route=>{
    if(route.request().method()==='PUT'&&route.request().postDataJSON()?.draft===null&&!delayedClear){
      delayedClear=true;await delay(700);
    }
    await route.continue();
  };
  await page.route('**/api/working-draft/local',delayClear);
  await button('Record').evaluate(node=>{node.click();node.click();});
  const completed=await wait(s=>!s.syncBusy&&!s.dirty&&s.candidates.length===1&&
    s.loaded===s.candidates[0]&&s.base===s.candidates[0],'quiet Sync did not adopt the completed candidate',120000);
  assert.equal(candidateCalls().length,1,'repeated Sync clicks must reuse one candidate request');
  const current=completed.candidates[0];
  await savedDraft(d=>d.localDraft===null&&d.current?.runId===current,'completed Sync did not clear recovery and update current');
  assert.equal(delayedClear,true,'the clear/adoption race was exercised');
  await page.unroute('**/api/working-draft/local',delayClear);
  assert.ok((await exported(current)).has('obj-'+object),'retained candidate must contain the restored geometry');

  await page.locator('.stage__versions-toggle').click();
  // #302: this runtime has a Design Tree, so Versions keeps the working draft, recovery and files
  // while its design history is one link to the tree, like the footer's.
  await page.locator('.versions [data-design-tree-link]').waitFor();
  assert.equal(await page.locator('.versions [data-design-stage], .versions [data-preview-candidate]').count(),0,'no second list of Stages or candidates beside the Design Tree');
  assert.equal(await page.locator('.stage__versions-current, .stage__versions-new').count(),0,'the footer leaves position and new options to the Stage chip');
  assert.equal(await page.getByRole('button',{name:'Open in Design tree',exact:true}).count(),2,'the footer and Versions each offer the one link');
  const recovery=page.locator('.versions details').filter({has:page.locator('summary').filter({hasText:'自动恢复点'})});
  assert.equal(await recovery.evaluate(node=>node.open),false,'automatic recovery starts collapsed');
  assert.equal(await page.locator('[data-working-draft]:visible').count(),1,'only current is expanded before saving a milestone');
  await page.getByRole('textbox',{name:'重点版本名称',exact:true}).fill('Autosave milestone');
  await page.getByRole('button',{name:'保存重点版本',exact:true}).click();
  await savedDraft(d=>d.saved.some(row=>row.runId===current&&row.label==='Autosave milestone'),'manual milestone was not saved');
  await page.getByText('Autosave milestone',{exact:true}).waitFor();
  assert.equal(await page.locator('[data-working-draft]:visible').count(),2,'current and the explicit milestone stay visible');
  assert.equal(await recovery.evaluate(node=>node.open),false,'saving a milestone does not expand recovery');
  // Retained commands can be from another app version. A replay failure must
  // leave the original recovery untouched instead of autosaving an empty base.
  const beforeInvalid=await readDraft();
  const clearsBeforeInvalid=draftWrites.filter(row=>row.body?.draft===null).length;
  const invalid={source:{...saved.localDraft.source,sourceRunId:current,stateDigest:completed.stateDigest},
    commands:[{kind:'delete',elementId:'missing-recovery-fixture-object'}]};
  await call('PUT','/api/working-draft/local',{projectId:beforeInvalid.projectId,
    baseRevisionSha256:beforeInvalid.revisionSha256,draft:invalid});
  await page.reload();
  await wait(s=>s.status==='ready'&&s.loaded===current&&!s.busy,'source for incompatible recovery',30000);
  await page.locator('.stage__versions-toggle').click();
  await page.getByRole('alert').filter({hasText:'The draft could not be restored; its saved record is kept'}).waitFor();
  await delay(700);
  assert.deepEqual((await readDraft()).localDraft?.commands,invalid.commands,'failed replay must not clear retained recovery');
  assert.equal(draftWrites.filter(row=>row.body?.draft===null).length,clearsBeforeInvalid,'failed replay must not even submit an automatic clear');
  console.log('PASS working draft: local PUT without candidate, stale-window refusal, cold preview/base restoration, Undo clears recovery, one explicit Sync, current/manual milestone, collapsed recovery, incompatible recovery preserved');
} else if (authoredOnly) {
  assert.deepEqual(await runIds(),[], 'authored-only fixture must begin with no retained run');
  assert.deepEqual((await call('GET','/api/artifacts')).artifacts,[], 'authored-only fixture must begin with no export');
  await page.goto(`http://127.0.0.1:${http.address().port}/?embedded=tool`);
  await wait(s=>s.sessionStatus==='ready'&&s.stateDigest&&s.view&&!s.busy,'authored-only session',30000);
  const initial=await snap(),writesBefore=sent.length;
  assert.equal(initial.sourceRunId,null);assert.equal(initial.sourceLabel,null);assert.equal(initial.loaded,undefined);
  const authoredContext=await page.evaluate(()=>window.__workspaceDesignContext);
  assert.equal(authoredContext.designContext.sourceRunId,null,'authored context must not invent a retained run');
  assert.equal(authoredContext.designContext.stateDigest,initial.stateDigest);
  assert.equal(initial.view.hasBaseModel,false);assert.equal(initial.view.drafts.length,0);
  assert.equal(await button('Record').count(),0,'an empty project offers no Record');
  const steady=await toolAt();
  console.log('0 · authored-only project: first local drawing, selection, delete/undo, then one explicit Sync');
  const first=await rectangle(2,1.25);
  await button('Record').waitFor();await steadyTools(steady,'Record appearing');
  // Authored fixture primitives become visible with the first local snapshot;
  // they are not drawings created by these gestures.
  const authoredPrimitives=(await snap()).view.drafts.filter(object=>object.id!==first).length;
  // Autosave holds the drawing in the working draft; it is still not a candidate.
  await page.waitForFunction(()=>window.__workspaceDesignContext?.unavailableReason==='unsynced');
  assert.equal(await page.evaluate(()=>window.__workspaceDesignContext.designContext),null,'local geometry cannot impersonate a saved chat base');
  let state=await snap();assert.equal(state.loaded,undefined);assert.equal(state.sourceRunId,null);assert.equal(state.view.hasBaseModel,false);
  assert.equal(await page.locator('.stage-empty').count(),0,'a retained local object must replace the empty-canvas message');
  assert.equal(await page.locator('.viewport-state').count(),0,'a local model must not be covered by the idle overlay');
  assert.equal(await page.locator('canvas').first().evaluate(canvas=>getComputedStyle(canvas).opacity),'1');
  await pick(first,true);assert.equal((await snap()).pickedStatus,'local');
  const second=await rectangle(1,.75);
  await pick(first,true);await page.keyboard.press('Escape');await wait(s=>!s.picked&&!s.selection,'clear local selection');
  const face=await page.evaluate(id=>window.__view.face(id,true),first);assert.ok(face);
  await page.mouse.move(0,0);await page.mouse.move(face.x,face.y);await wait(s=>s.view.hover,'local preselection without a base model');
  await pick(first,true);await page.keyboard.press('Delete');await wait(s=>s.view.drafts.length===authoredPrimitives+1&&!s.view.drafts.some(o=>o.id===first),'local delete without a base model');
  await page.keyboard.press('Control+z');await wait(s=>s.view.drafts.length===authoredPrimitives+2,'local undo without a base model');
  await pick(second);assert.equal((await snap()).pickedStatus,'local');
  assert.equal(sent.length,writesBefore,'authored-only draw/pick/delete/undo must make no write request');
  assert.deepEqual(await runIds(),[],'local edits must not create the first run');
  console.log('  authored-only local selection/preselection/delete/undo passed; starting first Sync');
  let releaseFirst,firstReady,later;
  const releasedFirst=new Promise(resolve=>releaseFirst=resolve),firstHeld=new Promise(resolve=>firstReady=resolve);
  if(authoredInput)await page.route('**/api/proposals/sketch',async route=>{
    const response=await route.fetch();assert.equal(response.status(),201);firstReady();await releasedFirst;await route.fulfill({response});
  },{times:1});
  const beforeSyncRequests=requests.length;
  const syncStart=Date.now();await button('Record').click();
  if(authoredInput){
    await within(firstHeld,15000,'first authored-only proposal was not held');const whileHeld=sent.length;
    assert.equal((await snap()).syncBusy,true);assert.equal((await snap()).busy,false);
    if(authoredUndo){
      await page.keyboard.press('Control+z');await page.keyboard.press('Control+z');
      await wait(s=>s.index===0&&s.view.drafts.length===0,'undo both drawings during first Sync');
      assert.equal(await page.locator('.stage-empty').count(),1,'undo to zero local objects restores the empty canvas');
    }else{
      await deselect();const nextCorner=await page.evaluate(()=>window.__view.blank(6));assert.ok(nextCorner);
      later=await rectangle(.8,.6,nextCorner);await pick(first,true);await page.keyboard.press('Delete');
      await wait(s=>s.view.drafts.length===authoredPrimitives+2&&!s.view.drafts.some(o=>o.id===first),'delete during first Sync');
    }
    assert.equal(sent.length,whileHeld,'continued local edits wrote while first Sync was held');
    assert.equal(candidateCalls().length,0);assert.deepEqual(await runIds(),[]);releaseFirst();
  }
  await wait(s=>{
    assert.ok(!s.error,`first authored-only Sync refused: ${s.error}`);
    return !s.syncBusy&&s.candidates.length===1&&(authoredInput ? s.dirty :
      !s.dirty&&s.loaded===s.candidates[0]&&s.base===s.candidates[0]);
  },
    'first authored-only Sync did not produce and display its candidate',120000).catch(async error=>{await stages(syncStart,'authored-only failure');throw error;});
  state=await snap();assert.equal(candidateCalls().length,1);assert.equal((await runIds()).length,1);
  if(!authoredInput){
    await page.waitForFunction(()=>window.__workspaceDesignContext?.designContext?.sourceRunId!==null&&
      window.__workspaceDesignContext?.unavailableReason===null);
    assert.equal(await page.evaluate(()=>window.__workspaceDesignContext.designContext.sourceRunId),state.base,
      'only a completed Sync adopted as the editing base updates chat context');
    await button('Record').waitFor({state:'detached'});await steadyTools(steady,'Record leaving');
  }else{
    assert.equal(await page.evaluate(()=>window.__workspaceDesignContext.designContext),null,'edits during Sync remain unsaved context');
  }
  const proposals=sent.slice(writesBefore).filter(row=>row.path==='/api/proposals/sketch');
  assert.equal(proposals.length,2);for(const proposal of proposals){assert.equal(proposal.body.sourceRunId,null);assert.equal(proposal.body.stateDigest,initial.stateDigest);}
  const model=await exported(state.candidates[0]);assert.equal(model.get('obj-'+first)?.z,1.25);assert.equal(model.get('obj-'+second)?.z,.75);
  if(authoredInput){
    // Let artifact discovery and any home-load effect settle after the job.
    await delay(500);state=await snap();
    assert.equal(state.loaded,undefined);assert.equal(state.sourceLabel,null);assert.equal(state.sourceRunId,null);
    assert.equal(state.base,initial.base);assert.equal(state.view.hasBaseModel,false);assert.equal(state.dirty,true);
    if(authoredUndo){
      assert.equal(state.index,0);assert.equal(state.view.drafts.length,0);
      assert.equal(await page.locator('.stage-empty').count(),1,'first export discovery must preserve the empty local view');
    }else{
      assert.equal(state.view.drafts.length,authoredPrimitives+2);assert.ok(state.view.drafts.some(o=>o.id===later));assert.ok(!state.view.drafts.some(o=>o.id===first));
      assert.ok(!model.has('obj-'+later),'later drawing leaked into first Sync snapshot');await pick(later,true);
    }
    assert.ok(!requests.slice(beforeSyncRequests).some(row=>row.path.endsWith('/bytes')),'home loading must not replace continued local work when the first export appears');
  }else{
    assert.equal(state.view.hasBaseModel,true);assert.equal(state.view.drafts.length,0);await pick(first,true);
    await page.waitForFunction(id=>window.__workspaceDesignContext?.designContext?.elementId===id,first);
    assert.equal(await page.evaluate(()=>window.__workspaceDesignContext.designContext.targetComponentId),proposals[0].body.componentId,
      'retained selection carries its exact component and element into chat context');
  }
  assert.ok(!requests.some(row=>row.method==='GET'&&new URL(row.path,'http://fixture').searchParams.get('run')?.startsWith('studio-projection')),
    'the authored projection placeholder must never be requested as a retained run');
  await stages(syncStart,authoredUndo?'authored-only undo-to-empty first Sync':authoredContinue?'authored-only continued first Sync':'authored-only first Sync');
  console.log(authoredUndo ? 'PASS authored-only undo to empty: first Sync saves its two captured drawings once; current index 0 / zero drafts stay empty and unsynced; no model download or synthetic-run request' :
    authoredContinue ? 'PASS authored-only continued input: first Sync yields one captured candidate; later local drawing/deletion remain visible and unsynced; first export discovery performs no model download' :
    'PASS authored-only: zero artifacts/runs → two local drawings with selection/preselection/delete/undo and zero writes → one explicit candidate, real OCCT export and visible saved model');
} else if(scaleOnly) {
  await openSeed('Scale seed model');
  const originalRuns=await runIds(),writeStart=sent.length,id='seed-block';
  const initialVertices=[0,1.5].flatMap(z=>[[10,0,z],[13,0,z],[10.5,2,z]]);
  const bounds=points=>({min:[0,1,2].map(i=>Math.min(...points.map(p=>p[i]))),max:[0,1,2].map(i=>Math.max(...points.map(p=>p[i])))});
  const center=points=>{const b=bounds(points);return b.min.map((v,i)=>(v+b.max[i])/2);};
  const sameVector=(actual,expected,label,tolerance=1e-4)=>{
    assert.ok(actual,`${label}: missing vector`);
    assert.ok(actual.every((v,i)=>Math.abs(v-expected[i])<tolerance),`${label}: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);
  };
  const sameVertices=async(target,expected,label)=>{
    const actual=await page.evaluate(id=>window.__view.vertices(id),target);
    assert.equal(actual.length,expected.length,`${label}: corner count ${JSON.stringify(actual)}`);
    for(const point of expected)assert.ok(actual.some(p=>p.every((v,i)=>Math.abs(v-point[i])<1e-4)),`${label}: missing ${JSON.stringify(point)} in ${JSON.stringify(actual)}`);
  };
  const frames=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const scaled=(points,pivot,factors)=>points.map(point=>point.map((v,i)=>pivot[i]+(v-pivot[i])*factors[i]));
  async function aim(world) {
    const p=await page.evaluate(point=>window.__view.project(point),world);
    assert.equal(await page.evaluate(p=>document.elementFromPoint(p.x,p.y)?.classList.contains('stage-scale'),p),true,'Scale pointer must meet its real overlay');
    await page.mouse.move(p.x,p.y);await frames();return p;
  }
  async function arm(expected,mode='uniform',keyboard=false) {
    await deselect();await pick(id);const before=await snap();
    if(keyboard)await page.keyboard.press('s');else await button('Scale').click();
    await page.locator('.stage-scale[data-phase="reference"]').waitFor({state:'visible'});
    if(mode!=='uniform')await page.getByLabel('Scale axes',{exact:true}).selectOption(mode);
    const g=(await snap()).gesture.scale,pivot=center(expected);
    sameVector(g.plane.origin,pivot,'Scale world AABB pivot');assert.equal(g.mode,mode);
    const direction=mode==='uniform'?g.plane.xAxis:mode==='x'?[1,0,0]:mode==='y'?[0,1,0]:[0,0,1];
    const point=await aim(pivot.map((v,i)=>v+2.5*direction[i]));await sameVertices(id,expected,'reference pointer preserves source');
    await page.mouse.click(point.x,point.y);await page.locator('.stage-scale[data-phase="factor"]').waitFor({state:'visible'});await frames();
    const captured=await snap();assert.ok(captured.gesture.scale.reference);assert.equal(captured.index,before.index);
    sameVector(captured.gesture.scale.scale,[1,1,1],'reference captures unit scale');
    await sameVertices(id,expected,'reference does not jump');if(captured.view.preview)await sameVertices('preview',expected,'unit-scale preview');
    return {before,pivot:captured.gesture.scale.plane.origin,reference:captured.gesture.scale.reference};
  }
  const target=(g,factor)=>g.pivot.map((v,i)=>v+g.reference[i]*factor);
  console.log('S1 · uniform 1.4× six vertices, reference without jump, same-frame Click and zero pointer React commits');
  const first=await arm(initialVertices,'uniform',true),firstFactors=[1.4,1.4,1.4],firstResult=scaled(initialVertices,first.pivot,firstFactors);
  const firstPoint=await aim(target(first,1.4));await sameVertices('preview',firstResult,'uniform positive preview');
  await sameVertices(id,initialVertices,'uniform preview preserves source');assert.equal(sent.length,writeStart);
  const qa=path.join(root,'qa');await mkdir(qa,{recursive:true});await page.screenshot({path:path.join(qa,'scale-preview.png')});
  const points=await page.evaluate(points=>points.map(p=>window.__view.project(p)),[1.45,1.5,1.55,1.6].map(f=>target(first,f)));
  const reuse=await page.evaluate(async points=>{
    const refs=window.__view.previewIdentity(),commits=window.__stageCommits,overlay=document.querySelector('.stage-scale');
    for(const p of points){for(let i=0;i<3;i++)overlay.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:p.x,clientY:p.y,pointerId:1}));await new Promise(requestAnimationFrame);}
    return {commits:window.__stageCommits-commits,reused:refs.length>0&&refs.every((item,i)=>item===window.__view.previewIdentity()[i])};
  },points);assert.deepEqual(reuse,{commits:0,reused:true});
  await page.evaluate(p=>{const overlay=document.querySelector('.stage-scale');
    for(let i=8;i>=0;i--)overlay.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:p.x+i,clientY:p.y,pointerId:1}));
    overlay.dispatchEvent(new PointerEvent('click',{bubbles:true,clientX:p.x,clientY:p.y,pointerId:1}));},firstPoint);
  let state=await wait(s=>!s.gesture.scale&&s.index===(first.before.index??0)+1,'uniform Scale commit');
  await sameVertices(id,firstResult,'uniform committed vertices');assert.equal(state.view.preview,null);
  const firstIndex=state.index;await page.keyboard.press('Enter');assert.equal((await snap()).index,firstIndex);
  await page.keyboard.press('Control+z');await wait(s=>s.index===(first.before.index??0),'Scale undo');await sameVertices(id,initialVertices,'undone original vertices');
  await page.keyboard.press('Control+y');await wait(s=>s.index===firstIndex,'Scale redo');await sameVertices(id,firstResult,'redone vertices');
  console.log('S2 · negative X pointer mirrors the asymmetric triangle; unequal signed XYZ numeric values override pointer and Enter commits');
  const numeric=await arm(firstResult,'x');await aim(target(numeric,-.75));
  sameVector((await snap()).gesture.scale.scale,[-.75,1,1],'only pointer X changes');
  await sameVertices('preview',scaled(firstResult,numeric.pivot,[-.75,1,1]),'negative X pointer vertices');
  const factors=[-.8,1.3,.6],secondResult=scaled(firstResult,numeric.pivot,factors),fields=page.getByRole('form',{name:'Scale S',exact:true}).locator('input');
  assert.equal(await fields.count(),3);await fields.first().fill('');await fields.first().pressSequentially(String(factors[0]));
  for(let i=1;i<3;i++)await fields.nth(i).fill(String(factors[i]));
  await frames();await sameVertices('preview',secondResult,'unequal signed XYZ numeric preview');
  await aim(target(numeric,2));await sameVertices('preview',secondResult,'numeric XYZ overrides pointer');
  await fields.last().press('Enter');await wait(s=>!s.gesture.scale&&s.index===numeric.before.index+1,'XYZ Scale Enter');await sameVertices(id,secondResult,'numeric committed vertices');
  console.log('S3 · zero pointer factor is null and cannot commit a stale preview; Esc preserves geometry/history');
  const cancelled=await arm(secondResult,'z');await aim(target(cancelled,1.8));
  await sameVertices('preview',scaled(secondResult,cancelled.pivot,[1,1,1.8]),'Z pointer preview');
  const centerPoint=await aim(cancelled.pivot);assert.equal((await snap()).gesture.scale.scale,null,'center has zero factor');
  await page.mouse.click(centerPoint.x,centerPoint.y);assert.equal((await snap()).index,cancelled.before.index);assert.ok((await snap()).gesture.scale);
  const zeroInput=page.getByRole('form',{name:'Scale S',exact:true}).locator('input').first();
  await zeroInput.fill('0');await zeroInput.press('Enter');await frames();
  state=await snap();assert.equal(state.index,cancelled.before.index);assert.equal(state.view.preview,null);assert.ok(state.gesture.scale);
  await page.keyboard.press('Escape');state=await wait(s=>!s.gesture.scale,'Scale Esc');assert.equal(state.index,cancelled.before.index);assert.equal(state.view.preview,null);
  await sameVertices(id,secondResult,'Esc preserves vertices');assert.equal(sent.length,writeStart);assert.deepEqual(await runIds(),originalRuns);
  console.log('S4 · frozen Sync saves the first two scales while later local Y scaling survives');
  let release,ready;const released=new Promise(resolve=>release=resolve),held=new Promise(resolve=>ready=resolve);
  await page.route('**/api/proposals/transform',async route=>{const response=await route.fetch();assert.equal(response.status(),201);ready();await released;await route.fulfill({response});},{times:1});
  await button('Record').click();await within(held,15000,'Scale Sync transform held');const heldWrites=sent.length;
  assert.equal((await snap()).syncBusy,true);assert.equal((await snap()).busy,false);
  const late=await arm(secondResult,'y'),lateResult=scaled(secondResult,late.pivot,[1,1.2,1]);
  const latePoint=await aim(target(late,1.2));await page.mouse.click(latePoint.x,latePoint.y);
  await wait(s=>!s.gesture.scale&&s.index===late.before.index+1,'Scale during Sync');await sameVertices(id,lateResult,'late Y scaling');
  assert.equal(sent.length,heldWrites);assert.equal(candidateCalls().length,0);release();await wait(s=>!s.syncBusy&&s.candidates.length===1,'Scale candidate',120000);
  state=await snap();assert.equal(state.loaded,seedRun);assert.equal(state.base,seedRun);assert.equal(state.dirty,true);assert.equal(candidateCalls().length,1);
  await sameVertices(id,lateResult,'late Scale survives Sync');const saved=(await exported(state.candidates[0])).get('obj-'+id),expectedBox=bounds(secondResult);
  sameVector(saved?.min,expectedBox.min,'real OCCT frozen scaled triangle min',.0011);sameVector(saved?.max,expectedBox.max,'real OCCT frozen scaled triangle max',.0011);
  const transforms=sent.slice(writeStart).filter(row=>row.path==='/api/proposals/transform');assert.equal(transforms.length,2);
  for(const transform of transforms){assert.equal(transform.body.kind,'scale');assert.equal(transform.body.sourceRunId,seedRun);}
  console.log('PASS Scale: asymmetric six vertices, uniform and signed XYZ factors, reference/latest-pointer/numeric Enter, reused preview/zero pointer commits, zero/Esc/Undo/Redo and one frozen real OCCT candidate');
} else if(rotateOnly) {
  await openSeed('Rotate seed model');
  const originalRuns=await runIds(),writeStart=sent.length,id='seed-block';
  // Independently rotate the six known corners; asymmetric geometry exposes sign/axis errors.
  const initialVertices=[0,1.5].flatMap(z=>[[10,0,z],[13,0,z],[10.5,2,z]]);
  const bounds=points=>({min:[0,1,2].map(i=>Math.min(...points.map(p=>p[i]))),max:[0,1,2].map(i=>Math.max(...points.map(p=>p[i])))});
  const center=points=>{const b=bounds(points);return b.min.map((v,i)=>(v+b.max[i])/2);};
  const rotated=(points,pivot,axis,degrees)=>points.map(point=>{
    const v=point.map((value,i)=>value-pivot[i]),angle=degrees*Math.PI/180,c=Math.cos(angle),s=Math.sin(angle);
    const dot=v.reduce((sum,value,i)=>sum+value*axis[i],0);
    const cross=[axis[1]*v[2]-axis[2]*v[1],axis[2]*v[0]-axis[0]*v[2],axis[0]*v[1]-axis[1]*v[0]];
    return v.map((value,i)=>pivot[i]+value*c+cross[i]*s+axis[i]*dot*(1-c));
  });
  const sameVector=(actual,expected,label,tolerance=1e-4)=>{
    assert.ok(actual,`${label}: missing vector`);
    assert.ok(actual.every((v,i)=>Math.abs(v-expected[i])<tolerance),`${label}: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);
  };
  const sameVertices=async(target,expected,label)=>{
    const actual=await page.evaluate(id=>window.__view.vertices(id),target);
    assert.equal(actual.length,expected.length,`${label}: corner count ${JSON.stringify(actual)}`);
    for(const point of expected)assert.ok(actual.some(p=>p.every((v,i)=>Math.abs(v-point[i])<1e-4)),`${label}: missing ${JSON.stringify(point)} in ${JSON.stringify(actual)}`);
  };
  const frames=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const axes={x:[1,0,0],y:[0,1,0],z:[0,0,1]};
  async function aim(world) {
    const point=await page.evaluate(p=>window.__view.project(p),world);
    assert.equal(await page.evaluate(p=>document.elementFromPoint(p.x,p.y)?.classList.contains('stage-rotate'),point),true,'reference/angle point must meet the real Rotate overlay');
    await page.mouse.move(point.x,point.y);await frames();return point;
  }
  async function arm(expected,axis='z',keyboard=false) {
    await deselect();await pick(id);const before=await snap();
    if(keyboard)await page.keyboard.press('q');else await button('Rotate').click();
    await page.locator('.stage-rotate[data-phase="reference"]').waitFor({state:'visible'});
    if(axis!=='z')await page.getByLabel('Rotation axis',{exact:true}).selectOption(axis);
    const gesture=(await snap()).gesture.rotate,pivot=center(expected),normal=axes[axis];
    sameVector(gesture.plane.origin,pivot,'world AABB center');sameVector(gesture.plane.normal,normal,'chosen rotation axis');
    const direction=axis==='x'?[0,2.5,0]:[2.5,0,0],reference=pivot.map((v,i)=>v+direction[i]);
    const point=await aim(reference);await sameVertices(id,expected,'reference pointer must not rotate');
    await page.mouse.click(point.x,point.y);await page.locator('.stage-rotate[data-phase="angle"]').waitFor({state:'visible'});await frames();
    const captured=await snap();assert.ok(captured.gesture.rotate.reference);assert.equal(captured.index,before.index);
    assert.ok(Math.abs(captured.gesture.rotate.angleDegrees)<1e-6,'reference capture must begin at zero angle');
    await sameVertices(id,expected,'reference click must not jump');
    if(captured.view.preview)await sameVertices('preview',expected,'zero rotation preview');
    return {before,pivot,normal,reference};
  }
  console.log('R1 · asymmetric triangle: Z +37° preview, latest same-frame Click and reused geometry without pointer React commits');
  const first=await arm(initialVertices,'z',true),firstAngle=37;
  const firstResult=rotated(initialVertices,first.pivot,first.normal,firstAngle);
  const firstPoint=await aim(rotated([first.reference],first.pivot,first.normal,firstAngle)[0]);
  await sameVertices('preview',firstResult,'positive Z preview');await sameVertices(id,initialVertices,'rotation preview preserves source');
  assert.ok(Math.abs((await snap()).gesture.rotate.angleDegrees-firstAngle)<1e-5);assert.equal(sent.length,writeStart);
  const qa=path.join(root,'qa');await mkdir(qa,{recursive:true});
  await page.screenshot({path:path.join(qa,'rotate-preview.png')});
  const points=await page.evaluate(points=>points.map(p=>window.__view.project(p)),[38,39,40,41].map(angle=>rotated([first.reference],first.pivot,first.normal,angle)[0]));
  const reuse=await page.evaluate(async points=>{
    const refs=window.__view.previewIdentity(),commits=window.__stageCommits,overlay=document.querySelector('.stage-rotate');
    for(const p of points){for(let i=0;i<3;i++)overlay.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:p.x,clientY:p.y,pointerId:1}));await new Promise(requestAnimationFrame);}
    return {commits:window.__stageCommits-commits,reused:refs.length>0&&refs.every((item,i)=>item===window.__view.previewIdentity()[i])};
  },points);assert.deepEqual(reuse,{commits:0,reused:true});
  await page.evaluate(p=>{
    const overlay=document.querySelector('.stage-rotate');
    for(let i=8;i>=0;i--)overlay.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:p.x+i,clientY:p.y,pointerId:1}));
    overlay.dispatchEvent(new PointerEvent('click',{bubbles:true,clientX:p.x,clientY:p.y,pointerId:1}));
  },firstPoint);
  let state=await wait(s=>!s.gesture.rotate&&s.index===(first.before.index??0)+1,'same-frame Rotate commit');
  await sameVertices(id,firstResult,'positive Z committed vertices');assert.equal(state.view.preview,null);
  const firstIndex=state.index;await page.keyboard.press('Enter');assert.equal((await snap()).index,firstIndex,'repeated Enter must not repeat rotation');
  await page.keyboard.press('Control+z');await wait(s=>s.index===(first.before.index??0),'Rotate undo');await sameVertices(id,initialVertices,'undone original triangle');
  await page.keyboard.press('Control+y');await wait(s=>s.index===firstIndex,'Rotate redo');await sameVertices(id,firstResult,'redone triangle');
  console.log('R2 · Y -28° numeric override survives pointer motion and Enter commits the exact signed angle');
  const numeric=await arm(firstResult,'y'),numericAngle=-28,input=page.getByRole('form',{name:'Rotate Q',exact:true}).locator('input');
  const secondResult=rotated(firstResult,numeric.pivot,numeric.normal,numericAngle);
  await input.fill(String(numericAngle));await frames();await sameVertices('preview',secondResult,'negative Y numeric preview');
  await aim(rotated([numeric.reference],numeric.pivot,numeric.normal,45)[0]);await sameVertices('preview',secondResult,'numeric angle overrides pointer');
  await input.press('Enter');state=await wait(s=>!s.gesture.rotate&&s.index===numeric.before.index+1,'numeric Rotate commit');
  await sameVertices(id,secondResult,'negative Y committed vertices');
  console.log('R3 · X -23° pointer preview has the correct handedness; Esc leaves geometry/history unchanged');
  const cancelled=await arm(secondResult,'x'),cancelAngle=-23;
  await aim(rotated([cancelled.reference],cancelled.pivot,cancelled.normal,cancelAngle)[0]);
  await sameVertices('preview',rotated(secondResult,cancelled.pivot,cancelled.normal,cancelAngle),'negative X pointer preview');
  assert.ok(Math.abs((await snap()).gesture.rotate.angleDegrees-cancelAngle)<1e-5);
  const actualPivot=(await snap()).gesture.rotate.plane.origin,centerPoint=await aim(actualPivot);
  assert.equal((await snap()).gesture.rotate.angleDegrees,null,'the pivot has no angle');
  await page.mouse.click(centerPoint.x,centerPoint.y);
  assert.equal((await snap()).index,cancelled.before.index,'clicking the pivot must not commit the previous angle');
  assert.ok((await snap()).gesture.rotate);
  await page.keyboard.press('Escape');state=await wait(s=>!s.gesture.rotate,'Rotate Esc');
  assert.equal(state.index,cancelled.before.index);assert.equal(state.view.preview,null);await sameVertices(id,secondResult,'Esc preserved triangle');
  assert.equal(sent.length,writeStart,'local Rotate/Undo/Redo/Esc wrote to API');assert.deepEqual(await runIds(),originalRuns);
  console.log('R4 · frozen Sync saves both rotations once while a later X rotation remains local');
  let release,ready;const released=new Promise(resolve=>release=resolve),held=new Promise(resolve=>ready=resolve);
  await page.route('**/api/proposals/transform',async route=>{const response=await route.fetch();assert.equal(response.status(),201);ready();await released;await route.fulfill({response});},{times:1});
  await button('Record').click();await within(held,15000,'Rotate Sync transform was not held');
  const heldWrites=sent.length;assert.equal((await snap()).syncBusy,true);assert.equal((await snap()).busy,false);
  const late=await arm(secondResult,'x'),lateAngle=19,lateResult=rotated(secondResult,late.pivot,late.normal,lateAngle);
  const latePoint=await aim(rotated([late.reference],late.pivot,late.normal,lateAngle)[0]);await page.mouse.click(latePoint.x,latePoint.y);
  await wait(s=>!s.gesture.rotate&&s.index===late.before.index+1,'Rotate during Sync');await sameVertices(id,lateResult,'late X local rotation');
  assert.equal(sent.length,heldWrites);assert.equal(candidateCalls().length,0);release();
  await wait(s=>!s.syncBusy&&s.candidates.length===1,'Rotate candidate',120000);
  state=await snap();assert.equal(state.loaded,seedRun);assert.equal(state.base,seedRun);assert.equal(state.dirty,true);assert.equal(candidateCalls().length,1);
  await sameVertices(id,lateResult,'late rotation survives Sync');
  const saved=(await exported(state.candidates[0])).get('obj-'+id),expectedBox=bounds(secondResult);
  sameVector(saved?.min,expectedBox.min,'real OCCT frozen rotated triangle min',.0011);sameVector(saved?.max,expectedBox.max,'real OCCT frozen rotated triangle max',.0011);
  const transforms=sent.slice(writeStart).filter(row=>row.path==='/api/proposals/transform');assert.equal(transforms.length,2);
  for(const transform of transforms){assert.equal(transform.body.kind,'rotate');assert.equal(transform.body.sourceRunId,seedRun);}
  console.log('PASS Rotate: asymmetric six vertices, signed Z/Y/X angles, reference without jump, exact latest-pointer Click/numeric Enter, reused preview/zero pointer commits, Esc/Undo/Redo and one frozen real OCCT candidate');
} else if(moveCopyOnly) {
  await openSeed('Move/Copy seed model');
  const originalRuns=await runIds(),writeStart=sent.length;
  const block=await rectangle(2,1.25);
  const boxOf=(state,id)=>state.view.drafts.find(object=>object.id===id);
  const translated=(box,delta)=>({min:box.min.map((v,i)=>v+delta[i]),max:box.max.map((v,i)=>v+delta[i])});
  const sameBox=(actual,expected,label,tolerance=1e-4)=>{
    assert.ok(actual,`${label}: object missing`);
    for(const bound of ['min','max'])for(let i=0;i<3;i++)assert.ok(Math.abs(actual[bound][i]-expected[bound][i])<tolerance,
      `${label}: ${bound}[${i}] ${actual[bound][i]} != ${expected[bound][i]}`);
  };
  const frames=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  async function arm(id,tool,keyboard=false,top=true) {
    await deselect();const face=await pick(id,top),before=await snap();
    if(keyboard)await page.keyboard.press('m');else await button(tool==='copy'?'Copy':'Move').click();
    await page.locator('.stage-move[data-phase="anchor"]').waitFor({state:'visible'});
    await page.mouse.move(face.x+20,face.y+15);await frames();
    let state=await snap();assert.equal(state.index,before.index);sameBox(boxOf(state,id),boxOf(before,id),'arming cannot move the object');
    await page.mouse.click(face.x,face.y);await page.locator('.stage-move[data-phase="target"]').waitFor({state:'visible'});await frames();
    state=await snap();assert.ok(state.gesture.move?.anchor,'first click must capture a base point');
    assert.equal(state.index,before.index);sameBox(boxOf(state,id),boxOf(before,id),'selecting a base point cannot jump the object');
    if(state.view.preview)sameBox(state.view.preview,boxOf(before,id),'zero translation preview');
    return {before,anchor:state.gesture.move.anchor,box:boxOf(before,id)};
  }
  async function movePointer(anchor,delta) {
    const point=await page.evaluate(point=>window.__view.project(point),anchor.map((v,i)=>v+delta[i]));
    assert.equal(await page.evaluate(point=>document.elementFromPoint(point.x,point.y)?.classList.contains('stage-move'),point),true,
      'pointer must meet the real Move/Copy overlay');
    await page.mouse.move(point.x,point.y);await frames();return point;
  }
  console.log('M1 · select a base point, then preview and commit the latest same-frame Move without a request');
  const first=await arm(block,'move',true),delta=[3,1.5,0];
  await movePointer(first.anchor,delta);let state=await snap();
  sameBox(state.view.preview,translated(first.box,delta),'pointer Move preview');sameBox(boxOf(state,block),first.box,'Move preview retains the source');
  assert.equal(state.index,first.before.index);assert.equal(sent.length,writeStart);
  const qa=path.join(root,'qa');await mkdir(qa,{recursive:true});
  await page.screenshot({path:path.join(qa,'move-copy-preview.png')});
  const samples=await page.evaluate(anchor=>[.1,.2,.3,.4].map(d=>window.__view.project(anchor.map((v,i)=>v+[3+d,1.5,0][i]))),first.anchor);
  const reuse=await page.evaluate(async points=>{
    const identity=window.__view.previewIdentity(),commits=window.__stageCommits;
    const overlay=document.querySelector('.stage-move');
    for(const point of points){
      for(let i=0;i<3;i++)overlay.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:point.x,clientY:point.y,pointerId:1}));
      await new Promise(requestAnimationFrame);
    }
    return {commits:window.__stageCommits-commits,reused:identity.every((item,i)=>item===window.__view.previewIdentity()[i])};
  },samples);
  assert.deepEqual(reuse,{commits:0,reused:true});
  const finalPoint=await page.evaluate(point=>window.__view.project(point),first.anchor.map((v,i)=>v+delta[i]));
  const clickCoordinates=await page.evaluate(point=>{
    const overlay=document.querySelector('.stage-move');
    for(let i=0;i<8;i++)overlay.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:point.x+i,clientY:point.y,pointerId:1}));
    const move=new PointerEvent('pointermove',{bubbles:true,clientX:point.x,clientY:point.y,pointerId:1});overlay.dispatchEvent(move);
    const click=new PointerEvent('click',{bubbles:true,clientX:point.x,clientY:point.y,pointerId:1});
    const translation=[...window.__gesture().move.translation];overlay.dispatchEvent(click);
    return {pointer:[move.clientX,move.clientY],click:[click.clientX,click.clientY],translation};
  },finalPoint);
  console.log('  final pointer/click coordinates',JSON.stringify(clickCoordinates));
  state=await wait(s=>s.index===first.before.index+1&&!s.gesture.move,'same-frame Move did not finish');
  sameBox(boxOf(state,block),translated(first.box,delta),'committed pointer Move');assert.equal(state.view.preview,null);
  await page.keyboard.press('Enter');assert.equal((await snap()).index,state.index,'a second Enter repeated the completed Move');
  await page.keyboard.press('Control+z');await wait(s=>s.index===first.before.index,'Move undo');sameBox(boxOf(await snap(),block),first.box,'undone Move');
  await page.keyboard.press('Control+y');await wait(s=>s.index===first.before.index+1,'Move redo');
  sameBox(boxOf(await snap(),block),translated(first.box,delta),'redone Move');
  console.log('M2 · Copy uses exact XYZ/Enter, preserves its source and keeps its element id through Undo/Redo');
  const copy=await arm(block,'copy'),copyDelta=[4.5,-1.25,2],copyForm=page.getByRole('form',{name:'Copy',exact:true});
  const fields=copyForm.locator('input');assert.equal(await fields.count(),3);
  for(let i=0;i<3;i++)await fields.nth(i).fill(String(copyDelta[i]));
  await frames();sameBox((await snap()).view.preview,translated(copy.box,copyDelta),'typed XYZ Copy preview');
  await movePointer(copy.anchor,[6,3,0]);sameBox((await snap()).view.preview,translated(copy.box,copyDelta),'typed XYZ overrides later pointer motion');
  await fields.last().press('Enter');state=await wait(s=>s.view.drafts.length===2&&!s.gesture.move,'Copy Enter did not finish');
  const copied=state.view.drafts.find(object=>object.id!==block);assert.ok(copied);assert.notEqual(copied.id,block);
  sameBox(boxOf(state,block),copy.box,'Copy source');sameBox(copied,translated(copy.box,copyDelta),'Copy result');
  const copyIndex=state.index;await page.keyboard.press('Enter');assert.equal((await snap()).index,copyIndex);
  await page.keyboard.press('Control+z');await wait(s=>s.view.drafts.length===1,'Copy undo');
  await page.keyboard.press('Control+y');state=await wait(s=>s.view.drafts.length===2,'Copy redo');
  assert.equal(state.view.drafts.find(object=>object.id!==block).id,copied.id,'redo must restore the same local copy identity');
  console.log('M2b · pointer Move captures another local volume corner, holds jitter, commits precisely and undoes');
  const snappedMove=await arm(block,'move',false,false);
  const snapMotion=await page.evaluate(async ({id,anchor})=>{
    const overlay=document.querySelector('.stage-move');
    const move=p=>overlay.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:p.x,clientY:p.y,pointerId:1}));
    let target;
    for(const world of window.__view.vertices(id)) {
      const screen=window.__view.project(world);move(screen);
      const snap=window.__gesture().modelSnap;
      if(snap?.kind==='endpoint'&&snap.objectName==='draft:'+id && Math.hypot(...snap.point.map((v,i)=>v-anchor[i]))>1e-4) {target={screen,snap};break;}
    }
    if(!target)return null;
    const times=[],translations=[];
    for(const dx of [1,-1,.5,-.5]) {
      const started=performance.now();move({x:target.screen.x+dx,y:target.screen.y});
      translations.push([...window.__gesture().move.translation]);
      await new Promise(requestAnimationFrame);times.push(performance.now()-started);
    }
    const started=performance.now();
    overlay.dispatchEvent(new PointerEvent('click',{bubbles:true,clientX:target.screen.x-.5,clientY:target.screen.y,pointerId:1}));
    await new Promise(requestAnimationFrame);
    return {target,translations,pointerToFrameMs:times,clickToFrameMs:performance.now()-started};
  },{id:copied.id,anchor:snappedMove.anchor});
  assert.ok(snapMotion,'the real local copy must offer a visible endpoint');
  const snapDelta=snapMotion.target.snap.point.map((v,i)=>v-snappedMove.anchor[i]);
  for(const actual of snapMotion.translations)assert.deepEqual(actual,snapDelta,'tiny pointer noise cannot change the held endpoint');
  state=await wait(s=>!s.gesture.move&&s.index===snappedMove.before.index+1,'snapped Move did not complete');
  sameBox(boxOf(state,block),translated(snappedMove.box,snapDelta),'exact snapped Move geometry');
  assert.equal(sent.length,writeStart,'snapped gesture must remain local');
  await page.keyboard.press('Control+z');await wait(s=>s.index===snappedMove.before.index,'snapped Move undo');
  sameBox(boxOf(await snap(),block),snappedMove.box,'snapped Move undo geometry');
  console.log('TIMING snapped Move (handler through next RAF, excludes GPU presentation)',JSON.stringify({pointerToFrameMs:snapMotion.pointerToFrameMs,clickToFrameMs:snapMotion.clickToFrameMs}));
  console.log('M3 · Esc discards a pointer preview and leaves local history and both objects unchanged');
  // The raised copy occludes the original top; use its genuinely visible +X face.
  const cancelled=await arm(block,'move',false,false);
  assert.deepEqual((await snap()).gesture.move.plane.normal,[1,0,0]);
  await movePointer(cancelled.anchor,[0,-1,1]);await page.keyboard.press('Escape');
  state=await wait(s=>!s.gesture.move,'Esc did not cancel Move');assert.equal(state.index,cancelled.before.index);assert.equal(state.view.preview,null);
  sameBox(boxOf(state,block),cancelled.box,'Esc source');sameBox(boxOf(state,copied.id),copied,'Esc copy');
  assert.equal(sent.length,writeStart,'local Move/Copy/Undo/Redo/Esc wrote to the API');assert.deepEqual(await runIds(),originalRuns);
  console.log('M4 · Sync freezes both objects; a later pointer Move survives while one real candidate is produced');
  const frozen=await snap();let release,ready;const released=new Promise(resolve=>release=resolve),held=new Promise(resolve=>ready=resolve);
  await page.route('**/api/proposals/sketch',async route=>{const response=await route.fetch();assert.equal(response.status(),201);ready();await released;await route.fulfill({response});},{times:1});
  await button('Record').click();await within(held,15000,'Move/Copy Sync proposal was not held');
  const heldWrites=sent.length;assert.equal((await snap()).syncBusy,true);assert.equal((await snap()).busy,false);
  const late=await arm(block,'move',false,false),lateDelta=[0,-2,1];const latePoint=await movePointer(late.anchor,lateDelta);await page.mouse.click(latePoint.x,latePoint.y);
  state=await wait(s=>s.index===late.before.index+1&&!s.gesture.move,'Move during Sync did not finish');
  sameBox(boxOf(state,block),translated(late.box,lateDelta),'local Move during Sync');assert.equal(sent.length,heldWrites);assert.equal(candidateCalls().length,0);
  release();await wait(s=>!s.syncBusy&&s.candidates.length===1,'Move/Copy Sync did not finish',120000);
  state=await snap();assert.equal(state.loaded,seedRun);assert.equal(state.base,seedRun);assert.equal(state.dirty,true);
  sameBox(boxOf(state,block),translated(late.box,lateDelta),'late Move retained after Sync');assert.equal(candidateCalls().length,1);
  const exportedModel=await exported(state.candidates[0]);
  sameBox(exportedModel.get('obj-'+block),boxOf(frozen,block),'real OCCT saved source',.0011);
  sameBox(exportedModel.get('obj-'+copied.id),boxOf(frozen,copied.id),'real OCCT saved copy',.0011);
  const transforms=sent.slice(writeStart).filter(row=>row.path==='/api/proposals/transform');
  assert.deepEqual(transforms.map(row=>row.body.kind),['move','copy']);
  assert.equal(transforms[1].body.copyElementId,copied.id);assert.deepEqual(transforms[1].body.translation,[4.5,2,-1.25]);
  for(const transform of transforms)assert.equal(transform.body.sourceRunId,seedRun);
  console.log('PASS pointer Move/Copy: base-point capture without jump, reused preview/zero pointer commits, latest pointer Click, exact XYZ/Enter, stable copy identity, Esc/Undo/Redo, zero local writes and one frozen OCCT candidate');
} else {
// R14: a boot that runs long says what it does, not a brand, and how long it has waited.
let releaseBoot;const bootHeld=new Promise(resolve=>releaseBoot=resolve);
await page.route('**/api/project',async route=>{await bootHeld;await route.continue();},{times:1});
const opening=openSeed('initial model');opening.catch(()=>{});// awaited below; a failure still surfaces there
const boot=page.locator('.boot[data-mode="boot"]');
await boot.locator('.boot__elapsed').waitFor();
assert.equal(await boot.locator('.boot__title').innerText(),'Opening…','the boot overlay is headed by what it does');
assert.match(await boot.locator('.boot__elapsed').innerText(),/^Still waiting · \d+ s$/);
assert.equal(await boot.locator('[role="status"] .boot__elapsed').count(),0,'the count is not a live region');
// #302 (NA-1): the waiting line is bilingual text. The Hub keeps a project it hides mounted
// under visibility: hidden, and the line must hide with it instead of showing through.
const waiting=boot.locator('[role="status"] .bilingual-text__layer--active').first(),visibility=node=>getComputedStyle(node).visibility;
assert.equal(await waiting.evaluate(visibility),'visible');
await page.locator('.project-workspace').evaluate(node=>{node.parentElement.style.visibility='hidden';});
assert.equal(await waiting.evaluate(visibility),'hidden','a hidden workspace hides its loading line');
await page.locator('.project-workspace').evaluate(node=>{node.parentElement.style.visibility='';});
releaseBoot();await opening;
const originalRuns=await runIds(), beforeWrites=sent.length;
// SS-5: Record is offered, by name, only while there is something to record.
assert.equal(await button('Record').count(),0,'no dead Record icon before any edit');
const steady=await toolAt(),narrowSteady=await narrowToolAt();
// NA-4: a tool's shortcut is announced, not only shown in its hover tooltip.
for(const [name,keys] of [['Select','Space'],['Rectangle','R'],['Push/Pull','P'],['Undo model','Control+Z'],['Redo model','Control+Shift+Z']])
  assert.equal(await button(name).getAttribute('aria-keyshortcuts'),keys,`${name} announces ${keys}`);
console.log('1 · completed rectangles/lines stay local and independently pickable');
const block=await rectangle(2,1), curve=await line();
await button('Record').waitFor();assert.equal(await button('Record').isEnabled(),true);
assert.equal(await button('Record').locator('.model-tool-button__label').innerText(),'Record','Record carries its name beside the icon');
await steadyTools(steady,'Record appearing');await narrowRecord(narrowSteady);
let state=await snap();assert.equal(state.view.drafts.find(o=>o.id===curve).spec.closed,false);
assert.deepEqual(state.view.drafts.find(o=>o.id===curve).visible,[true,false,false]);
await pick(block,true);assert.equal((await snap()).pickedStatus,'local');
console.log('2 · two successive P gestures re-pick the moved face, with latest-pointer and numeric override');
await button('Push/Pull').click();await wait(s=>s.gesture.pushPull,'P did not start');
const pushPullForm=page.getByRole('form',{name:'Push/Pull P',exact:true});
assert.equal(await pushPullForm.getByRole('button',{name:'Apply',exact:true}).getAttribute('aria-keyshortcuts'),'Enter');
assert.equal(await pushPullForm.getByRole('button',{name:'Close tool',exact:true}).getAttribute('aria-keyshortcuts'),'Escape','Esc is announced by its key name');
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
// Reframe through the current view menu; the primary Fit button was retired.
await button('View tools').click();
await page.locator('#view-tools').getByRole('button',{name:'Isometric',exact:true}).click();
await button('View tools').click();
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
await page.route('**/api/proposals/sketch',hold,{times:1});const syncStart=performance.now(),heldSyncWall=Date.now();await button('Record').click();await within(held,15000,'sketch response was not held');
state=await snap();assert.equal(state.syncBusy,true);assert.equal(state.busy,false);assert.equal(await button('Record').isDisabled(),true);
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
const quietSyncStart=performance.now(),quietSyncWall=Date.now();await button('Record').click();await wait(s=>!s.syncBusy&&!s.dirty&&s.candidates.length===2&&s.loaded===s.candidates[1]&&s.base===s.candidates[1],
  'second Sync did not show the completed model',120000);
console.log('TIMING quiet Sync click → visible candidate',Math.round(performance.now()-quietSyncStart),'ms (no artificial hold)');
await stages(quietSyncWall,'quiet auto display');
await button('Record').waitFor({state:'detached'});await steadyTools(steady,'Record leaving');
state=await snap();assert.equal(candidateCalls().length,2);assert.ok((await exported(state.candidates[1])).has('obj-'+later));
assert.equal(state.view.drafts.length,0,'the completed batch must not overlap its saved model');
const continuedDraft=await call('GET','/api/working-draft');
assert.equal(continuedDraft.current?.runId,state.candidates[1],'a second Sync after late edits must retain the adopted successor, not the previous completed candidate');
assert.equal(continuedDraft.localDraft,null,'quiet adoption clears the old source recovery once the saved base holds the batch');
console.log('6 · a delayed 422 after Undo releases Sync; corrected geometry replaces the failed snapshot');
const failedObject=await rectangle(.9,.9), failedBase=(await snap()).base;
let releaseFailure, failureReady;
const failureReleased=new Promise(resolve=>releaseFailure=resolve), failureHeld=new Promise(resolve=>failureReady=resolve);
await page.route('**/api/proposals/sketch',async route=>{
  failureReady();await failureReleased;
  await route.fulfill({status:422,contentType:'application/json',body:JSON.stringify({code:'INVALID_SKETCH',detail:'Fixture rejected the captured drawing.'})});
},{times:1});
await button('Record').click();await within(failureHeld,15000,'failure was not held');await page.keyboard.press('Control+z');
await wait(s=>!s.view.drafts.some(o=>o.id===failedObject),'Undo while rejected request is held');releaseFailure();
await wait(s=>!s.syncBusy&&Boolean(s.error),'422 did not release Sync');
await page.locator('.model-tools__sync--status-only [role="alert"]').waitFor();await steadyTools(steady,'a Record error with nothing left to record');
assert.equal(candidateCalls().length,2,'a refused snapshot cannot start a candidate');
const corrected=await rectangle(1.2,.7);await button('Record').click();
await wait(s=>!s.syncBusy&&s.candidates.length===3&&s.loaded===s.candidates[2]&&s.base===s.candidates[2],'corrected Sync did not finish',120000);
state=await snap();const correctedExport=await exported(state.candidates[2]);
assert.ok(correctedExport.has('obj-'+corrected));assert.ok(!correctedExport.has('obj-'+failedObject));
assert.notEqual(state.base,failedBase);assert.equal(candidateCalls().length,3);
console.log('7 · annotation and document keys cannot act on the local model');
const inkObject=await rectangle(.8,.6);await pick(inkObject);const inkModelIndex=(await snap()).index;
const annotate=page.locator('button[aria-controls="annotation-tools"]');await annotate.click();
assert.deepEqual(await page.getByRole('group',{name:'Annotation colour',exact:true}).getByRole('button').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('aria-label'))),
  ['Red','Blue','Yellow','White'],'annotation swatches announce colour names, not hex codes');
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
await button('Record').click();await within(downloadHeld,120000,'background download did not start');
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
await button('Record').click();await wait(s=>!s.syncBusy&&Boolean(s.error),'job-read failure did not release Sync',60000);
assert.ok(failedReads>=3);assert.equal(candidateCalls().length,5);const retryCalls=sent.length;
await page.unroute('**/api/jobs/*');await button('Record').click();await wait(s=>!s.syncBusy&&!s.error,'job reconnect did not settle',60000);
assert.equal(candidateCalls().length,5);assert.equal(sent.length,retryCalls,'job reconnect wrote another proposal/candidate');
console.log('TIMING local actions (automation observation, 100 ms polling maximum)',JSON.stringify(localTimings));
const qa=path.join(root,'qa');await mkdir(qa,{recursive:true});await deselect();await button('Line').hover();await delay(400);
await page.screenshot({path:path.join(qa,'manual-sync-toolbar.png')});
console.log('PASS manual Sync: local draw/P/delete/undo/redo zero writes; delayed Sync permits continued editing; one candidate per snapshot; exact OCCT export');
}
} finally {
  closing=true;await browser?.close().catch(()=>{});await vite?.close().catch(()=>{});
  if(http.listening)await new Promise(resolve=>http.close(resolve));api?.kill();await delay(300);await rm(root,{recursive:true,force:true}).catch(()=>{});
}
assert.deepEqual(errors,[]);

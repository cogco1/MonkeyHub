/** Real project Runtime/P036/SDK/UI; the injected image adapter is offline.
 * Camera capture uses the registered 3DM fixture in a real WebGL scene with a
 * fixture-supplied view reader. It does not test App's dirty-state detection.
 * Never a live provider or architectural-project acceptance test.
 */
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const repoRoot = path.resolve(webRoot, "../../../..");
const apiRoot = path.join(repoRoot, "apps/archflow-studio/api");
const temporary = await mkdtemp(path.join(tmpdir(), "monkeyhub-render-browser-"));
const python = process.env.PYTHON ?? "python";
const pythonEnv = { ...process.env, PYTHONUTF8: "1", PYTHONPATH: [repoRoot, apiRoot].join(path.delimiter) };
const errors = [], requests = [], processes = [], passed = [];
let browser, page, server, current, hubOrigin;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(read, accepts, label) {
  const deadline = Date.now() + 20000; let value;
  do { value = await read(); if (accepts(value)) return value; await delay(80); } while (Date.now() < deadline);
  assert.fail(`${label}: ${JSON.stringify(value)}\n${processes.map((p) => p.log).join("\n")}`);
}
async function freePort() {
  const probe = createHttpServer();
  await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
  const port = probe.address().port; await new Promise((resolve) => probe.close(resolve)); return port;
}
const pythonSource = `
import sys, threading, time, base64
from uuid import uuid4
from pathlib import Path
from io import BytesIO
from PIL import Image, ImageDraw
from starlette.responses import Response
import uvicorn
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_RENDER_JOB
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.artifacts import save_document
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.application.render_contract import RenderCapability, RenderOutput, RenderProviderError
root, project_id, port = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
capture_fixture = project_id == 'capture'
reference_run = None
if capture_fixture:
    from fastapi.testclient import TestClient
    from tests.support import PROJECT_ID, REFERENCE_RUN_ID, make_project, runner_state_digest
    from tests.test_working_copies import register_model
    root = root / 'capture'
    repo, _ = make_project(root)
    project_id, reference_run = PROJECT_ID, REFERENCE_RUN_ID
else:
    repo = FilesystemProjectRepository.initialize(root / project_id, project_id=project_id, initial_state={'project_id': project_id, 'version': 0})
project = root / project_id
def image(color):
    canvas = Image.new('RGB', (720, 480), '#e2e4e0'); draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 340, 720, 480), fill='#c3c8bd')
    draw.polygon([(115,340),(115,165),(365,105),(365,290)], fill=color)
    draw.polygon([(365,105),(585,190),(585,365),(365,290)], fill='#8c9ba4')
    draw.polygon([(150,210),(245,186),(245,287),(150,311)], fill='#475562')
    draw.text((28,28), 'SYNTHETIC TEST IMAGE / ' + project_id, fill='#28343a')
    out = BytesIO(); canvas.save(out, format='PNG'); return out.getvalue()
class Adapter:
    def __init__(self): self.calls=[]; self.release=threading.Event()
    def capability(self): return RenderCapability('test-image', 'Offline test adapter', 'test-model', True, sizes=('2K',), aspect_ratios=('source','4:3'), max_references=3)
    def generate(self, request):
        self.calls.append({'requestId':request.request_id,'source':request.source.ref.asset_sha256,'references':[x.ref.asset_sha256 for x in request.references]})
        if 'SLOW' in request.direction: self.release.wait(20)
        if 'FAIL' in request.direction: raise RenderProviderError('failed','unsupported_input')
        if 'UNKNOWN' in request.direction: raise RenderProviderError('unknown','transport_unknown')
        return RenderOutput(image('#c4a16d'), 'image/png')
adapter=Adapter()
app=create_app(StudioSettings(project_dir=project,reference_run=reference_run,cad_export='off',monitor_dir=root/'monitor'),render_adapter=adapter)
if capture_fixture:
    client = TestClient(app)
    model_bytes = (Path.cwd()/'tests/fixtures/model-source-a.3dm').read_bytes()
    model = register_model(client, reference_run, runner_state_digest(repo, reference_run), model_bytes)
    stage = client.post('/api/design-stages/initialize',json={'projectId':project_id,'modelSource':model['modelSource']})
    if stage.status_code != 201: raise RuntimeError(stage.text)
    model_fixture = {'projectId':project_id,'modelSource':model['modelSource'],'sourceStageRef':stage.json()['stageRef']}
    @app.get('/fixture/model')
    def source_model(): return model_fixture
    @app.get('/fixture/model-bytes')
    def source_model_bytes(): return Response(model_bytes,media_type='application/octet-stream')
@app.get('/fixture/image')
def source_image(color: str='white'): return Response(image(color), media_type='image/png')
@app.get('/fixture/plan-model')
def plan_model(revised: bool=False):
    # An imported model to draw, and the same file with one object removed: another source.
    data = (Path.cwd()/'tests/fixtures/model-source-a.3dm').read_bytes()
    if revised:
        from archflow.adapters.model_formats import ThreeDM
        adapter = ThreeDM(); model = adapter.read(data); model.meshes = model.meshes[1:]; data = adapter.write(model)
    return Response(data, media_type='application/octet-stream')
@app.get('/fixture/metrics')
def metrics(): return {'calls':adapter.calls,'head':repr(repo.read_head())}
@app.post('/fixture/release')
def release(): adapter.release.set(); return {'ok':True}
@app.post('/fixture/legacy')
def legacy():
    job_id='render-'+uuid4().hex; run=repo.create_run(job_id)
    document=save_document(bound_project(app.state),job_id,'retained-native.png','image/png',base64.b64encode(image('#948ed1')).decode())
    repo.put_json(run=run,destination=PersistenceDestination(PersistenceArea.RUN_RECORD,run_id=job_id),record_kind=STUDIO_RENDER_JOB,payload={
        'schema':'StudioRenderJob@1','projectId':project_id,'jobId':job_id,'instance':'retired-runtime','sequence':1,'status':'succeeded',
        'createdAt':'2026-09-21T00:00:00Z','renderer':'monkeyhub-three-webgl2-v1','documentSha256':document.asset_sha256})
    return {'jobId':job_id,'fileName':document.file_name}
uvicorn.run(app,host='127.0.0.1',port=port,log_level='warning')
`;
// Real Hub and its managed Runtime, with private settings and no CLI discovery.
// Cold navigation checks persisted authored inputs, not only HEAD.
const hubSource = `
import sys, os, site, hashlib
from pathlib import Path
import uvicorn
from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.settings import save_application_settings, save_user_settings
from archflow_studio_api.transport.settings import ApplicationSettingsDto, UserSettingsDto
from monkeyhub_api.main import create_app, HubSettings, HubServer
from fastapi.staticfiles import StaticFiles
root, source = Path(sys.argv[1]), Path(sys.argv[2])
port, studio_port, monitor_port = map(int, sys.argv[3:6])
os.environ['PYTHONPATH']=os.pathsep.join([os.environ.get('PYTHONPATH',''),site.getusersitepackages()])
for key, name in [('APPDATA','roaming'),('LOCALAPPDATA','local'),('CODEX_HOME','codex'),('CLAUDE_CONFIG_DIR','claude')]: os.environ[key]=str(root/name)
for key in list(os.environ):
    if key.startswith('ARCHFLOW_STUDIO_') or key=='MONKEYHUB_RENDER_API_KEY': os.environ.pop(key)
projects=root/'hub-projects'; project=projects/'cold-render'
for name in ['cold-render','cold-arch']:
    FilesystemProjectRepository.initialize(projects/name,project_id=name,initial_state={'project_id':name,'version':0})
save_application_settings(root/'hub-runtime',ApplicationSettingsDto(projectDir=str(project),workspaceDir=str(projects),cadExport='off',studioPort=studio_port,monitorPort=monitor_port))
save_user_settings(UserSettingsDto(language='en',renderProvider='off'))
app=create_app(HubSettings(runtime_root=root/'hub-runtime',port=port),source_root=source)
app.state.chats.providers=lambda *args,**kwargs: []
def snapshot(name):
    return {str(p.relative_to(projects/name)).replace(chr(92),'/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in (projects/name).rglob('*') if p.is_file() and not p.name.endswith('.lock')}
baseline={name:snapshot(name) for name in ['cold-render','cold-arch']}
@app.get('/fixture/metrics')
def metrics(): return {'baseline':baseline,'current':{name:snapshot(name) for name in baseline}}
@app.post('/fixture/shutdown')
def shutdown(): server.should_exit=True; return {'ok':True}
app.mount('/',StaticFiles(directory=source/'apps/monkeyhub/web/dist',html=True),name='hub-web')
server=HubServer(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning'))
server.run()
`;
const origins = {};
async function api(project, route, method = "GET", body) {
  const response = await fetch(origins[project] + route, { method, headers: body ? { "content-type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  assert.ok(response.ok, `${method} ${route}: ${response.status} ${response.ok ? "" : await response.text()}`);
  return response.json();
}
async function step(name, action) {
  if (process.argv.includes("--capture-only") && !name.startsWith("perspective and orthographic")) return;
  current = name; await action(); passed.push(name); console.log(`PASS ${name}`);
}
const fixture = `
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ProjectWorkspace} from '/src/app/ProjectWorkspace';
import {UserPreferencesProvider,usePreferences} from '/test/TestProviders';
import '/src/styles.css';
import '/@fs/${path.resolve(repoRoot, "apps/shared-web/src/base.css").replaceAll("\\", "/")}';
function Project({id,active}){
 const [workspace,setWorkspace]=useState('render');
 const preferences=usePreferences();
 window.renderFixture ??= {}; window.renderFixture[id]={setWorkspace,setTheme:preferences.setTheme,setLanguage:preferences.setLanguage};
 return <div hidden={!active} inert={!active} style={{height:'100%',display:active?'block':'none'}}>
   <ProjectWorkspace expectedProjectId={id} workspace={workspace} active={active} onWorkspaceChange={setWorkspace}/>
 </div>;
}
function App(){const [selected,setSelected]=useState('project-a');
 return <><nav><button onClick={()=>setSelected('project-a')}>Project A</button><button onClick={()=>setSelected('project-b')}>Project B</button>
  {['render','board','drawing','arch'].map(value=><button key={value} onClick={()=>window.renderFixture[selected].setWorkspace(value)}>{value}</button>)}</nav>
  <main style={{height:'calc(100% - 44px)'}}>{['project-a','project-b'].map(id=><UserPreferencesProvider key={id} baseUrl={location.origin+'/'+id}><Project id={id} active={selected===id}/></UserPreferencesProvider>)}</main></>;
}
createRoot(document.getElementById('root')).render(<App/>);
`;
const captureFixture = `
import React from 'react';
import {createRoot} from 'react-dom/client';
import {Scene,Color,AmbientLight,DirectionalLight,Box3,Vector3,PerspectiveCamera,OrthographicCamera} from 'three';
import {Rhino3dmLoader} from 'three/examples/jsm/loaders/3DMLoader.js';
import RenderWorkspace from '/src/workspaces/render/RenderWorkspace';
import {captureRenderView} from '/src/workspaces/monkeyarch/viewer/renderView';
import {UserPreferencesProvider} from '/test/TestProviders';
import '/src/styles.css';
import '/@fs/${path.resolve(repoRoot, "apps/shared-web/src/base.css").replaceAll("\\", "/")}';
const source=await fetch('/capture/fixture/model').then(response=>response.json());
const loader=new Rhino3dmLoader(); loader.setLibraryPath('/rhino3dm/');
const object=await loader.loadAsync('/capture/fixture/model-bytes'); loader.dispose();
const scene=new Scene(); scene.background=new Color('#d9e0e4'); scene.add(object,new AmbientLight(0xffffff,2));
const light=new DirectionalLight(0xffffff,3); light.position.set(3,8,5); scene.add(light);
const box=new Box3().setFromObject(object),center=box.getCenter(new Vector3()),size=box.getSize(new Vector3()).length();
if(!Number.isFinite(size)||size<=0)throw new Error('The registered model fixture has no renderable geometry.');
let camera,visible=false,sourceIssue=null;
function setProjection(kind){
 const aspect=kind==='perspective'?1.6:.75;
 camera=kind==='perspective'?new PerspectiveCamera(43,aspect,size/1000,size*100):new OrthographicCamera(-size*aspect/2,size*aspect/2,size/2,-size/2,size/1000,size*100);
 camera.position.copy(center).add(new Vector3(size*.8,size*.6,size)); camera.lookAt(center); camera.updateProjectionMatrix(); camera.updateMatrixWorld(true); visible=true;
}
function readView(){return visible?{...captureRenderView(scene,camera,center,43,1),modelSource:source.modelSource,sourceStageRef:source.sourceStageRef,sourceIssue}:null;}
window.captureFixture={source,setProjection,setIssue:value=>{sourceIssue=value;},setVisible:value=>{visible=value;},
 moveCamera:()=>{camera.position.x+=size*.35;camera.lookAt(center);camera.updateMatrixWorld(true);},
 snapshot:()=>{const view=readView();return view?{modelSource:view.modelSource,sourceStageRef:view.sourceStageRef,aspect:view.aspect,worldMatrix:view.camera.matrixWorld.toArray(),projectionMatrix:view.camera.projectionMatrix.toArray()}:null;}};
createRoot(document.getElementById('root')).render(<UserPreferencesProvider baseUrl={location.origin+'/capture'}><RenderWorkspace projectId={source.projectId} active={true} refreshKey={0} onBoard={()=>{}} readModelView={readView} onModeling={()=>{}}/></UserPreferencesProvider>);
`;
try {
  for (const id of ["project-a", "project-b", "capture"]) {
    const port = await freePort();
    const child = spawn(python, ["-c", pythonSource, temporary, id, String(port)], { cwd: apiRoot, env: pythonEnv, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    const process = { child, log: "" }; processes.push(process);
    child.stdout.on("data", (chunk) => { process.log = (process.log + chunk).slice(-5000); });
    child.stderr.on("data", (chunk) => { process.log = (process.log + chunk).slice(-5000); });
    origins[id] = `http://127.0.0.1:${port}`;
    await until(() => fetch(origins[id] + "/api/health").then((r) => r.ok).catch(() => false), Boolean, `${id} Runtime ready`);
  }
  server = await createServer({ root: webRoot, configFile: false, publicDir: "../.generated/public", cacheDir: path.join(temporary, "vite"), logLevel: "error",
    resolve: { dedupe: ["react", "react-dom"] }, optimizeDeps: { include: ["react", "react-dom/client", "react/jsx-runtime", "react/jsx-dev-runtime"] },
    server: { host: "127.0.0.1", port: 0, strictPort: true, proxy: Object.fromEntries(Object.entries(origins).map(([id, target]) => [`/${id}`, { target, rewrite: (url) => url.slice(id.length + 1) }])) },
    plugins: [{ name: "render-fixture", resolveId(id) { if (["/render-fixture.tsx", "/camera-fixture.tsx"].includes(id)) return path.join(webRoot, id.slice(1)).replaceAll("\\", "/"); },
      load(id) { if (id === path.join(webRoot, "render-fixture.tsx").replaceAll("\\", "/")) return fixture;
        if (id === path.join(webRoot, "camera-fixture.tsx").replaceAll("\\", "/")) return captureFixture; },
      configureServer(vite) { vite.middlewares.use((request, response, next) => {
        if (request.url !== "/" && request.url !== "/camera") return next();
        response.setHeader("content-type", "text/html"); response.end('<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><style>html,body,#root{height:100%;margin:0}nav{height:44px;display:flex;gap:8px}*{box-sizing:border-box}</style></head><body><div id="root" class="project-workspace"></div><script type="module" src="/'+(request.url === '/camera' ? 'camera' : 'render')+'-fixture.tsx"></script></body></html>');
      }); },
    }],
  });
  await server.listen(); const origin = server.resolvedUrls.local[0];
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  page.setDefaultTimeout(15000);
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (message) => { if (message.type() === "error") console.error(message.text()); });
  page.on("request", (request) => { if (request.url().includes("/api/")) requests.push({ url: request.url(), method: request.method() }); });
  await page.goto(origin);
  const workspace = () => page.locator('[data-project-surface="render"]:visible');
  const selectAI = () => workspace().getByRole("button", { name: "AI", exact: true }).click();
  await selectAI();
  const direction = () => workspace().getByRole("textbox", { name: "Visual direction" });
  const generate = () => workspace().getByRole("button", { name: "Generate", exact: true });
  const history = () => workspace().locator(".render-list button");
  const refresh = () => workspace().getByRole("button", { name: "Refresh status", exact: true });
  const jobs = () => api("project-a", "/api/render/jobs");
  const headBefore = (await api("project-a", "/fixture/metrics")).head;
  let first, second, source, refs;
  await step("Render starts without model initialization; capability defaults and ordered original uploads", async () => {
    await direction().waitFor();
    assert.equal(requests.some((r) => /\/api\/(state|artifacts|project\/modeling)/.test(r.url)), false);
    await until(() => workspace().getByRole("combobox", { name: "Size", exact: true }).inputValue(), (value) => value === "2K", "provider default size loaded");
    const image = async (color) => Buffer.from(await (await fetch(origins["project-a"] + `/fixture/image?color=${color}`)).arrayBuffer());
    await workspace().getByLabel("Upload source", { exact: true }).setInputFiles({ name: "source.png", mimeType: "image/png", buffer: await image("white") });
    await until(() => workspace().getByRole("combobox", { name: "Source image", exact: true }).inputValue(), Boolean, "source saved");
    await workspace().getByLabel("Upload references", { exact: true }).setInputFiles([
      { name: "warm.png", mimeType: "image/png", buffer: await image("pink") }, { name: "cool.png", mimeType: "image/png", buffer: await image("lightblue") },
    ]);
    await until(() => workspace().locator(".render-references li").count(), (n) => n === 2, "two refs saved");
    await workspace().getByRole("button", { name: "Move reference up 2", exact: true }).click();
    await direction().fill("Soft morning light");
    await generate().dblclick();
    first = (await until(jobs, (r) => r.jobs[0]?.status === "succeeded", "first output")).jobs[0];
    await refresh().click(); await until(() => workspace().locator('.render-image img').count(), (n) => n > 0, "visible output");
    assert.equal((await api("project-a", "/fixture/metrics")).calls.length, 1);
    source = first.request.source; refs = first.request.references;
    const documents = (await api("project-a", "/api/documents")).documents;
    assert.equal(documents.find((d) => d.assetSha256 === refs[0].assetSha256).fileName, "cool.png");
    assert.equal(first.document.modelSource, null);
    assert.equal(first.request.output.size, "2K");
  });
  await step("source comparison, zoom and authenticated-byte download keep exact generation identity", async () => {
    await workspace().getByRole("button", { name: "Compare", exact: true }).click();
    await until(() => workspace().locator('.render-image img').count(), (n) => n === 2, "source and result");
    await workspace().getByRole("button", { name: "Zoom in", exact: true }).click();
    assert.equal(await workspace().locator('.render-image img').first().evaluate((el) => el.style.width), "125%");
    await workspace().getByRole("button", { name: "Fit", exact: true }).click();
    assert.match(await workspace().locator('.render-metadata').innerText(), /source.png/);
    assert.match(await workspace().locator('.render-metadata').innerText(), /No model association/);
    const download = page.waitForEvent("download"); await workspace().getByRole("link", { name: "Download", exact: true }).click();
    assert.equal((await download).suggestedFilename(), first.document.fileName);
    await page.screenshot({ path: path.join(temporary, "render-wide.png"), fullPage: true });
  });
  await step("zoomed result can be dragged and Fit resets the image viewport", async () => {
    await workspace().getByRole("button", { name: "Result", exact: true }).click();
    for (let i = 0; i < 5; i++) await workspace().getByRole("button", { name: "Zoom in", exact: true }).click();
    const pane = workspace().locator('.render-image');
    const box = await pane.boundingBox();
    await page.mouse.move(box.x + box.width * .8, box.y + box.height * .7);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * .3, box.y + box.height * .3, { steps: 6 });
    await page.mouse.up();
    assert.ok(await pane.evaluate((el) => el.scrollLeft > 0 && el.scrollTop > 0), 'drag moves both image axes');
    await workspace().getByRole("button", { name: "Fit", exact: true }).click();
    await until(() => pane.evaluate((el) => [el.scrollLeft, el.scrollTop]), (p) => p[0] === 0 && p[1] === 0, 'fit resets pan');
    assert.equal(await workspace().locator('.render-model-canvas').isVisible(), false, 'no blank canvas when no model is loaded');
  });
  await step("project and workspace switches preserve inputs, stop hidden polling and do not cancel or resend", async () => {
    await direction().fill("SLOW afternoon"); await generate().click();
    await until(jobs, (r) => r.jobs.some((j) => j.status === "running"), "running offline job");
    await page.getByRole("button", { name: "Project B", exact: true }).click(); await selectAI();
    await direction().waitFor(); assert.equal(await direction().inputValue(), "");
    await delay(500); const count = requests.filter((r) => r.url.includes("project-a/api/render/")).length;
    await delay(2800); assert.equal(requests.filter((r) => r.url.includes("project-a/api/render/")).length, count);
    await api("project-a", "/fixture/release", "POST");
    await page.getByRole("button", { name: "Project A", exact: true }).click();
    assert.equal(await direction().inputValue(), "SLOW afternoon");
    second = (await until(jobs, (r) => r.jobs.length === 2 && r.jobs.every((j) => j.status === "succeeded"), "background completion")).jobs[0];
    await refresh().click(); await until(() => history().count(), (n) => n === 2, "two histories");
    assert.notEqual(first.jobId, second.jobId); assert.equal(first.document.assetSha256, second.document.assetSha256);
    assert.equal((await api("project-a", "/fixture/metrics")).calls.length, 2);
    await page.getByRole("button", { name: "drawing", exact: true }).click();
    await page.getByRole("button", { name: "render", exact: true }).click();
    assert.equal(await direction().inputValue(), "SLOW afternoon");
  });
  await step("selected-image loading, HTTP failure and corrupt bytes recover by read without generating", async () => {
    const firstButton = () => history().filter({ hasText: "Soft morning light" });
    const secondButton = () => history().filter({ hasText: "SLOW afternoon" });
    const firstBytes = (url) => url.pathname.includes('/api/documents/') && url.pathname.endsWith('/bytes') && url.searchParams.get('runId') === first.document.runId;
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    await page.route(firstBytes, async (route) => { await gate; await route.continue(); });
    await firstButton().click();
    await workspace().getByText('Loading image…', { exact: true }).waitFor();
    assert.equal(await workspace().locator('.render-image img').count(), 0, 'previous result is not shown under the new selection');
    release();
    await until(() => workspace().locator('.render-image img').count(), (n) => n === 1, 'selected bytes loaded');
    await page.unroute(firstBytes);
    for (const response of [
      { status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Temporary image read failure' }) },
      { status: 200, contentType: 'image/png', body: 'not a valid image' },
    ]) {
      await secondButton().click();
      await until(() => workspace().locator('.render-image img').count(), (n) => n === 1, 'second result ready');
      await page.route(firstBytes, (route) => route.fulfill(response));
      await firstButton().click();
      await workspace().getByRole('button', { name: 'Reload image', exact: true }).waitFor();
      assert.equal(await workspace().locator('.render-image img').count(), 0, 'broken bytes are not displayed as a usable image');
      await page.unroute(firstBytes);
      await workspace().getByRole('button', { name: 'Reload image', exact: true }).click();
      await until(() => workspace().locator('.render-image img').count(), (n) => n === 1, 'read retry recovers image');
    }
    assert.equal((await api('project-a', '/fixture/metrics')).calls.length, 2, 'image recovery never calls the generation adapter');
  });
  await step("failure preserves old image; unknown and refresh never replay the provider", async () => {
    await history().filter({ hasText: "SLOW afternoon" }).click();
    await until(() => workspace().locator('.render-image img').last().getAttribute('alt'), (alt) => alt === second.document.fileName, 'previous successful result loaded');
    const previous = await workspace().locator('.render-image img').last().getAttribute("alt");
    await direction().fill("FAIL input"); await generate().click();
    await until(jobs, (r) => r.jobs[0].status === "failed", "failed input"); await refresh().click();
    await until(() => history().first().innerText(), (text) => text.includes("Failed"), "failed status readback");
    assert.equal(await workspace().locator('.render-image img').last().getAttribute("alt"), previous);
    await direction().fill("UNKNOWN response"); await generate().click();
    await until(jobs, (r) => r.jobs[0].status === "unknown", "unknown call"); await refresh().click();
    await until(() => history().first().innerText(), (text) => text.includes("Unknown outcome"), "unknown status readback");
    await history().first().click();
    assert.match(await workspace().innerText(), /unconfirmed.*charge/s);
    await refresh().click(); await page.reload(); await selectAI(); await direction().waitFor();
    assert.equal((await api("project-a", "/fixture/metrics")).calls.length, 4);
    await until(() => history().count(), (n) => n === 4, "cold history reloaded");
  });
  await step("a changed source marks its result outdated without silently rebinding the original", async () => {
    let replacement = Buffer.from(await (await fetch(origins["project-a"] + '/fixture/image?color=darkgreen')).arrayBuffer());
    await api("project-a", "/api/documents", "POST", { projectId: "project-a", fileName: "revised-reference.png", mimeType: "image/png",
      contentBase64: replacement.toString("base64"), replacesPages: [{ ...refs[0], newPageIndex: 0 }] });
    await refresh().click(); await history().filter({ hasText: "Soft morning light" }).click();
    await until(() => workspace().locator('.render-source-state').innerText(), (text) => text.includes("Source outdated"), "reference update is explicit");
    await workspace().getByRole("button", { name: "Use updated source", exact: true }).click();
    assert.match(await workspace().getByRole("combobox", { name: "Source image", exact: true }).locator('option:checked').innerText(), /source.png/);
    assert.match(await workspace().locator('.render-references li').first().innerText(), /revised-reference/);
    replacement = Buffer.from(await (await fetch(origins["project-a"] + '/fixture/image?color=purple')).arrayBuffer());
    await api("project-a", "/api/documents", "POST", { projectId: "project-a", fileName: "revised-source.png", mimeType: "image/png",
      contentBase64: replacement.toString("base64"), replacesPages: [{ ...source, newPageIndex: 0 }] });
    await refresh().click(); await history().filter({ hasText: "Soft morning light" }).click();
    await until(() => workspace().locator('.render-source-state').innerText(), (text) => text.includes("Source outdated"), "outdated result is explicit");
    assert.deepEqual((await jobs()).jobs.find((j) => j.jobId === first.jobId).request.source, source);
    await workspace().getByRole("button", { name: "Use updated source", exact: true }).click();
    assert.match(await workspace().getByRole("combobox", { name: "Source image", exact: true }).locator('option:checked').innerText(), /revised-source/);
    assert.equal((await api("project-a", "/fixture/metrics")).calls.length, 4, "loading a replacement is not generation");
    // A drawing is followed through the replacement its rebuild registered
    // (#291), and only that: a newer revision that registered none is another page.
    const onlyPage = (document) => ({ runId: document.runId, assetSha256: document.assetSha256, revisionRef: document.revisionRef ?? null, pageIndex: 0 });
    const key = (document) => JSON.stringify([document.runId, document.revisionRef ?? document.assetSha256]);
    const upload = async (fileName, color, replaced) => api("project-a", "/api/documents", "POST", { projectId: "project-a", fileName, mimeType: "image/png",
      contentBase64: Buffer.from(await (await fetch(origins["project-a"] + `/fixture/image?color=${color}`)).arrayBuffer()).toString("base64"),
      ...(replaced ? { replacesPages: [{ ...onlyPage(replaced), newPageIndex: 0 }] } : {}) });
    const model = async (fileName, revised) => api("project-a", "/api/model-assets", "POST", { projectId: "project-a", fileName,
      contentBase64: Buffer.from(await (await fetch(origins["project-a"] + `/fixture/plan-model?revised=${revised}`)).arrayBuffer()).toString("base64") });
    const plan = (asset, extra = {}) => api("project-a", "/api/drawings/plans", "POST", { projectId: "project-a",
      sourceAsset: { runId: asset.runId, assetSha256: asset.sha256 }, cutHeight: 1.2, bottom: 0, scaleDenominator: 50, ...extra });
    const wall = await model("wall.3dm", false), drawing = await plan(wall), planReference = await upload("plan-reference.png", "orange");
    const planJob = await api("project-a", "/api/render/jobs", "POST", { projectId: "project-a", requestId: randomUUID(), providerId: "test-image",
      source: onlyPage(drawing), references: [onlyPage(planReference)], direction: "Plan in evening light", output: { size: "2K", aspectRatio: "source" } });
    const planRender = async () => (await jobs()).jobs.find((job) => job.jobId === planJob.jobId);
    await until(planRender, (job) => job?.status === "succeeded", "drawing render");
    const [x0, y0, x1, y1] = drawing.viewRecipe.frame.crop_uv;
    const reshaped = await plan(wall, { previousRevisionRef: drawing.revisionRef, cropUv: [x0 - 2, y0, x1 + 2, y1] });
    assert.equal(reshaped.drawingId, drawing.drawingId);
    assert.notEqual(reshaped.pages[0].width / reshaped.pages[0].height, drawing.pages[0].width / drawing.pages[0].height, "the wider crop changes the page shape");
    assert.deepEqual(reshaped.replacesPages, [], "a revision with another page shape registers no replacement");
    assert.equal((await planRender()).sourceState, "current", "a newer revision that registered no replacement leaves the render current");
    await upload("revised-plan-reference.png", "teal", planReference);
    const sourceImage = () => workspace().getByRole("combobox", { name: "Source image", exact: true });
    await refresh().click(); await history().filter({ hasText: "Plan in evening light" }).click();
    await until(() => workspace().locator('.render-source-state').innerText(), (text) => text.includes("Source outdated"), "drawing render outdated by its reference");
    await workspace().getByRole("button", { name: "Use updated source", exact: true }).click();
    assert.equal(await sourceImage().inputValue(), key(drawing), "the drawing's newest revision is never guessed as its replacement");
    assert.match(await workspace().locator('.render-references li').first().innerText(), /revised-plan-reference/);
    const rebuilt = await plan(await model("revised-wall.3dm", true), { previousRevisionRef: drawing.revisionRef });
    assert.deepEqual(rebuilt.replacesPages, [{ ...onlyPage(drawing), newPageIndex: 0 }], "a rebuild on another source replaces the page it continues");
    await refresh().click();
    await until(() => sourceImage().locator("option").evaluateAll((options) => options.map((option) => option.value)),
      (values) => values.includes(key(rebuilt)), "rebuilt drawing listed");
    await workspace().getByRole("button", { name: "Use updated source", exact: true }).click();
    assert.equal(await sourceImage().inputValue(), key(rebuilt), "the rebuilt drawing is reached through its registered replacement");
    assert.match(await workspace().locator('.render-references li').first().innerText(), /revised-plan-reference/);
    assert.equal((await api("project-a", "/fixture/metrics")).calls.length, 5, "following a rebuilt drawing is not generation");
  });
  await step("Board receives the same exact page, selects it and can restore only the explicitly deleted old result", async () => {
    await history().filter({ hasText: "Soft morning light" }).click();
    await workspace().getByRole("button", { name: "Send to Board", exact: true }).click();
    const board = () => api("project-a", "/api/board");
    const exact = (el) => el.type === "image" && !el.isDeleted && el.customData?.sourceDocument?.runId === first.document.runId;
    await until(board, (b) => b.elements.some(exact), "exact render page persisted on board");
    await page.locator('.monkeyboard-canvas canvas').first().waitFor();
    // Explicit handoff selects the page and transfers keyboard focus to Board.
    await until(() => page.evaluate(() => document.activeElement?.classList.contains('excalidraw')), Boolean, "Board keyboard focus");
    await page.keyboard.press("Delete");
    await until(board, (b) => !b.elements.some(exact), "selected source page deleted");
    const before = await board(); const seen = before.seenDocuments;
    await page.getByRole("button", { name: "render", exact: true }).click();
    await workspace().getByRole("button", { name: "Send to Board", exact: true }).click();
    const after = await until(board, (b) => b.elements.some(exact), "only chosen page reinserted");
    assert.equal(after.elements.filter(exact).length, 1);
    assert.deepEqual(after.seenDocuments, seen);
    assert.equal(after.elements.filter((el) => !el.isDeleted && el.type === "image").length,
      before.elements.filter((el) => !el.isDeleted && el.type === "image").length + 1);
  });
  await step("wide, medium, narrow and dark layouts remain usable; all rendering leaves Design HEAD unchanged", async () => {
    await page.getByRole("button", { name: "render", exact: true }).click();
    await until(() => workspace().locator('.render-image img').count(), (n) => n > 0, "returning image loaded");
    for (const width of [900, 375]) {
      await page.setViewportSize({ width, height: 1000 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({ path: path.join(temporary, `render-${width}.png`), fullPage: true });
      if (width === 375) {
        await workspace().locator('.render-results').scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(temporary, 'render-375-results.png'), fullPage: true });
        assert.equal(await workspace().getByRole('button', { name: 'Send to Board', exact: true }).isVisible(), true);
      }
    }
    await page.setViewportSize({ width: 1440, height: 1050 });
    await page.evaluate(() => window.renderFixture['project-a'].setTheme('dark'));
    await page.screenshot({ path: path.join(temporary, "render-dark.png"), fullPage: true });
    assert.equal((await api("project-a", "/fixture/metrics")).head, headBefore);
    assert.deepEqual(errors, []);
  });
  await step("a source and direction alone can generate; a lost response recovers by read without replay", async () => {
    await workspace().getByRole("button", { name: "Use these inputs", exact: true }).click();
    while (await workspace().locator('.render-references li').count()) await workspace().getByRole('button', { name: 'Remove reference 1', exact: true }).click();
    await direction().fill('Source only');
    let intercepted = false;
    await page.route('**/project-a/api/render/jobs', async (route) => {
      if (!intercepted && route.request().method() === 'POST') { intercepted = true; await route.fetch(); await route.abort(); }
      else await route.continue();
    });
    await generate().click();
    await until(jobs, (r) => r.jobs.length === 6 && r.jobs[0].status === 'succeeded', 'source only generation');
    await refresh().click();
    await until(() => history().count(), (n) => n === 6, 'lost response recovered by history');
    const latest = (await jobs()).jobs[0]; assert.equal(latest.request.references.length, 0);
    assert.equal((await api('project-a', '/fixture/metrics')).calls.length, 6);
    assert.equal((await api('project-a', '/fixture/metrics')).head, headBefore);
    await page.unroute('**/project-a/api/render/jobs');
  });
  await step("retained native result reopens in its own project without invented source or paid replay", async () => {
    const native = await api('project-b', '/fixture/legacy', 'POST');
    await page.getByRole('button', { name: 'Project B', exact: true }).click(); await selectAI();
    await until(() => history().count(), (n) => n === 1, 'native history visible');
    await until(() => workspace().locator('.render-image img').count(), (n) => n === 1, 'native image read');
    assert.match(await workspace().locator('.render-metadata').innerText(), /retained-native.png/);
    assert.equal(await workspace().getByRole('button', { name: 'Source', exact: true }).isDisabled(), true);
    assert.equal(await workspace().getByRole('button', { name: 'Compare', exact: true }).isDisabled(), true);
    assert.equal(await workspace().getByRole('button', { name: 'Use these inputs', exact: true }).isDisabled(), true);
    const download = page.waitForEvent('download');
    await workspace().getByRole('link', { name: 'Download', exact: true }).click();
    assert.equal((await download).suggestedFilename(), native.fileName);
    await page.screenshot({ path: path.join(temporary, 'render-native-history.png'), fullPage: true });
    await page.reload();
    await page.getByRole('button', { name: 'Project B', exact: true }).click(); await selectAI();
    await until(() => workspace().locator('.render-image img').count(), (n) => n === 1, 'native result survives reload');
    assert.equal((await api('project-b', '/fixture/metrics')).calls.length, 0);
    assert.equal((await api('project-b', '/api/render/jobs')).jobs[0].jobId, native.jobId);
    await page.getByRole('button', { name: 'Project A', exact: true }).click(); await selectAI();
    await until(() => history().count(), (n) => n === 6, 'project A keeps only its six attempts');
    assert.equal(await history().filter({ hasText: native.fileName }).count(), 0);
  });
  await step("perspective and orthographic model views become exact frozen inputs without generating until requested", async () => {
    const capturePage = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    const captureRequests = [];
    capturePage.on("pageerror", error => errors.push(String(error)));
    capturePage.on("console", message => { if (message.type() === "error") console.error(message.text()); });
    capturePage.on("requestfailed", request => console.error(`Capture fixture: ${request.url()} ${request.failure()?.errorText}`));
    capturePage.on("request", request => {
      if (request.method() === "POST" && request.url().endsWith("/api/render/views")) captureRequests.push(request.postDataJSON());
    });
    await capturePage.goto(new URL("camera", origin).href);
    await capturePage.getByRole("button", { name: "AI", exact: true }).click();
    const capture = () => capturePage.getByRole("button", { name: "Use current view as source", exact: true });
    const selectedSource = () => capturePage.getByRole("combobox", { name: "Source image", exact: true }).inputValue();
    try { await capture().waitFor(); }
    catch (error) { await capturePage.screenshot({ path: path.join(temporary, "capture-failure.png"), fullPage: true }); throw error; }
    assert.equal(await capture().isDisabled(), true, "no model view cannot be captured");
    const initial = await api("capture", "/fixture/metrics");
    const retained = [];
    for (const [index, projection] of ["perspective", "orthographic"].entries()) {
      await capturePage.evaluate(kind => window.captureFixture.setProjection(kind), projection);
      await until(() => capture().isEnabled(), Boolean, `${projection} saved model view is available`);
      await capturePage.evaluate(() => window.captureFixture.setIssue("unsaved"));
      await until(() => capture().isDisabled(), Boolean, "dirty view disables capture");
      await capturePage.getByText("Sync edits and open the saved candidate in Modeling before using this view.", { exact: true }).waitFor();
      assert.equal(captureRequests.length, index, "blocked views never send a capture request");
      await capturePage.evaluate(() => window.captureFixture.setIssue(null));
      await until(() => capture().isEnabled(), Boolean, "saved source restored");
      // JSON transport normalizes Three's -0 matrix entries to 0.
      const view = JSON.parse(JSON.stringify(await capturePage.evaluate(() => window.captureFixture.snapshot())));
      let releaseCapture, releaseDocuments, oldDocumentsRead = false;
      if (index === 0) {
        const captureGate = new Promise(resolve => { releaseCapture = resolve; });
        const documentsGate = new Promise(resolve => { releaseDocuments = resolve; });
        await capturePage.route("**/capture/api/render/views", async route => { await captureGate; await route.continue(); });
        await capturePage.route("**/capture/api/documents", async route => {
          const response = await route.fetch(); oldDocumentsRead = true;
          await documentsGate; await route.fulfill({ response });
        });
      }
      await capture().click();
      if (index === 0) {
        await until(() => captureRequests.length, count => count === 1, "capture waiting to save");
        await capturePage.getByRole("button", { name: "Refresh status", exact: true }).click();
        await until(() => oldDocumentsRead, Boolean, "refresh read the documents before capture was saved");
        releaseCapture();
      }
      await until(selectedSource, value => value && !retained.some(document => document.assetSha256 === JSON.parse(value)[1]), `${projection} selected as source`);
      if (index === 0) {
        const selected = await selectedSource();
        releaseDocuments();
        await until(() => capturePage.getByRole("button", { name: "Refresh status", exact: true }).isEnabled(), Boolean, "stale refresh finished");
        assert.equal(await selectedSource(), selected, "a late refresh cannot replace the just-saved source with its older document list");
        await capturePage.unroute("**/capture/api/render/views"); await capturePage.unroute("**/capture/api/documents");
      }
      const [, sha] = JSON.parse(await selectedSource());
      const document = (await api("capture", "/api/documents")).documents.find(row => row.assetSha256 === sha);
      assert.ok(document, "captured source is retained in the real Runtime");
      assert.deepEqual(document.modelSource, view.modelSource);
      assert.equal(document.sourceStageRef, view.sourceStageRef);
      assert.deepEqual(document.viewRecipe.camera, { projection, worldMatrix: view.worldMatrix, projectionMatrix: view.projectionMatrix, exposure: 1 });
      const [width, height] = document.viewRecipe.screenSize;
      assert.equal(Math.max(width, height), 2048);
      assert.ok(Math.abs(width / height - view.aspect) <= 1 / height, "capture preserves the original aspect within pixel rounding");
      assert.deepEqual(captureRequests[index].modelSource, view.modelSource);
      assert.deepEqual(captureRequests[index].camera, document.viewRecipe.camera);
      assert.deepEqual(captureRequests[index].screenSize, [width, height]);
      const bytesUrl = `/api/documents/${sha}/bytes?runId=${encodeURIComponent(document.runId)}`;
      const bytes = Buffer.from(await (await fetch(origins.capture + bytesUrl)).arrayBuffer());
      assert.equal(createHash("sha256").update(bytes).digest("hex"), sha);
      assert.deepEqual([bytes.readUInt32BE(16), bytes.readUInt32BE(20)], [width, height], "PNG pixels agree with retained dimensions");
      const pixelChunks = png => {
        const chunks = [];
        for (let offset = 8; offset < png.length;) {
          const length = png.readUInt32BE(offset);
          if (png.toString("ascii", offset + 4, offset + 8) === "IDAT") chunks.push(png.subarray(offset + 8, offset + 8 + length));
          offset += length + 12;
        }
        return Buffer.concat(chunks);
      };
      assert.equal(pixelChunks(Buffer.from(captureRequests[index].pngBase64, "base64")).equals(pixelChunks(bytes)), true,
        "retaining source identity metadata preserves the exact frontend PNG image data");
      const colors = await capturePage.evaluate(async url => {
        const image = await createImageBitmap(await (await fetch('/capture' + url)).blob());
        const canvas = document.createElement('canvas'); canvas.width = 32; canvas.height = 32;
        const context = canvas.getContext('2d'); context.drawImage(image, 0, 0, 32, 32); image.close();
        const data = context.getImageData(0, 0, 32, 32).data, colors = new Set();
        for (let i = 0; i < data.length; i += 4) colors.add(Array.from(data.slice(i, i + 3)).join(','));
        return colors.size;
      }, bytesUrl);
      assert.ok(colors > 3, "capture contains rendered model geometry, not an empty background");
      assert.equal((await api("capture", "/fixture/metrics")).calls.length, index, "saving a view never invokes the provider");
      const selected = await selectedSource();
      await capturePage.evaluate(() => window.captureFixture.moveCamera());
      const changed = await capturePage.evaluate(() => window.captureFixture.snapshot());
      assert.notDeepEqual(changed.worldMatrix, view.worldMatrix);
      assert.equal(await selectedSource(), selected, "moving the live camera cannot replace the selected frozen source");
      assert.deepEqual((await api("capture", "/api/documents")).documents.find(row => row.assetSha256 === sha), document);
      await capturePage.getByRole("textbox", { name: "Visual direction", exact: true }).fill(`Captured ${projection} view`);
      await capturePage.getByRole("button", { name: "Generate", exact: true }).click();
      const job = (await until(() => api("capture", "/api/render/jobs"), value => value.jobs.length === index + 1 && value.jobs[0].status === "succeeded", `${projection} generated from capture`)).jobs[0];
      assert.equal(job.request.source.assetSha256, sha);
      assert.deepEqual(job.document.modelSource, view.modelSource);
      assert.equal((await api("capture", "/fixture/metrics")).calls[index].source, sha);
      retained.push(document);
      await capturePage.getByRole("button", { name: "Refresh status", exact: true }).click();
      const result = capturePage.locator(".render-list button").filter({ hasText: `Captured ${projection} view` });
      await until(() => result.innerText(), text => text.includes("Complete"), `${projection} result appears in the UI`);
      await result.click();
      await capturePage.getByRole("button", { name: "Compare", exact: true }).click();
      await until(() => capturePage.locator(".render-image img").count(), count => count === 2, "saved model view and its offline result visible together");
      await capturePage.screenshot({ path: path.join(temporary, `render-capture-${projection}.png`), fullPage: true });
    }
    const documents = (await api("capture", "/api/documents")).documents;
    for (const document of retained) assert.deepEqual(documents.find(row => row.assetSha256 === document.assetSha256), document,
      "changing projection and generating another view never rewrites earlier pixels or camera metadata");
    await capturePage.evaluate(() => window.captureFixture.setVisible(false));
    await until(() => capture().isDisabled(), Boolean, "removing the model view disables capture again");
    assert.equal(captureRequests.length, 2);
    assert.equal((await api("capture", "/fixture/metrics")).head, initial.head, "view capture and rendering leave model HEAD unchanged");
    assert.deepEqual(errors, []);
    await capturePage.close();
  });
  await step("Physical real geometry, camera gestures, reload and late scene reads preserve edits without AI replay", async () => {
    const physicalPage = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    physicalPage.on('pageerror', error => errors.push(String(error)));
    await physicalPage.goto(origin);
    await physicalPage.getByRole('button', { name: 'Project B', exact: true }).click();
    const panel = physicalPage.getByRole('region', { name: 'Physical Render Scene', exact: true });
    const bytes = Buffer.from(await (await fetch(origins['project-b'] + '/fixture/plan-model')).arrayBuffer());
    const importInput = panel.getByLabel('Import geometry', { exact: true });
    await until(() => importInput.isEnabled(), Boolean, 'project initial read finished before importing');
    await importInput.setInputFiles({ name: 'architectural-fixture.3dm', mimeType: 'application/octet-stream', buffer: bytes });
    try { await panel.locator('canvas').waitFor(); }
    catch (error) {
      await physicalPage.screenshot({ path: path.join(temporary, 'physical-import-failure.png'), fullPage: true });
      console.error(await panel.innerText());
      console.error(processes.map(p => p.log).join('\n'));
      throw error;
    }
    await panel.locator('summary').filter({ hasText: 'Camera and quality' }).click();
    const camera = async () => panel.locator('input').evaluateAll(es => es.map(e => ({ label: e.parentElement.textContent, value: e.value })).filter(e => /^(Camera |FOV|Orthographic)/.test(e.label)));
    const before = await camera();
    const canvas = panel.locator('canvas');
    await canvas.scrollIntoViewIfNeeded();
    const rect = await canvas.boundingBox();
    async function drag(button, dx, dy) {
      await physicalPage.mouse.move(rect.x + rect.width/2, rect.y + rect.height/2);
      await physicalPage.mouse.down({button});
      await physicalPage.mouse.move(rect.x + rect.width/2 + dx, rect.y + rect.height/2 + dy, {steps: 8});
      await physicalPage.mouse.up({button});
    }
    await drag('left', 70, 25); const orbit = await camera(); assert.notDeepEqual(orbit, before, 'orbit updates camera');
    await drag('right', 35, 20); const pan = await camera(); assert.notDeepEqual(pan, orbit, 'pan updates target/position');
    await physicalPage.mouse.wheel(0, 160);
    await until(camera, value => JSON.stringify(value) !== JSON.stringify(pan), 'dolly updates camera');
    await panel.getByRole('combobox', {name: 'Projection', exact: true}).selectOption('orthographic');
    await panel.getByRole('spinbutton', {name: 'Orthographic height', exact: true}).fill('8');
    await panel.getByRole('spinbutton', {name: 'Camera target X', exact: true}).fill('0.25');
    const savedCamera = await camera();
    await panel.getByRole('button', {name: 'Save scene', exact: true}).click();
    await until(() => api('project-b', '/api/render/scene'), s => s.scene?.camera.target[0] === .25 && s.status === 'current', 'physical saved');
    await physicalPage.getByRole('button', {name: 'drawing', exact: true}).click();
    await physicalPage.getByRole('button', {name: 'render', exact: true}).click();
    assert.deepEqual(await camera(), savedCamera, 'workspace return restores camera');
    await physicalPage.reload();
    await physicalPage.getByRole('button', {name: 'Project B', exact: true}).click();
    await panel.locator('canvas').waitFor();
    await panel.locator('summary').filter({hasText: 'Camera and quality'}).click();
    assert.deepEqual(await camera(), savedCamera, 'whole-page reload restores saved camera');
    assert.equal(await panel.getByRole('combobox', {name: 'Projection', exact: true}).inputValue(), 'orthographic');
    let release, read = false;
    const gate = new Promise(resolve => {release = resolve;});
    await physicalPage.route('**/project-b/api/render/scene', async route => { const response = await route.fetch(); read = true; await gate; await route.fulfill({response}); });
    await panel.getByRole('button', {name: 'Reload saved scene', exact: true}).click();
    await until(() => read, Boolean, 'old scene response held');
    await panel.getByRole('spinbutton', {name: 'Camera target X', exact: true}).fill('0.75');
    release();
    await until(() => panel.getByRole('button', {name: 'Reload saved scene', exact: true}).isEnabled(), Boolean, 'held refresh finished');
    assert.equal(await panel.getByRole('spinbutton', {name: 'Camera target X', exact: true}).inputValue(), '0.75', 'late saved scene must not replace a newer camera edit');
    await physicalPage.unroute('**/project-b/api/render/scene');
    assert.equal((await api('project-b', '/fixture/metrics')).calls.length, 0, 'Physical never invokes the AI adapter');
    assert.deepEqual(errors, []);
    await physicalPage.screenshot({ path: path.join(temporary, 'physical-regression.png'), fullPage: true });
    await physicalPage.close();
  });
  await step("cold real Hub Render leaves every project content file unchanged; entering Arch seeds only then", async () => {
    const ports = [await freePort(), await freePort(), await freePort()];
    hubOrigin = `http://127.0.0.1:${ports[0]}`;
    const child = spawn(python, ['-c', hubSource, temporary, repoRoot, ...ports.map(String)], {
      cwd: repoRoot, env: { ...pythonEnv, PYTHONPATH: pythonEnv.PYTHONPATH + path.delimiter + path.join(repoRoot, 'apps/monkeyhub/api') },
      stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
    });
    const process = { child, log: '' }; processes.push(process);
    child.stdout.on('data', (chunk) => { process.log = (process.log + chunk).slice(-5000); });
    child.stderr.on('data', (chunk) => { process.log = (process.log + chunk).slice(-5000); });
    await until(() => fetch(hubOrigin + '/api/health').then((r) => r.ok).catch(() => false), Boolean, 'real Hub ready');
    const hubPage = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    const posts = [];
    hubPage.on('request', (request) => { if (request.method() === 'POST') posts.push(request.url()); });
    await hubPage.goto(hubOrigin);
    await hubPage.getByRole('button', { name: 'cold-render', exact: true }).first().click();
    await hubPage.getByRole('button', { name: 'Render', exact: true }).click();
    await hubPage.getByRole("button", { name: "Physical", exact: true }).waitFor({ timeout: 30000 });
    const read = () => fetch(hubOrigin + '/fixture/metrics').then((r) => r.json());
    let metrics = await read();
    assert.deepEqual(metrics.current, metrics.baseline, 'Runtime and Render must not add authored inputs or any project content');
    assert.equal(posts.some((url) => url.includes('/api/project/modeling')), false);
    await hubPage.getByRole('button', { name: 'Modeling', exact: true }).click();
    metrics = await until(read, (m) => JSON.stringify(m.current['cold-render']) !== JSON.stringify(m.baseline['cold-render']), 'Runtime-first to Arch seeds author inputs');
    assert.equal(posts.filter((url) => url.includes('/api/project/modeling')).length, 1);
    assert.ok(Object.keys(metrics.current['cold-render']).some((name) => name.startsWith('input/')), 'explicit Arch creates authored input');
    await hubPage.getByRole('button', { name: 'Render', exact: true }).click();
    await hubPage.getByRole('button', { name: 'Modeling', exact: true }).click();
    assert.equal(posts.filter((url) => url.includes('/api/project/modeling')).length, 1, 'shared readiness does not repeat Arch seed');
    await hubPage.getByRole('button', { name: 'cold-arch', exact: true }).first().click();
    await hubPage.getByRole('button', { name: 'Modeling', exact: true }).click();
    metrics = await until(read, (m) => JSON.stringify(m.current['cold-arch']) !== JSON.stringify(m.baseline['cold-arch']), 'Arch-first still seeds author inputs');
    assert.ok(Object.keys(metrics.current['cold-arch']).some((name) => name.startsWith('input/')));
    await hubPage.close();
    await fetch(hubOrigin + '/fixture/shutdown', { method: 'POST' });
    await until(() => child.exitCode, (code) => code !== null, 'Hub and owned workers stop'); hubOrigin = undefined;
  });
  console.log(JSON.stringify({ passed, screenshots: temporary, actualProvider: false, requests: requests.length }));
} catch (error) {
  await page?.screenshot({ path: path.join(temporary, "failure.png"), fullPage: true }).catch(() => {});
  console.error(`FAIL ${current}; ${temporary}`); console.error(errors); throw error;
} finally {
  await browser?.close(); await server?.close();
  if (hubOrigin) {
    await fetch(hubOrigin + '/fixture/shutdown', { method: 'POST' }).catch(() => {});
    await delay(1500);
  }
  for (const process of processes) if (process.child.exitCode === null) { const exited = new Promise((resolve) => process.child.once("exit", resolve)); process.child.kill(); await exited; }
}

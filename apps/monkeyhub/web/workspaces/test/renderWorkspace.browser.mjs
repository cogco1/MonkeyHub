/** Real project Runtime/P036/SDK/UI; the injected image adapter is offline.
 * Never a live provider acceptance test. All assets are synthetic test images.
 */
import assert from "node:assert/strict";
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
project = root / project_id
repo = FilesystemProjectRepository.initialize(project, project_id=project_id, initial_state={'project_id': project_id, 'version': 0})
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
app=create_app(StudioSettings(project_dir=project,cad_export='off',monitor_dir=root/'monitor'),render_adapter=adapter)
@app.get('/fixture/image')
def source_image(color: str='white'): return Response(image(color), media_type='image/png')
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
async function step(name, action) { current = name; await action(); passed.push(name); console.log(`PASS ${name}`); }
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
try {
  for (const id of ["project-a", "project-b"]) {
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
    plugins: [{ name: "render-fixture", resolveId(id) { if (id === "/render-fixture.tsx") return path.join(webRoot, "render-fixture.tsx").replaceAll("\\", "/"); },
      load(id) { if (id === path.join(webRoot, "render-fixture.tsx").replaceAll("\\", "/")) return fixture; },
      configureServer(vite) { vite.middlewares.use((request, response, next) => {
        if (request.url !== "/") return next();
        response.setHeader("content-type", "text/html"); response.end('<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><style>html,body,#root{height:100%;margin:0}nav{height:44px;display:flex;gap:8px}*{box-sizing:border-box}</style></head><body><div id="root" class="project-workspace"></div><script type="module" src="/render-fixture.tsx"></script></body></html>');
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
    await page.getByRole("button", { name: "Project B", exact: true }).click();
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
    await refresh().click(); await page.reload(); await direction().waitFor();
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
    await until(jobs, (r) => r.jobs.length === 5 && r.jobs[0].status === 'succeeded', 'source only generation');
    await refresh().click();
    await until(() => history().count(), (n) => n === 5, 'lost response recovered by history');
    const latest = (await jobs()).jobs[0]; assert.equal(latest.request.references.length, 0);
    assert.equal((await api('project-a', '/fixture/metrics')).calls.length, 5);
    assert.equal((await api('project-a', '/fixture/metrics')).head, headBefore);
    await page.unroute('**/project-a/api/render/jobs');
  });
  await step("retained native result reopens in its own project without invented source or paid replay", async () => {
    const native = await api('project-b', '/fixture/legacy', 'POST');
    await page.getByRole('button', { name: 'Project B', exact: true }).click();
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
    await page.getByRole('button', { name: 'Project B', exact: true }).click();
    await until(() => workspace().locator('.render-image img').count(), (n) => n === 1, 'native result survives reload');
    assert.equal((await api('project-b', '/fixture/metrics')).calls.length, 0);
    assert.equal((await api('project-b', '/api/render/jobs')).jobs[0].jobId, native.jobId);
    await page.getByRole('button', { name: 'Project A', exact: true }).click();
    await until(() => history().count(), (n) => n === 5, 'project A keeps only its five attempts');
    assert.equal(await history().filter({ hasText: native.fileName }).count(), 0);
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
    await hubPage.locator('[data-project-surface="render"]:visible').getByRole('textbox', { name: 'Visual direction' }).waitFor({ timeout: 30000 });
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

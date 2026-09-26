/** Actual Runtime/P036 and rendered Publish/Board components; no provider calls. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer as httpServer } from "node:http";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";
const root = fileURLToPath(new URL("..", import.meta.url)), repo = path.resolve(root, "../../../..");
const temporary = await mkdtemp(path.join(tmpdir(), "monkeyhub-publish-browser-"));
const processes = [], origins = {}, errors = [], passed = [];
let browser, vite, page, current;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(read, accepts, label) {
  const end = Date.now() + 30000; let value;
  do { value = await read(); if (accepts(value)) return value; await delay(100); } while (Date.now() < end);
  assert.fail(`${label}: ${JSON.stringify(value)} ${processes.map((p) => p.log).join("\n")}`);
}
async function freePort() { const s = httpServer(); await new Promise((r) => s.listen(0, "127.0.0.1", r)); const port = s.address().port; await new Promise((r) => s.close(r)); return port; }
const source = `
import sys,base64
from pathlib import Path
from io import BytesIO
from PIL import Image,ImageDraw
from fastapi.testclient import TestClient
import uvicorn
from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
root, project, port=Path(sys.argv[1]),sys.argv[2],int(sys.argv[3])
fresh=not (root/project/'project.json').exists()
repository=FilesystemProjectRepository.initialize(root/project,project_id=project,initial_state={'project_id':project,'version':0}) if fresh else FilesystemProjectRepository.open(root/project)
app=create_app(StudioSettings(project_dir=root/project,cad_export='off'))
image=Image.new('RGB',(1000,600),'white');draw=ImageDraw.Draw(image)
draw.rectangle((80,100,920,490),outline='#384150',width=5)
for x in (340,660):draw.line((x,100,x,490),fill='#384150',width=4)
draw.rectangle((110,130,240,220),outline='#777777',width=2)
draw.text((90,50),'SYNTHETIC PLAN / '+project,fill='black')
out=BytesIO();image.save(out,format='PNG')
if fresh:
 with TestClient(app) as client:
  document=client.post('/api/documents',json={'projectId':project,'fileName':'Room plan.png','mimeType':'image/png','contentBase64':base64.b64encode(out.getvalue()).decode()}).json()
  print(document,flush=True)
  draw.rectangle((440,280,600,400),fill='#e2b676')
  out=BytesIO();image.save(out,format='PNG')
  client.post('/api/documents',json={'projectId':project,'fileName':'Spatial diagram.png','mimeType':'image/png','contentBase64':base64.b64encode(out.getvalue()).decode()})
@app.get('/fixture/head')
def head():return {'head':repr(repository.read_head())}
@app.get('/fixture/transparency')
def transparency():
 from reportlab.pdfgen.canvas import Canvas
 pixels=Image.new('RGBA',(100,100),(0,0,0,0)); ImageDraw.Draw(pixels).rectangle((50,0,99,99),fill=(0,0,255,128))
 png=BytesIO(); pixels.save(png,format='PNG')
 pdf=BytesIO(); canvas=Canvas(pdf,pagesize=(100,100),invariant=1); canvas.setFillColorRGB(0,0,1); canvas.setFillAlpha(.5); canvas.rect(50,0,50,100,fill=1,stroke=0); canvas.save()
 return [{'projectId':project,'fileName':name,'mimeType':mime,'contentBase64':base64.b64encode(data).decode()} for name,mime,data in [('Transparent.png','image/png',png.getvalue()),('Transparent.pdf','application/pdf',pdf.getvalue())]]
uvicorn.run(app,host='127.0.0.1',port=port,log_level='warning')
`;
const fixture = `
import React,{useState} from 'react';import{createRoot}from'react-dom/client';
import{ProjectWorkspace}from'/src/app/ProjectWorkspace';import{UserPreferencesProvider}from'/test/TestProviders';
import'/src/styles.css';import'/@fs/${path.resolve(repo, "apps/shared-web/src/base.css").replaceAll("\\", "/")}';
function Project({id}){const[workspace,setWorkspace]=useState('publish');
return <UserPreferencesProvider baseUrl={location.origin+'/'+id}><nav>{['publish','board','render'].map(w=><button key={w} onClick={()=>setWorkspace(w)}>{w}</button>)}</nav><main style={{height:'calc(100% - 40px)'}}><ProjectWorkspace expectedProjectId={id} workspace={workspace} onWorkspaceChange={setWorkspace}/></main></UserPreferencesProvider>}
function App(){const[id,setId]=useState('pub-a');return <><button id="project-switch" onClick={()=>setId(id==='pub-a'?'pub-b':'pub-a')}>{id}</button><div style={{height:'calc(100% - 30px)'}}><Project key={id} id={id}/></div></>}
createRoot(document.getElementById('root')).render(<App/>);
`;
async function startRuntime(id, port = null) {
  port ??= await freePort();
  const child = spawn(process.env.PYTHON ?? "python", ["-c", source, temporary, id, String(port)], { cwd: repo,
    env: { ...process.env, PYTHONUTF8: "1", PYTHONPATH: [repo, path.join(repo, "apps/archflow-studio/api")].join(path.delimiter) }, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
  const p = { child, log: "", id, port }; processes.push(p); child.stdout.on("data", (c) => p.log += c); child.stderr.on("data", (c) => p.log += c);
  origins[id] = `http://127.0.0.1:${port}`;
  await until(() => fetch(origins[id] + "/api/health").then((r) => r.ok).catch(() => false), Boolean, "Runtime ready");
}
try {
  for (const id of ["pub-a", "pub-b"]) await startRuntime(id);
  vite = await createServer({ root, configFile: false, publicDir: "../.generated/public", cacheDir: path.join(temporary, "vite"), logLevel: "error",
    resolve: { dedupe: ["react", "react-dom"] }, optimizeDeps: { include: ["react", "react-dom/client", "react/jsx-runtime", "react/jsx-dev-runtime"] },
    server: { host: "127.0.0.1", port: 0, proxy: Object.fromEntries(Object.entries(origins).map(([id, target]) => [`/${id}`, { target, rewrite: (url) => url.slice(id.length + 1) }])) },
    plugins: [{ name: "publication-fixture", resolveId(id) { if (id === "/fixture.tsx") return path.join(root, "fixture.tsx").replaceAll("\\", "/"); }, load(id) { if (id === path.join(root, "fixture.tsx").replaceAll("\\", "/")) return fixture; },
      configureServer(server) { server.middlewares.use((request, response, next) => { if (request.url.split("?")[0] !== "/") return next(); response.setHeader("content-type", "text/html"); response.end('<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body,#root{height:100%;margin:0}*{box-sizing:border-box}nav{height:40px}</style></head><body><div id="root" class="project-workspace"></div><script type="module" src="/fixture.tsx"></script></body></html>'); }); } }],
  });
  await vite.listen();
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  page = await browser.newPage({ viewport: { width: 1440, height: 960 }, acceptDownloads: true });
  page.on("pageerror", (error) => errors.push(error.message));
  const api = (id, route) => fetch(origins[id] + route).then((r) => r.json());
  const head = await api("pub-a", "/fixture/head");
  const pub = () => page.locator('[data-project-surface="publish"]:visible');
  async function step(name, action) { current = name; await action(); passed.push(name); console.log("PASS", name); }
  const board = () => page.locator('[data-project-surface="board"]:visible');
  async function sendBoardSelection() {
    await board().locator('.monkeyboard-canvas canvas').first().waitFor();
    await until(() => api('pub-a', '/api/board'), (value) => value.elements.filter((item) => item.type === 'image' && !item.isDeleted).length === 2, 'Board has the retained source page');
    await board().locator('.excalidraw').focus();
    await page.keyboard.press('Escape'); await page.keyboard.press('v'); await page.keyboard.press('Control+a');
    const actions = board().locator('details').filter({ has: page.locator('summary', { hasText: 'More board actions' }) });
    if (!await actions.evaluate((element) => element.open)) await actions.locator('summary').click();
    await board().getByRole('button', { name: 'Add to Publish', exact: true }).click();
    await pub().getByLabel('Publication title').waitFor();
  }
  await page.goto(vite.resolvedUrls.local[0] + "?lang=en");
  await step("create native text and a real registered image page", async () => {
    await pub().getByRole("button", { name: "+ Page", exact: true }).click();
    await pub().getByLabel("Publication title").fill("Structure review");
    await pub().getByRole("button", { name: "Add text", exact: true }).click();
    await pub().getByLabel("Text", { exact: true }).fill("One building, three rooms");
    await pub().getByLabel("Project drawing / image").selectOption({ label: "Room plan.png" });
    await pub().getByRole("button", { name: "Place on page", exact: true }).click();
    await until(() => pub().locator('.publish-page img').count(), (n) => n === 1, "retained image loads");
    await until(() => api("pub-a", "/api/publication"), (x) => x.pages.length === 1 && x.title === "Structure review", "save page");
  });
  await step("drag, resize and crop remain editable after cold reopen", async () => {
    const image = pub().locator('.publish-element').filter({ has: page.locator('img') });
    const box = await image.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.down(); await page.mouse.move(box.x + box.width / 2 + 12, box.y + box.height / 2 + 10, { steps: 4 }); await page.mouse.up();
    await pub().getByLabel("width", { exact: true }).fill("760");
    await pub().getByLabel("Crop left %", { exact: true }).fill("10");
    await until(() => api("pub-a", "/api/publication"), (x) => x.pages[0].elements[1].width === 760 && x.pages[0].elements[1].crop[0] === .1, "save manipulation");
    await page.reload();
    await until(() => pub().locator('.publish-page img').count(), (n) => n === 1, "cold read image");
    assert.match(await pub().innerText(), /One building, three rooms/);
    await page.screenshot({ path: path.join(temporary, "publish-wide.png"), fullPage: true });
  });
  await step("PPTX and PDF export the saved source; switching project isolates pages", async () => {
    for (const format of ["PPTX", "PDF"]) { const pending = page.waitForEvent("download"); await pub().getByRole("button", { name: format, exact: true }).click(); const result = await pending; const content = await readFile(await result.path()); assert.ok(content.length > 1000); assert.ok(result.suggestedFilename().endsWith(format.toLowerCase())); }
    await page.locator('#project-switch').click(); await pub().getByRole("button", { name: "+ Page", exact: true }).waitFor();
    assert.equal(await pub().locator('.publish-page').count(), 0);
    await page.locator('#project-switch').click(); await pub().locator('.publish-page').waitFor();
    assert.deepEqual(await api("pub-a", "/fixture/head"), head);
  });
  await step("multiple pages reorder and narrow screen has no horizontal overflow", async () => {
    await pub().getByRole("button", { name: "+ Page", exact: true }).click(); await pub().getByRole("button", { name: "Add text", exact: true }).click();
    await pub().getByLabel("Text", { exact: true }).fill("Second page"); await pub().getByRole("button", { name: "Move page up", exact: true }).click();
    await until(() => api("pub-a", "/api/publication"), (x) => x.pages[0].elements[0].text === "Second page", "page order persisted");
    for (const width of [900, 390]) { await page.setViewportSize({ width, height: 850 }); await delay(250); assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)); await page.screenshot({ path: path.join(temporary, `publish-${width}.png`), fullPage: true }); }
  });
  await step('a delayed old GET cannot roll back a newer saved draft', async () => {
    await page.setViewportSize({ width: 1440, height: 960 });
    await page.getByRole('button', { name: 'render', exact: true }).click();
    let release, captured = false;
    const gate = new Promise((resolve) => { release = resolve; });
    const readingPublication = (url) => url.pathname === '/pub-a/api/publication';
    await page.route(readingPublication, async (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      const response = await route.fetch();
      captured = true;
      await gate;
      await route.fulfill({ response });
    });
    await page.getByRole('button', { name: 'publish', exact: true }).click();
    await until(() => captured, Boolean, 'old publication read captured');
    await pub().getByLabel('Publication title').fill('Newer saved review');
    await until(() => api('pub-a', '/api/publication'), (value) => value.title === 'Newer saved review', 'new save reaches P036');
    await until(() => pub().getByRole('status').textContent(), (value) => value === 'Saved', 'save response completes');
    release();
    await delay(250);
    assert.equal(await pub().getByLabel('Publication title').inputValue(), 'Newer saved review');
    await page.unroute(readingPublication);
    await pub().getByLabel('Publication title').fill('Subsequent edit still saves');
    await until(() => api('pub-a', '/api/publication'), (value) => value.title === 'Subsequent edit still saves', 'no stale revision remains in the UI');
    assert.equal(await pub().getByRole('alert').count(), 0);
  });
  await step('Board selection appends an exact page while preserving unsaved publication edits', async () => {
    const before = await api('pub-a', '/api/publication');
    await pub().getByLabel('Publication title').fill('Unsaved manual review title');
    await page.getByRole('button', { name: 'board', exact: true }).click();
    await sendBoardSelection();
    const appended = await until(() => api('pub-a', '/api/publication'), (value) => value.pages.length === before.pages.length + 2, 'Board page appended');
    assert.equal(appended.title, 'Unsaved manual review title');
    assert.deepEqual(appended.pages.slice(0, before.pages.length), before.pages, 'manual coordinates, crop and text are not rebuilt');
    const savedBoard = await api('pub-a', '/api/board');
    const placed = savedBoard.elements.filter((item) => item.type === 'image' && !item.isDeleted).sort((a, b) => a.y - b.y || a.x - b.x);
    assert.deepEqual(appended.pages.slice(-2).map((p) => p.elements.find((item) => item.kind === 'image').source), placed.map((item) => item.customData.sourceDocument));
    await until(() => pub().locator('.publish-page img').count(), (value) => value === 1, 'appended source image visible');
    await page.screenshot({ path: path.join(temporary, 'publish-board-handoff.png'), fullPage: true });
  });
  await step('Board handoff waits for a busy export and does not duplicate already imported pages', async () => {
    const before = await api('pub-a', '/api/publication');
    let release, captured = false;
    const gate = new Promise((resolve) => { release = resolve; });
    await page.route('**/pub-a/api/publication/export', async (route) => {
      const response = await route.fetch(); captured = true; await gate; await route.fulfill({ response });
    });
    const download = page.waitForEvent('download');
    await pub().getByRole('button', { name: 'PDF', exact: true }).click();
    await until(() => captured, Boolean, 'export response delayed');
    await page.getByRole('button', { name: 'board', exact: true }).click();
    const handed = page.waitForResponse((response) => response.url().includes('/pub-a/api/publication/from-board') && response.request().method() === 'POST');
    await sendBoardSelection();
    assert.equal(await pub().getByRole('button', { name: 'PDF', exact: true }).isDisabled(), true, 'export still owns the in-flight operation');
    release(); await download;
    const response = await handed;
    assert.equal(response.status(), 200, await response.text());
    const after = await api('pub-a', '/api/publication');
    assert.deepEqual(after.pages, before.pages, 'retrying the same retained Board selection is idempotent');
    await until(() => pub().getByRole('button', { name: 'PDF', exact: true }).isEnabled(), Boolean, 'handoff response applied');
    assert.equal(await pub().locator('.publish-page').count(), 1, 'idempotent handoff still shows an existing page');
    assert.deepEqual(await api('pub-a', '/fixture/head'), head);
    await page.unroute('**/pub-a/api/publication/export');
  });
  await step('a failed autosave keeps the draft and does not reimport deleted Board pages', async () => {
    const before = await api('pub-a', '/api/publication');
    await pub().getByRole('button', { name: 'Delete page', exact: true }).click();
    await until(() => api('pub-a', '/api/publication'), (value) => value.pages.length === before.pages.length - 1, 'manual page deletion saved');
    const kept = (await api('pub-a', '/api/publication')).pages;
    const savingPublication = (url) => url.pathname === '/pub-a/api/publication';
    let failed = false;
    await page.route(savingPublication, async (route) => {
      if (route.request().method() !== 'PUT') return route.continue();
      failed = true;
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Temporary save failure' }) });
    });
    await pub().getByLabel('Publication title').fill('Retain this after failure');
    await until(() => failed, Boolean, 'save refused');
    await pub().getByRole('button', { name: 'Retry save', exact: true }).waitFor();
    assert.equal(await pub().getByLabel('Publication title').inputValue(), 'Retain this after failure');
    await page.unroute(savingPublication);
    await pub().getByRole('button', { name: 'Retry save', exact: true }).click();
    await until(() => pub().getByRole('status').textContent(), (value) => value === 'Saved', 'retry acknowledged');
    assert.equal((await api('pub-a', '/api/publication')).title, 'Retain this after failure');
    await delay(300);
    assert.deepEqual((await api('pub-a', '/api/publication')).pages, kept, 'retry must not replay the previous Board import');
    assert.deepEqual(await api('pub-a', '/fixture/head'), head);
  });
  await step('typing during a delayed autosave keeps the newest text and survives project close/reopen', async () => {
    let release, captured = false, writes = 0;
    const gate = new Promise((resolve) => { release = resolve; });
    const savingPublication = (url) => url.pathname === '/pub-a/api/publication';
    await page.route(savingPublication, async (route) => {
      if (route.request().method() !== 'PUT') return route.continue();
      writes++;
      const response = await route.fetch();
      if (writes === 1) { captured = true; await gate; }
      await route.fulfill({ response });
    });
    await pub().getByLabel('Publication title').fill('First typing pause');
    await until(() => captured, Boolean, 'autosave request paused');
    await pub().getByLabel('Publication title').fill('Newest edit during save');
    assert.equal(writes, 1, 'writes remain serialized');
    release();
    await until(() => pub().getByRole('status').textContent(), (value) => value === 'Saved', 'latest edit acknowledged');
    assert.equal((await api('pub-a', '/api/publication')).title, 'Newest edit during save');
    await page.unroute(savingPublication);
    await pub().getByLabel('Publication title').fill('Final edit before project switch');
    await page.locator('#project-switch').click();
    await until(() => api('pub-a', '/api/publication'), (value) => value.title === 'Final edit before project switch', 'unmount flush');
    await page.locator('#project-switch').click();
    await until(() => pub().getByLabel('Publication title').inputValue(), (value) => value === 'Final edit before project switch', 'cold project read');
    assert.equal(await pub().getByRole('button', { name: 'Save', exact: true }).count(), 0);
  });
  await step('immediate project reopen cannot adopt a GET older than its pending save', async () => {
    let releaseWrite, releaseRead, writeCaptured = false, readCaptured = false;
    const writing = new Promise((resolve) => { releaseWrite = resolve; });
    const reading = new Promise((resolve) => { releaseRead = resolve; });
    const publicationRoute = (url) => url.pathname === '/pub-a/api/publication';
    await page.route(publicationRoute, async (route) => {
      if (route.request().method() === 'PUT') { writeCaptured = true; await writing; return route.continue(); }
      if (route.request().method() === 'GET') {
        const response = await route.fetch(); readCaptured = true; await reading; return route.fulfill({ response });
      }
      return route.continue();
    });
    await pub().getByLabel('Publication title').fill('Pending across immediate reopen');
    await until(() => writeCaptured, Boolean, 'PUT held before commit');
    await page.locator('#project-switch').click();
    await pub().getByRole('button', { name: '+ Page', exact: true }).waitFor();
    await page.locator('#project-switch').click();
    await until(() => readCaptured, Boolean, 'old GET captured on new surface');
    assert.equal(await pub().getByLabel('Publication title').inputValue(), 'Pending across immediate reopen');
    releaseWrite();
    await until(() => pub().getByRole('status').textContent(), (value) => value === 'Saved', 'pending save acknowledged after remount');
    releaseRead(); await delay(250);
    assert.equal(await pub().getByLabel('Publication title').inputValue(), 'Pending across immediate reopen');
    assert.equal(await pub().getByLabel('Project drawing / image').locator('option', { hasText: 'Room plan.png' }).count(), 1, 'source list survives a rejected old publication read');
    await page.unroute(publicationRoute);
    await pub().getByLabel('Publication title').fill('Next edit has the correct saved base');
    await until(() => pub().getByRole('status').textContent(), (value) => value === 'Saved', 'CAS base retained');
    assert.equal((await api('pub-a', '/api/publication')).title, 'Next edit has the correct saved base');
  });
  await step('failed autosave survives project close until it can be retried', async () => {
    const savingPublication = (url) => url.pathname === '/pub-a/api/publication';
    await page.route(savingPublication, (route) => route.request().method() === 'PUT'
      ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Offline for close' }) }) : route.continue());
    await pub().getByLabel('Publication title').fill('Recover this after closing project');
    await pub().getByRole('button', { name: 'Retry save', exact: true }).waitFor();
    await page.locator('#project-switch').click();
    await pub().getByRole('button', { name: '+ Page', exact: true }).waitFor();
    await page.locator('#project-switch').click();
    await pub().getByRole('button', { name: 'Retry save', exact: true }).waitFor();
    assert.equal(await pub().getByLabel('Publication title').inputValue(), 'Recover this after closing project');
    await page.unroute(savingPublication);
    await pub().getByRole('button', { name: 'Retry save', exact: true }).click();
    await until(() => pub().getByRole('status').textContent(), (value) => value === 'Saved', 'reopened failure recovered');
  });
  await step('Board import completes across immediate project close/reopen without a stale editing base', async () => {
    let release, captured = false;
    const gate = new Promise((resolve) => { release = resolve; });
    await page.route('**/pub-a/api/publication/from-board', async (route) => { captured = true; await gate; await route.continue(); });
    const before = await api('pub-a', '/api/publication');
    await page.getByRole('button', { name: 'board', exact: true }).click();
    await sendBoardSelection();
    await until(() => captured, Boolean, 'Board POST pending');
    await page.locator('#project-switch').click();
    await pub().getByRole('button', { name: '+ Page', exact: true }).waitFor();
    await page.locator('#project-switch').click();
    await pub().getByLabel('Publication title').waitFor();
    assert.equal(await pub().getByLabel('Publication title').isDisabled(), true, 'import owns the document until acknowledgement');
    release();
    await until(() => pub().getByLabel('Publication title').isEnabled(), Boolean, 'import delivered to remounted surface');
    await page.unroute('**/pub-a/api/publication/from-board');
    const imported = await api('pub-a', '/api/publication');
    assert.ok(imported.pages.length >= before.pages.length);
    await pub().getByLabel('Publication title').fill('Edit after reopened Board import');
    await until(() => pub().getByRole('status').textContent(), (value) => value === 'Saved', 'imported CAS base available');
    assert.equal((await api('pub-a', '/api/publication')).title, 'Edit after reopened Board import');
    assert.deepEqual((await api('pub-a', '/api/publication')).pages, imported.pages);
  });
  await step('a Board import that fails after project close remains retryable on reopen', async () => {
    let release, captured = false;
    const gate = new Promise((resolve) => { release = resolve; });
    await page.route('**/pub-a/api/publication/from-board', async (route) => {
      captured = true; await gate;
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Delayed import failure' }) });
    });
    await page.getByRole('button', { name: 'board', exact: true }).click();
    await sendBoardSelection(); await until(() => captured, Boolean, 'import pending before close');
    await page.locator('#project-switch').click();
    await pub().getByRole('button', { name: '+ Page', exact: true }).waitFor();
    await page.locator('#project-switch').click();
    await pub().getByLabel('Publication title').waitFor(); release();
    await pub().getByRole('button', { name: 'Retry save', exact: true }).waitFor();
    await page.unroute('**/pub-a/api/publication/from-board');
    const response = page.waitForResponse((reply) => reply.url().endsWith('/api/publication/from-board') && reply.request().method() === 'POST');
    await pub().getByRole('button', { name: 'Retry save', exact: true }).click();
    assert.equal((await response).status(), 200);
    await until(() => pub().getByRole('status').textContent(), (value) => value === 'Saved', 'retained Board command retried');
    assert.equal(await pub().getByRole('alert').count(), 0);
  });
  await step('repeatable layout separates multiple text boxes from images and refuses overfull content', async () => {
    await pub().getByRole('button', { name: '+ Page', exact: true }).click();
    for (const text of ['Title block', 'Second note', 'Third note']) {
      await pub().getByRole('button', { name: 'Add text', exact: true }).click();
      await pub().getByLabel('Text', { exact: true }).fill(text);
    }
    await pub().getByLabel('Project drawing / image').selectOption({ label: 'Room plan.png' });
    await pub().getByRole('button', { name: 'Place on page', exact: true }).click();
    await pub().getByRole('button', { name: 'Apply image + title layout', exact: true }).click();
    await until(() => pub().getByRole('status').textContent(), (value) => value === 'Saved', 'rule saved');
    const layout = (await api('pub-a', '/api/publication')).pages.at(-1);
    const texts = layout.elements.filter((item) => item.kind === 'text'), picture = layout.elements.find((item) => item.kind === 'image');
    assert.ok(texts.every((item) => item.y + item.height <= picture.y));
    for (let i = 1; i < texts.length; ++i) assert.ok(texts[i-1].y + texts[i-1].height < texts[i].y);
    await pub().getByRole('button', { name: 'Apply image + title layout', exact: true }).click();
    await until(() => pub().getByRole('status').textContent(), (value) => value === 'Saved', 'repeat rule saved');
    assert.deepEqual((await api('pub-a', '/api/publication')).pages.at(-1), layout);
    await pub().getByLabel('width', { exact: true }).fill('320');
    await pub().getByRole('button', { name: 'Center horizontally', exact: true }).click();
    assert.equal(await pub().getByLabel('x', { exact: true }).inputValue(), '320');
    await pub().locator('.publish-element').filter({ hasText: 'Third note' }).click();
    await pub().getByLabel('height', { exact: true }).fill('220');
    await until(() => pub().getByRole('status').textContent(), (value) => value === 'Saved', 'large text box saved');
    const before = (await api('pub-a', '/api/publication')).pages;
    await pub().getByRole('button', { name: 'Apply image + title layout', exact: true }).click();
    await pub().getByRole('alert').filter({ hasText: 'not enough room' }).waitFor();
    assert.deepEqual((await api('pub-a', '/api/publication')).pages, before, 'refused rule preserves authored placement');
    await pub().getByLabel('height', { exact: true }).fill('75');
    await pub().getByRole('button', { name: 'Apply image + title layout', exact: true }).click();
    await until(() => pub().getByRole('status').textContent(), (value) => value === 'Saved', 'layout repaired');
  });
  await step('image and PDF previews preserve transparency when composed over other content', async () => {
    for (const body of await api('pub-a', '/fixture/transparency')) {
      const result = await fetch(origins['pub-a'] + '/api/documents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      assert.equal(result.status, 201);
    }
    await page.getByRole('button', { name: 'render', exact: true }).click();
    await page.getByRole('button', { name: 'publish', exact: true }).click();
    await pub().getByLabel('Project drawing / image').locator('option', { hasText: 'Transparent.pdf' }).waitFor({ state: 'attached' });
    await pub().getByRole('button', { name: '+ Page', exact: true }).click();
    for (const name of ['Transparent.png', 'Transparent.pdf']) {
      await pub().getByLabel('Project drawing / image').selectOption({ label: name });
      await pub().getByRole('button', { name: 'Place on page', exact: true }).click();
      const image = pub().locator('.publish-element.selected img');
      await image.waitFor();
      const alpha = await image.evaluate((image) => {
        const canvas = document.createElement('canvas'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
        const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
        return [context.getImageData(5, 5, 1, 1).data[3], context.getImageData(Math.floor(canvas.width * .75), Math.floor(canvas.height * .5), 1, 1).data[3]];
      });
      assert.equal(alpha[0], 0, name + ' keeps the background transparent');
      assert.ok(alpha[1] >= 126 && alpha[1] <= 129, name + ' keeps partial opacity');
    }
    await until(() => pub().getByRole('status').textContent(), (value) => value === 'Saved', 'transparent placements saved');
  });
  await step('saved pages and identical PDF reopen after an actual Runtime restart', async () => {
    const before = await api('pub-a', '/api/publication');
    const exportBody = { projectId: 'pub-a', revisionSha256: before.revisionSha256, format: 'pdf' };
    const output = async () => Buffer.from(await (await fetch(origins['pub-a'] + '/api/publication/export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(exportBody) })).arrayBuffer());
    const original = await output();
    const running = processes.findLast((p) => p.id === 'pub-a');
    const exited = new Promise((resolve) => running.child.once('exit', resolve));
    running.child.kill(); await exited;
    await startRuntime('pub-a', running.port);
    await page.reload(); await pub().getByLabel('Publication title').waitFor();
    assert.deepEqual(await api('pub-a', '/api/publication'), before);
    assert.deepEqual(await output(), original);
    assert.deepEqual(await api('pub-a', '/fixture/head'), head);
  });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed, temporary }));
} catch (error) {
  await page?.screenshot({ path: path.join(temporary, 'failure.png'), fullPage: true }).catch(() => {});
  console.error(`FAIL ${current}; ${temporary}`); console.error(errors); throw error;
} finally { await browser?.close(); await vite?.close(); for (const p of processes) p.child.kill(); }

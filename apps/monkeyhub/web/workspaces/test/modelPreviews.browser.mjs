/** Retained model previews through the real viewport, Runtime and project storage. */
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
const temporary = await mkdtemp(path.join(tmpdir(), "monkeyhub-model-preview-browser-"));
const pythonEnv = { ...process.env, PYTHONUTF8: "1", PYTHONPATH: [repoRoot, apiRoot].join(path.delimiter) };
const passed = [], errors = [], captures = [];
let browser, page, server, child, origin, current, runtimeLog = "";
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(read, accepts, label) {
  const deadline = Date.now() + 30000; let value;
  do { value = await read(); if (accepts(value)) return value; await delay(100); } while (Date.now() < deadline);
  assert.fail(`${label}: ${JSON.stringify(value)}\n${runtimeLog}`);
}
async function freePort() {
  const probe = createHttpServer(); await new Promise(resolve => probe.listen(0, "127.0.0.1", resolve));
  const port = probe.address().port; await new Promise(resolve => probe.close(resolve)); return port;
}
const pythonSource = `
import sys, base64, hashlib, rhino3dm
from pathlib import Path
import uvicorn
from tests.support import make_project, PROJECT_ID, REFERENCE_RUN_ID, runner_state_digest, retain_runner_receipt
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.application.artifacts import register_model_asset
from archflow_studio_api.application.binding import bound_project
root, port = Path(sys.argv[1]), int(sys.argv[2])
repo, _ = make_project(root)
second = repo.create_run('preview-other-run')
retain_runner_receipt(repo, second, design_state_digest=runner_state_digest(repo, second.run_id))
third = repo.create_run('preview-switch-run')
retain_runner_receipt(repo, third, design_state_digest=runner_state_digest(repo, third.run_id))
app = create_app(StudioSettings(project_dir=root/PROJECT_ID, reference_run=REFERENCE_RUN_ID, cad_export='off'))
for run, filename in [(REFERENCE_RUN_ID, 'a'),(second.run_id, 'b'),(third.run_id,'c')]:
    raw = (Path('tests/fixtures') / ('model-source-'+('a' if filename=='c' else filename)+'.3dm')).read_bytes()
    if filename == 'c':
        model = rhino3dm.File3dm.FromByteArray(raw)
        model.Strings['preview-fixture'] = 'third'
        raw = base64.b64decode(model.Encode())
    register_model_asset(bound_project(app.state), run, runner_state_digest(repo, run), 'model-'+filename+'.3dm', base64.b64encode(raw).decode())
@app.get('/fixture/head')
def head(): return hashlib.sha256(repo.layout.head.read_bytes()).hexdigest()
uvicorn.run(app,host='127.0.0.1',port=port,log_level='warning')
`;
const fixture = `
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ProjectWorkspace} from '/src/app/ProjectWorkspace';
import {ModelThumbnail} from '/src/features/artifacts/ModelThumbnail';
import {retainModelPreview} from '/src/features/artifacts/useRetainedModelPreview';
import {UserPreferencesProvider} from '/test/TestProviders';
import '/src/styles.css';
import '/@fs/${path.resolve(repoRoot, "apps/shared-web/src/base.css").replaceAll("\\", "/")}';
window.captureRace = async (phase) => {
 const calls=[]; let current=phase!=='dirty';
 const source={runId:'r',stateDigest:'s',assetSha256:'a'};
 const studio={modelPreview:async()=>{calls.push('read');if(phase==='read')current=false;return phase==='exists'?{}:null;},
  capture:async(...args)=>{calls.push('save');return {};}};
 const saved=await retainModelPreview(studio,source,async()=>{calls.push('pixels');if(phase==='pixels')current=false;return new Blob(['png']);},()=>current);
 return {calls,saved};
};
function App(){const [workspace,setWorkspace]=useState(new URLSearchParams(location.search).get('workspace')==='board'?'board':'arch');
 return <UserPreferencesProvider baseUrl={location.origin}>
  <ProjectWorkspace expectedProjectId="demo-project" workspace={workspace} onWorkspaceChange={setWorkspace}/>
 </UserPreferencesProvider>;}
createRoot(document.getElementById('root')).render(<App/>);
`;
async function api(route) { const response = await fetch(origin + route); assert.ok(response.ok, `${route}: ${response.status}`); return response.json(); }
async function step(name, action) { current = name; await action(); passed.push(name); console.log(`PASS ${name}`); }
const surface = () => page.locator('[data-project-surface="arch"]:visible');
const preview = source => api(`/api/model-assets/${source.assetSha256}/preview?runId=${source.runId}&stateDigest=${source.stateDigest}`);
async function versions() {
  const toggle = page.locator('.project-bar .stage__versions-toggle');
  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
  const panel = surface().getByRole('region', { name: 'Model versions', exact: true });
  await panel.locator('.vcard__export[aria-pressed="true"]').first().waitFor({state:'attached'});
  const summary = panel.locator('summary').filter({ hasText: '已有模型与历史运行' });
  await summary.waitFor();
  if (!(await summary.evaluate(el => el.parentElement.open))) await summary.click();
  return panel;
}
try {
  const port = await freePort(); origin = `http://127.0.0.1:${port}`;
  child = spawn(process.env.PYTHON ?? 'python', ['-c', pythonSource, temporary, String(port)],
    { cwd: apiRoot, env: pythonEnv, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => { runtimeLog = (runtimeLog + chunk).slice(-8000); });
  await until(() => fetch(origin + '/api/health').then(r => r.ok).catch(() => false), Boolean, 'Runtime ready');
  const artifacts = (await api('/api/artifacts')).artifacts;
  const a = artifacts.find(row => row.fileName === 'model-a.3dm'), b = artifacts.find(row => row.fileName === 'model-b.3dm');
  const c = artifacts.find(row => row.fileName === 'model-c.3dm');
  assert.ok(a?.modelSource && b?.modelSource && c?.modelSource);
  const head = await api('/fixture/head');
  assert.equal(await preview(a.modelSource), null); assert.equal(await preview(b.modelSource), null);
  server = await createServer({ root: webRoot, configFile: false, publicDir: '../.generated/public', cacheDir: path.join(temporary, 'vite'), logLevel: 'error',
    resolve: { dedupe: ['react','react-dom'] }, optimizeDeps: { include: ['react','react-dom/client','react/jsx-runtime','react/jsx-dev-runtime'] },
    server: { host: '127.0.0.1', port: 0, strictPort: true, proxy: { '/api': origin, '/fixture': origin } },
    plugins: [{ name: 'model-previews-fixture', resolveId(id) { if (id === '/model-previews-fixture.tsx') return path.join(webRoot,'model-previews-fixture.tsx').replaceAll('\\','/'); },
      load(id) { if (id === path.join(webRoot,'model-previews-fixture.tsx').replaceAll('\\','/')) return fixture; },
      configureServer(vite) { vite.middlewares.use((request,response,next) => {
        if (request.url?.split('?')[0] !== '/') return next(); response.setHeader('content-type','text/html');
        response.end('<html><head><meta charset="utf-8"/><style>html,body,#root{height:100%;margin:0}*{box-sizing:border-box}</style></head><body><div id="root" class="project-workspace"></div><script type="module" src="/model-previews-fixture.tsx"></script></body></html>');
      }); },
    }],
  });
  await server.listen();
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
  browser = await chromium.launch({ headless: true, channel: 'chrome' });
  page = await browser.newPage({ viewport: { width: 1440, height: 1050 } }); page.setDefaultTimeout(20000);
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/api/captures')) captures.push(request.postDataJSON()); });
  await page.goto(server.resolvedUrls.local[0]);
  await step('stable real viewport retains its exact source asynchronously', async () => {
    await surface().locator('.viewport-host').waitFor();
    const image = await until(() => preview(a.modelSource), Boolean, 'first preview retained');
    assert.deepEqual(image.modelSource, a.modelSource);
    assert.equal(await api('/fixture/head'), head);
    assert.deepEqual((await api('/api/worktrees')).representations, []);
  });
  await step('unseen version falls back to icon, viewed version shows retained PNG', async () => {
    const panel = await versions();
    await panel.locator('.vcard__export').filter({hasText:'model-a.3dm'}).locator('.model-thumbnail img').waitFor();
    const next = panel.locator('.vcard__export').filter({hasText:'model-b.3dm'});
    assert.equal(await next.locator('.model-thumbnail svg').count(),1);
    assert.equal(await next.locator('.model-thumbnail img').count(),0);
    await next.click();
    await until(() => preview(b.modelSource), Boolean, 'second exact preview retained');
    await next.locator('.model-thumbnail img').waitFor();
    assert.deepEqual(captures.map(row => row.modelSource), [a.modelSource,b.modelSource]);
  });
  await step('fresh browser context reads saved previews without local cache or recapturing', async () => {
    await page.close();
    page = await browser.newPage({ viewport:{width:1100,height:850} });
    page.on('pageerror',error=>errors.push(String(error)));
    const writes=[];
    page.on('request',request=>{if(request.method()==='POST' && request.url().endsWith('/api/captures')){writes.push(request.url());captures.push(request.postDataJSON());}});
    await page.goto(server.resolvedUrls.local[0]);
    await surface().locator('.viewport-host').waitFor();
    const panel=await versions();
    for(const fileName of ['model-b.3dm','model-a.3dm']){
      const version=panel.locator('.vcard__export').filter({hasText:fileName});
      await version.scrollIntoViewIfNeeded();
      await version.locator('.model-thumbnail img').waitFor();
    }
    assert.deepEqual(writes,[]);
    assert.equal(await api('/fixture/head'),head);
    await page.screenshot({path:path.join(temporary,'retained-previews.png'),fullPage:true});
  });
  await step('switching the real App while preview lookup is pending never saves the former view', async () => {
    let blockedReads=0, release;
    const gate=new Promise(resolve=>{release=resolve;});
    const pattern='**/api/model-assets/*/preview?*';
    const hold=async route=>{
      if(new URL(route.request().url()).searchParams.get('runId')===c.runId){blockedReads+=1;await gate;}
      await route.continue();
    };
    await page.route(pattern,hold);
    const panel=await versions();
    const third=panel.locator('.vcard__export').filter({hasText:'model-c.3dm'});
    await third.click();
    await until(()=>third.getAttribute('aria-pressed'),value=>value==='true','third model installed');
    await until(()=>blockedReads,value=>value>=1,'preview read suspended');
    // Allow the App's post-load capture lookup to join the thumbnail lookup.
    await delay(350);
    const original=panel.locator('.vcard__export').filter({hasText:'model-a.3dm'});
    await original.click();
    await until(()=>original.getAttribute('aria-pressed'),value=>value==='true','original model restored');
    release();
    await delay(400);
    await page.unroute(pattern,hold);
    assert.equal(await preview(c.modelSource),null);
    assert.deepEqual(captures.map(row=>row.modelSource),[a.modelSource,b.modelSource]);
  });
  await step('unreadable retained PNG returns to the model icon', async () => {
    const document=await preview(b.modelSource);
    await page.route(`**/api/documents/${document.assetSha256}/bytes?*`,route=>route.fulfill({status:200,contentType:'image/png',body:'damaged image'}));
    const unreadable=page.waitForResponse(response=>response.url().includes(`/api/documents/${document.assetSha256}/bytes`));
    await page.reload();
    await surface().locator('.viewport-host').waitFor();
    const panel=await versions(), second=panel.locator('.vcard__export').filter({hasText:'model-b.3dm'});
    await second.scrollIntoViewIfNeeded();
    await unreadable;
    await second.locator('.model-thumbnail svg').waitFor();
    assert.equal(await second.locator('.model-thumbnail img').count(),0);
    assert.equal(await api('/fixture/head'),head);
  });
  await step('dirty or switched source cannot register stale pixels; existing preview skips encoding', async () => {
    for(const [phase,calls] of [['dirty',[]],['read',['read']],['pixels',['read','pixels']],['exists',['read']]]){
      assert.deepEqual(await page.evaluate(phase=>window.captureRace(phase),phase),{calls,saved:false});
    }
    assert.deepEqual(await page.evaluate(()=>window.captureRace('stable')),{calls:['read','pixels','save'],saved:true});
    assert.deepEqual(errors,[]);
  });
  await step('model thumbnails leave Board unchanged until explicitly placed, including after reopening', async () => {
    const image = await preview(a.modelSource), before = await api('/api/board');
    const boardWrites = [];
    const recordWrite = request => {
      if (request.method() === 'PUT' && new URL(request.url()).pathname === '/api/board') boardWrites.push(request.postDataJSON());
    };
    page.on('request', recordWrite);
    const boardUrl = `${server.resolvedUrls.local[0]}?workspace=board`;
    const waitForBoard = async () => {
      await page.locator('[data-project-surface="board"]:visible .monkeyboard-canvas canvas').first().waitFor();
      await page.locator('.monkeyboard-initializing').waitFor({ state: 'hidden' });
    };
    const waitForDiscovery = async () => {
      // Observe the real five-second discovery tick and its save debounce.
      await page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === '/api/documents');
      await delay(1000);
    };
    await page.goto(boardUrl); await waitForBoard();
    await page.getByRole('button', { name: 'Project documents', exact: true }).click();
    const source = page.locator('.monkeyboard-source').filter({ hasText: image.fileName });
    await source.waitFor();
    await waitForDiscovery();
    assert.deepEqual(await api('/api/board'), before, 'automatic preview discovery must preserve the saved scene and revision');
    assert.deepEqual(boardWrites, [], 'automatic previews must not trigger a Board save');

    await source.getByRole('button', { name: 'Add page', exact: true }).click();
    const placed = await until(() => api('/api/board'), board => board.elements.some(element =>
      !element.isDeleted && element.type === 'image' && element.customData?.sourceDocument?.assetSha256 === image.assetSha256), 'explicit thumbnail placement saved');
    const images = placed.elements.filter(element => !element.isDeleted && element.type === 'image');
    assert.equal(images.length, 1, 'placing one thumbnail must not receive other model previews');
    assert.deepEqual(images[0].customData.sourceDocument, {
      runId: image.runId, assetSha256: image.assetSha256, revisionRef: null, pageIndex: 0,
    });
    assert.ok(boardWrites.length > 0);
    boardWrites.length = 0;
    await page.reload(); await waitForBoard(); await waitForDiscovery();
    assert.deepEqual(await api('/api/board'), placed, 'reopening must retain the explicitly placed preview without adding other thumbnails');
    assert.deepEqual(boardWrites, [], 'reopening a saved preview must not produce another scene revision');
    assert.equal(await api('/fixture/head'), head);
    assert.deepEqual(errors, []);
    page.off('request', recordWrite);
  });
  console.log(JSON.stringify({passed,screenshots:temporary}));
} catch(error) {
  await page?.screenshot({path:path.join(temporary,'failure.png'),fullPage:true}).catch(()=>{});
  console.error(`FAIL ${current}; ${temporary}\n${runtimeLog}`); console.error(errors); throw error;
} finally {
  await browser?.close(); await server?.close();
  if(child?.exitCode===null){const stopped=new Promise(resolve=>child.once('exit',resolve));child.kill();await stopped;}
}

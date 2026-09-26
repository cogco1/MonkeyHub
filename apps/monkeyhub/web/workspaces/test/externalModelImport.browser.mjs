/** External 3DM import through the real Runtime, P036, SDK and viewport.
 * Small public fixtures test identity and interaction, not large-model performance.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, readFile, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const repoRoot = path.resolve(webRoot, "../../../..");
const apiRoot = path.join(repoRoot, "apps/archflow-studio/api");
const temporary = await mkdtemp(path.join(tmpdir(), "monkeyhub-external-import-browser-"));
const pythonEnv = { ...process.env, PYTHONUTF8: "1", PYTHONPATH: [repoRoot, apiRoot].join(path.delimiter) };
const passed = [], errors = [], requests = [], resourceRequests = [], observations = [];
let browser, page, server, child, origin, current, runtimeLog = "";
const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));
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
import sys, hashlib
from pathlib import Path
import uvicorn
from tests.support import make_empty_project, PROJECT_ID
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
root, port = Path(sys.argv[1]), int(sys.argv[2])
repo = make_empty_project(root)
app = create_app(StudioSettings(project_dir=root/PROJECT_ID, cad_export='off', monitor_dir=root/'monitor'))
@app.get('/fixture/metrics')
def metrics():
    records = [__import__('json').loads(p.read_text(encoding='utf-8')) for p in repo.layout.runs.rglob('*.json') if p.parent.name=='records']
    return {'head':hashlib.sha256(repo.layout.head.read_bytes()).hexdigest(), 'recordSchemas':[p.get('schema') for p in records]}
uvicorn.run(app,host='127.0.0.1',port=port,log_level='warning')
`;
const fixture = `
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ProjectWorkspace} from '/src/app/ProjectWorkspace';
import {UserPreferencesProvider} from '/test/TestProviders';
import '/src/styles.css';
import '/@fs/${path.resolve(repoRoot, "apps/shared-web/src/base.css").replaceAll("\\", "/")}';
function App(){const [workspace,setWorkspace]=useState('arch');
 return <UserPreferencesProvider baseUrl={location.origin}>
  <ProjectWorkspace expectedProjectId="demo-project" workspace={workspace} onWorkspaceChange={setWorkspace}
    onDesignContextChange={value=>{window.externalImportContext=value;}}/>
 </UserPreferencesProvider>;}
createRoot(document.getElementById('root')).render(<App/>);
`;
async function api(route) { const result = await fetch(origin + route); assert.ok(result.ok, `${route}: ${result.status}`); return result.json(); }
async function step(name, action) { current = name; await action(); passed.push(name); console.log(`PASS ${name}`); }
const surface = () => page.locator('[data-project-surface="arch"]:visible');
const viewport = () => surface().locator('.viewport-host');
const rows = async () => (await api('/api/artifacts')).artifacts;
async function drop(bytes, name) {
  const transfer = await page.evaluateHandle(({ bytes, name }) => {
    const transfer = new DataTransfer(); transfer.items.add(new File([Uint8Array.from(bytes)], name)); return transfer;
  }, { bytes: [...bytes], name });
  try { await viewport().dispatchEvent('drop', { dataTransfer: transfer }); } finally { await transfer.dispose(); }
}
async function versions() {
  const toggle = page.locator('.project-bar .stage__versions-toggle');
  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
  const panel = surface().getByRole('region', { name: 'Model versions', exact: true });
  const summary = panel.locator('summary').filter({ hasText: '已有模型与历史运行' });
  await summary.waitFor();
  if (!(await summary.evaluate(el => el.parentElement.open))) await summary.click();
  return panel;
}
async function shown(sha) {
  const artifact = (await rows()).find(row => row.sha256 === sha);
  assert.ok(artifact);
  await until(async () => {
    const panel = await versions();
    const card = panel.locator('.vcard__export').filter({ hasText: artifact.fileName });
    return { selected: await card.count() ? await card.getAttribute('aria-pressed') : null,
      disabled: await card.count() ? await card.isDisabled() : true,
      loading: await surface().locator('.viewport-state--loading').count(),
      viewportError: await surface().locator('.viewport-state--error').count(),
      alert: await surface().getByRole('alert').allTextContents() };
  }, value => value.selected === 'true' && !value.disabled && value.loading === 0 && value.viewportError === 0,
  'retained model visible');
}
try {
  for (const relative of ['rhino3dm/rhino3dm.js', 'rhino3dm/rhino3dm.wasm', 'nurbsFallback.worker.js']) {
    assert.ok((await stat(path.join(webRoot, '../.generated/public', relative))).size > 0,
      `${relative} is missing; run npm run sync before this browser smoke`);
  }
  const port = await freePort(); origin = `http://127.0.0.1:${port}`;
  child = spawn(process.env.PYTHON ?? 'python', ['-c', pythonSource, temporary, String(port)],
    { cwd: apiRoot, env: pythonEnv, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => { runtimeLog = (runtimeLog + chunk).slice(-8000); });
  await until(() => fetch(origin + '/api/health').then(r => r.ok).catch(() => false), Boolean, 'Runtime ready');
  server = await createServer({ root: webRoot, configFile: false, publicDir: '../.generated/public', cacheDir: path.join(temporary, 'vite'), logLevel: 'error',
    resolve: { dedupe: ['react','react-dom'] }, optimizeDeps: { include: ['react','react-dom/client','react/jsx-runtime','react/jsx-dev-runtime'] },
    server: { host: '127.0.0.1', port: 0, strictPort: true, proxy: { '/api': origin, '/fixture': origin } },
    plugins: [{ name: 'external-import-fixture', resolveId(id) { if (id === '/external-import-fixture.tsx') return path.join(webRoot,'external-import-fixture.tsx').replaceAll('\\','/'); },
      load(id) { if (id === path.join(webRoot,'external-import-fixture.tsx').replaceAll('\\','/')) return fixture; },
      configureServer(vite) { vite.middlewares.use((request,response,next) => {
        if (request.url !== '/') return next(); response.setHeader('content-type','text/html');
        response.end('<html><head><meta charset="utf-8"/><style>html,body,#root{height:100%;margin:0}*{box-sizing:border-box}</style></head><body><div id="root" class="project-workspace"></div><script type="module" src="/external-import-fixture.tsx"></script></body></html>');
      }); },
    }],
  });
  await server.listen();
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
  browser = await chromium.launch({ headless: true, channel: 'chrome' });
  page = await browser.newPage({ viewport: { width: 1440, height: 1050 } }); page.setDefaultTimeout(20000);
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => {
    if (request.url().includes('/api/')) requests.push({url:request.url(), method:request.method()});
    if (/rhino3dm|nurbsFallback\.worker|\.wasm(?:\?|$)/.test(request.url()))
      resourceRequests.push({url:request.url(), method:request.method(), status:null});
  });
  page.on('requestfailed', request => {
    const row=resourceRequests.findLast(item=>item.url===request.url() && item.status===null);
    if(row) row.status=`FAILED: ${request.failure()?.errorText ?? 'unknown'}`;
  });
  page.on('response', async response => {
    const row=resourceRequests.findLast(item=>item.url===response.url() && item.status===null);
    if(row) row.status=response.status();
    if (response.url().includes('/api/') && response.status() >= 400) console.error(`${response.status()} ${response.url()} ${await response.text().catch(()=> '')}`);
  });
  await page.goto(server.resolvedUrls.local[0], {waitUntil:'domcontentloaded'});
  await viewport().waitFor();
  await until(() => page.evaluate(() => window.externalImportContext), value => value?.designContext?.stateDigest, 'authored editing context ready');
  const beforeContext = await page.evaluate(() => window.externalImportContext);
  const head = (await api('/fixture/metrics')).head;
  const a = await readFile(path.join(apiRoot, 'tests/fixtures/model-source-a.3dm'));
  const b = await readFile(path.join(apiRoot, 'tests/fixtures/model-source-b.3dm'));
  let first, second;
  await step('drop original 3DM, retain exact bytes and display without semantic authority', async () => {
    await drop(a, 'external-a.3dm');
    first = (await until(rows, value => value.length === 1, 'external registration'))[0];
    await shown(first.sha256);
    observations.push({ operation:'first small-fixture import functional smoke', operationId:null, timing:null,
      inputBytes:a.length, sourceVisible:'unknown', firstVisibleFrame:'not measured',
      reason:'the browser smoke has no operation-bound start/end event window' });
    assert.equal(first.representation, 'external'); assert.equal(first.modelSource,null); assert.equal(first.designStateDigest,null); assert.equal(first.sourceStageRef,null);
    const downloaded = Buffer.from(await (await fetch(origin+`/api/artifacts/${first.sha256}/bytes`)).arrayBuffer()); assert.deepEqual(downloaded,a);
    assert.equal((await api('/fixture/metrics')).head,head);
    await page.screenshot({ path:path.join(temporary,'external-first.png'), fullPage:true });
  });
  await step('second source is a distinct retained version; repeated bytes do not duplicate', async () => {
    await drop(b,'external-b.3dm'); second=(await until(rows,value=>value.length===2,'second retained source')).find(row=>row.sha256!==first.sha256);
    await shown(second.sha256); await drop(b,'renamed-b.3dm'); await shown(second.sha256);
    assert.equal((await rows()).length,2); assert.equal((await rows()).find(row=>row.sha256===second.sha256).receiptRef,second.receiptRef);
    assert.equal((await api('/fixture/metrics')).head,head);
  });
  await step('failed import preserves the previous view and writes no source', async () => {
    const refused = page.waitForResponse(response => response.url().endsWith('/api/model-assets') && response.status()===422);
    await drop(Buffer.from('3D Geometry File Format broken'),'broken.3dm');
    assert.equal((await(await refused).json()).code,'MODEL_ASSET_INVALID');
    await surface().getByRole('alert').waitFor();
    assert.equal((await rows()).length,2); assert.equal(await surface().locator('.viewport-state--error').count(),0);
    assert.equal(await surface().locator('.vcard__export[aria-pressed="true"]').count(),1);
    assert.match(await surface().locator('.vcard__export[aria-pressed="true"]').innerText(),/external-b\.3dm/);
    assert.equal((await api('/fixture/metrics')).head,head);
  });
  await step('page reopen and selecting either old source preserves exact original bytes', async () => {
    await page.reload(); await viewport().waitFor();
    for (const row of [first,second]) {
      const panel=await versions(); const open=panel.locator('.vcard__export').filter({hasText:row.fileName}); await open.click();
      await shown(row.sha256);
      const downloaded=Buffer.from(await(await fetch(origin+`/api/artifacts/${row.sha256}/bytes`)).arrayBuffer());
      assert.deepEqual(downloaded,row.sha256===first.sha256?a:b);
      assert.equal(await surface().locator('.viewport-state--error').count(),0);
    }
  });
  await step('external viewing cannot become a semantic edit base or an accepted Stage', async () => {
    const panel=await versions();
    const continuing=panel.getByRole('button',{name:'Continue from here',exact:true});
    assert.equal(await continuing.count()===0 || await continuing.isDisabled(),true,'external source must not offer an exact-state continuation');
    assert.equal(await surface().getByRole('button',{name:'Undo model',exact:true}).isDisabled(),true);
    assert.equal(await surface().getByRole('button',{name:'Redo model',exact:true}).isDisabled(),true);
    const s0=panel.getByRole('button',{name:'Accept as S0',exact:true}); if(await s0.count()) assert.equal(await s0.isDisabled(),true);
    const context=await page.evaluate(()=>window.externalImportContext);
    assert.ok(context?.designContext===null || context?.designContext?.sourceRunId===beforeContext?.designContext?.sourceRunId);
    assert.equal((await api('/fixture/metrics')).head,head);
    assert.equal(requests.some(r=>r.method==='POST' && /\/api\/(candidates|proposals|design-history)/.test(r.url)),false);
    assert.deepEqual(errors,[]);
    await page.screenshot({path:path.join(temporary,'external-reopened.png'),fullPage:true});
  });
  assert.ok(resourceRequests.some(row=>row.url.endsWith('/rhino3dm/rhino3dm.wasm') && row.status===200),
    `Rhino WASM was not loaded: ${JSON.stringify(resourceRequests)}`);
  console.log(JSON.stringify({passed,observations,resourceRequests,screenshots:temporary,scope:'small public fixtures, not a large-model latency benchmark'}));
} catch(error) {
  await page?.screenshot({path:path.join(temporary,'failure.png'),fullPage:true}).catch(()=>{});
  const ui = await page?.evaluate(() => ({
    selected:[...document.querySelectorAll('.vcard__export[aria-pressed="true"]')].map(node=>node.textContent?.trim()),
    disabled:[...document.querySelectorAll('.vcard__export:disabled')].map(node=>node.textContent?.trim()),
    loading:document.querySelectorAll('.viewport-state--loading').length,
    viewportError:[...document.querySelectorAll('.viewport-state--error')].map(node=>node.textContent?.trim()),
  })).catch(error=>({diagnosticError:String(error)}));
  console.error(`FAIL ${current}; ${temporary}\n${runtimeLog}`);
  console.error(JSON.stringify({ui,pageErrors:errors,artifactBytesRequests:requests.filter(row=>/\/artifacts\/[^/]+\/bytes/.test(row.url)),resourceRequests}));
  throw error;
} finally {
  await browser?.close(); await server?.close();
  if(child?.exitCode===null){const stopped=new Promise(resolve=>child.once('exit',resolve));child.kill();await stopped;}
}

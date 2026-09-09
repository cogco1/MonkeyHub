import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";

const playwrightModule = process.env.PLAYWRIGHT_MODULE?.trim();
if (!playwrightModule) {
  throw new Error("Set PLAYWRIGHT_MODULE to the Playwright module file path.");
}

// A standalone consumer of the real layer, with an in-memory parent history.
// It starts its own ephemeral server and never connects to a project API.
const root = fileURLToPath(new URL("..", import.meta.url)).replaceAll("\\", "/").replace(/\/$/, "");
const { chromium } = await import(pathToFileURL(playwrightModule).href);
const fixture = `
import React, {useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';
import {DocumentTextLayer} from '/src/features/stage/DocumentTextLayer.tsx';
import {UserPreferencesProvider} from '/src/features/settings/preferences.tsx';
const initial={width:800,height:600,scale:1,color:'#243b53',fontSize:.03,lineWidth:.004,active:true,panning:false,readOnly:false};
const metrics=window.textFixture={changes:[],parentDown:[],undoEvents:0};
function App(){
 const [marks,setMarks]=useState([]),[options,setOptions]=useState(initial),[version,setVersion]=useState(0),[visible,setVisible]=useState(true);
 const history=useRef([[]]),cursor=useRef(0);
 const change=next=>{history.current=history.current.slice(0,cursor.current+1);history.current.push(next);cursor.current++;metrics.changes.push(structuredClone(next));setMarks(next)};
 Object.assign(metrics,{get:()=>marks,options:()=>options,setOptions:patch=>flushSync(()=>setOptions(old=>({...old,...patch}))),setVisible:value=>flushSync(()=>setVisible(value)),
  reset:(seed=[])=>flushSync(()=>{metrics.changes=[];metrics.parentDown=[];metrics.undoEvents=0;history.current=[seed];cursor.current=0;setMarks(seed);setOptions(initial);setVisible(true);setVersion(old=>old+1)})});
 return <div id="workspace" className="document-workspace" style={{visibility:visible?'visible':'hidden'}} inert={!visible} onKeyDown={event=>{
  if((event.ctrlKey||event.metaKey)&&['z','y'].includes(event.key.toLowerCase())){
   if(event.target.closest('input,textarea,[contenteditable]'))return;
   event.preventDefault();metrics.undoEvents++;
   const redo=event.shiftKey||event.key.toLowerCase()==='y';
   cursor.current=Math.max(0,Math.min(history.current.length-1,cursor.current+(redo?1:-1)));setMarks(history.current[cursor.current]);
  }
 }}><div className="document-viewport" tabIndex={0} onPointerDown={event=>{metrics.parentDown.push(event.button);event.currentTarget.focus({preventScroll:true})}}>
  <div className="document-page" style={{width:options.width*options.scale,height:options.height*options.scale}}>
   <DocumentTextLayer key={version} {...options} annotations={marks} onChange={change}/>
  </div>
 </div></div>
}
createRoot(document.getElementById('root')).render(<UserPreferencesProvider><App/></UserPreferencesProvider>);
`;
const server = await createServer({ root, configFile: false, logLevel: "error", publicDir: false,
  server: { host: "127.0.0.1", port: 0, strictPort: true },
  plugins: [{ name: "document-text-fixture", resolveId(id) { if (id === "/text-fixture.tsx") return `${root}/text-fixture.tsx`; },
    load(id) { if (id === `${root}/text-fixture.tsx`) return fixture; },
    configureServer(vite) { vite.middlewares.use((request, response, next) => {
      if (request.url !== "/") return next();
      response.setHeader("Content-Type", "text/html");
      response.end('<html><head><meta charset="utf-8"/><style>body{margin:0;background:#ddd}#workspace{position:relative}.document-viewport{position:absolute;left:40px;top:40px;width:800px;height:600px;overflow:hidden;background:#ccc;outline:none}.document-page{position:absolute;left:0;top:0;background:white;pointer-events:none}.document-text-mark{box-sizing:border-box}</style></head><body><div id="root"></div><script type="module" src="/text-fixture.tsx"></script></body></html>');
    }); } }],
});
let browser;
let page;
let current;
const passed = [];
const errors = [];
const near = (actual, expected, tolerance = 0.003) => assert.ok(Math.abs(actual - expected) < tolerance, `${actual} != ${expected}`);
const state = () => page.evaluate(() => ({ marks: window.textFixture.get(), changes: window.textFixture.changes.length,
  undoEvents: window.textFixture.undoEvents, parentDown: window.textFixture.parentDown, focus: document.activeElement?.className }));
async function frames() { await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))); }
async function clickAt(x, y) {
  const rect = await page.locator(".document-page").boundingBox();
  await page.mouse.click(rect.x + rect.width * x, rect.y + rect.height * y);
}
const input = () => page.locator(".document-text-editor textarea");
const mark = () => page.locator(".document-text-mark").first();
async function submit(label, x = 0.25, y = 0.2) {
  await clickAt(x, y); await input().fill(label); await input().press("Control+Enter"); await frames();
}
async function step(name, action) { current = name; await action(); passed.push(name); console.log(`PASS ${name}`); }
const seed = { id: "existing-text", kind: "text", label: "existing\nsecond", points: [[0.25, 0.2]], fontSize: 0.03, color: "#243b53", lineWidth: 0.004 };

try {
  await server.listen();
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  page = await browser.newPage({ viewport: { width: 1000, height: 760 } });
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("request", (request) => { assert.ok(!request.url().includes("/api/"), "the fixture must not call a project API"); });
  const address = server.httpServer.address();
  await page.goto(`http://127.0.0.1:${address.port}/`);
  await page.waitForFunction(() => window.textFixture?.get);

  await step("native textarea undo and Chinese composition stay local until one explicit commit", async () => {
    await clickAt(0.25, 0.2);
    await input().pressSequentially("native undo");
    await input().press("Control+z");
    assert.notEqual(await input().inputValue(), "native undo");
    assert.equal((await state()).undoEvents, 0);
    await input().dispatchEvent("compositionstart", { data: "中" });
    await input().fill("中文\n第二行");
    await input().press("Control+Enter");
    assert.equal((await state()).changes, 0);
    await input().dispatchEvent("compositionend", { data: "中文" });
    await input().press("Control+Enter"); await frames();
    const current = await state();
    assert.equal(current.changes, 1); assert.equal(current.marks[0].label, "中文\n第二行");
    assert.deepEqual(current.marks[0].points, [[0.25, 0.2]]);
    assert.equal(current.marks[0].fontSize, 0.03);
    assert.equal(current.focus, "document-viewport");
    await page.keyboard.press("Control+z"); await frames(); assert.equal((await state()).marks.length, 0);
    await page.keyboard.press("Control+y"); await frames(); assert.equal((await state()).marks.length, 1);
  });

  await step("multiline editing preserves raw text, scales with the page, and popover shortcuts reach parent history", async () => {
    await mark().dblclick(); await input().fill("  中文\n第二行  "); await input().press("Control+Enter"); await frames();
    assert.equal((await state()).marks[0].label, "  中文\n第二行  ");
    assert.equal((await state()).changes, 2);
    await page.locator(".document-text-controls button").first().focus();
    const undoBefore = (await state()).undoEvents;
    await page.keyboard.press("Control+z"); await frames();
    assert.equal((await state()).undoEvents, undoBefore + 1);
    assert.equal((await state()).marks[0].label, "中文\n第二行");
    await page.keyboard.press("Control+y"); await frames();
    await page.evaluate(() => window.textFixture.setOptions({ scale: 0.5 }));
    const metrics = await mark().evaluate((node) => ({ font: getComputedStyle(node).fontSize, line: getComputedStyle(node).lineHeight, left: node.style.left, top: node.style.top }));
    assert.deepEqual(metrics, { font: "9px", line: "11.25px", left: "100px", top: "60px" });
    await mark().dblclick();
    assert.ok(await input().evaluate((node) => parseFloat(getComputedStyle(node).fontSize) >= 14));
    await input().press("Escape");
    await page.evaluate(() => window.textFixture.setOptions({ scale: 1 }));
  });

  await step("drag previews do not write, pointerup writes once, and Escape cancels movement", async () => {
    const before = await state();
    const box = await mark().boundingBox();
    await page.mouse.move(box.x + 3, box.y + 3); await page.mouse.down();
    await page.mouse.move(box.x + 83, box.y + 63); await frames();
    assert.equal((await state()).changes, before.changes);
    await page.mouse.up(); await frames();
    const moved = await state();
    assert.equal(moved.changes, before.changes + 1);
    near(moved.marks[0].points[0][0], 0.35); near(moved.marks[0].points[0][1], 0.3);
    const movedBox = await mark().boundingBox();
    await page.mouse.move(movedBox.x + 3, movedBox.y + 3); await page.mouse.down();
    await page.mouse.move(movedBox.x + 103, movedBox.y + 43);
    await page.keyboard.press("Escape"); await page.mouse.up(); await frames();
    assert.equal((await state()).changes, moved.changes);
    assert.deepEqual((await state()).marks[0].points, moved.marks[0].points);
  });

  await step("editor Delete is native, cancel restores viewport focus, and selected Delete is one undoable change", async () => {
    await mark().dblclick();
    const before = await state();
    await input().press("Home"); await input().press("Delete");
    assert.equal((await state()).changes, before.changes);
    await input().press("Escape"); await frames();
    assert.equal((await state()).focus, "document-viewport");
    await page.keyboard.press("Control+z"); await frames();
    near((await state()).marks[0].points[0][0], 0.25);
    await page.keyboard.press("Control+y"); await frames();
    await mark().click({ position: { x: 3, y: 3 } }); await page.keyboard.press("Delete"); await frames();
    assert.equal((await state()).changes, before.changes + 1); assert.equal((await state()).marks.length, 0);
    assert.equal((await state()).focus, "document-viewport");
    await page.keyboard.press("Control+z"); await frames(); assert.equal((await state()).marks.length, 1);
  });

  await step("empty new text and Escape leave no marks; Finish commits once and immediate Undo works", async () => {
    await page.evaluate(() => window.textFixture.reset());
    await clickAt(0.2, 0.2); await input().fill("  \n "); await input().press("Control+Enter"); await frames();
    assert.equal((await state()).changes, 0); assert.equal((await state()).marks.length, 0);
    await clickAt(0.2, 0.2); await input().fill("discard me"); await input().press("Escape"); await frames();
    assert.equal((await state()).changes, 0);
    await clickAt(0.2, 0.2); await input().fill("finished");
    await page.locator(".document-text-editor__actions button").last().click(); await frames();
    assert.equal((await state()).changes, 1); assert.equal((await state()).focus, "document-viewport");
    await page.keyboard.press("Control+z"); await frames(); assert.equal((await state()).marks.length, 0);
  });

  await step("non-text tools, read-only and temporary pan pass input through; middle button also reaches the viewport", async () => {
    await page.evaluate((value) => window.textFixture.reset([value]), seed);
    for (const patch of [{ active: false }, { active: true, readOnly: true }, { readOnly: false, panning: true }]) {
      await page.evaluate((value) => window.textFixture.setOptions(value), patch);
      const box = await mark().boundingBox(); await page.mouse.click(box.x + 3, box.y + 3);
      assert.equal(await input().count(), 0);
    }
    await page.evaluate(() => window.textFixture.setOptions({ panning: false }));
    const box = await mark().boundingBox(); await page.mouse.click(box.x + 3, box.y + 3, { button: "middle" });
    assert.deepEqual((await state()).parentDown, [0, 0, 0, 1]);
    assert.equal((await state()).changes, 0);
  });

  await step("edge controls remain reachable and the editor stays inside the hidden/inert document workspace", async () => {
    await page.evaluate(() => window.textFixture.reset());
    await clickAt(0.99, 0.99); await input().fill("edge text");
    const viewport = await page.locator(".document-viewport").boundingBox();
    const panel = await page.locator(".document-text-editor").boundingBox();
    const finish = await page.locator(".document-text-editor__actions button").last().boundingBox();
    assert.ok(panel.x >= viewport.x && panel.y >= viewport.y);
    assert.ok(panel.x + panel.width <= viewport.x + viewport.width && panel.y + panel.height <= viewport.y + viewport.height);
    assert.ok(finish.y + finish.height <= viewport.y + viewport.height);
    assert.equal(await page.locator(".document-text-editor").evaluate((node) => node.closest(".document-workspace")?.id), "workspace");
    await page.evaluate(() => window.textFixture.setVisible(false));
    assert.equal(await page.locator(".document-text-editor").isVisible(), false);
    await page.evaluate(() => window.textFixture.setVisible(true));
    assert.equal(await input().inputValue(), "edge text");
    await input().press("Control+Enter"); await frames();
    near((await state()).marks[0].points[0][0], 0.99); near((await state()).marks[0].points[0][1], 0.99);
  });

  await step("existing style is preserved until an explicit toolbar change commits with the draft", async () => {
    await page.evaluate((value) => window.textFixture.reset([value]), { ...seed, color: "#c02020", fontSize: 0.02 });
    await mark().dblclick();
    assert.equal((await state()).changes, 0);
    await page.evaluate(() => window.textFixture.setOptions({ color: "#208030", fontSize: 0.04 }));
    assert.equal((await state()).changes, 0);
    await input().press("Control+Enter"); await frames();
    assert.equal((await state()).changes, 1);
    assert.equal((await state()).marks[0].color, "#208030");
    assert.equal((await state()).marks[0].fontSize, 0.04);
    assert.equal((await state()).marks[0].label, seed.label);
  });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: passed.length, browserErrors: 0 }, null, 2));
} catch (error) {
  console.error(JSON.stringify({ failed: current, passed, browserErrors: errors, state: page ? await state().catch(() => null) : null }, null, 2));
  throw error;
} finally {
  await browser?.close(); await server.close();
}

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";

// An isolated canvas fixture: no model files, application state, or API calls.
// Performance numbers measure this fixed workload, not a complex 3DM scene.
const root = fileURLToPath(new URL("..", import.meta.url)).replace(/[\\/]$/, "").replaceAll("\\", "/");
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
const baselineId = `${root.replaceAll("\\", "/")}/src/features/stage/__baselineAnnotate.tsx`;
const fixture = `
import React, {useState, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';
import {Annotate} from '${process.argv.includes('--baseline') ? '/baseline-annotate.tsx' : '/src/workspaces/monkeyarch/Annotate.tsx'}';
import {UserPreferencesProvider} from '/src/features/settings/preferences.tsx';
const camera={position:[1,2,3],target:[0,0,0],fov:50};
const base=Array.from({length:120},(_,i)=>({kind:'freehand',screen:Array.from({length:180},(_,j)=>[10+j*4,10+i*4+Math.sin(j/12)*7]),screenSize:[800,600],camera,hits:[],color:'#2468dd',lineWidth:2}));
window.fixture={committed:[],erased:[],hitCalls:0,sampleCost:0,canvasDraws:0,latencies:[],durations:[],longTasks:[],longFrames:[],downstream:[],paints:0};
const metrics=window.fixture;
new PerformanceObserver(list=>metrics.longTasks.push(...list.getEntries().map(e=>e.duration))).observe({type:'longtask',buffered:true});
let previousFrame=0; const frame=now=>{if(previousFrame&&now-previousFrame>34)metrics.longFrames.push(now-previousFrame);previousFrame=now;requestAnimationFrame(frame)};requestAnimationFrame(frame);
const proto=CanvasRenderingContext2D.prototype, original=proto.stroke;
proto.stroke=function(...args){const result=original.apply(this,args);metrics.canvasDraws++;if(metrics.pendingInput){metrics.latencies.push(performance.now()-metrics.pendingInput);performance.mark('annotate-ink-'+metrics.sequence);metrics.pendingInput=0;metrics.paints++;}return result;};
function App(){const [gestures,setGestures]=useState(base),[tool,setTool]=useState('freehand'),[eraser,setEraser]=useState(false),[cancelToken,setCancel]=useState(0);
const viewportRef=useRef({camera:()=>camera,sampleAt:(x,y)=>{metrics.hitCalls++;const start=performance.now();while(performance.now()-start<metrics.sampleCost){}return {objectName:'fixture',userStrings:{Element:'fixture'},world:[x,y,0]};},unprojectOnPlane:(x,y)=>[x,y,0]});
Object.assign(metrics,{setTool:t=>flushSync(()=>{setTool(t);setEraser(false)}),setEraser:e=>flushSync(()=>setEraser(e)),reset:()=>flushSync(()=>{setGestures([]);setCancel(n=>n+1);metrics.committed=[];metrics.erased=[];metrics.hitCalls=0;}),getGestures:()=>gestures});
return <><input id="text"/><div id="stage"><canvas className="viewport-canvas" onPointerDown={e=>{metrics.downstream.push(e.button);e.currentTarget.setPointerCapture(e.pointerId);}}/><Annotate viewportRef={viewportRef} tool={tool} eraser={eraser} onErase={indices=>{metrics.erased.push(indices);setGestures(old=>old.filter((_,i)=>!indices.includes(i)));}} gestures={gestures} onGesture={g=>{metrics.committed.push(g);setGestures(old=>[...old,g]);}} style={{color:'#2468dd',lineWidth:2}} cancelToken={cancelToken}/></div></>}
createRoot(document.getElementById('root')).render(<React.StrictMode><UserPreferencesProvider><App/></UserPreferencesProvider></React.StrictMode>);
`;
const server = await createServer({
  root, configFile: false, logLevel: "error", publicDir: false,
  server: { host: "127.0.0.1", port: 5189, strictPort: true },
  plugins: [{ name: "annotate-fixture", resolveId(id) { if (id === "/baseline-annotate.tsx") return baselineId; if (id === "/fixture.tsx") return `${root}/fixture.tsx`; }, async load(id) { if (id === baselineId) return readFile(new URL("../../baseline/src/features/stage/Annotate.tsx", import.meta.url), "utf8"); if (id.endsWith("/fixture.tsx")) return fixture; }, configureServer(server) { server.middlewares.use((req,res,next)=>{if(req.url!=="/")return next();res.setHeader("Content-Type","text/html");res.end('<html><style>body{margin:0}#stage{position:relative;width:800px;height:600px}.annotate,.viewport-canvas{position:absolute;inset:0;width:100%;height:100%}.annotate{pointer-events:none;touch-action:none}.annotate[data-armed="true"]{pointer-events:auto}.annotate__note,.annotate__label{position:absolute}</style><div id="root"></div><script type="module" src="/fixture.tsx"></script></html>');});} }],
});
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL ?? "chrome" });
  const page = await browser.newPage({ viewport: { width: 1000, height: 760 }, deviceScaleFactor: 2 });
  page.on("pageerror", error => { console.error(error); });
  await page.goto("http://127.0.0.1:5189/");
  await page.waitForFunction(()=>window.fixture?.getGestures);
  await page.waitForTimeout(250);
  const cdp=await page.context().newCDPSession(page);
  await cdp.send('Tracing.start',{categories:'devtools.timeline,blink.user_timing,disabled-by-default-devtools.timeline.frame',transferMode:'ReturnAsStream'});
  const profile = await page.evaluate(async()=>{
    const canvas=document.querySelector('canvas.annotate[data-armed="true"]');
    const rect=canvas.getBoundingClientRect(),m=window.fixture;
    // Synthetic pointer events give the same fixed samples and cadence on both versions.
    const capture=canvas.setPointerCapture,releaseCapture=canvas.releasePointerCapture;
    canvas.setPointerCapture=()=>{};canvas.releasePointerCapture=()=>{};
    m.sequence=0;
    const send=(type,x,y,buttons=1)=>{const begin=performance.now();if(type==='pointermove'){m.pendingInput=begin;performance.mark('annotate-input-'+(++m.sequence));}canvas.dispatchEvent(new PointerEvent(type,{bubbles:true,pointerId:71,isPrimary:true,button:0,buttons,clientX:rect.left+x,clientY:rect.top+y}));m.durations.push(performance.now()-begin);};
    m.latencies=[];m.durations=[];m.longTasks=[];m.longFrames=[];m.canvasDraws=0;m.paints=0;
    send('pointerdown',20,300);
    for(let n=0;n<960;n++){send('pointermove',20+n*.7,300+Math.sin(n/12)*80);await new Promise(resolve=>setTimeout(resolve,4));}
    const drawing={draws:m.canvasDraws,latencies:[...m.latencies],durations:[...m.durations],longTasks:[...m.longTasks],longFrames:[...m.longFrames],paints:m.paints};
    m.sampleCost=.2;const before=performance.now();send('pointerup',695,300,0);const release=performance.now()-before;
    await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
    canvas.setPointerCapture=capture;canvas.releasePointerCapture=releaseCapture;
    const pct=(xs,p)=>xs.sort((a,b)=>a-b)[Math.min(xs.length-1,Math.floor(xs.length*p))]??0;
    return {samples:960,savedStrokes:120,pointsPerSavedStroke:180,dpr:devicePixelRatio,paintCalls:drawing.draws,paintedFrames:drawing.paints,inputToCanvasWriteP50:pct(drawing.latencies,.5),inputToCanvasWriteP95:pct(drawing.latencies,.95),inputHandlerP95:pct(drawing.durations,.95),longTasks:{count:drawing.longTasks.length,max:Math.max(0,...drawing.longTasks)},longFrames:{thresholdMs:34,count:drawing.longFrames.length,max:Math.max(0,...drawing.longFrames)},releaseMs:release,hitCalls:m.hitCalls};
  });
  const traceDone=new Promise(resolve=>cdp.once('Tracing.tracingComplete',resolve));
  await cdp.send('Tracing.end');
  const {stream}=await traceDone;
  let traceText='';for(;;){const chunk=await cdp.send('IO.read',{handle:stream});traceText+=chunk.data;if(chunk.eof)break;}await cdp.send('IO.close',{handle:stream});
  const trace=JSON.parse(traceText).traceEvents;
  const inputs=new Map(trace.filter(e=>e.name.startsWith('annotate-input-')).map(e=>[e.name.slice(15),e.ts]));
  const writes=trace.filter(e=>e.name.startsWith('annotate-ink-')).sort((a,b)=>a.ts-b.ts);
  const paints=trace.filter(e=>e.name==='DrawFrame').sort((a,b)=>a.ts-b.ts);
  const presentation=[];
  for(let i=0;i<paints.length;i++){
    const latest=writes.findLast(e=>e.ts<=paints[i].ts&&e.ts>(paints[i-1]?.ts??0));
    if(latest){const input=inputs.get(latest.name.slice(13));if(input!==undefined)presentation.push((paints[i].ts-input)/1000);}
  }
  const percentile=(xs,p)=>xs.sort((a,b)=>a-b)[Math.min(xs.length-1,Math.floor(xs.length*p))]??null;
  profile.browserFrame={observedFrames:presentation.length,inputToDrawFrameP50:percentile(presentation,.5),inputToDrawFrameP95:percentile(presentation,.95),marks:inputs.size,inkWrites:writes.length,frameEvents:paints.length};
  console.log(JSON.stringify({ mode: process.argv.includes('--baseline') ? 'baseline' : 'current', fixtureProfile: profile }));
  if (!process.argv.includes('--baseline')) {
    await page.evaluate(()=>{window.fixture.sampleCost=0;window.fixture.reset();});
    const ink=page.locator('canvas.annotate[data-armed="true"]');
    const box=await ink.boundingBox();
    await page.mouse.click(box.x+30,box.y+30);
    await page.waitForFunction(()=>window.fixture.committed.length===1);
    assert.equal(await page.evaluate(()=>window.fixture.committed[0].screen.length),1,"single freehand click persists");
    await page.mouse.move(box.x+50,box.y+50);await page.mouse.down();await page.mouse.move(box.x+52,box.y+52);await page.mouse.up();
    await page.waitForFunction(()=>window.fixture.committed.length===2);
    assert.equal(await page.evaluate(()=>window.fixture.committed[1].screen.at(-1)[0]),52,"short stroke endpoint persists");
    await page.mouse.move(box.x+100,box.y+100);await page.mouse.down();await page.mouse.move(box.x+900,box.y+650);await page.mouse.up();
    await page.waitForFunction(()=>window.fixture.committed.length===3);
    await page.mouse.move(box.x+100,box.y+120);await page.mouse.down();await page.keyboard.press('Escape');await page.mouse.up();
    assert.equal(await page.evaluate(()=>window.fixture.committed.length),3,"Escape cancels live ink");
    await page.mouse.move(box.x+120,box.y+120);await page.mouse.down();await ink.dispatchEvent('pointercancel',{pointerId:1});await page.mouse.up();
    assert.equal(await page.evaluate(()=>window.fixture.committed.length),3,"pointercancel cancels live ink");
    await page.evaluate(()=>window.fixture.setEraser(true));
    await page.mouse.move(box.x+20,box.y+30);await page.mouse.down();await page.mouse.move(box.x+40,box.y+30);await page.mouse.up();
    assert.deepEqual(await page.evaluate(()=>window.fixture.erased),[[0]],"one eraser drag commits one set of stroke indices");
    await page.mouse.move(box.x+50,box.y+50);await page.mouse.down();await ink.dispatchEvent('pointercancel',{pointerId:1});await page.mouse.up();
    assert.equal(await page.evaluate(()=>window.fixture.erased.length),1,"cancelled eraser leaves committed annotations intact");
    await page.evaluate(()=>window.fixture.setEraser(false));
    await page.locator('#text').focus();await page.keyboard.press('Space');assert.equal(await page.locator('#text').inputValue(),' ',"Space remains text in inputs");
    await page.locator('#text').blur();
    await page.keyboard.down('Space');await page.mouse.click(box.x+160,box.y+160);await page.keyboard.up('Space');
    await page.mouse.click(box.x+180,box.y+180,{button:'middle'});
    assert.deepEqual(await page.evaluate(()=>window.fixture.downstream),[0,1],"Space and middle button preserve the viewer's own controls");
    await page.mouse.move(box.x+50,box.y+150);await page.mouse.down();
    await ink.evaluate(canvas=>{const r=canvas.getBoundingClientRect();const move=new PointerEvent('pointermove',{bubbles:true,pointerId:1,button:0,buttons:1,clientX:r.left+90,clientY:r.top+190});Object.defineProperty(move,'getCoalescedEvents',{value:()=>[new PointerEvent('pointermove',{clientX:r.left+60,clientY:r.top+190}),new PointerEvent('pointermove',{clientX:r.left+80,clientY:r.top+200})]});canvas.dispatchEvent(move);});
    await page.mouse.up();
    assert.equal(await page.evaluate(()=>window.fixture.committed.at(-1).screen.some(p=>p[0]===80&&p[1]===200)),true,"coalesced events preserve the fast curve's intermediate points");
    await page.evaluate(()=>window.fixture.setTool('arc'));
    await page.mouse.move(box.x+200,box.y+200);await page.mouse.down();await page.mouse.move(box.x+300,box.y+200);await page.mouse.up();
    await page.mouse.click(box.x+250,box.y+250);
    assert.deepEqual(await page.evaluate(()=>window.fixture.committed.at(-1).screen),[[200,200],[300,200],[250,250]],"arc keeps its three-point workflow");
    await page.evaluate(()=>window.fixture.setTool('freehand'));
    await ink.evaluate(canvas=>{canvas.setPointerCapture=undefined;canvas.hasPointerCapture=undefined;});
    const count=await page.evaluate(()=>window.fixture.committed.length);
    await page.mouse.move(box.x+100,box.y+300);await page.mouse.down();await page.mouse.move(box.x+900,box.y+650);await page.mouse.up();
    assert.equal(await page.evaluate(()=>window.fixture.committed.length),count+1,"no-capture fallback finishes outside the overlay");
    assert.deepEqual(await page.evaluate(()=>window.fixture.committed.at(-1).screen.at(-1)),[900,650]);
    console.log("Annotate headless behavior PASS: point, short stroke, outside release, Escape, pointercancel, eraser commit/cancel, Space/middle viewer transfer, text Space, coalesced curve, arc, missing-capture fallback, DPR 2.");
  }
} finally { await browser?.close(); await server.close(); }

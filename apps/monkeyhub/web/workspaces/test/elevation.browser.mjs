/** Isolated Stage/WebGL test: real fields, selection, overlays and local history; no user's application. */
import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
const webRoot = fileURLToPath(new URL("..", import.meta.url));
const cacheDir = await mkdtemp(path.join(tmpdir(), "elevation-browser-"));
const http = createHttpServer(), errors = [], projectCalls = [];
let browser, vite, page;
const html = `<!doctype html><html><head><style>#root,.stage {height:100vh} #root .viewport-state {display:none}</style></head>
<body><div id="root" class="project-workspace"></div><script type="module">
import React,{useState,useRef,useEffect} from "react";
import {createRoot} from "react-dom/client";
import {Vector3} from "three";
import {Stage} from "/src/features/stage/Stage.tsx";
import {UserPreferencesProvider} from "/test/TestProviders.tsx";
import {createModelDraft,currentDraft,applyDraftCommand,undoDraft,redoDraft,elevationOf} from "/src/features/stage/modelDraft.ts";
import "/src/styles.css";
import "/@fs/${fileURLToPath(new URL("../../src/styles.css", import.meta.url)).replaceAll("\\", "/")}";
const noop=()=>{};
const mass=(id,x,base,height)=>({elementId:id,componentId:"model",created:true,originalObjectNames:[],
 spec:{profile:[[x,0],[x+4,0],[x+4,3],[x,3]],base,height}});
const initial=createModelDraft([mass("lower",0,0,3),mass("upper",0,5,2),mass("free",7,5,2)]);
function Harness(){
 const viewport=useRef(null),[history,setHistory]=useState(initial),[selected,select]=useState(null),[error,setError]=useState(null);
 const snapshot=currentDraft(history),objects=[...snapshot.objects.values()],object=snapshot.objects.get(selected);
 const apply=command=>{try{setHistory(applyDraftCommand(history,command));setError(null);return true;}catch(e){setError(e.message);return false;}};
 window.viewport=viewport;window.select=select;
 window.snapshot=()=>({index:history.index,commands:snapshot.commands,levels:snapshot.levels,objects:objects.map(o=>({id:o.elementId,spec:o.spec,elevation:elevationOf(o)}))});
 useEffect(()=>{viewport.current?.draftPreview({objects:objects.filter(o=>o.spec&&!o.deleted).map(o=>({elementId:o.elementId,spec:o.spec})),hiddenObjectNames:[]});},[snapshot]);
 return React.createElement(Stage,{viewportRef:viewport,embedded:true,status:"ready",message:"",picked:null,
 versions:[],workingCopies:[],loadedShas:[],loadingSha:null,designHistory:null,documentView:{open:false,mounted:false},displayMode:"model",tool:null,
 gestures:[],home:null,hasModel:true,editingBaseRunId:"base",loadedRunId:"base",modelAnnotations:null,annotationsReady:false,documentModelSources:[],
 editingModelSource:null,viewedModelSource:null,artifactError:null,baseError:null,blend:null,captureState:"idle",onTool:noop,onInspection:noop,
 onStatus:noop,onRequestFile:noop,onOpenFile:noop,onSource:noop,onPick:p=>select(p?.draftElementId??null),
 model:{onDelete:noop,canDelete:!!selected,deleting:false,subject:selected,onUndo:()=>setHistory(undoDraft),canUndo:history.index>0,
 onRedo:()=>setHistory(redoDraft),canRedo:history.index+1<history.snapshots.length,onClearSelection:()=>select(null),hasSelection:!!selected,busy:false,error,
 elevation:object?{object,objects,levels:snapshot.levels,onApply:apply}:null}});
}
createRoot(document.getElementById("root")).render(React.createElement(UserPreferencesProvider,null,React.createElement(Harness)));
window.projectPoint=point=>{const r=document.querySelector("canvas").getBoundingClientRect(),c=window.readRuntime().camera;c.updateMatrixWorld();
 const p=new Vector3(...point).project(c);return [r.left+(p.x+1)*r.width/2,r.top+(1-p.y)*r.height/2];};
</script></body></html>`;
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
try {
  vite = await createServer({root:webRoot,configFile:false,resolve:{dedupe:["react","react-dom"]},cacheDir,publicDir:"../.generated/public",logLevel:"silent",
    plugins:[{name:"read-only-elevation-probe",enforce:"pre",transform(source,id){
      if(!id.replaceAll("\\","/").endsWith("/viewer/ThreeDmViewport.tsx"))return;
      const marker="  const pickAt = useCallback(";
      assert.equal(source.split(marker).length,2);
      return {code:source.replace(marker,"  (window as any).readRuntime = () => runtimeRef.current;\n"+marker),map:null};
    }},react()],server:{middlewareMode:true,hmr:false,ws:{server:http},watch:null}});
  http.on("request",async(request,response)=>{
    if(request.url.startsWith("/elevation-test")){response.setHeader("Content-Type","text/html");response.end(await vite.transformIndexHtml(request.url,html));}
    else if(request.url.startsWith("/api/")){projectCalls.push(request.url);response.writeHead(500).end();}
    else vite.middlewares(request,response);
  });
  await new Promise(resolve=>http.listen(0,"127.0.0.1",resolve));
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{}),args:["--enable-unsafe-swiftshader","--no-sandbox"]});
  page=await browser.newPage({viewport:{width:1280,height:850},locale:"en-US"});
  page.on("pageerror",error=>errors.push(error.message));page.setDefaultTimeout(12000);
  await page.goto(`http://127.0.0.1:${http.address().port}/elevation-test?lang=en`);
  await page.waitForFunction(()=>window.readRuntime?.()?.draftObjects.size===3);
  await page.evaluate(()=>window.viewport.current.fitView());
  const position=await page.evaluate(()=>window.projectPoint([2,1.5,7]));await page.mouse.click(...position);
  await page.getByRole("complementary",{name:"Mass elevation"}).waitFor();
  const field=name=>page.getByRole("spinbutton",{name,exact:true});
  const set=async(name,value)=>{await field(name).fill(String(value));await field(name).press("Enter");};
  const state=()=>page.evaluate(()=>window.snapshot());
  const object=(snapshot,id)=>snapshot.objects.find(row=>row.id===id);
  await page.getByLabel("Base reference",{exact:true}).selectOption("element-top:lower");
  assert.equal(object(await state(),"upper").spec.base,3);
  await page.evaluate(()=>window.select("lower"));await set("Height",4.5);
  assert.equal(object(await state(),"upper").spec.base,4.5);
  assert.equal(object(await state(),"free").spec.base,5);
  await page.locator(".elevation-panel strong").click();await page.keyboard.press("Control+z");
  assert.equal(object(await state(),"lower").spec.height,3);
  await page.keyboard.press("Control+Shift+z");assert.equal(object(await state(),"lower").spec.height,4.5);
  await page.evaluate(()=>window.select("upper"));await set("Base Z",5.5);
  assert.equal(object(await state(),"upper").elevation.baseReference.offset,1);
  await page.getByText("Reference elevations",{exact:true}).click();
  await page.getByLabel("Datum name",{exact:true}).fill("Roof ref");await page.getByLabel("Datum elevation",{exact:true}).fill("12");
  await page.getByRole("button",{name:"Create datum",exact:true}).click();
  const datum=(await state()).levels[0];assert.equal(datum.name,"Roof ref");
  await page.getByLabel("Top reference",{exact:true}).selectOption(`level:${datum.levelId}`);
  assert.equal(object(await state(),"upper").spec.height,6.5);
  await page.getByLabel("Datum elevation",{exact:true}).fill("13");await page.getByRole("button",{name:"Update datum",exact:true}).click();
  assert.equal(object(await state(),"upper").spec.height,7.5);
  const valid=await state();await set("Height",-2);
  await page.getByRole("alert").waitFor();assert.deepEqual(await state(),valid);
  await set("Height",8);assert.equal(object(await state(),"upper").spec.height,8);
  await field("Top Z").focus();
  assert.equal(await page.evaluate(()=>window.readRuntime().elevationGuide.children[0].geometry.getAttribute("position").getZ(0)),13.5);
  await page.getByLabel("Base reference",{exact:true}).selectOption("");
  await page.evaluate(()=>window.select("lower"));await set("Height",6);
  assert.equal(object(await state(),"upper").spec.base,5.5,"detached mass remains free");
  // Both appearances and a narrow layout retain accessible, reachable controls.
  for(const theme of ["light","dark"]){
    await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
    await page.setViewportSize({width:800,height:600});
    const panel=await page.locator(".elevation-panel").boundingBox();assert.ok(panel.x>=0&&panel.x+panel.width<=800);
    if(process.env.BROWSER_OUTPUT){await mkdir(process.env.BROWSER_OUTPUT,{recursive:true});await page.screenshot({path:path.join(process.env.BROWSER_OUTPUT,`elevation-${theme}.png`)});}
  }
  assert.deepEqual(errors,[]);assert.deepEqual(projectCalls,[]);
  console.log("Stage elevation: real numeric/binding/datum forms, propagation, free mass, undo/redo, failed-state preservation and viewport guide PASS");
}finally{
  await browser?.close();await vite?.close();await new Promise(resolve=>http.close(resolve));await rm(cacheDir,{recursive:true,force:true});
}

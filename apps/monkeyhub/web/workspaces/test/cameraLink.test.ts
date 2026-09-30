import assert from "node:assert/strict";
import test from "node:test";
import { OrthographicCamera, PerspectiveCamera, Scene } from "three";
import { CameraLink } from "../src/workspaces/render/cameraLink.ts";
import type { Camera, Geometry, PhysicalScene } from "../src/workspaces/render/sceneTypes.ts";
import type { RenderView } from "../src/workspaces/monkeyarch/viewer/renderView.ts";

function fixture() {
  const camera = new PerspectiveCamera(60, .5, .1, 100000);
  camera.position.set(1000, -2000, 3000); camera.up.set(0, 0, 1);
  const source = {runId:"model-a",assetSha256:"asset-a",stateDigest:"state-a"};
  let view:RenderView = {scene:new Scene(),camera,target:[500,0,250],fov:60,aspect:.5,exposure:1,metersPerUnit:.001,modelSource:source};
  const geometry:Geometry={source:{geometryRevision:"geometry-a",preview:source},meshes:[]};
  const scene:PhysicalScene={geometryRevision:"geometry-a",camera:{position:[1,-2,3],target:[0,0,0],up:[0,0,1],projection:"perspective",fov:45,orthoScale:8},
    materials:[],assignments:{},regions:[],lights:[],environment:{color:"#ffffff",strength:1,background:null,environmentMap:null},exposure:0,
    settings:{width:1000,height:1000,samples:8,denoise:true}};
  const applied:{camera:Camera;aspect:number}[]=[],edits:Camera[]=[];
  const link=new CameraLink();
  link.attachModel({read:()=>view,apply:(camera,aspect)=>applied.push({camera,aspect})});
  const detach=link.attachPhysical({geometry,scene,edit:camera=>edits.push(camera)});
  return {link,camera,scene,geometry,applied,edits,detach,setView:(next:Partial<RenderView>)=>{view={...view,...next};}};
}

test("camera link converts millimetres and preserves the output gate instead of copying viewport FOV",()=>{
  const f=fixture();assert.equal(f.link.linked(),true);assert.equal(f.applied.length,1);
  f.link.changed();const next=f.edits[0]!;
  assert.deepEqual(next.position,[1,-2,3]);assert.deepEqual(next.target,[.5,0,.25]);
  assert.ok(Math.abs(next.fov-2*Math.atan(Math.tan(Math.PI/6)/2)*180/Math.PI)<1e-10);
  assert.equal(next.orthoScale,8);assert.equal(f.scene.camera.fov,45,"link has no independent persistent writer");
});

test("orthographic zoom and viewport margin map to one world-space output height",()=>{
  const f=fixture(),camera=new OrthographicCamera(-2000,2000,4000,-4000,.1,100000);
  camera.position.set(1000,-2000,3000);camera.zoom=2;
  f.setView({camera});f.link.changed();
  assert.equal(f.edits[0]!.projection,"orthographic");assert.equal(f.edits[0]!.orthoScale,2);
  assert.equal(f.edits[0]!.fov,45);
});

test("resize restores the retained composition without writing another scene revision",()=>{
  const f=fixture();f.link.changed("resize");assert.equal(f.applied.length,2);assert.deepEqual(f.edits,[]);
  assert.deepEqual(f.applied[1],{camera:f.scene.camera,aspect:1});
});

for(const [name,patch] of Object.entries({unknownUnits:{metersPerUnit:undefined},negativeUnits:{metersPerUnit:-1},loading:{sourceIssue:"loading"},unsaved:{sourceIssue:"unsaved"},foreignModel:{modelSource:{runId:"model-b",assetSha256:"asset-a",stateDigest:"state-a"}}})){
  test(`camera link refuses ${name} rather than overwriting the retained scene`,()=>{
    const f=fixture();f.setView(patch as Partial<RenderView>);f.link.changed();f.link.restore();
    assert.equal(f.link.linked(),false);assert.deepEqual(f.edits,[]);assert.equal(f.applied.length,1);
  });
}

test("stale geometry and a detached project cannot publish to an earlier scene",()=>{
  const f=fixture();f.geometry.source.geometryRevision="geometry-b";f.link.changed();assert.deepEqual(f.edits,[]);
  f.detach();assert.equal(f.link.linked(),false);f.link.changed();assert.deepEqual(f.edits,[]);
});

test("retained external files link without fabricating an OCCT modelSource",()=>{
  const f=fixture();f.setView({modelSource:null,displaySource:{runId:"model-a",assetSha256:"asset-a"}});
  f.link.changed();assert.equal(f.edits.length,1);
  f.setView({displaySource:{runId:"model-b",assetSha256:"asset-a"}});f.link.changed();assert.equal(f.edits.length,1);
});

import assert from "node:assert/strict";
import test from "node:test";
import { meshRegion, triangleMaterials, type Geometry, type PhysicalScene } from "../src/workspaces/render/sceneTypes.ts";

test("selected translated mesh defines region bounds and assignment without changing geometry", () => {
  const geometry: Geometry = {source:{geometryRevision:"revision-a"},meshes:[
    {name:"Door",vertices:[[10,20,30],[12,20,30],[10,20,33]],triangles:[[0,1,2]],normals:null,texcoords:null},
    {name:"Door",vertices:[[100,200,300],[102,200,300],[100,200,303]],triangles:[[0,1,2]],normals:null,texcoords:null},
  ]};
  const before=structuredClone(geometry);
  const region=meshRegion(geometry,1,"finish","unique-region");
  assert.equal(region.name,"Door"); assert.equal(region.mesh,1);
  assert.deepEqual(region.center,[101,200,301.5]); assert.ok(region.radius[1]>0);
  const scene={materials:[{id:"neutral"},{id:"finish"}],assignments:{"0":"neutral","1":"neutral"},regions:[region]} as unknown as PhysicalScene;
  assert.deepEqual(triangleMaterials(geometry.meshes[0]!,0,scene),[0]);
  assert.deepEqual(triangleMaterials(geometry.meshes[1]!,1,scene),[1]);
  region.name="Window"; assert.equal(region.id,"unique-region");
  assert.deepEqual(geometry,before);
  scene.regions=[];assert.deepEqual(triangleMaterials(geometry.meshes[1]!,1,scene),[0]);
});

test("unnamed source remains a generic mesh and empty selection is refused", () => {
  const geometry: Geometry={source:{geometryRevision:"g"},meshes:[{name:"",vertices:[[0,0,0]],triangles:[],normals:null,texcoords:null}]};
  assert.equal(meshRegion(geometry,0,"n","r").name,"Mesh 0");
  assert.throws(()=>meshRegion(geometry,1,"n","r"),/non-empty source mesh/);
});

// Independent readback using the frontend's existing Three.js GLTFLoader.
// node tests/model_export_glb_reader.mjs <three-package-dir> <generated.glb>
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';
const root = resolve(process.argv[2]);
const { GLTFLoader } = await import(pathToFileURL(resolve(root, 'examples/jsm/loaders/GLTFLoader.js')));
const { Box3 } = await import(pathToFileURL(resolve(root, 'build/three.module.js')));
const bytes = readFileSync(process.argv[3]);
const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
const gltf = await new GLTFLoader().parseAsync(buffer, '');
let meshes = 0, triangles = 0;
gltf.scene.traverse(object => {
  if (object.isMesh) {
    meshes++;
    triangles += object.geometry.index.count / 3;
  }
});
const box = new Box3().setFromObject(gltf.scene);
assert.equal(meshes, 1);
assert.equal(triangles, 1);
// The test fixture is meters/Z-up (1,2,3)..(2,3,3), GLB is meters/Y-up.
assert.deepEqual(box.min.toArray(), [1, 3, -3]);
assert.deepEqual(box.max.toArray(), [2, 3, -2]);
console.log(JSON.stringify({ reader: 'Three.js GLTFLoader', meshes, triangles,
  bounds: [box.min.toArray(), box.max.toArray()] }));

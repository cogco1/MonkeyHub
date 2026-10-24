import assert from "node:assert/strict";
import test from "node:test";

import { BoxGeometry, BufferGeometry, Color, Float32BufferAttribute, Group, Mesh, MeshStandardMaterial, Plane, PlaneGeometry, Vector3 } from "three";

import {
  buildSectionCaps, capColour, closedGeometry, disposeSectionCaps, syncSectionCaps,
} from "../src/workspaces/monkeyarch/viewer/sectionCaps.ts";

test("a mesh is closed when every edge borders two triangles, its corners welded by position (G5)", () => {
  assert.equal(closedGeometry(new BoxGeometry(1, 2, 3)), true, "a box's faces share their edges once welded");
  assert.equal(closedGeometry(new BoxGeometry(1, 2, 3).toNonIndexed()), true, "a triangle soup of a box is still closed");
  assert.equal(closedGeometry(new PlaneGeometry(1, 1)), false, "a sheet has a free border");
  const open = new BoxGeometry(1, 1, 1).toNonIndexed();
  const positions = open.getAttribute("position").array as Float32Array;
  // Drop the last face (two triangles): the box is open on that side.
  const missing = new BufferGeometry();
  missing.setAttribute("position", new Float32BufferAttribute(positions.slice(0, positions.length - 18), 3));
  assert.equal(closedGeometry(missing), false);
});

test("a cut member's fill is its own colour darkened", () => {
  const fill = capColour(new MeshStandardMaterial({ color: new Color(0.5, 0.4, 0.2) }));
  assert.ok(Math.abs(fill.r - 0.3) < 1e-6 && Math.abs(fill.g - 0.24) < 1e-6 && Math.abs(fill.b - 0.12) < 1e-6);
  assert.ok(capColour([]) instanceof Color, "a member without a material still gets a fill");
});

test("only displayed closed members get stencil passes and a fill, in drawing order, laid on the plane", () => {
  const model = new Group();
  const solid = new Mesh(new BoxGeometry(2, 2, 2), new MeshStandardMaterial({ color: 0x808080 }));
  solid.position.set(5, 0, 0);
  const sheet = new Mesh(new PlaneGeometry(2, 2), new MeshStandardMaterial());
  const hidden = new Mesh(new BoxGeometry(1, 1, 1), new MeshStandardMaterial());
  hidden.visible = false;
  model.add(solid, sheet, hidden);
  const planes = [new Plane(new Vector3(0, 0, -1), 0.5)];
  const caps = buildSectionCaps(model, planes, 40);
  assert.ok(caps);
  assert.deepEqual(caps.members.map((member) => member.source), [solid]);
  const [member] = caps.members;
  assert.deepEqual([...member.passes.map((pass) => pass.renderOrder), member.fill.renderOrder], [1000, 1001, 1002]);
  assert.ok(member.passes.every((pass) => (pass.material as MeshStandardMaterial).clippingPlanes === planes));
  assert.equal(member.passes[0].matrix.elements[12], 5, "the passes stand where the member stands");
  syncSectionCaps(caps, planes[0], new Vector3(5, 0, 0));
  assert.ok(Math.abs(member.fill.position.z - 0.5) < 1e-9, "the fill lies on the plane");
  assert.ok(member.fill.position.x === 5, "near the model");
  solid.visible = false;
  syncSectionCaps(caps, planes[0], new Vector3());
  assert.equal(member.fill.visible, false, "a member that is hidden shows no fill");
  disposeSectionCaps(caps);
  assert.equal(caps.group.parent, null);
  assert.equal(buildSectionCaps(new Group().add(sheet.clone()), planes, 10), null, "nothing closed, nothing to fill");
});

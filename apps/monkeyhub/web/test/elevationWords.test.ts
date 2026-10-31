import assert from "node:assert/strict";
import test from "node:test";
import { elementWords } from "../src/features/stage/elementWords.ts";

test("the elevation reference list names an element by what it is, never by its id (S15)", () => {
  assert.equal(elementWords("wallbase.wall.head-joint-c2-1-body", true), "墙");
  assert.equal(elementWords("floor.slab-2-body", true), "楼板");
  assert.equal(elementWords("roof-north", true), "屋顶");
  assert.equal(elementWords("frame.beam-3", false), "Beam");
  assert.equal(elementWords("xyz-17", true), "构件");
});

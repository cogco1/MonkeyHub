import assert from "node:assert/strict";
import test from "node:test";

import {
  encodeViewportPng,
  type PngCanvas,
} from "../src/viewer/viewportScreenshot.ts";

test("PNG encoding redraws the viewport immediately before serialization", async () => {
  const events: string[] = [];
  const png = new Blob(["png bytes"], { type: "image/png" });
  const canvas: PngCanvas = {
    width: 1200,
    height: 800,
    toBlob(callback, type) {
      events.push(`encode:${type}`);
      callback(png);
    },
  };

  const result = await encodeViewportPng(canvas, () => events.push("render"));

  assert.equal(result, png);
  assert.deepEqual(events, ["render", "encode:image/png"]);
});

test("PNG encoding rejects an empty canvas without rendering or encoding", async () => {
  const events: string[] = [];
  await assert.rejects(
    encodeViewportPng(
      {
        width: 0,
        height: 800,
        toBlob: () => events.push("encode"),
      },
      () => events.push("render"),
    ),
    /no drawable pixels/,
  );
  assert.deepEqual(events, []);
});

test("PNG encoding rejects a null or empty browser result", async () => {
  await assert.rejects(
    encodeViewportPng(
      { width: 1200, height: 800, toBlob: (callback) => callback(null) },
      () => undefined,
    ),
    /did not produce a PNG/,
  );
  await assert.rejects(
    encodeViewportPng(
      {
        width: 1200,
        height: 800,
        toBlob: (callback) => callback(new Blob([], { type: "image/png" })),
      },
      () => undefined,
    ),
    /did not produce a PNG/,
  );
});

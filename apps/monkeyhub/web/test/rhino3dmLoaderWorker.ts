import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * The installed Rhino3dmLoader's own decoder, run in this process for the Node tests.
 *
 * The loader's worker cannot start here (no Web Worker), so its body is
 * assembled exactly as ``Rhino3dmLoader._initLibrary`` assembles it -
 * ``rhino3dm.js`` text followed by the ``Rhino3dmWorker`` function body -
 * and run in-process with a ``self`` shim; the decode message it posts is
 * what a test hands to the real ``_createGeometry``. Nothing of the decoder is
 * re-implemented.
 */

const web = dirname(dirname(fileURLToPath(import.meta.url)));
export const loaderPath = join(web, "node_modules", "three", "examples", "jsm", "loaders", "3DMLoader.js");
const rhinoDir = join(web, "node_modules", "rhino3dm");

interface WorkerMessage {
  type: string;
  id: number;
  data?: DecodedFile;
  error?: unknown;
}

export interface DecodedFile {
  layers: Array<{ name: string; visible: boolean; color: { r: number; g: number; b: number } }>;
  materials: Array<{ name: string; transparency: number; diffuseColor: { r: number; g: number; b: number } }>;
  objects: Array<{
    objectType: string;
    attributes: {
      name: string;
      visible: boolean;
      layerIndex: number;
      materialSource: { name: string; value: number };
      materialIndex: number;
      drawColor: { r: number; g: number; b: number };
    };
  }>;
}

/** Run the loader's own worker body in this process and decode one file with it. */
export async function decodeWithLoaderWorker(bytes: Buffer): Promise<DecodedFile> {
  const source = readFileSync(loaderPath, "utf8");
  const start = source.indexOf("function Rhino3dmWorker()");
  const end = source.indexOf("export {", start);
  assert.ok(start >= 0 && end > start, "the installed 3DMLoader.js carries Rhino3dmWorker");
  const fn = source.slice(start, end);
  const body = [
    "/* rhino3dm.js */",
    readFileSync(join(rhinoDir, "rhino3dm.js"), "utf8"),
    "/* worker */",
    fn.substring(fn.indexOf("{") + 1, fn.lastIndexOf("}")),
  ].join("\n");

  const posted: WorkerMessage[] = [];
  const self = { postMessage: (message: WorkerMessage) => posted.push(message) };
  const scope = globalThis as { onmessage?: (event: { data: unknown }) => void };
  const previous = scope.onmessage;
  try {
    // rhino3dm.js detects Node and asks for fs; the loader hands the wasm over as bytes.
    new Function("require", "__dirname", "__filename", "self", body)(
      createRequire(import.meta.url),
      rhinoDir,
      join(rhinoDir, "rhino3dm.js"),
      self,
    );
    const onmessage = scope.onmessage;
    assert.equal(typeof onmessage, "function", "the worker body installs onmessage");
    onmessage!({ data: { type: "init", libraryConfig: { wasmBinary: readFileSync(join(rhinoDir, "rhino3dm.wasm")) } } });
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    onmessage!({ data: { type: "decode", id: 1, buffer } });
    const deadline = Date.now() + 30_000;
    while (posted.length === 0) {
      assert.ok(Date.now() < deadline, "the worker decoded the file within 30 s");
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  } finally {
    scope.onmessage = previous;
  }
  const [message] = posted;
  assert.equal(message.type, "decode", `worker replied ${message.type}: ${String(message.error)}`);
  return message.data!;
}

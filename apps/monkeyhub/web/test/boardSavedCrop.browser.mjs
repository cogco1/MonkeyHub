/** A saved Board crop of an A4 page shows the same region after the page raster moved to the server (#368).
 *
 * Excalidraw keeps an image's `crop` in the source image's pixels. Board drew a PDF page 2048 px on its long
 * edge, so an A4 page (595 x 842 pt) was 1447 x 2048 px, and saved crops are in those pixels. This opens a
 * saved board whose one element crops the red quarter of such a page, against a real Studio API with the
 * Hub's cache directory, and samples the real canvas: only red may show. A raster of another size would move
 * the crop onto the green and blue quarters.
 */
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer as createHttpServer, request as httpRequest } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import { workspaceFixture } from "./workspaceFixture.mjs";

const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const repoRoot = path.resolve(webRoot, "../../..");
const apiRoot = path.resolve(repoRoot, "services/project-runtime");
const python = process.env.PYTHON ?? "python";
const pythonEnv = { ...process.env, PYTHONUTF8: "1",
  PYTHONPATH: [repoRoot, path.join(apiRoot, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) };
const root = await mkdtemp(path.join(tmpdir(), "monkeyboard-saved-crop-"));
const projectDir = path.join(root, "demo-project");
const errors = [];
const http = createHttpServer();
let api, vite, browser, page, closing = false, apiLog = "";
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

try {
  const fixture = spawnSync(python, ["-c", `
import base64, json, sys
from io import BytesIO
from pathlib import Path
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject
from tests.support import make_empty_project
make_empty_project(Path(sys.argv[1]))
# One A4 page in four quarters: red top left, green top right, blue bottom left, yellow bottom right.
writer = PdfWriter()
page = writer.add_blank_page(width=595, height=842)
content = DecodedStreamObject()
content.set_data(b"1 0 0 rg 0 421 297.5 421 re f 0 1 0 rg 297.5 421 297.5 421 re f "
                 b"0 0 1 rg 0 0 297.5 421 re f 1 1 0 rg 297.5 0 297.5 421 re f\\n")
page[NameObject("/Contents")] = writer._add_object(content)
output = BytesIO()
writer.write(output)
print(json.dumps({"pdf": base64.b64encode(output.getvalue()).decode()}))
`, root], { cwd: apiRoot, encoding: "utf8", env: pythonEnv });
  assert.equal(fixture.status, 0, fixture.stderr || fixture.stdout);
  const { pdf } = JSON.parse(fixture.stdout.trim());

  const apiPort = await new Promise((resolve) => {
    const probe = createHttpServer();
    probe.listen(0, "127.0.0.1", () => { const { port } = probe.address(); probe.close(() => resolve(port)); });
  });
  api = spawn(python, ["-m", "project_runtime.main", "--port", String(apiPort), "--project-dir", projectDir], {
    cwd: apiRoot, env: { ...pythonEnv, ARCHFLOW_STUDIO_CAD_EXPORT: "off", ARCHFLOW_STUDIO_INTENT_PROVIDER: "deterministic",
      ARCHFLOW_STUDIO_CACHE_DIR: path.join(root, "cache") },
    stdio: ["ignore", "pipe", "pipe"],
  });
  api.on("error", (error) => errors.push(error.message));
  api.stdout.on("data", (chunk) => { apiLog = (apiLog + chunk).slice(-8000); });
  api.stderr.on("data", (chunk) => { apiLog = (apiLog + chunk).slice(-8000); });
  const apiOrigin = `http://127.0.0.1:${apiPort}`;
  for (let attempt = 0; attempt < 3000; attempt++) {
    if (await fetch(`${apiOrigin}/api/health`).then((response) => response.ok).catch(() => false)) break;
    assert.ok(attempt < 2999 && api.exitCode === null, `Studio startup failed: ${apiLog}`);
    await delay(100);
  }
  async function call(method, route, body) {
    const response = await fetch(apiOrigin + route, { method,
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body) });
    const value = await response.json();
    assert.ok(response.ok, `${method} ${route}: ${response.status} ${JSON.stringify(value)}`);
    return value;
  }
  const project = await call("GET", "/api/project");
  const document = await call("POST", "/api/documents", { projectId: project.projectId, runId: null,
    fileName: "a4-quarters.pdf", mimeType: "application/pdf", contentBase64: pdf });
  // The board as a build before #368 saved it: the red quarter of the 1447 x 2048 px page.
  const crop = { x: 0, y: 0, width: 723, height: 1024, naturalWidth: 1447, naturalHeight: 2048 };
  const element = { id: "cropped-a4", type: "image", fileId: "cropped-a4-page", x: 0, y: 0, width: 361.5, height: 512,
    angle: 0, isDeleted: false, scale: [1, 1], status: "saved", crop,
    customData: { sourceDocument: { runId: document.runId, assetSha256: document.assetSha256,
      revisionRef: document.revisionRef ?? null, pageIndex: 0 } } };
  const current = await call("GET", "/api/board");
  await call("PUT", "/api/board", { projectId: project.projectId, baseRevisionSha256: current.revisionSha256,
    title: current.title, elements: [element],
    seenDocuments: [JSON.stringify([document.runId, document.revisionRef ?? document.assetSha256])] });

  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, logLevel: "error", publicDir: ".generated/public",
    cacheDir: path.join(root, "vite-cache"), define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    plugins: [react(), workspaceFixture()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (!request.url.startsWith("/api/")) { vite.middlewares(request, response); return; }
    const proxied = httpRequest({ hostname: "127.0.0.1", port: apiPort, path: request.url,
      method: request.method, headers: { ...request.headers, host: `127.0.0.1:${apiPort}` } }, (answer) => {
      response.writeHead(answer.statusCode, answer.headers); answer.pipe(response);
    });
    proxied.on("error", (error) => {
      if (!closing) errors.push(error.message);
      if (!response.headersSent) response.writeHead(502);
      response.end();
    });
    request.pipe(proxied);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1200, height: 900 }, locale: "en-US" });
  await context.addInitScript(() => { window.EXCALIDRAW_ASSET_PATH = `${location.origin}/excalidraw/`; });
  await context.route((url) => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, (route) => {
    errors.push(`External request: ${route.request().url()}`); return route.abort("blockedbyclient");
  });
  page = await context.newPage(); page.setDefaultTimeout(30_000);
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto(`${origin}/?view=board&embedded=tool&lang=en`, { waitUntil: "domcontentloaded" });
  await page.locator(".monkeyboard-initializing").waitFor({ state: "hidden" });
  await page.locator(".monkeyboard-canvas canvas").first().waitFor();
  // The quarters the static canvas shows, by colour.
  const quarters = () => page.evaluate(() => {
    const canvas = document.querySelector("canvas.excalidraw__canvas.static") ?? document.querySelector(".excalidraw canvas");
    const image = canvas.getContext("2d", { willReadFrequently: true }).getImageData(0, 0, canvas.width, canvas.height);
    const counts = { red: 0, green: 0, blue: 0, yellow: 0 };
    for (let index = 0; index < image.data.length; index += 4) {
      const [r, g, b, a] = image.data.slice(index, index + 4);
      if (a < 200) continue;
      if (r > 200 && g < 60 && b < 60) counts.red += 1;
      else if (r < 60 && g > 200 && b < 60) counts.green += 1;
      else if (r < 60 && g < 60 && b > 200) counts.blue += 1;
      else if (r > 200 && g > 200 && b < 60) counts.yellow += 1;
    }
    return counts;
  });
  let shown = await quarters();
  for (let attempt = 0; attempt < 150 && shown.red === 0; attempt++) {
    await delay(100);
    if (attempt % 20 === 10) {
      await page.locator(".monkeyboard-canvas canvas").last().click({ position: { x: 5, y: 5 } }).catch(() => {});
      await page.keyboard.press("Shift+1"); // fit the board's content on screen
    }
    shown = await quarters();
  }
  assert.ok(shown.red > 10_000, `The saved crop must show the red quarter: ${JSON.stringify(shown)}`);
  assert.deepEqual({ green: shown.green, blue: shown.blue, yellow: shown.yellow }, { green: 0, blue: 0, yellow: 0 },
    `The saved crop must show only its own quarter: ${JSON.stringify(shown)}`);
  const saved = await call("GET", "/api/board");
  assert.deepEqual(saved.elements.find((item) => item.id === element.id).crop, crop, "Opening the board keeps its crop");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: "a saved crop of an A4 page shows the same region", shown }));
} catch (error) {
  console.error(`FAILED: ${error?.stack ?? error}`);
  if (page && !page.isClosed()) console.error(await page.locator("body").innerText().catch(() => ""));
  console.error(JSON.stringify({ errors, apiLog }));
  throw error;
} finally {
  closing = true;
  await browser?.close(); await vite?.close();
  if (http.listening) await new Promise((resolve) => http.close(resolve));
  if (api && api.exitCode === null) {
    // A graceful stop can wait on a proxied stream the closed browser left open; do not let it hold the run.
    const exited = new Promise((resolve) => api.once("exit", resolve)); api.kill();
    const stuck = setTimeout(() => api.kill("SIGKILL"), 10_000); await exited; clearTimeout(stuck);
  }
  assert.ok(path.basename(root).startsWith("monkeyboard-saved-crop-"));
  await rm(root, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
}

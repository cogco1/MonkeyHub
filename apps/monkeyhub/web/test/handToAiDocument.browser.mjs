/** S08: 「交给 AI」 on a document page, with its references folded beside it.
 *
 * The real page editor (DocumentCanvas, its annotation draft and page raster) inside the project's hand-to-AI
 * context; the context records what it is handed and the project API is answered in memory. A page with one
 * saved circle gets a sentence and one reference with its purpose: the page as drawn, the marks file and the
 * reference go over, the page's marks and words are cleared, and the screen shows no service words. The older
 * page note (exact-model submit) is not on the ordinary screen.
 */
import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { crc32, deflateSync } from "node:zlib";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import { cmd } from "./commandLocator.mjs";
import { assertPlainScreen } from "./plainScreen.mjs";
import { workspaceFixture } from "./workspaceFixture.mjs";

const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const cacheDir = await mkdtemp(path.join(tmpdir(), "hand-to-ai-document-"));
const projectId = "hand-to-ai-document-fixture";

function png(width, height, [r, g, b]) {
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
    const check = Buffer.alloc(4); check.writeUInt32BE(crc32(body) >>> 0);
    return Buffer.concat([length, body, check]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4);
  header[8] = 8; header[9] = 2;
  const row = Buffer.alloc(1 + width * 3);
  for (let x = 0; x < width; x++) row.set([r, g, b], 1 + x * 3);
  const raw = Buffer.concat(Array.from({ length: height }, () => row));
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header), chunk("IDAT", deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}
const documentOf = (letter, fileName, colour) => ({ document: { projectId, runId: "documents-a", assetSha256: letter.repeat(64), revisionRef: null, fileName,
  mimeType: "image/png", sizeBytes: 1, pageCount: 1, pages: [{ pageIndex: 0, width: 600, height: 400, rotation: 0 }],
  modelSource: null, modelSourceBindingRef: null, sourceStageRef: null }, bytes: png(600, 400, colour) });
const plan = documentOf("a", "一层平面.png", [230, 230, 230]), detail = documentOf("b", "卧室墙角.png", [200, 220, 240]);
const circle = { id: "circle-1", kind: "circle", points: [[0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4]], color: "#e03131", lineWidth: 0.004 };
const sentence = "这扇窗加宽到一米五";

let vite, browser;
const http = createHttpServer();
const escaped = [], errors = [], saves = [];
try {
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] },
    logLevel: "error", cacheDir, publicDir: ".generated/public",
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    plugins: [workspaceFixture(), { name: "hand-to-ai-document-fixture", enforce: "pre", transform(code, id) {
      const filename = id.split("?")[0].replaceAll("\\", "/"), root = webRoot.replaceAll("\\", "/");
      if (filename === `${root}/test/workspace-fixture.tsx`) return { code: `
        import { useState } from "react";
        import { createRoot } from "react-dom/client";
        import { DocumentCanvas } from "/src/workspaces/monkeydiagram/DocumentCanvas";
        import { createDocumentAnnotationsController } from "/src/workspaces/monkeydiagram/useDocumentAnnotations";
        import { HandToAiContext } from "/src/features/handToAi/handToAi";
        import { UserPreferencesProvider, useStudio } from "/test/TestProviders.tsx";
        import "/src/app/styles.css";
        window.__handed = [];
        const handOver = async (request) => {
          const files = await Promise.all(request.files.map(async (file) => ({ name: file.name, type: file.type, size: file.size,
            text: file.type === "application/json" ? await file.text() : null })));
          window.__handed.push({ ...request, files });
          return { chatId: "chat-1" };
        };
        function Page() {
          const studio = useStudio();
          const [controller] = useState(() => createDocumentAnnotationsController(studio));
          return <div style={{ height: "100vh" }}><DocumentCanvas projectId=${JSON.stringify(projectId)} runId="documents-a" controller={controller} busy={false}
            onSubmit={async () => { throw new Error("Unexpected page note"); }} modelSources={[]} editingModelSource={null}
            onContinueModelSource={async () => undefined} documentVisualInputAvailable initialSourceSha=${JSON.stringify(plan.document.assetSha256)} initialPageIndex={0} /></div>;
        }
        createRoot(document.getElementById("root")).render(<UserPreferencesProvider>
          <HandToAiContext.Provider value={handOver}><Page /></HandToAiContext.Provider>
        </UserPreferencesProvider>);
      `, map: null };
    } }, react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (request.url?.startsWith("/api/")) { escaped.push(request.url); response.writeHead(405); response.end(); return; }
    vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(() => {
    localStorage.setItem("archflow-studio.user-preferences", JSON.stringify({ version: 1, language: "zh-CN" }));
  });
  await context.route((url) => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, (route) => {
    escaped.push(route.request().url()); return route.abort("blockedbyclient");
  });
  const page = await context.newPage(); page.setDefaultTimeout(15_000);
  page.on("pageerror", (error) => errors.push(error.message));
  // The page's saved marks, as the Runtime keeps them; each save is a new revision.
  const saved = new Map([[plan.document.assetSha256, { annotations: [circle], comment: "", revision: 1 }], [detail.document.assetSha256, { annotations: [], comment: "", revision: 0 }]]);
  const answer = (sha) => { const page = saved.get(sha);
    return { projectId, runId: "documents-a", assetSha256: sha, pageIndex: 0, drawingRevisionRef: null, tracingCalibration: null,
      revisionSha256: page.revision ? String(page.revision).padStart(64, "0") : null, annotations: page.annotations, comment: page.comment }; };
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    if (method === "GET" && url.pathname === "/api/documents") return route.fulfill({ json: { projectId, documents: [plan.document, detail.document] } });
    const bytes = [plan, detail].find((item) => url.pathname === `/api/documents/${item.document.assetSha256}/bytes`);
    if (method === "GET" && bytes) return route.fulfill({ status: 200, contentType: "image/png", body: bytes.bytes });
    if (method === "GET" && url.pathname === "/api/document-annotations") return route.fulfill({ json: answer(url.searchParams.get("assetSha256")) });
    if (method === "PUT" && url.pathname === "/api/document-annotations") {
      const body = request.postDataJSON(); saves.push(body);
      const current = saved.get(body.assetSha256);
      saved.set(body.assetSha256, { annotations: body.annotations, comment: body.comment ?? "", revision: current.revision + 1 });
      return route.fulfill({ json: answer(body.assetSha256) });
    }
    if (method === "GET" && url.pathname === "/api/document-comments") return route.fulfill({ json: { projectId, runId: "documents-a", comments: [] } });
    escaped.push(`${method} ${url.pathname}`);
    return route.fulfill({ status: 405, json: { detail: "Unexpected fixture request" } });
  });

  await page.goto(origin);
  const bar = page.locator(".document-hand-to-ai");
  await bar.waitFor();
  await page.waitForFunction(() => !document.querySelector(".hand-to-ai__words")?.disabled);
  assert.equal(await cmd(page, "markup.hand_to_ai.submit_page_note").count(), 0, "the exact-model page note is not on the ordinary screen");
  assert.equal(await page.locator(".hand-to-ai__references").getAttribute("open"), null, "references are folded until opened");
  await assertPlainScreen(page, "document page with 交给 AI");
  console.log("PASS the page offers one 交给 AI, its references folded");

  await bar.locator(".hand-to-ai__words").fill(sentence);
  await bar.locator(".hand-to-ai__references summary").click();
  const reference = bar.locator(".document-reference").filter({ hasText: "卧室墙角.png" });
  await reference.locator("input[type=checkbox]").first().check();
  await reference.locator("input[type=text]").fill("墙角做法参照这里");
  assert.match(await bar.locator(".hand-to-ai__references summary").innerText(), /附参照（1）/);
  await cmd(bar, "markup.hand_to_ai.hand_over").click();
  await page.waitForFunction(() => window.__handed.length === 1);
  const [handed] = await page.evaluate(() => window.__handed);
  assert.equal(handed.surface, "document");
  assert.equal(handed.words, sentence);
  assert.equal(handed.marks, 1);
  assert.deepEqual(handed.files.map((file) => file.name),
    ["hand-to-ai-document-page.png", "hand-to-ai-document-marks.json", "hand-to-ai-document-reference-1.png"]);
  const marks = JSON.parse(handed.files[1].text);
  assert.equal(marks.marks[0].note, sentence, "the sentence hangs on the circle");
  assert.equal(marks.page.fileName, "一层平面.png");
  assert.deepEqual(marks.references, [{ fileName: "卧室墙角.png", pageNumber: 1, purpose: "墙角做法参照这里", withMarks: false }]);
  console.log("PASS the page, its circle with the sentence, and the reference with its purpose are handed over");

  await page.waitForFunction(() => document.querySelectorAll(".document-workspace [data-saved-ink] [data-stroke-id]").length === 0);
  assert.equal(await bar.locator(".hand-to-ai__words").inputValue(), "", "the words are cleared from the page");
  await page.getByText("已交给 AI").waitFor();
  for (let attempt = 0; attempt < 50 && !saves.some((body) => body.annotations.length === 0); attempt++) await new Promise((resolve) => setTimeout(resolve, 100));
  assert.ok(saves.some((body) => body.annotations.length === 0 && !body.comment), `the cleared page is saved, its earlier revision kept: ${JSON.stringify(saves.map((body) => [body.annotations.length, body.comment]))}`);
  await assertPlainScreen(page, "after 交给 AI on the page");
  console.log("PASS the page's marks and words are cleared once handed over");

  assert.deepEqual(escaped, [], "No unexpected API or external service requests");
  assert.deepEqual(errors, [], "No page errors");
  console.log("PASS 交给 AI on a document page");
} finally {
  await browser?.close(); await vite?.close();
  await new Promise((resolve) => http.close(resolve));
}

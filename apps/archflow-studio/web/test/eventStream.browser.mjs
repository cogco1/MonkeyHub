import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";

const playwrightModule = process.env.PLAYWRIGHT_MODULE?.trim();
if (!playwrightModule) {
  throw new Error("Set PLAYWRIGHT_MODULE to the Playwright module file path.");
}

const eventTypes = [
  "candidate.queued", "candidate.running", "candidate.succeeded", "candidate.failed",
  "validation.computed", "model_asset.registered", "working_copy.option_added",
];
const event = (seq, type = "model_asset.registered") => ({
  seq, at: "2026-09-09T00:00:00Z", type, runId: "event-stream-fixture",
});

const fixture = `
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { useStudioEvents } from "/src/features/events/EventStream.tsx";

const state = window.__eventStream = { connections: [], callbacks: [], lines: [] };
class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = new Map();
    this.closed = false;
    this.closeCount = 0;
    state.connections.push(this);
  }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }
  removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
  close() { this.closed = true; this.closeCount++; }
  open() { if (!this.closed) this.onopen?.(new Event("open")); }
  fail() { if (!this.closed) this.onerror?.(new Event("error")); }
  emit(type, value) {
    if (this.closed) return;
    const message = new MessageEvent(type, { data: typeof value === "string" ? value : JSON.stringify(value) });
    for (const listener of this.listeners.get(type) ?? []) listener(message);
    if (type === "message") this.onmessage?.(message);
  }
}
window.EventSource = FakeEventSource;
function Harness() {
  const [enabled, setEnabled] = useState(false);
  const [callbackVersion, setCallbackVersion] = useState("first");
  const lines = useStudioEvents(enabled, (event) => state.callbacks.push({ callbackVersion, event }));
  state.lines = lines;
  state.callbackVersion = callbackVersion;
  return React.createElement(React.Fragment, null,
    React.createElement("button", { onClick: () => setEnabled(true) }, "Enable"),
    React.createElement("button", { onClick: () => setEnabled(false) }, "Disable"),
    React.createElement("button", { onClick: () => setCallbackVersion("latest") }, "Update callback"),
    React.createElement("pre", { id: "lines" }, JSON.stringify(lines)));
}
const root = createRoot(document.getElementById("root"));
state.unmount = () => root.unmount();
root.render(React.createElement(Harness));
`;

test("Studio events keep one connection and deliver only accepted frames to the current callback", async (t) => {
  const root = fileURLToPath(new URL("../", import.meta.url));
  const cacheDir = await mkdtemp(join(tmpdir(), "monkeyarch-event-stream-test-"));
  const http = createHttpServer();
  const vite = await createServer({
    root, configFile: false, appType: "custom", publicDir: false, cacheDir,
    plugins: [react(), {
      name: "event-stream-test-fixture",
      resolveId(id) { if (id === "/__event_stream_fixture.jsx") return "\0event-stream-fixture.jsx"; },
      load(id) { if (id === "\0event-stream-fixture.jsx") return fixture; },
    }],
    server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null },
  });
  let browser;
  t.after(async () => {
    await browser?.close();
    if (http.listening) await new Promise((resolve, reject) => {
      http.close((error) => error ? reject(error) : resolve());
    });
    await vite.close();
    assert.equal(dirname(resolve(cacheDir)), resolve(tmpdir()));
    assert.ok(basename(cacheDir).startsWith("monkeyarch-event-stream-test-"));
    await rm(cacheDir, { recursive: true, force: true });
  });
  http.on("request", (request, response) => {
    if (request.url === "/__event_stream_test__") {
      void vite.transformIndexHtml(request.url, '<!doctype html><html><body><div id="root"></div><script type="module" src="/__event_stream_fixture.jsx"></script></body></html>')
        .then((html) => { response.setHeader("Content-Type", "text/html"); response.end(html); });
    } else vite.middlewares(request, response, () => { response.statusCode = 404; response.end(); });
  });
  await new Promise((resolve) => { http.listen(0, "127.0.0.1", resolve); });
  const address = http.address();
  assert.ok(address !== null && typeof address !== "string");
  const { chromium } = await import(pathToFileURL(playwrightModule).href);
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  const apiRequests = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    apiRequests.push(`${route.request().method()} ${route.request().url()}`);
    await route.abort("blockedbyclient");
  });
  await page.goto(`http://127.0.0.1:${address.port}/__event_stream_test__`);
  await page.locator("#lines").waitFor();

  await t.test("disabled mount stays disconnected; every named event is registered and duplicate seq is delivered once", async () => {
    assert.equal(await page.evaluate(() => window.__eventStream.connections.length), 0);
    await page.getByRole("button", { name: "Enable", exact: true }).click();
    await page.waitForFunction(() => window.__eventStream.connections.length === 1);
    assert.deepEqual(await page.evaluate(() => [...window.__eventStream.connections[0].listeners.keys()]), eventTypes);
    assert.equal(await page.evaluate(() => new URL(window.__eventStream.connections[0].url, location.href).pathname), "/api/events");
    await page.evaluate((events) => {
      const source = window.__eventStream.connections[0];
      source.open();
      for (const event of events) source.emit(event.type, event);
      source.emit(events[5].type, events[5]);
      source.emit("message", events[5]);
    }, eventTypes.map((type, index) => event(index + 1, type)));
    await page.waitForFunction(() => window.__eventStream.lines.length === 7);
    assert.deepEqual(await page.evaluate(() => window.__eventStream.callbacks.map((call) => call.event.type)), eventTypes);
    assert.deepEqual(await page.evaluate(() => window.__eventStream.lines.map((line) => line.seq)), [1, 2, 3, 4, 5, 6, 7]);
  });

  await t.test("a React rerender updates the callback without reopening the stream", async () => {
    await page.getByRole("button", { name: "Update callback", exact: true }).click();
    await page.waitForFunction(() => window.__eventStream.callbackVersion === "latest");
    await page.evaluate((event) => window.__eventStream.connections[0].emit(event.type, event), event(8, "working_copy.option_added"));
    await page.waitForFunction(() => window.__eventStream.lines.length === 8);
    assert.deepEqual(await page.evaluate(() => ({
      connections: window.__eventStream.connections.length,
      closed: window.__eventStream.connections[0].closed,
      callback: window.__eventStream.callbacks.at(-1).callbackVersion,
    })), { connections: 1, closed: false, callback: "latest" });
  });

  await t.test("malformed frames remain visible without a callback and seq gaps retain their original notice", async () => {
    await page.evaluate((event) => {
      const source = window.__eventStream.connections[0];
      source.emit("model_asset.registered", "{malformed");
      source.emit(event.type, event);
    }, event(11));
    await page.waitForFunction(() => window.__eventStream.lines.length === 11);
    assert.equal(await page.evaluate(() => window.__eventStream.callbacks.length), 9);
    assert.deepEqual(await page.evaluate(() => window.__eventStream.lines
      .filter((line) => line.kind !== "event")
      .map(({ kind, messageKey, parameters }) => ({ kind, messageKey, parameters }))), [
      { kind: "transport", messageKey: "evidence.events.parseFailure", parameters: { frame: "{malformed" } },
      { kind: "gap", messageKey: "evidence.events.gapMany", parameters: { count: 2, start: 9, end: 10 } },
    ]);
  });

  await t.test("reconnection accepts a new process's low seq on the same EventSource", async () => {
    await page.evaluate((event) => {
      const source = window.__eventStream.connections[0];
      source.fail(); source.open(); source.emit(event.type, event);
    }, event(1, "working_copy.option_added"));
    await page.waitForFunction(() => window.__eventStream.lines.length === 14);
    assert.equal(await page.evaluate(() => window.__eventStream.connections.length), 1);
    assert.equal(await page.evaluate(() => window.__eventStream.callbacks.length), 10);
    assert.deepEqual(await page.evaluate(() => window.__eventStream.lines.slice(-3).map((line) => line.messageKey ?? line.key)), [
      "evidence.events.dropped", "evidence.events.reconnected", "seq:2:1",
    ]);
  });

  await t.test("disabling and unmounting both close their connection and remove named listeners", async () => {
    await page.getByRole("button", { name: "Disable", exact: true }).click();
    await page.waitForFunction(() => window.__eventStream.connections[0].closed);
    assert.equal(await page.evaluate(() => [...window.__eventStream.connections[0].listeners.values()].reduce((n, listeners) => n + listeners.size, 0)), 0);
    await page.evaluate((event) => window.__eventStream.connections[0].emit(event.type, event), event(2));
    assert.equal(await page.evaluate(() => window.__eventStream.callbacks.length), 10);
    await page.getByRole("button", { name: "Enable", exact: true }).click();
    await page.waitForFunction(() => window.__eventStream.connections.length === 2);
    await page.evaluate((event) => {
      const source = window.__eventStream.connections[1];
      source.open(); source.emit(event.type, event);
    }, event(1));
    await page.waitForFunction(() => window.__eventStream.lines.at(-1)?.key === "seq:3:1");
    assert.equal(await page.evaluate(() => window.__eventStream.callbacks.at(-1).callbackVersion), "latest");
    await page.evaluate(() => window.__eventStream.unmount());
    await page.waitForFunction(() => window.__eventStream.connections[1].closed);
    assert.deepEqual(await page.evaluate(() => window.__eventStream.connections.map((source) => ({
      closeCount: source.closeCount,
      listeners: [...source.listeners.values()].reduce((n, listeners) => n + listeners.size, 0),
    }))), [{ closeCount: 1, listeners: 0 }, { closeCount: 1, listeners: 0 }]);
  });

  assert.deepEqual(errors, []);
  assert.deepEqual(apiRequests, [], "the fixture never contacts an API or opens a real SSE connection");
});

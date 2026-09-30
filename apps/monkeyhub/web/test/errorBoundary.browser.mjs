/** The crash recovery action reloads the document without changing its identity or storage. */
import assert from "node:assert/strict";
import path from "node:path";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const temporary = await mkdtemp(path.join(tmpdir(), "monkeyhub-error-boundary-browser-"));
const fixtureId = path.join(webRoot, "error-boundary-fixture.tsx").replaceAll("\\", "/");
const fixture = `
import React from 'react';
import {createRoot} from 'react-dom/client';
import {ErrorBoundary} from '/src/app/ErrorBoundary';
import {UserPreferencesProvider} from '/test/TestProviders';
import '/src/styles.css';
function Crash() { throw new Error('fixture render failure'); }
function App() {
  const crash = new URLSearchParams(location.search).has('crash');
  return <UserPreferencesProvider><ErrorBoundary>{crash ? <Crash/> : <p>ready</p>}</ErrorBoundary></UserPreferencesProvider>;
}
createRoot(document.getElementById('root')).render(<App/>);
`;

let browser, page, server;
try {
  server = await createServer({
    root: webRoot,
    configFile: false,
    cacheDir: path.join(temporary, "vite"),
    logLevel: "error",
    resolve: { dedupe: ["react", "react-dom"] },
    optimizeDeps: { include: ["react", "react-dom/client", "react/jsx-runtime", "react/jsx-dev-runtime"] },
    plugins: [{
      name: "error-boundary-fixture",
      resolveId(id) {
        if (id === "/error-boundary-fixture.tsx") return fixtureId;
      },
      load(id) {
        if (id === fixtureId) return fixture;
      },
      configureServer(vite) {
        vite.middlewares.use((request, response, next) => {
          if (request.url?.split("?")[0] !== "/") return next();
          response.setHeader("content-type", "text/html");
          response.end('<html><body><div id="root"></div><script type="module" src="/error-boundary-fixture.tsx"></script></body></html>');
        });
      },
    }],
  });
  await server.listen();
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  page = await browser.newPage();
  page.setDefaultTimeout(20_000);
  await page.addInitScript(() => {
    const loads = Number(sessionStorage.getItem("fixture.documentLoads") ?? "0") + 1;
    sessionStorage.setItem("fixture.documentLoads", String(loads));
  });

  const origin = server.resolvedUrls.local[0];
  await page.goto(origin);
  const retained = {
    project: JSON.stringify({ projectDir: "/projects/villa", activeTool: "arch" }),
    design: JSON.stringify({ projectId: "villa", head: "sha256:retained-design" }),
  };
  await page.evaluate(values => {
    localStorage.setItem("monkeyhub.chat-view.v1", values.project);
    localStorage.setItem("fixture.retained-design", values.design);
  }, retained);

  const crashUrl = `${origin}?crash=render&project=villa`;
  await page.goto(crashUrl);
  const reload = page.getByRole("button", { name: "Reload the page", exact: true });
  await reload.waitFor();
  const loadsBefore = await page.evaluate(() => Number(sessionStorage.getItem("fixture.documentLoads")));
  await Promise.all([page.waitForNavigation({ waitUntil: "domcontentloaded" }), reload.click()]);
  await page.getByRole("button", { name: "Reload the page", exact: true }).waitFor();

  assert.equal(page.url(), crashUrl, "reload must keep the exact current URL and project locator");
  assert.equal(await page.evaluate(() => Number(sessionStorage.getItem("fixture.documentLoads"))), loadsBefore + 1,
    "the button must create a new document rather than remounting the failed React tree");
  assert.deepEqual(await page.evaluate(() => ({
    project: localStorage.getItem("monkeyhub.chat-view.v1"),
    design: localStorage.getItem("fixture.retained-design"),
  })), retained, "reload must leave persisted project selection and design state unchanged");
  const screenshot = path.join(temporary, "reloaded-error-boundary.png");
  await page.screenshot({ path: screenshot, fullPage: true });
  console.log(JSON.stringify({
    passed: "ErrorBoundary reload navigates the same document URL and preserves persisted state",
    screenshot,
  }));
} finally {
  await browser?.close();
  await server?.close();
}

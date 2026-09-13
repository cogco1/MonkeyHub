/** Observe a real benchmark turn without intercepting requests or authoring timing events. */
import { mkdir } from "node:fs/promises";
import { homedir } from "node:os";
import { isAbsolute, join } from "node:path";
import { pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

const [hubArgument, chatId, monitorArgument, outputDir] = process.argv.slice(2);
const pollMs = 300;
let browser;
let stage = "arguments";

function refuse(code) {
  const error = new Error(code);
  error.observerCode = code;
  throw error;
}

function origin(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) || url.username || url.password) {
    refuse("ISOLATED_LOOPBACK_URL_REQUIRED");
  }
  return url.origin;
}

async function readJson(base, route) {
  const response = await fetch(new URL(route, base), { signal: AbortSignal.timeout(5000), redirect: "error" });
  if (!response.ok) refuse(`HTTP_${response.status}`);
  return response.json();
}

function latestUser(session) {
  return session.messages?.findLast((message) => message.role === "user");
}

try {
  if (process.argv.length !== 6 || !chatId || !outputDir || !isAbsolute(outputDir)) refuse("FOUR_ARGUMENTS_WITH_ABSOLUTE_OUTPUT_REQUIRED");
  const hub = origin(hubArgument);
  const monitor = origin(monitorArgument);
  const sessionRoute = `/api/chat/sessions/${encodeURIComponent(chatId)}`;
  stage = "read_session";
  let session = await readJson(hub, sessionRoute);
  const user = latestUser(session);
  if (!user?.id || !session.projectDir) refuse("TURN_BINDING_MISSING");
  const turnId = user.id;

  stage = "resolve_studio";
  const apps = await readJson(hub, `/api/apps?${new URLSearchParams({ projectDir: session.projectDir })}`);
  const studio = apps.find((app) => app.appId === "monkeyarch");
  if (studio?.state !== "running" || !studio.url) refuse("STUDIO_NOT_RUNNING");
  const studioOrigin = origin(studio.url);

  // Prepare the independent browser while the provider is still working.
  stage = "launch_browser";
  const playwright = process.env.PLAYWRIGHT_MODULE ?? join(homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs");
  const { chromium } = await import(pathToFileURL(playwright).href);
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  const preview = await context.newPage();

  stage = "wait_candidate";
  const candidateDeadline = Date.now() + 240000;
  let candidateId;
  while (Date.now() < candidateDeadline) {
    // Only messages belonging to the frozen turn may select its preview.
    if (latestUser(session)?.id !== turnId) refuse("TURN_CHANGED");
    const position = session.messages.findIndex((message) => message.id === turnId);
    const candidate = session.messages.slice(position + 1).find((message) =>
      message.status === "complete" && typeof message.candidateId === "string" && message.candidateId.length > 0);
    if (candidate) {
      candidateId = candidate.candidateId;
      break;
    }
    if (session.status !== "running") refuse("TURN_FINISHED_WITHOUT_CANDIDATE");
    await delay(pollMs);
    session = await readJson(hub, sessionRoute);
  }
  if (!candidateId) refuse("CANDIDATE_TIMEOUT");

  stage = "open_preview";
  const previewUrl = new URL("/", studioOrigin);
  previewUrl.search = new URLSearchParams({ workspace: "monkeyarch", candidate: candidateId }).toString();
  const previewRequestedAt = Date.now();
  const projectionDeadline = previewRequestedAt + 90000;
  await preview.goto(previewUrl.href, { waitUntil: "domcontentloaded", timeout: 45000 });

  stage = "wait_model_projection";
  let trace;
  let projection;
  while (Date.now() < projectionDeadline) {
    const snapshot = await readJson(monitor, "/api/traces");
    trace = snapshot.traces.find((row) => row.turn_id === turnId);
    projection = trace?.spans.find((span) => span.phase === "model_projection" && span.source === "studio" &&
      span.status === "succeeded" && span.ended_at && Date.parse(span.started_at) >= previewRequestedAt);
    if (projection) break;
    await delay(pollMs);
  }
  if (!projection) refuse("MODEL_PROJECTION_TIMEOUT");

  stage = "capture_preview";
  await mkdir(outputDir, { recursive: true });
  await preview.screenshot({ path: join(outputDir, "preview.png"), fullPage: true });

  stage = "capture_monitor";
  const dashboard = await context.newPage();
  await dashboard.goto(`${monitor}/?lang=zh-CN&theme=light`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await dashboard.waitForFunction((id) => Array.from(document.querySelector("#trace-select")?.options ?? []).some((option) => option.value === id), trace.trace_id, { timeout: 15000 });
  await dashboard.locator("#trace-select").selectOption(trace.trace_id);
  await dashboard.locator(`#trace-waterfall button[data-span-id="${projection.event_id}"]`).waitFor({ state: "attached", timeout: 15000 });
  await dashboard.screenshot({ path: join(outputDir, "monitor.png"), fullPage: true });

  console.log(JSON.stringify({ state: "succeeded", turn_id: turnId, model_projection: "succeeded",
    first_visible_ms: trace.summary.first_visible_ms, verified_ms: trace.summary.verified_ms,
    candidate_readback_succeeded: trace.spans.some((span) => span.phase === "candidate_readback" && span.details?.success === true),
    validation_receipt_observed: trace.spans.some((span) => span.phase === "validation" && typeof span.details?.validator_pass === "boolean") }));
} catch (error) {
  // Tool errors may contain URLs, private paths or page text. Export only our
  // fixed stage/code vocabulary; the benchmark runner owns detailed evidence.
  console.error(JSON.stringify({ state: "failed", stage, code: error.observerCode ?? "PREVIEW_OBSERVER_FAILED" }));
  process.exitCode = 1;
} finally {
  await browser?.close();
}

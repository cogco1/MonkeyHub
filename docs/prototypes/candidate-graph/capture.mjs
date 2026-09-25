/*
 * Screenshots and checks for the GH-284 Candidate Graph dev prototype.
 *
 *   node docs/prototypes/candidate-graph/capture.mjs
 *
 * Opens index.html through file:// in Chrome (Playwright channel "chrome"),
 * captures the six review states at 1440x900 plus a 390x844 narrow view and a
 * dark-theme view, walks the same states through the UI, and fails on any
 * console error, page error, failed load or non-file request.
 * PLAYWRIGHT_MODULE may point at another playwright/index.mjs.
 */
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const modulePath = process.env.PLAYWRIGHT_MODULE || "D:/MONKEYHUB_DEV/cache/headless-tests/node_modules/playwright/index.mjs";
const { chromium } = await import(pathToFileURL(modulePath).href);
const pageUrl = pathToFileURL(path.join(here, "index.html")).href;
const outDir = path.join(here, "screenshots");
await mkdir(outDir, { recursive: true });

const WIDE = { width: 1440, height: 900 };
const NARROW = { width: 390, height: 844 };

/* Each check returns a list of problems (empty when the state renders as specified). */
const CHECKS = {
  1: () => {
    const p = [];
    if (document.querySelector(".drawer")) p.push("tree should be closed");
    const chip = document.querySelector(".stage-chip")?.textContent.replace(/\s+/g, " ") || "";
    if (!chip.includes("S2 · Layout") || !chip.includes("Current")) p.push(`chip text: ${chip}`);
    if (!document.querySelector(".badge")) p.push("no compact badges");
    const card = document.querySelector('.result[data-study="massing"]')?.textContent || "";
    if (!card.includes("5 schemes ready") || !card.includes("Show in Design Tree")) p.push("result card text");
    return p;
  },
  2: () => {
    const p = [];
    if (!document.querySelector(".drawer .strip")) p.push("no spine strip");
    const rows = document.querySelectorAll('[data-anchor="study:massing"] .crow[data-status="ready"]').length;
    if (rows !== 5) p.push(`massing rows ${rows}`);
    if (!document.querySelector('[data-anchor="node:S0"] [data-anchor="study:massing"]')) p.push("study not under S0");
    const acts = [...document.querySelectorAll('[data-anchor="node:massing-D"] .acts .act')].map((b) => b.textContent.trim());
    for (const want of ["View", "Compare", "Continue from here", "Accept as next Stage", "Reject / Archive"]) {
      if (!acts.some((text) => text.startsWith(want))) p.push(`missing action ${want}`);
    }
    if (!document.querySelector('[data-anchor="node:massing-D"] .act[data-act="accept-ask"]')?.disabled) p.push("accept should need Continue first");
    return p;
  },
  3: () => {
    const p = [];
    const tiles = document.querySelectorAll(".compare .ctile").length;
    if (tiles < 2 || tiles > 5) p.push(`tiles ${tiles}`);
    if (!document.querySelector('.ctile[data-chosen="true"]')) p.push("nothing chosen");
    if (!document.querySelector('.compare__choice [data-act="continue"]')) p.push("no continue offer");
    if (document.querySelector('.compare [data-act="accept-ask"]')) p.push("compare offers accept");
    if (!document.querySelector(".drawer")) p.push("tree not beside compare");
    return p;
  },
  4: () => {
    const p = [];
    const banner = document.querySelector(".ro-banner")?.textContent.replace(/\s+/g, " ") || "";
    if (!/Viewing S0 · read-only · Continue from here/.test(banner)) p.push(`banner: ${banner}`);
    const chip = document.querySelector(".stage-chip")?.textContent || "";
    if (!chip.includes("S2 · Layout")) p.push("Working Head moved");
    return p;
  },
  5: () => {
    const p = [];
    if (!document.querySelector('.crow[data-anchor="node:entrance-A"][data-line="true"]')) p.push("A not on the current line");
    const accept = document.querySelector('.snode--head [data-act="accept-ask"]');
    if (!accept || accept.disabled) p.push("no separate Accept step");
    if (document.querySelectorAll(".snode:not(.snode--head)").length !== 3) p.push("Continue created a Stage");
    return p;
  },
  6: () => {
    const p = [];
    for (const status of ["ready", "working", "queued"]) {
      if (!document.querySelector(`[data-anchor="study:entrance"] .crow[data-status="${status}"]`)) p.push(`no ${status} row`);
    }
    if (!document.querySelector('[data-anchor="node:entrance-B"] details[open] li[data-result="running"]')) p.push("runs not in Advanced details");
    return p;
  },
};

const problems = [];
const browser = await chromium.launch({ channel: "chrome" });

async function open(hash, { viewport = WIDE, colorScheme = "light" } = {}) {
  const context = await browser.newContext({ viewport, colorScheme, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const label = `${hash} ${viewport.width}x${viewport.height} ${colorScheme}`;
  page.on("console", (msg) => { if (msg.type() === "error") problems.push(`${label}: console error: ${msg.text()}`); });
  page.on("pageerror", (err) => problems.push(`${label}: page error: ${err.message}`));
  page.on("requestfailed", (req) => problems.push(`${label}: failed load: ${req.url()}`));
  page.on("request", (req) => { if (!/^(file|data|about):/.test(req.url())) problems.push(`${label}: network request: ${req.url()}`); });
  await page.goto(`${pageUrl}#${hash}`);
  await page.waitForFunction(() => document.documentElement.dataset.ready === "1", null, { timeout: 10000 });
  return { page, context, label };
}

async function check(page, label, state) {
  const found = await page.evaluate(`(${CHECKS[state].toString()})()`);
  for (const item of found) problems.push(`${label}: ${item}`);
}

const shots = [
  ["01-closed.png", 1], ["02-tree-open.png", 2], ["03-compare.png", 3],
  ["04-old-stage-readonly.png", 4], ["05-continued.png", 5], ["06-running.png", 6],
];
for (const [file, state] of shots) {
  const { page, context, label } = await open(`state=${state}&theme=light`);
  await check(page, label, state);
  await page.screenshot({ path: path.join(outDir, file) });
  await context.close();
  console.log(`captured ${file}`);
}

{
  const { page, context, label } = await open("state=2&theme=light", { viewport: NARROW });
  await check(page, label, 2);
  const sheet = await page.evaluate(() => {
    const drawer = document.querySelector(".drawer");
    const box = drawer?.getBoundingClientRect();
    return drawer ? { position: getComputedStyle(drawer).position, width: box.width, bottom: box.bottom } : null;
  });
  if (!sheet || sheet.position !== "fixed" || sheet.width !== NARROW.width || sheet.bottom !== NARROW.height) problems.push(`${label}: tree is not a full-height sheet ${JSON.stringify(sheet)}`);
  await page.screenshot({ path: path.join(outDir, "07-narrow.png") });
  await context.close();
  console.log("captured 07-narrow.png");
}

/* Dark mode: every state renders and passes its check; one screenshot. */
for (let state = 1; state <= 6; state += 1) {
  const { page, context, label } = await open(`state=${state}&theme=dark`, { colorScheme: "dark" });
  await check(page, label, state);
  const ground = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  if (ground !== "rgb(32, 32, 34)") problems.push(`${label}: dark ground is ${ground}`);
  if (state === 2) { await page.screenshot({ path: path.join(outDir, "08-dark-tree-open.png") }); console.log("captured 08-dark-tree-open.png"); }
  await context.close();
}

/* The same states reached through the UI rather than the hash. */
{
  const { page, context, label } = await open("state=1&theme=light");
  const step = async (name, fn) => { try { await fn(); } catch (err) { problems.push(`${label}: UI walk "${name}": ${err.message.split("\n")[0]}`); } };
  await step("chip opens the tree beside the workspace", async () => {
    await page.click(".stage-chip");
    await page.waitForSelector(".drawer");
    if (!(await page.$(".ws .viewport"))) throw new Error("workspace replaced");
    await page.click('[data-act="tree-close"]');
  });
  await step("result card focuses its Study", async () => {
    await page.click('.result[data-study="massing"] .result__open');
    await page.waitForSelector('.study[data-anchor="study:massing"][data-focus="true"]');
  });
  await step("compare, choose, continue", async () => {
    await page.click('[data-act="compare"][data-id="massing"]');
    await page.waitForSelector(".compare .ctile");
    await page.click('[data-act="compare-choose"][data-id="massing-B"]');
    const before = await page.textContent(".stage-chip");
    if (!before.includes("S2")) throw new Error("choosing moved the Working Head");
    await page.click('.compare__choice [data-act="continue"]');
    await page.waitForSelector('.crow[data-anchor="node:massing-B"][data-line="true"]');
    if (!(await page.$('.snode[data-anchor="node:S1"][data-line="false"]'))) throw new Error("fork did not keep S1 as history");
  });
  await step("old Stage read-only", async () => {
    await page.click('[data-act="state"][data-id="1"]');
    await page.click(".stage-chip");
    await page.click('.scard[data-id="S0"]');
    await page.click('.snode[data-anchor="node:S0"] .acts [data-act="view"]');
    await page.waitForSelector(".ro-banner");
    const chip = await page.textContent(".stage-chip");
    if (!chip.includes("S2 · Layout")) throw new Error("viewing moved the Working Head");
  });
  await step("running badge, continue, accept as a separate step", async () => {
    await page.click('[data-act="back-head"]');
    await page.click('.badge:has(.dot--run)');
    await page.waitForSelector('[data-anchor="study:entrance"] .crow[data-status="working"]');
    await page.click('.crow__main[data-id="entrance-A"]');
    await page.click('[data-anchor="node:entrance-A"] .acts [data-act="continue"]');
    if (await page.$('.snode[data-anchor="node:S3"]')) throw new Error("Continue created a Stage");
    await page.click('.snode--head [data-act="accept-ask"]');
    await page.click('[data-act="accept-confirm"]');
    await page.waitForSelector('.snode[data-anchor="node:S3"]');
  });
  await step("admission and rejection before admission", async () => {
    await page.click('[data-act="state"][data-id="6"]');
    await page.click('[data-act="sim-admit"]');
    await page.waitForSelector('.crow[data-anchor="node:entrance-B"][data-status="ready"]');
    await page.click('[data-act="sim-reject"]');
    if (await page.$('.crow[data-anchor="node:entrance-C"]')) throw new Error("rejected result is still a node");
    const adv = await page.textContent('[data-key="adv:entrance"]');
    if (!adv.includes("Rejected before admission")) throw new Error("rejection not kept in Advanced details");
  });
  await context.close();
  console.log("walked the states through the UI");
}

await browser.close();
if (problems.length) {
  console.error(`\n${problems.length} problem(s):\n${problems.map((p) => `  - ${p}`).join("\n")}`);
  process.exit(1);
}
console.log("\nAll states rendered with no console errors, page errors, failed loads or network requests.");

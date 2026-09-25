/*
 * Screenshots and checks for the GH-284 Candidate Graph dev prototype.
 *
 *   node docs/prototypes/candidate-graph/capture.mjs            # 09-12 and every check
 *   node docs/prototypes/candidate-graph/capture.mjs --states   # also rewrite 01-08
 *
 * Opens index.html through file:// in Chrome (Playwright channel "chrome").
 * It checks the six review states at 1440x900, a 390x844 narrow view and the
 * dark theme, walks the states through the UI, and checks the growth tree:
 * one continuous trunk, N-1 twigs per Study (N when none was continued), no
 * edge crossings and no overlapping nodes. It writes 09-12 each run. 01-08
 * record the reviewed list view (PR #298) and are rewritten only with
 * --states. Any console error, page error, failed load or non-file request
 * fails the run. PLAYWRIGHT_MODULE may point at another playwright/index.mjs.
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
const WRITE_STATES = process.argv.includes("--states");
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

/* The growth tree: one trunk stroke through the Working Head's lineage, N-1 twigs per
   Study off the trunk (N when none was continued), no crossings, no overlapping nodes. */
const GROWTH = () => {
  const p = [];
  const trunks = document.querySelectorAll('.gt path[data-trunk="true"]');
  if (trunks.length !== 1) p.push(`trunk paths: ${trunks.length}`);
  const d = trunks[0]?.getAttribute("d") || "";
  if ((d.match(/M/g) || []).length !== 1) p.push("the trunk is more than one stroke");
  const pts = d.slice(1).split("L").map((part) => part.trim().split(/\s+/).map(Number));
  for (let i = 1; i < pts.length; i += 1) if (!(pts[i][0] > pts[i - 1][0]) || pts[i][1] !== pts[0][1]) p.push(`the trunk turns back at point ${i}`);
  const cg = window.__candidateGraph;
  const trunkNodes = [...document.querySelectorAll('.gtn[data-role="trunk"]')].map((n) => n.dataset.id);
  if (JSON.stringify(trunkNodes) !== JSON.stringify(cg.trunk)) p.push("trunk nodes differ from the lineage");
  if (trunkNodes.length !== pts.length) p.push(`${pts.length} trunk points for ${trunkNodes.length} trunk nodes`);
  if (trunkNodes[trunkNodes.length - 1] !== "head") p.push("the trunk does not end at Current");
  const onTrunk = new Set(cg.trunk);
  for (const study of cg.studies) {
    if (!onTrunk.has(study.base)) continue;
    const chosen = study.items.filter((id) => onTrunk.has(id)).length;
    const twigs = document.querySelectorAll(`.gtn[data-role="twig"][data-study="${study.id}"]`).length;
    if (twigs !== study.items.length - chosen) p.push(`${study.id}: ${twigs} twigs for ${study.items.length} options, ${chosen} continued`);
  }
  const segs = [];
  for (const path of document.querySelectorAll(".gt path")) {
    const q = path.getAttribute("d").slice(1).split("L").map((part) => part.trim().split(/\s+/).map(Number));
    for (let i = 1; i < q.length; i += 1) segs.push([q[i - 1], q[i]]);
  }
  const inside = (v, a, b) => v > Math.min(a, b) + 1e-6 && v < Math.max(a, b) - 1e-6;
  const shared = (a1, a2, b1, b2) => Math.min(Math.max(a1, a2), Math.max(b1, b2)) - Math.max(Math.min(a1, a2), Math.min(b1, b2));
  let crossings = 0;
  for (let i = 0; i < segs.length; i += 1) {
    for (let j = i + 1; j < segs.length; j += 1) {
      const a = segs[i], b = segs[j];
      const aH = a[0][1] === a[1][1], bH = b[0][1] === b[1][1];
      if (aH !== bH) {
        const [h, v] = aH ? [a, b] : [b, a];
        if (inside(v[0][0], h[0][0], h[1][0]) && inside(h[0][1], v[0][1], v[1][1])) crossings += 1;
      } else if (aH && a[0][1] === b[0][1] && shared(a[0][0], a[1][0], b[0][0], b[1][0]) > 1e-6) crossings += 1;
      else if (!aH && a[0][0] === b[0][0] && shared(a[0][1], a[1][1], b[0][1], b[1][1]) > 1e-6) crossings += 1;
    }
  }
  if (crossings) p.push(`${crossings} edge crossing(s)`);
  const boxes = [...document.querySelectorAll(".gt foreignObject")].map((f) => ["x", "y", "width", "height"].map((k) => Number(f.getAttribute(k))));
  let overlaps = 0;
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      const [x1, y1, w1, h1] = boxes[i], [x2, y2, w2, h2] = boxes[j];
      if (x1 < x2 + w2 - 1e-6 && x2 < x1 + w1 - 1e-6 && y1 < y2 + h2 - 1e-6 && y2 < y1 + h1 - 1e-6) overlaps += 1;
    }
  }
  if (overlaps) p.push(`${overlaps} overlapping node footprint(s)`);
  if (!document.querySelector(".gt__zoom") || !document.querySelector(".gt__fit")) p.push("no zoom bar or Fit");
  if (!/Wheel to zoom · Space or middle mouse to pan/.test(document.querySelector(".gt__foot")?.textContent || "")) p.push("no canvas hint");
  return p;
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

async function growthCheck(page, label) {
  const found = await page.evaluate(`(${GROWTH.toString()})()`);
  for (const item of found) problems.push(`${label}: growth tree: ${item}`);
}

async function shoot(page, file, force = true) {
  if (!force) return;
  await page.screenshot({ path: path.join(outDir, file), animations: "disabled" });
  console.log(`captured ${file}`);
}

/* Centre of an element in page pixels. */
const centre = (page, selector) => page.evaluate((sel) => {
  const box = document.querySelector(sel).getBoundingClientRect();
  return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
}, selector);

const shots = [
  ["01-closed.png", 1], ["02-tree-open.png", 2], ["03-compare.png", 3],
  ["04-old-stage-readonly.png", 4], ["05-continued.png", 5], ["06-running.png", 6],
];
for (const [file, state] of shots) {
  const { page, context, label } = await open(`state=${state}&theme=light`);
  await check(page, label, state);
  await shoot(page, file, WRITE_STATES);
  await context.close();
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
  await shoot(page, "07-narrow.png", WRITE_STATES);
  await context.close();
}

/* Dark mode: every state renders and passes its check; one screenshot. */
for (let state = 1; state <= 6; state += 1) {
  const { page, context, label } = await open(`state=${state}&theme=dark`, { colorScheme: "dark" });
  await check(page, label, state);
  const ground = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  if (ground !== "rgb(32, 32, 34)") problems.push(`${label}: dark ground is ${ground}`);
  if (state === 2) await shoot(page, "08-dark-tree-open.png", WRITE_STATES);
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

/* Growth tree: fit, close to the Current tip, zoomed out; and the compact list. */
{
  const { page, context, label } = await open("view=tree&theme=light");
  await growthCheck(page, label);
  if ((await page.getAttribute(".hub", "data-mode")) !== "growth" || await page.isVisible(".ws")) problems.push(`${label}: the canvas does not open wide over the workspace`);
  if (!(await page.textContent(".stage-chip")).includes("S2 · Layout")) problems.push(`${label}: the chip is gone`);
  await page.click('.gtn[data-id="massing-D"] .gtn__hit');
  await page.click(".gt__fit");
  if (!(await page.isVisible(".gt-card"))) problems.push(`${label}: selecting a node shows no side card`);
  await shoot(page, "09-growth-tree.png");
  await context.close();
}
{
  const { page, context, label } = await open("view=tree&theme=light");
  const tip = await centre(page, ".gtn--head .gtn__tip");
  const twig = await centre(page, '.gtn[data-id="entrance-B"] .gtn__card');
  await page.mouse.move((tip.x + twig.x) / 2, (tip.y + twig.y) / 2);
  await page.mouse.wheel(0, -300);
  await page.mouse.wheel(0, -300);
  const level = await page.getAttribute(".gt", "data-level");
  if (level !== "close") problems.push(`${label}: wheel zoom reached "${level}", not "close"`);
  await shoot(page, "10-growth-tree-close.png");
  await context.close();
}
{
  const { page, context, label } = await open("view=tree&theme=light");
  await page.click('[data-act="gt-zoom"][data-id="out"]');
  await page.click('[data-act="gt-zoom"][data-id="out"]');
  const level = await page.getAttribute(".gt", "data-level");
  if (level !== "far") problems.push(`${label}: zooming out reached "${level}", not "far"`);
  if (await page.isVisible('.gtn[data-role="twig"] .gtn__name')) problems.push(`${label}: twig names still show when zoomed out`);
  await shoot(page, "11-growth-tree-overview.png");
  await context.close();
}
{
  const { page, context, label } = await open("state=2&theme=light");
  const minimal = await page.evaluate(() => [...document.querySelectorAll('.crow[data-selected="false"]')].every((row) => !row.querySelector(".crow__sum, .crow__meta") && row.querySelector(".sdot")));
  if (!minimal) problems.push(`${label}: unselected rows show more than thumbnail, letter, name and a status dot`);
  await shoot(page, "12-list-compact.png");
  await context.close();
}
for (const [hash, options] of [["state=5&view=tree&theme=light", {}], ["state=6&view=tree&theme=dark", { colorScheme: "dark" }], ["view=tree&theme=light", { viewport: NARROW }]]) {
  const { page, context, label } = await open(hash, options);
  await growthCheck(page, label);
  await context.close();
}
{
  const { page, context, label } = await open("state=1&theme=light");
  const step = async (name, fn) => { try { await fn(); } catch (err) { problems.push(`${label}: growth walk "${name}": ${err.message.split("\n")[0]}`); } };
  await step("toggle opens the growth tree", async () => {
    await page.click(".stage-chip");
    await page.click('[data-act="mode"][data-id="growth"]');
    await page.waitForSelector(".gt");
  });
  await step("drag pans the canvas", async () => {
    const before = await page.getAttribute(".gt__world", "transform");
    const box = await page.locator(".gt").boundingBox();
    await page.mouse.move(box.x + 60, box.y + 60);
    await page.mouse.down();
    await page.mouse.move(box.x + 160, box.y + 110, { steps: 4 });
    await page.mouse.up();
    if ((await page.getAttribute(".gt__world", "transform")) === before) throw new Error("the view did not move");
    await page.click(".gt__fit");
  });
  await step("continue from an old twig starts a new branch; the old future stays, muted", async () => {
    await page.click('.gtn[data-id="massing-D"] .gtn__hit');
    await page.click('.gt-card [data-act="continue"]');
    await page.waitForSelector('.gtn[data-role="trunk"][data-id="massing-D"]');
    if (!(await page.$('.gtn[data-id="S2"][data-muted="true"]'))) throw new Error("the abandoned future is not kept muted");
  });
  await growthCheck(page, `${label} after a fork`);
  await step("Accept only on Current", async () => {
    await page.click('.gtn--head .gtn__tiphit');
    await page.click('.gt-card [data-act="accept-ask"]');
    await page.click('.gt-card [data-act="accept-confirm"]');
    await page.waitForSelector('.gtn[data-role="trunk"][data-id="S3"]');
  });
  await growthCheck(page, `${label} after accepting S3`);
  await context.close();
  console.log("walked the growth tree through the UI");
}

await browser.close();
if (problems.length) {
  console.error(`\n${problems.length} problem(s):\n${problems.map((p) => `  - ${p}`).join("\n")}`);
  process.exit(1);
}
console.log("\nAll states rendered with no console errors, page errors, failed loads or network requests.");

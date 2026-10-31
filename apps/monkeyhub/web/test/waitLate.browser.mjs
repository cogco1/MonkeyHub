import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";

// ADR-011 section 8, "Waiting": a reading or loading line shows nothing for its first second, though it is in the
// page at once for assistive technology; under reduced motion it still waits, then appears without fading.
const css = await readFile(fileURLToPath(new URL("../../../../packages/web-shared/src/base.css", import.meta.url)));
const server = createServer((request, response) => {
  if (request.url === "/base.css") { response.writeHead(200, { "content-type": "text/css" }); response.end(css); return; }
  response.writeHead(200, { "content-type": "text/html" });
  response.end('<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/base.css"></head><body>'
    + '<p id="wait" class="wait-late" role="status">正在读取…</p><p id="plain">Ready</p></body></html>');
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const url = `http://127.0.0.1:${server.address().port}/`;
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const passed = [];

async function check(name, contextOptions, expectFade) {
  const context = await browser.newContext(contextOptions);
  const page = await context.newPage();
  await page.goto(url);
  const early = await page.evaluate(() => {
    const element = document.getElementById("wait");
    const animation = element.getAnimations()[0];
    return { time: Number(animation.currentTime), opacity: getComputedStyle(element).opacity, text: element.textContent,
      delay: animation.effect.getTiming().delay, plain: getComputedStyle(document.getElementById("plain")).opacity };
  });
  assert.ok(early.time < 900, `${name}: measured while the first second was still running (${early.time} ms)`);
  assert.equal(early.opacity, "0", `${name}: nothing is shown during the first second`);
  assert.equal(early.delay, 1000, `${name}: the wait is one second`);
  assert.equal(early.text, "正在读取…", `${name}: the words are in the page at once for assistive technology`);
  assert.equal(early.plain, "1", `${name}: other text is not delayed`);
  await page.waitForFunction(() => Number(document.getElementById("wait").getAnimations()[0]?.currentTime ?? 9999) >= 1000, null, { timeout: 5000 });
  await page.waitForFunction(() => getComputedStyle(document.getElementById("wait")).opacity === "1", null, { timeout: 5000 });
  if (!expectFade) {
    const duration = await page.evaluate(() => document.getElementById("wait").getAnimations()[0].effect.getComputedTiming().duration);
    assert.equal(duration, 0, `${name}: reduced motion appears without fading`);
  }
  await context.close();
  passed.push(name);
  console.log(`PASS ${name}`);
}

try {
  await check("a reading line is invisible for a second, then shown", {}, true);
  await check("under reduced motion it still waits a second, then appears at once", { reducedMotion: "reduce" }, false);
  console.log(JSON.stringify({ passed }));
} finally {
  await browser.close();
  server.close();
}

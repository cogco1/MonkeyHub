"""Exercise the turn observatory against its real diagnostic journal and HTTP API."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from monkeymonitor.pricing import RateCard
from monkeymonitor.server import MonitorData, make_server
from monkeymonitor.store import UsageLog
from monkeymonitor.usage import TokenUsage, UsageEvent


BROWSER_CHECK = r"""
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { appendFile, readFile, mkdir } from 'node:fs/promises';
import { join } from 'node:path';
const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 960 }, reducedMotion: 'reduce' });
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto(process.env.MONITOR_TEST_URL + '/?lang=en&theme=light');
  await page.waitForFunction(() => document.querySelector('#trace-content').hidden === false);
  await page.locator('#trace-select').selectOption('turn-root');
  assert.equal(await page.locator('#raw-details').getAttribute('open'), null);
  assert.equal(await page.locator('#events-wrap').isVisible(), false);
  assert.equal(await page.locator('#trace-summary [data-metric="总历时"] dd').textContent(), '12 s');
  assert.equal(await page.locator('#trace-summary [data-metric="首次可见"] dd').textContent(), '11 s');
  assert.equal(await page.locator('#trace-summary [data-metric="候选已验证"] dd').textContent(), '10 s');
  assert.equal(await page.locator('#trace-summary [data-metric="模型轮次"] dd').textContent(), '1');
  assert.equal(await page.locator('#trace-summary [data-metric="Agent 续行"] dd').textContent(), '0');
  assert.equal(await page.locator('#trace-summary [data-metric="工具调用"] dd').textContent(), '2');
  assert.equal(await page.locator('#trace-summary [data-metric="Token 用量"] dd').textContent(), '1,050');
  assert.equal(await page.locator('#trace-summary [data-metric="费用 / 等值"] dd').textContent(), '$0.00214');
  assert.match(await page.locator('#trace-summary').textContent(), /not an actual charge/);
  assert.equal(await page.locator('#trace-waterfall .trace-lane[data-lane]').count(), 5);
  assert.equal(await page.locator('#trace-waterfall .trace-lane[data-lane]').evaluateAll(lanes => new Set(lanes.map(lane => getComputedStyle(lane.querySelector('.trace-bar')).borderTopColor)).size), 5);
  const provider = page.locator('#trace-waterfall button[data-span-id="model-round"]');
  const geometry = page.locator('#trace-waterfall button[data-span-id="geometry"]');
  const position = await provider.evaluate(bar => ({ left: parseFloat(bar.style.left), width: parseFloat(bar.style.width) }));
  assert.ok(Math.abs(position.left - 100 / 12) < .01);
  assert.ok(Math.abs(position.width - 25) < .01);
  assert.equal(await geometry.locator('..').locator('..').getAttribute('data-lane'), 'cad');
  assert.equal(await page.locator('#trace-waterfall [data-span-id="preview"]').evaluate(bar => bar.classList.contains('is-background')), true);
  assert.equal(await page.locator('#trace-waterfall .trace-critical-segment').count() > 0, true);
  assert.equal(await page.locator('#trace-tree li[data-span-id="geometry"]').getAttribute('data-parent-id'), 'candidate');
  assert.equal(await page.locator('#trace-tree li[data-span-id="candidate"]').getAttribute('data-parent-id'), 'turn-root');
  assert.match(await page.locator('#trace-waterfall button[data-span-id="geometry"]').getAttribute('aria-label'), /OCCT geometry export/);
  for (const id of ['transport-root', 'transport-inner']) {
    assert.equal(await page.locator(`#trace-waterfall button[data-span-id="${id}"]`).count(), 0);
    assert.equal(await page.locator(`#trace-tree li[data-span-id="${id}"]`).count(), 0);
  }
  const measuredSummary = await page.locator('#trace-summary').textContent();
  const measuredPath = await page.locator('.trace-critical').innerHTML();
  await page.locator('#trace-show-transport').check();
  assert.equal(await page.locator('#trace-tree li[data-span-id="geometry"]').getAttribute('data-parent-id'), 'transport-inner');
  for (const id of ['transport-root', 'transport-inner']) {
    assert.equal(await page.locator(`#trace-waterfall button[data-span-id="${id}"]`).count(), 1);
    assert.equal(await page.locator(`#trace-tree li[data-span-id="${id}"]`).count(), 1);
    await page.locator(`#trace-waterfall button[data-span-id="${id}"]`).click();
    assert.equal(await page.locator('#trace-evidence h3').textContent(), 'Transport request');
    await page.locator('#trace-evidence .trace-raw > summary').click();
    assert.match(await page.locator('#trace-evidence pre').textContent(), /api_request/);
  }
  await page.locator('#trace-show-transport').uncheck();
  assert.equal(await page.locator('#trace-summary').textContent(), measuredSummary);
  assert.equal(await page.locator('.trace-critical').innerHTML(), measuredPath);
  assert.equal(await page.locator('#trace-evidence .trace-raw').count(), 0);
  assert.equal(await page.locator('#trace-diagnostics [data-code="schema_read"]').count(), 1);
  assert.match(await page.locator('#trace-attribution').textContent(), /Attributed blocking time/);
  await page.locator('.trace-warnings > summary').click();
  assert.equal(await page.locator('#trace-coverage a').getAttribute('href'), 'https://example.com/prices');
  assert.match(await page.locator('#trace-coverage').textContent(), /First response received 500 ms/);
  assert.match(await page.locator('#trace-coverage').textContent(), /Usage records: 1/);
  await page.locator('.trace-warnings > summary').click();
  await provider.focus(); await page.keyboard.press('Enter');
  assert.equal(await page.locator('#trace-evidence-title').evaluate(element => element === document.activeElement), true);
  await page.locator('#trace-evidence .trace-raw > summary').click();
  assert.match(await page.locator('#trace-evidence pre').textContent(), /model-round/);
  assert.equal(await provider.getAttribute('aria-pressed'), 'true');
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#trace-download').click();
  const download = await downloadPromise;
  const exported = JSON.parse(await readFile(await download.path(), 'utf8'));
  assert.equal(exported.trace_id, 'turn-root'); assert.equal(exported.spans.some(span => span.event_id === 'geometry'), true);
  assert.equal(exported.spans.filter(span => span.phase === 'api_request').length, 2);
  const candidateBranch = page.locator('#trace-tree summary[data-branch-id="candidate"]');
  await candidateBranch.focus(); await page.keyboard.press('Enter');
  assert.equal(await candidateBranch.locator('..').getAttribute('open'), '');
  const readingPosition = await page.evaluate(() => {
    const pre = document.querySelector('#trace-evidence pre');
    const overflowing = pre.scrollHeight > pre.clientHeight;
    if (overflowing) pre.scrollTop = Math.min(160, pre.scrollHeight - pre.clientHeight);
    return { windowY: scrollY, rawY: pre.scrollTop, overflowing };
  });
  assert.ok(readingPosition.windowY > 0, 'the reader is below the top of the page');
  await appendFile(process.env.MONITOR_TEST_LOG, process.env.MONITOR_TEST_UPDATE + '\n', 'utf8');
  await page.waitForFunction(() => document.querySelector('#trace-summary [data-metric="总历时"] dd').textContent === '14 s'
    && document.querySelector('#trace-tree li[data-span-id="late-stage"]'), null, { timeout: 15000 });
  assert.equal(await page.locator('#trace-select').inputValue(), 'turn-root');
  assert.equal(await provider.getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('#trace-evidence .trace-raw').getAttribute('open'), '');
  assert.equal(await candidateBranch.locator('..').getAttribute('open'), '', 'the expanded branch survives a live update');
  assert.equal(await candidateBranch.evaluate(element => element === document.activeElement), true,
    'live updates preserve keyboard focus on the branch being read');
  assert.ok(Math.abs(await page.evaluate(() => scrollY) - readingPosition.windowY) <= 1,
    'live updates preserve the window scroll position');
  if (readingPosition.overflowing) {
    assert.equal(await page.locator('#trace-evidence pre').evaluate(pre => pre.scrollTop), readingPosition.rawY,
      'live updates preserve the raw record scroll position');
  }
  const availableTasks = await page.locator('#trace-select option').evaluateAll(options => options.map(option => option.value));
  const readingAnchor = await candidateBranch.evaluate(element => element.getBoundingClientRect().top);
  await page.route('**/api/traces', route => route.fulfill({ status: 503, json: { error: 'Journal busy' } }), { times: 1 });
  await page.waitForFunction(() => !document.querySelector('#trace-notice').hidden, null, { timeout: 15000 });
  assert.deepEqual(await page.locator('#trace-select option').evaluateAll(options => options.map(option => option.value)), availableTasks,
    'a busy journal must retain the entire task list');
  assert.equal(await page.locator('#trace-select').inputValue(), 'turn-root');
  assert.equal(await provider.getAttribute('aria-pressed'), 'true');
  assert.equal(await candidateBranch.locator('..').getAttribute('open'), '');
  assert.equal(await candidateBranch.evaluate(element => element === document.activeElement), true);
  assert.equal(await page.locator('#trace-evidence .trace-raw').getAttribute('open'), '');
  assert.ok(Math.abs(await candidateBranch.evaluate(element => element.getBoundingClientRect().top) - readingAnchor) <= 1,
    'the busy notice must not move the content being read within the viewport');
  assert.equal(await page.locator('#trace-evidence pre').evaluate(pre => pre.scrollTop), readingPosition.rawY);
  await page.waitForFunction(() => document.querySelector('#trace-notice').hidden, null, { timeout: 15000 });
  assert.ok(Math.abs(await candidateBranch.evaluate(element => element.getBoundingClientRect().top) - readingAnchor) <= 1,
    'recovery must preserve the reading position when the notice disappears');
  const output = process.env.MONITOR_WEB_QA_DIR;
  if (output) { await mkdir(output, { recursive: true }); await page.screenshot({ path: join(output, 'monitor-turn-desktop.png'), fullPage: true }); }

  const beforeRoot = await (await page.request.get(process.env.MONITOR_TEST_URL + '/api/traces')).json();
  const orphanTrace = beforeRoot.traces.find(trace => trace.spans.some(span => span.event_id === 'orphan-model'));
  assert.ok(orphanTrace, 'spans are viewable before their Hub turn root is recorded');
  assert.notEqual(orphanTrace.trace_id, 'late-root');
  assert.equal(beforeRoot.traces[0].trace_id, 'unknown-root', 'another, newer task remains first');
  await page.locator('#trace-select').selectOption(orphanTrace.trace_id);
  const orphanModel = page.locator('#trace-waterfall button[data-span-id="orphan-model"]');
  await orphanModel.click();
  await page.locator('#trace-evidence .trace-raw > summary').click();
  assert.equal(await orphanModel.getAttribute('aria-pressed'), 'true');
  await appendFile(process.env.MONITOR_TEST_LOG, process.env.MONITOR_TEST_LATE_ROOT + '\n', 'utf8');
  await page.waitForFunction(() => document.querySelector('#trace-select option[value="late-root"]'), null, { timeout: 15000 });
  assert.equal(await page.locator('#trace-select').inputValue(), 'late-root',
    'a late Hub root keeps the selected task instead of selecting the newest task');
  assert.equal(await page.locator('#trace-select option').first().getAttribute('value'), 'unknown-root');
  assert.equal(await orphanModel.getAttribute('aria-pressed'), 'true', 'the selected span survives a change of trace id');
  assert.equal(await page.locator('#trace-evidence .trace-raw').getAttribute('open'), '');
  assert.equal(JSON.parse(await page.locator('#trace-evidence pre').textContent()).event_id, 'orphan-model');

  await page.route('**/api/traces', async route => {
    const response = await route.fetch();
    const payload = await response.json();
    assert.ok(payload.traces.some(trace => trace.trace_id === 'late-root'));
    await route.fulfill({ response, json: { ...payload, traces: payload.traces.filter(trace => trace.trace_id !== 'late-root') } });
  }, { times: 1 });
  await page.waitForFunction(() => !document.querySelector('#trace-notice').hidden
    || document.querySelector('#trace-select').value !== 'late-root', null, { timeout: 15000 });
  assert.equal(await page.locator('#trace-select').inputValue(), 'late-root',
    'a successful response missing the selected task must not select another task');
  assert.equal(await page.locator('#trace-content').isVisible(), true);
  assert.equal(await orphanModel.getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('#trace-evidence .trace-raw').getAttribute('open'), '');
  assert.equal(JSON.parse(await page.locator('#trace-evidence pre').textContent()).event_id, 'orphan-model');
  assert.equal(await page.locator('#trace-summary [data-metric="总历时"] dd').textContent(), '12 s');
  assert.equal(await page.locator('#trace-notice').isVisible(), true);
  assert.match(await page.locator('#trace-notice').textContent(), /absent.*last loaded snapshot/i);
  await appendFile(process.env.MONITOR_TEST_LOG, process.env.MONITOR_TEST_RESTORED_ROOT + '\n', 'utf8');
  await page.waitForFunction(() => document.querySelector('#trace-select').value === 'late-root'
    && document.querySelector('#trace-summary [data-metric="总历时"] dd').textContent === '16 s'
    && document.querySelector('#trace-notice').hidden, null, { timeout: 15000 });
  assert.equal(await orphanModel.getAttribute('aria-pressed'), 'true', 'the restored task continues to update the viewed span');
  assert.equal(await page.locator('#trace-evidence .trace-raw').getAttribute('open'), '');

  await page.locator('#trace-select').selectOption('unknown-root');
  assert.equal(await page.locator('#trace-summary [data-metric="Token 用量"] dd').textContent(), '—');
  assert.equal(await page.locator('#trace-summary [data-metric="模型轮次"] dd').textContent(), '—');
  assert.equal(await page.locator('#trace-summary [data-metric="费用 / 等值"] dd').textContent(), 'Price unavailable');
  assert.equal(await page.locator('#trace-summary [data-metric="首次可见"] dd').textContent(), '—');
  await page.locator('#trace-project').selectOption('trace-project');
  assert.equal(await page.locator('#trace-select').inputValue(), 'turn-root');
  for (const viewport of [{ width: 375, height: 812 }, { width: 812, height: 375 }]) {
    await page.setViewportSize(viewport);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  }
  await page.goto(process.env.MONITOR_TEST_URL + '/?lang=zh-CN&theme=dark');
  await page.waitForFunction(() => document.querySelector('#trace-content').hidden === false);
  await page.locator('#trace-select').selectOption('turn-root');
  await page.setViewportSize({ width: 375, height: 812 });
  await page.evaluate(() => document.documentElement.style.setProperty('--font-scale', '1.3'));
  assert.match(await page.locator('#trace-summary').textContent(), /总历时.*14 s/);
  assert.match(await page.locator('#trace-waterfall button[data-span-id="geometry"]').getAttribute('aria-label'), /OCCT 几何导出/);
  assert.equal(await page.locator('#trace-waterfall .trace-lane[data-lane]').evaluateAll(lanes => new Set(lanes.map(lane => getComputedStyle(lane.querySelector('.trace-bar')).borderTopColor)).size), 5);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (output) await page.screenshot({ path: join(output, 'monitor-turn-mobile-dark.png'), fullPage: true });
  assert.deepEqual(errors, []);
} finally { await browser.close(); }
"""


class MonitorTraceWebTests(unittest.TestCase):
    def test_live_turn_timeline_tree_usage_price_and_export(self):
        runtime = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node"
        node = shutil.which("node") or str(runtime / "bin/node.exe")
        playwright = Path(os.environ.get("PLAYWRIGHT_MODULE", str(runtime / "node_modules/playwright/index.mjs")))
        if not Path(node).is_file() or not playwright.is_file():
            self.skipTest("Node and Playwright are required for the real browser check")
        with TemporaryDirectory(prefix="monkeymonitor-trace-ui-") as directory:
            root = Path(directory)
            store = UsageLog(root / "usage")
            origin = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
            base = UsageEvent("turn-root", "hub", "none", "none", "hub_turn", "completed", origin.isoformat(), TokenUsage(),
                              duration_ms=12000, ended_at=(origin + timedelta(seconds=12)).isoformat(),
                              project_id="trace-project", session_id="trace-session", turn_id="trace-turn", model_call=False,
                              timing_scope="agent_turn", details={"blocking": True})

            def span(event_id, phase, offset, elapsed, parent="turn-root", source="hub", blocking=True, **values):
                start = origin + timedelta(milliseconds=offset)
                return replace(base, event_id=event_id, source=source, phase=phase, started_at=start.isoformat(),
                               ended_at=(start + timedelta(milliseconds=elapsed)).isoformat(), duration_ms=elapsed,
                               parent_event_id=parent, timing_scope="service", details={"blocking": blocking}, **values)

            rate = RateCard("test", "fixture-model", input="2", cached_input="0.2", cache_write_input="2", cache_write_1h_input="2", output="10",
                            source_url="https://example.com/prices", effective_date="2026-09-01", billing_plan="api_standard")
            model = span("model-round", "model_request", 1000, 3000, parent="provider-activity", provider="test", model="fixture-model", model_call=True,
                         tokens=TokenUsage(1000, 50, 200, 0, 0, 0), billing_mode="subscription_equivalent")
            # The exact rate context accompanies the producing model event.
            model = replace(model, timing_scope="model_call", details={"blocking": True, "billing_plan": "api_standard"}, rate_snapshot=rate.to_dict(), rate_match_status="matched")
            records = [base, span("context", "context_build", 0, 1000), span("first-response", "first_response", 500, 0, blocking=False),
                       span("provider-activity", "provider_round", 1000, 3000), model,
                       replace(span("schema-one", "tool_call", 4000, 500), details={"blocking": True, "tool_name": "schema", "request_kind": "schema_read", "input_identity": {"context_digest": "a" * 64}}),
                       replace(span("schema-two", "tool_call", 4500, 500), details={"blocking": True, "tool_name": "schema", "request_kind": "schema_read", "input_identity": {"context_digest": "a" * 64}}),
                       span("transport-root", "api_request", 5000, 5000, source="studio"),
                       span("candidate", "candidate", 5000, 5000, parent="transport-root", source="studio"),
                       span("transport-inner", "api_request", 6000, 3000, parent="candidate", source="studio"),
                       span("geometry", "geometry_export.occt.occt", 6000, 3000, parent="transport-inner", source="studio"),
                       span("verified", "verified", 9000, 1000, parent="candidate", source="studio"),
                       span("preview", "model_install", 10000, 1000, source="studio", blocking=False),
                       span("visible", "first_visible", 11000, 0, source="studio", blocking=False),
                       replace(base, event_id="unknown-root", turn_id="unknown-turn", project_id="unknown-project", session_id="unknown-session",
                               started_at=(origin + timedelta(minutes=1)).isoformat(), ended_at=(origin + timedelta(seconds=72)).isoformat())]
            records.append(replace(records[-1], event_id="unmeasured-activity", phase="provider_round", parent_event_id="unknown-root"))
            records.append(replace(records[-1], event_id="native-token-record", source="codex", phase="agent", model_call=True,
                                   timing_scope="unknown", duration_ms=None, ended_at=None, tokens=TokenUsage(input_tokens=77)))
            orphan_start = origin - timedelta(seconds=30)
            late_root = replace(base, event_id="late-root", project_id="orphan-project", session_id="orphan-session", turn_id="orphan-turn",
                                started_at=orphan_start.isoformat(), ended_at=(orphan_start + timedelta(seconds=12)).isoformat())
            orphan_model = replace(model, event_id="orphan-model", parent_event_id="late-root", project_id=late_root.project_id,
                                   session_id=late_root.session_id, turn_id=late_root.turn_id,
                                   started_at=(orphan_start + timedelta(seconds=1)).isoformat(), ended_at=(orphan_start + timedelta(seconds=4)).isoformat())
            orphan_tool = replace(late_root, event_id="orphan-tool", phase="tool_call", timing_scope="service", parent_event_id="orphan-model",
                                  started_at=(orphan_start + timedelta(seconds=4)).isoformat(), ended_at=(orphan_start + timedelta(seconds=5)).isoformat(), duration_ms=1000)
            records.extend([orphan_model, orphan_tool])
            for record in records:
                store.append(record)
            updated = [replace(base, duration_ms=14000, ended_at=(origin + timedelta(seconds=14)).isoformat()),
                       span("late-stage", "context_build", 12000, 1000)]
            server = make_server(MonitorData(root / "usage"), 0)
            thread = Thread(target=server.serve_forever, daemon=True); thread.start()
            script = root / "check.mjs"; script.write_text(BROWSER_CHECK, encoding="utf-8")
            env = {**os.environ, "PLAYWRIGHT_MODULE": str(playwright), "MONITOR_TEST_URL": f"http://127.0.0.1:{server.server_port}",
                   "MONITOR_TEST_LOG": str(store.path), "MONITOR_TEST_UPDATE": "\n".join(json.dumps(row.to_dict()) for row in updated),
                   "MONITOR_TEST_LATE_ROOT": json.dumps(late_root.to_dict()),
                   "MONITOR_TEST_RESTORED_ROOT": json.dumps(replace(late_root, duration_ms=16000, ended_at=(orphan_start + timedelta(seconds=16)).isoformat()).to_dict())}
            try:
                result = subprocess.run([node, str(script)], env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            finally:
                server.shutdown(); server.server_close(); thread.join()

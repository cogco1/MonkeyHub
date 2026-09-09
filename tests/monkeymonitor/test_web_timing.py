"""Exercise scope separation and explicit source selection through the real UI."""
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

from monkeymonitor.server import MonitorData, make_server
from monkeymonitor.store import UsageLog
from monkeymonitor.usage import TokenUsage, UsageEvent


BROWSER_CHECK = r"""
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(process.env.MONITOR_TEST_URL + '/?lang=en');
  await page.waitForFunction(() => document.querySelector('#event-count').textContent === '(7)');
  assert.equal(await page.locator('#stat-input').getAttribute('title'), '1,475 Token');
  assert.equal(await page.locator('#stat-output').getAttribute('title'), '117 Token');
  assert.equal(await page.locator('#stat-duration').textContent(), '300 ms');
  assert.equal(await page.locator('#events-body tr').count(), 7);
  assert.match(await page.locator('#events-body').textContent(), /Partial history/);
  assert.match(await page.locator('#events-body').textContent(), /Failed/);
  assert.equal(await page.locator('#operations').getAttribute('open'), null);
  await page.locator('#operations > summary').click();
  assert.equal(await page.locator('.scope-stat').count(), 6);
  await page.locator('#operation-group').selectOption('run');
  const run = page.locator('.operation-group').filter({ has: page.locator('summary', { hasText: 'project-a · run-a' }) });
  await run.locator(':scope > summary').click();
  const candidate = run.locator('[data-event-id="candidate-a"]');
  assert.match(await candidate.locator('.operation-timing').textContent(), /8 s.*No model call/);
  assert.equal(await run.locator('[data-event-id="candidate-a"]').count(), 1);
  assert.match(await run.locator('[data-event-id="export-a"] strong').textContent(), /Geometry export/);
  assert.match(await run.locator('[data-event-id="call-a"]').textContent(), /Linked intent call/);
  await candidate.locator('summary').click();
  assert.match(await candidate.locator('dl').textContent(), /Token usageUnknown/);
  assert.equal(await candidate.locator('.event-quote').count(), 0);
  assert.match(await run.locator('[data-event-id="save-a"]').textContent(), /Cancelled/);
  await page.locator('#operation-group').selectOption('source');
  const exact = page.locator('.operation-group').filter({ has: page.locator('summary', { hasText: 'stage://input/a' }) });
  assert.equal(await exact.count(), 1);
  await page.locator('#operation-group').selectOption('session');
  const family = page.locator('.operation-group').filter({ has: page.locator('summary', { hasText: 'Codex · root-session' }) });
  await family.locator(':scope > summary').click();
  assert.match(await family.textContent(), /Child session · child-session/);
  assert.equal(await family.locator('[data-event-id="lookalike"]').count(), 0);
  assert.equal(await family.locator('[data-event-id="agent-turn"]').count(), 1);
  await page.locator('#project-filter').selectOption('project-b');
  assert.equal(await page.locator('#stat-input').textContent(), '—');
  assert.equal(await page.locator('#stat-duration').textContent(), '—');
  assert.equal(await page.locator('#event-count').textContent(), '(0)');
  await page.locator('#project-filter').selectOption('');
  await page.locator('#event-sort').selectOption('duration_ms');
  assert.match(await page.locator('#events-body tr').first().textContent(), /400 ms/);
  const unknownCall = page.locator('#events-body tr').filter({ hasText: 'legacy-model' });
  await unknownCall.locator('button').click();
  assert.equal(await page.locator('input[name="cache_write_input_tokens"]').inputValue(), '');
  await page.locator('#close-calculator').click();

  await page.locator('#codex-sources > summary').click();
  await page.waitForFunction(() => !document.querySelector('#apply-sources').disabled);
  const paths = JSON.parse(process.env.MONITOR_TEST_SOURCES);
  await page.locator('#source-paths').fill(paths.join('\n'));
  await page.locator('#refresh').click();
  await page.waitForFunction(() => !document.querySelector('#refresh').disabled);
  assert.equal(await page.locator('#source-paths').inputValue(), paths.join('\n'));
  await page.locator('#apply-sources').click();
  await page.waitForFunction(() => document.querySelector('#source-status').textContent === 'Applied 2 sources');
  await page.waitForFunction(() => document.querySelector('#event-count').textContent === '(9)');
  assert.equal(await page.locator('#stat-input').getAttribute('title'), '1,481 Token');
  assert.equal(await page.locator('#coverage-title').textContent(), 'Source attribution unverified');
  assert.equal(await page.locator('#coverage').getAttribute('open'), null);
  await page.locator('#coverage > summary').click();
  assert.ok((await page.locator('#coverage-content').textContent()).includes('Legacy Codex 子代理的部分继承归属缺少已选父来源或 turn_id；未猜测扣除其计数。'),
    'The real parser warning must remain readable instead of becoming only a generic warning count');
  if (process.env.MONITOR_WEB_QA_DIR) {
    await mkdir(process.env.MONITOR_WEB_QA_DIR, { recursive: true });
    await page.screenshot({ path: join(process.env.MONITOR_WEB_QA_DIR, 'monitor-timing-attribution.png'), fullPage: true });
  }
  assert.deepEqual((await (await page.request.get(process.env.MONITOR_TEST_URL + '/api/sources/codex')).json()).paths, paths);
  await page.locator('#source-paths').fill('relative-file.jsonl');
  await page.locator('#apply-sources').click();
  await page.waitForFunction(() => document.querySelector('#source-status').textContent.startsWith('Sources unchanged:'));
  assert.equal(await page.locator('#source-paths').inputValue(), 'relative-file.jsonl');
  assert.deepEqual((await (await page.request.get(process.env.MONITOR_TEST_URL + '/api/sources/codex')).json()).paths, paths);
  await page.locator('#source-paths').fill('');
  await page.locator('#apply-sources').click();
  await page.waitForFunction(() => document.querySelector('#source-status').textContent === 'Applied 0 sources');
  await page.waitForFunction(() => document.querySelector('#event-count').textContent === '(7)');
  assert.equal(await page.locator('#coverage-title').textContent(), 'Some records are incomplete');
  assert.equal((await page.locator('#coverage-content').textContent()).includes('未猜测扣除其计数'), false);
  assert.deepEqual((await (await page.request.get(process.env.MONITOR_TEST_URL + '/api/sources/codex')).json()).paths, []);

  await page.locator('#project-filter').selectOption('diagnostic-project');
  await page.locator('#operation-group').selectOption('operation');
  assert.equal(await page.locator('#event-count').textContent(), '(1)');
  const edit = page.locator('[data-operation-id="edit-one"]');
  await edit.locator(':scope > summary').click();
  assert.match(await edit.locator(':scope > summary').textContent(), /Elapsed time 18 s/);
  assert.match(await edit.locator('.action-waits').textContent(), /Active wait 9 s.*Between actions 9 s/);
  assert.match(await edit.locator('.drawing-absence').textContent(), /No drawing generation was recorded/);
  assert.equal(await edit.locator('[data-event-id="diag-export"]').getAttribute('data-parent-event-id'), 'diag-candidate');
  assert.equal(await edit.locator('[data-event-id="diag-build"]').getAttribute('data-depth'), '4');
  const build = edit.locator('[data-event-id="diag-build"]');
  assert.equal(await build.locator('strong').textContent(), 'Geometry build');
  await build.locator(':scope > .event-diagnostics > summary').click();
  assert.match(await build.locator('[data-detail="recomputed_object_ids"]').textContent(), /window-01/);
  assert.equal((await build.locator('[data-detail="recomputed_object_ids"]').textContent()).includes('roof-unchanged'), false);
  assert.match(await build.locator('[data-detail="reused_object_ids"]').textContent(), /roof-unchanged/);
  assert.match(await build.locator('[data-detail="cache_status"]').textContent(), /Partial reuse/);
  assert.match(await build.locator('[data-detail="duplicate_reason"]').textContent(), /Recorded execution inputs match/);
  assert.match(await build.locator('[data-detail="reuse_opportunity"]').textContent(), /previous attempt failed or was cancelled/);
  assert.match(await build.locator('[data-detail="executed_stages"]').textContent(), /Geometry build/);
  const reused = page.locator('[data-operation-id="reuse-one"]');
  await reused.locator(':scope > summary').click();
  const reusedBuild = reused.locator('[data-event-id="diag-reuse"]');
  await reusedBuild.locator(':scope > .event-diagnostics > summary').click();
  assert.match(await reusedBuild.locator('[data-detail="duplicate_status"]').textContent(), /Reused a retained result/);
  assert.equal((await reusedBuild.textContent()).includes('Repeated execution recorded'), false);
  await reused.locator(':scope > summary').click();
  assert.match(await edit.locator('[data-event-id="diag-queue"] .operation-timing').textContent(), /Queue wait before execution/);
  assert.match(await edit.locator('[data-event-id="diag-request"] .inference-note').textContent(), /Pure model inference time is unknown/);
  const missingRoot = page.locator('[data-operation-id="missing-root"]');
  await missingRoot.locator(':scope > summary').click();
  assert.match(await missingRoot.locator(':scope > summary').textContent(), /Elapsed time Unknown/);
  assert.equal(await missingRoot.locator('[data-event-id="diag-candidate"]').count(), 0, 'Same run must not merge different actions');
  assert.equal(await missingRoot.locator('.action-total-unknown').count(), 1);
  const drawing = page.locator('[data-operation-id="drawing-one"]');
  await drawing.locator(':scope > summary').click();
  assert.match(await drawing.locator(':scope > summary').textContent(), /Elapsed time 5 s/);
  const generated = drawing.locator('[data-event-id="diag-drawing"]');
  await generated.locator(':scope > .event-diagnostics > summary').click();
  assert.match(await generated.locator('[data-detail="scope"]').textContent(), /Global visibility computation/);
  assert.equal(await generated.locator('[data-detail="recomputed_object_ids"]').count(), 0);
  assert.match(await generated.locator('[data-detail="input_object_ids"]').textContent(), /roof-unchanged/);
  assert.equal((await generated.locator('[data-detail="emitted_object_ids"]').textContent()).includes('roof-unchanged'), false);
  assert.match(await generated.locator('[data-detail="cache_reason"]').textContent(), /registered drawing inputs changed/);
  assert.match(await generated.locator('[data-detail="cache_checks"]').textContent(), /View settings: Changed/);
  const rawIdentity = generated.locator('.diagnostic-section').filter({ has: page.locator('summary', { hasText: 'Input identity and comparisons' }) });
  await rawIdentity.locator(':scope > summary').click();
  assert.match(await rawIdentity.locator('[data-detail="comparison_refs"]').textContent(), /drawing:previous/);
  assert.equal(await generated.locator('pre').count(), 0);
  assert.equal(await drawing.locator('.drawing-absence').count(), 0);
  const hlr = drawing.locator('[data-event-id="diag-hlr"]');
  await hlr.locator(':scope > .event-diagnostics > summary').click();
  assert.match(await hlr.locator('[data-detail="scope"]').textContent(), /Global visibility computation/);
  assert.match(await hlr.locator('[data-detail="input_object_ids"]').textContent(), /roof-unchanged/);
  assert.equal(await hlr.locator('[data-detail="recomputed_object_ids"]').count(), 0);
  if (process.env.MONITOR_WEB_QA_DIR) {
    await edit.screenshot({ path: join(process.env.MONITOR_WEB_QA_DIR, 'monitor-edit-diagnostics.png') });
    await drawing.screenshot({ path: join(process.env.MONITOR_WEB_QA_DIR, 'monitor-drawing-diagnostics.png') });
    await page.screenshot({ path: join(process.env.MONITOR_WEB_QA_DIR, 'monitor-operation-diagnostics.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: join(process.env.MONITOR_WEB_QA_DIR, 'monitor-operation-diagnostics-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 1280, height: 960 });
  }
  await page.locator('#project-filter').selectOption('');

  await page.locator('#operation-group').selectOption('run');
  const finalRun = page.locator('.operation-group').filter({ has: page.locator('summary', { hasText: 'project-a · run-a' }) });
  if (await finalRun.getAttribute('open') === null) await finalRun.locator(':scope > summary').click();
  const output = process.env.MONITOR_WEB_QA_DIR;
  if (output) { await mkdir(output, { recursive: true }); await page.screenshot({ path: join(output, 'monitor-timing-desktop.png'), fullPage: true }); }
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (output) await page.screenshot({ path: join(output, 'monitor-timing-mobile.png'), fullPage: true });
  await page.goto(process.env.MONITOR_TEST_URL + '/?lang=zh-CN&theme=dark');
  await page.waitForFunction(() => document.querySelector('#event-count').textContent === '(7)');
  assert.equal(await page.locator('#stat-duration').textContent(), '300 ms');
  assert.match(await page.locator('#duration-stat').textContent(), /模型请求耗时中位数/);
  await page.locator('#project-filter').selectOption('diagnostic-project');
  await page.locator('#operations > summary').click();
  const chineseEdit = page.locator('[data-operation-id="edit-one"]');
  await chineseEdit.locator(':scope > summary').click();
  const chineseBuild = chineseEdit.locator('[data-event-id="diag-build"]');
  assert.equal(await chineseBuild.locator('strong').textContent(), '几何构建');
  await chineseBuild.locator(':scope > .event-diagnostics > summary').click();
  assert.match(await chineseBuild.locator('[data-detail="reuse_opportunity"]').textContent(), /前次失败或取消，未得到可验证复用结果/);
  if (output) await chineseEdit.screenshot({ path: join(output, 'monitor-edit-zh-mobile.png') });
  if (output) await page.screenshot({ path: join(output, 'monitor-timing-zh-dark.png'), fullPage: true });
  assert.deepEqual(errors, []);
} finally { await browser.close(); }
"""


class MonitorTimingWebTests(unittest.TestCase):
    def test_timing_scopes_grouping_and_source_selection(self):
        runtime = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node"
        node = shutil.which("node") or str(runtime / "bin/node.exe")
        playwright = Path(os.environ.get("PLAYWRIGHT_MODULE", str(runtime / "node_modules/playwright/index.mjs")))
        if not Path(node).is_file() or not playwright.is_file():
            self.skipTest("Node and Playwright are required for the real browser check")
        with TemporaryDirectory(prefix="monkeymonitor-web-") as directory:
            root = Path(directory)
            store = UsageLog(root / "usage")
            base = UsageEvent("call-a", "studio", "test", "active-model", "intent", "succeeded", "2026-09-09T10:00:00Z", TokenUsage(1000, 50, 200, 100, 40, 10), duration_ms=200, project_id="project-a", ended_at="2026-09-09T10:00:00.200Z", timing_scope="model_call", model_call=True, source_ref="stage://input/a")
            candidate = replace(base, event_id="candidate-a", provider="none", model="none", phase="candidate", tokens=TokenUsage(), duration_ms=8000, ended_at="2026-09-09T10:00:08Z", run_id="run-a", model_call=False, timing_scope="service", related_event_id="call-a")
            records = [
                base,
                replace(candidate, status="running", duration_ms=None, ended_at=None), candidate,
                replace(candidate, event_id="export-a", phase="geometry_export.occt.incremental", duration_ms=3000, ended_at="2026-09-09T10:00:03Z", related_event_id="candidate-a"),
                replace(candidate, event_id="save-a", phase="stage_save", status="cancelled", duration_ms=100, ended_at="2026-09-09T10:00:00.100Z"),
                replace(candidate, event_id="load-b", phase="model_load", project_id="project-b", run_id="run-b", duration_ms=1200, ended_at="2026-09-09T10:00:01.200Z", source_ref="stage://input/b"),
                replace(base, event_id="client-call", timing_scope="client_wait", duration_ms=5000, ended_at="2026-09-09T10:00:05Z", tokens=TokenUsage(200, 20, 0)),
                replace(base, event_id="legacy-call", model="legacy-model", status="partial_history", timing_scope="unknown", model_call=None, duration_ms=9000, ended_at=None, tokens=TokenUsage(400, 40, 0)),
                replace(base, event_id="failed-call", status="failed", duration_ms=400, ended_at="2026-09-09T10:00:00.400Z", tokens=TokenUsage()),
                replace(base, event_id="root-call", source="codex", phase="agent", timing_scope="unknown", duration_ms=None, ended_at=None, tokens=TokenUsage(50, 5, 0), session_id="root-session", project_id=None, source_ref="codex:root-session"),
                replace(base, event_id="child-call", source="codex", phase="agent", timing_scope="unknown", duration_ms=None, ended_at=None, tokens=TokenUsage(25, 2, 0), session_id="child-session", parent_session_id="root-session", project_id=None, source_ref="codex:child-session"),
                replace(base, event_id="agent-turn", source="codex", phase="agent_turn", model_call=None, timing_scope="agent_turn", tokens=TokenUsage(), duration_ms=60000, ended_at="2026-09-09T10:01:00Z", session_id="root-session", turn_id="turn-one", project_id=None, source_ref="codex:root-session"),
                replace(candidate, event_id="lookalike", source="codex", phase="agent_turn", model_call=None, timing_scope="agent_turn", source_ref="codex:root-session", session_id=None, project_id=None),
            ]
            origin = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
            def diagnostic(event_id, phase, offset, elapsed, parent=None, operation="edit-one", scope="service", **values):
                start = origin + timedelta(seconds=offset)
                return replace(candidate, event_id=event_id, phase=phase, project_id="diagnostic-project", run_id="diagnostic-run", operation_id=operation, parent_event_id=parent,
                    related_event_id=None, source_ref="stage://diagnostic/input", started_at=start.isoformat(), ended_at=(start + timedelta(milliseconds=elapsed)).isoformat(),
                    duration_ms=elapsed, timing_scope=scope, **values)
            records.extend([
                diagnostic("diag-previous-build", "geometry_build", -50, 500, operation="previous-edit", status="failed", details={"input_identity": {"program_digest": "a" * 64}, "executed_stages": ["build_program_shapes"], "cache_status": "miss"}),
                diagnostic("diag-edit", "design_edit", 0, 18000, scope="interaction", details={"active_wait_ms": 9000, "between_actions_ms": 9000}),
                diagnostic("diag-intent-wait", "intent_wait", 0, 3000, "diag-edit", scope="client_wait"),
                diagnostic("diag-intent", "intent_compile", 0, 2900, "diag-intent-wait"),
                diagnostic("diag-request", "model_request", 0, 300, "diag-intent", scope="model_call", model_call=True, provider="test", model="request-model", details={"request_kind": "codex_cli", "model_inference_ms": None}),
                diagnostic("diag-candidate-wait", "candidate_wait", 12, 6000, "diag-edit", scope="client_wait"),
                diagnostic("diag-queue", "candidate_queue", 12, 1000, "diag-candidate-wait"),
                diagnostic("diag-candidate", "candidate", 13, 4000, "diag-candidate-wait"),
                diagnostic("diag-export", "geometry_export.occt.incremental", 14, 2500, "diag-candidate"),
                diagnostic("diag-build", "geometry_build", 14, 1000, "diag-export", details={"input_identity": {"program_digest": "a" * 64}, "scope": "program_geometry", "execution_path": "occt", "executed_stages": ["build_program_shapes"], "cache_status": "partial", "recomputed_object_ids": ["window-01"], "reused_object_ids": ["roof-unchanged"], "emitted_object_ids": ["window-01", "roof-unchanged"]}),
                diagnostic("diag-load", "model_load", 17, 1000, "diag-candidate-wait", scope="client_wait"),
                diagnostic("diag-orphan", "candidate", 20, 4000, "absent-root", operation="missing-root"),
                diagnostic("diag-drawing-wait", "drawing_wait", 30, 5000, operation="drawing-one", scope="client_wait"),
                diagnostic("diag-drawing", "drawing_generate", 30, 4500, "diag-drawing-wait", operation="drawing-one", details={
                    "scope": "global_visibility", "execution_path": "full_projection", "cache_status": "miss", "cache_reason": "registered_inputs_changed",
                    "input_object_ids": ["window-01", "roof-unchanged"], "emitted_object_ids": ["window-01"], "executed_stages": ["drawing.load", "drawing.hlr", "drawing.svg", "drawing.png", "drawing.persist", "drawing.register"],
                    "cache_checks": {"view_recipe": "changed", "bytes": "same"}, "comparison_refs": ["drawing:previous"], "output_refs": ["drawing:current"],
                    "input_identity": {"step_sha256": "b" * 64, "backend": "ocp", "backend_version": "diagnostic-version", "view_recipe": {"name": "front", "scale_denominator": 100}},
                }),
                diagnostic("diag-hlr", "drawing.hlr", 30, 4000, "diag-drawing", operation="drawing-one", details={"scope": "global_visibility", "input_object_ids": ["window-01", "roof-unchanged"], "emitted_object_ids": ["window-01"]}),
                diagnostic("diag-reuse", "geometry_build", 40, 5, operation="reuse-one", details={"input_identity": {"program_digest": "a" * 64}, "cache_status": "hit", "executed_stages": [], "recomputed_object_ids": [], "reused_object_ids": ["window-01", "roof-unchanged"], "emitted_object_ids": ["window-01", "roof-unchanged"]}),
            ])
            for event in records:
                store.append(event)
            paths = [root / f"source-{index}.jsonl" for index in range(2)]
            for index, path in enumerate(paths):
                meta = {"id": f"selected-{index}"}
                if index:
                    meta["parent_thread_id"] = "selected-0"
                rows = [{"type": "session_meta", "payload": meta}, {"type": "event_msg", "timestamp": "2026-09-09T11:00:00Z", "payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 3, "cached_input_tokens": 0, "output_tokens": 1}}}}]
                path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            server = make_server(MonitorData(root / "usage"), 0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            script = root / "check.mjs"
            script.write_text(BROWSER_CHECK, encoding="utf-8")
            env = {**os.environ, "PLAYWRIGHT_MODULE": str(playwright), "MONITOR_TEST_URL": f"http://127.0.0.1:{server.server_port}", "MONITOR_TEST_SOURCES": json.dumps([path.as_posix() for path in paths])}
            try:
                result = subprocess.run([node, str(script)], env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

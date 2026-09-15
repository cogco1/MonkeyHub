import assert from "node:assert/strict";
import test from "node:test";
import { aggregateTokens, formatDuration, projectIds, summarizeUsage, type MonitorEvent, type MonitorTrace } from "../src/monitorData.ts";

const tokens = (input: number | null, cached: number | null, output: number | null) => ({
  input_tokens: input, output_tokens: output, cached_input_tokens: cached,
  cache_write_input_tokens: 0, cache_write_1h_input_tokens: 0, reasoning_output_tokens: 0,
});

const events: MonitorEvent[] = [
  { event_id: "a", source: "studio", provider: "openai", model: "m", phase: "model_request", status: "succeeded", started_at: "2026-09-15T00:00:00Z", duration_ms: 1000, project_id: "p2", model_call: true, timing_scope: "model_call", tokens: tokens(100, 40, 20) },
  { event_id: "b", source: "codex", provider: "openai", model: "m", phase: "agent", status: "succeeded", started_at: "2026-09-15T00:01:00Z", duration_ms: 3000, project_id: "p1", model_call: true, timing_scope: "model_call", tokens: tokens(200, 50, 30) },
  { event_id: "c", source: "studio", provider: "none", model: "none", phase: "geometry_build", status: "succeeded", started_at: "2026-09-15T00:02:00Z", project_id: "p1", model_call: false, tokens: tokens(null, null, null) },
];

test("usage summary keeps cache inside total input and uses request p50", () => {
  const summary = summarizeUsage(events);
  assert.equal(summary.calls, 2);
  assert.equal(summary.cachedInput.value, 90);
  assert.equal(summary.uncachedInput.value, 210);
  assert.equal(summary.output.value, 50);
  assert.equal(summary.requestP50Ms.value, 2000);
});

test("aggregate tokens stays unknown when any model call is missing the field", () => {
  const complete = aggregateTokens(events);
  assert.equal(complete.input_tokens, 300);
  const incomplete = aggregateTokens([...events, { ...events[0], event_id: "d", tokens: tokens(null, 0, 1) }]);
  assert.equal(incomplete.input_tokens, null);
  assert.equal(incomplete.output_tokens, 51);
});

test("project list combines trace and usage identities without duplicates", () => {
  const traces: MonitorTrace[] = [{ trace_id: "t", project_id: "p3", started_at: "2026-09-15T00:00:00Z" }];
  assert.deepEqual(projectIds(events, traces), ["p1", "p2", "p3"]);
});

test("durations are compact without inventing missing timing", () => {
  assert.equal(formatDuration(null), "—");
  assert.equal(formatDuration(800), "800 ms");
  assert.equal(formatDuration(2500), "2.5 s");
  assert.equal(formatDuration(90_000), "1.5 min");
});

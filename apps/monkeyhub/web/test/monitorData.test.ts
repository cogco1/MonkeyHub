import assert from "node:assert/strict";
import test from "node:test";
import { aggregateTokens, formatDuration, isModelCall, uncachedInput, projectIds, summarizeUsage, quoteDraftReducer, type QuoteDraft, type MonitorEvent, type MonitorTrace } from "../src/monitorData.ts";

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

test("Codex turn boundaries and Hub rounds do not inflate calls or erase the calculator's known tokens", () => {
  const calls = Array.from({ length: 37 }, (_, index) => ({ ...events[1], event_id: `call-${index}` }));
  const turns = Array.from({ length: 4 }, (_, index) => ({ ...events[1], event_id: `turn-${index}`,
    phase: "agent_turn", timing_scope: "agent_turn", model_call: null, tokens: tokens(null, null, null) }));
  const operations = [{ ...events[2], source: "hub", phase: "provider_round" }, ...turns];
  const mixed = [...calls, ...operations];
  assert.equal(summarizeUsage(mixed).calls, 37);
  assert.deepEqual(summarizeUsage(mixed).cachedInput, { value: 1850, recorded: 37, missing: 0 });
  assert.deepEqual(aggregateTokens(mixed), aggregateTokens(calls));
  assert.deepEqual(mixed.filter(isModelCall), calls);
});

test("legacy model requests, explicit non-calls and missing usage have the same summary/detail/estimate scope", () => {
  const legacy = { ...events[0], model_call: undefined };
  const legacyCodex = { ...events[1], model_call: null };
  const nonCall = { ...legacy, model_call: false, tokens: tokens(null, null, null) };
  const unknown = { ...events[1], event_id: "unknown", tokens: tokens(null, null, null) };
  const mixed = [legacy, legacyCodex, nonCall, unknown];
  assert.deepEqual(mixed.filter(isModelCall), [legacy, legacyCodex, unknown]);
  assert.deepEqual(summarizeUsage(mixed).cachedInput, { value: 90, recorded: 2, missing: 1 });
  assert.equal(aggregateTokens(mixed).input_tokens, null);
  assert.equal(aggregateTokens([legacy]).input_tokens, 100);
});

test("loading more detail rows changes the visible subtotal, not the project total", () => {
  const calls = Array.from({ length: 21 }, (_, index) => ({ ...events[1], event_id: `call-${index}` }));
  const mixed = [events[2], ...calls];
  const detail = mixed.filter(isModelCall);
  assert.equal(summarizeUsage(detail.slice(0, 20)).uncachedInput.value, 3000);
  assert.equal(summarizeUsage(mixed).uncachedInput.value, 3150);
  assert.equal(detail.reduce((sum, event) => sum + uncachedInput(event)!, 0), 3150);
  assert.equal(uncachedInput({ ...events[1], tokens: tokens(20, 30, 0) }), null);
  assert.equal(uncachedInput({ ...events[1], tokens: tokens(20, null, 0) }), null);
});

test("project list combines trace and usage identities without duplicates", () => {
  const traces: MonitorTrace[] = [{ trace_id: "t", project_id: "p3", started_at: "2026-09-15T00:00:00Z" }];
  assert.deepEqual(projectIds(events, traces), ["p1", "p2", "p3"]);
});

test("durations are compact without inventing missing timing", () => {
  assert.equal(formatDuration(null), "—");
  assert.equal(formatDuration(undefined), "—");
  assert.equal(formatDuration(800), "800 ms");
  assert.equal(formatDuration(2500), "2.5 s");
  assert.equal(formatDuration(90_000), "1.5 min");
});

const emptyDraft = (): QuoteDraft => ({ projectId: "p1", values: {}, automatic: true });
const refreshDraft = (draft: QuoteDraft, input: number, projectId = "p1") =>
  quoteDraftReducer(draft, { type: "refresh", projectId, tokens: tokens(input, 0, 5) });

test("manual estimate inputs survive two successive diagnostic refreshes", () => {
  const automatic = refreshDraft(emptyDraft(), 100);
  const edited = quoteDraftReducer(automatic, { type: "edit", field: "input_tokens", value: "1234" });
  assert.equal(refreshDraft(edited, 200), edited);
  assert.equal(refreshDraft(edited, 300), edited);
  assert.equal(edited.values.input_tokens, "1234");
  assert.equal(automatic.values.input_tokens, "100");
});

test("calculating freezes the exact automatic snapshot until an explicit reset", () => {
  const automatic = refreshDraft(emptyDraft(), 100);
  const frozen = quoteDraftReducer(automatic, { type: "freeze" });
  assert.equal(frozen.values, automatic.values);
  assert.equal(refreshDraft(frozen, 200), frozen);
  assert.equal(refreshDraft(frozen, 300), frozen);
  const reset = quoteDraftReducer(frozen, { type: "reset", projectId: "p1", tokens: tokens(300, 0, 5) });
  assert.equal(reset.automatic, true);
  assert.equal(reset.values.input_tokens, "300");
  assert.equal(refreshDraft(reset, 400).values.input_tokens, "400");
});

test("identical polling preserves input identity but changed automatic totals replace it", () => {
  const automatic = refreshDraft(emptyDraft(), 100);
  assert.equal(refreshDraft(automatic, 100), automatic);
  const changed = refreshDraft(automatic, 200);
  assert.notEqual(changed.values, automatic.values);
  assert.equal(changed.values.input_tokens, "200");
});

test("switching projects replaces manual inputs with the new project's totals", () => {
  const edited = quoteDraftReducer(refreshDraft(emptyDraft(), 100), { type: "edit", field: "input_tokens", value: "999" });
  const switched = refreshDraft(edited, 40, "p2");
  assert.equal(switched.projectId, "p2");
  assert.equal(switched.automatic, true);
  assert.equal(switched.values.input_tokens, "40");
  assert.notEqual(switched.values, edited.values);
});

test("unknown reset counters remain empty rather than becoming zero", () => {
  const reset = quoteDraftReducer(emptyDraft(), { type: "reset", projectId: "p1", tokens: tokens(null, null, null) });
  assert.equal(reset.values.input_tokens, "");
  assert.equal(reset.values.cached_input_tokens, "");
  assert.equal(reset.values.output_tokens, "");
});

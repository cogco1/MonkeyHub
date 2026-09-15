export type MonitorTokenUsage = {
  input_tokens: number | null;
  output_tokens: number | null;
  cached_input_tokens: number | null;
  cache_write_input_tokens: number | null;
  cache_write_1h_input_tokens: number | null;
  reasoning_output_tokens: number | null;
};

export type MonitorEvent = {
  event_id: string;
  source: string;
  provider: string;
  model: string;
  phase: string;
  status: string;
  started_at: string;
  ended_at?: string | null;
  duration_ms?: number | null;
  project_id?: string | null;
  timing_scope?: string | null;
  model_call?: boolean | null;
  tokens: MonitorTokenUsage;
  details?: Record<string, unknown>;
};

export type MonitorSpan = {
  event_id?: string | null;
  span_id?: string | null;
  parent_span_id?: string | null;
  source?: string | null;
  provider?: string | null;
  model?: string | null;
  phase?: string | null;
  status?: string | null;
  label?: string | null;
  lane?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  offset_ms?: number | null;
  duration_ms?: number | null;
  blocking?: boolean | null;
  model_call?: boolean | null;
  details?: Record<string, unknown>;
  tokens?: MonitorTokenUsage;
  price?: Record<string, unknown> | null;
};

export type MonitorTrace = {
  trace_id: string;
  turn_id?: string | null;
  project_id?: string | null;
  session_id?: string | null;
  started_at: string;
  ended_at?: string | null;
  status?: string | null;
  summary?: Record<string, unknown>;
  spans?: MonitorSpan[];
  diagnostics?: Array<Record<string, unknown>>;
  warnings?: string[];
  coverage?: Record<string, unknown>;
  usage?: Record<string, unknown>;
  price?: Record<string, unknown>;
};

export type CoverageTotal = { value: number | null; recorded: number; missing: number };
export type UsageSummary = {
  calls: number;
  cachedInput: CoverageTotal;
  uncachedInput: CoverageTotal;
  output: CoverageTotal;
  requestP50Ms: CoverageTotal;
};

const measured = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;

function total(values: Array<number | null>): CoverageTotal {
  const known = values.filter((value): value is number => measured(value));
  return { value: known.length ? known.reduce((sum, value) => sum + value, 0) : null, recorded: known.length, missing: values.length - known.length };
}

export function summarizeUsage(events: readonly MonitorEvent[]): UsageSummary {
  const calls = events.filter((event) => event.model_call === true || event.phase === "model_request" || event.source === "codex");
  const cached = calls.map((event) => event.tokens?.cached_input_tokens ?? null);
  const uncached = calls.map((event) => {
    const input = event.tokens?.input_tokens;
    const cache = event.tokens?.cached_input_tokens;
    return measured(input) && measured(cache) && input >= cache ? input - cache : null;
  });
  const output = calls.map((event) => event.tokens?.output_tokens ?? null);
  const durations = calls
    .filter((event) => event.model_call === true || event.timing_scope === "model_call" || event.phase === "model_request")
    .map((event) => measured(event.duration_ms) ? event.duration_ms! : null);
  const knownDurations = durations.filter((value): value is number => measured(value)).sort((a, b) => a - b);
  let p50: number | null = null;
  if (knownDurations.length) {
    const middle = Math.floor(knownDurations.length / 2);
    p50 = knownDurations.length % 2 ? knownDurations[middle] : (knownDurations[middle - 1] + knownDurations[middle]) / 2;
  }
  return {
    calls: calls.length,
    cachedInput: total(cached),
    uncachedInput: total(uncached),
    output: total(output),
    requestP50Ms: { value: p50, recorded: knownDurations.length, missing: durations.length - knownDurations.length },
  };
}

export function aggregateTokens(events: readonly MonitorEvent[]): MonitorTokenUsage {
  const keys: (keyof MonitorTokenUsage)[] = [
    "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "cache_write_1h_input_tokens", "reasoning_output_tokens",
  ];
  const result = {} as MonitorTokenUsage;
  for (const key of keys) {
    const values = events.filter((event) => event.model_call === true || event.source === "codex").map((event) => event.tokens?.[key] ?? null);
    const known = values.filter((value): value is number => measured(value));
    result[key] = values.length && known.length === values.length ? known.reduce((sum, value) => sum + value, 0) : null;
  }
  return result;
}

export function formatCount(value: number | null): string {
  return value === null ? "—" : Math.round(value).toLocaleString();
}

export function formatDuration(value: number | null | undefined): string {
  if (!measured(value)) return "—";
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} s`;
  return `${(value / 60_000).toFixed(1)} min`;
}

export function projectIds(events: readonly MonitorEvent[], traces: readonly MonitorTrace[]): string[] {
  return [...new Set([
    ...events.map((event) => event.project_id).filter((value): value is string => Boolean(value)),
    ...traces.map((trace) => trace.project_id).filter((value): value is string => Boolean(value)),
  ])].sort();
}

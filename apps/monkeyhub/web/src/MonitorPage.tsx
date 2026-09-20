import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type CSSProperties, type FormEvent } from "react";
import type { AppearancePreferences } from "../../../shared-web/src/appearance.js";
import type { AppStatus, ApplicationSettingsDto } from "./api/generated";
import {
  aggregateTokens, formatCount, formatDuration, projectIds, summarizeUsage, quoteDraftReducer,
  type MonitorEvent, type MonitorTrace,
} from "./monitorData";
import "./MonitorPage.css";

type Props = { preferences: AppearancePreferences; active: boolean; onClose: () => void };
type EventResponse = { events: MonitorEvent[]; warnings: string[] };
type TraceResponse = { traces: MonitorTrace[]; warnings: string[]; lanes?: Array<{ id: string; label: string }> };
type Rate = {
  label?: string | null; provider: string; model: string;
  input?: string | null; cached_input?: string | null; cache_write_input?: string | null;
  cache_write_1h_input?: string | null; output?: string | null;
  source_url?: string | null; effective_date?: string | null; billing_plan?: string | null;
};
type Rates = { rates: Rate[] };
type SourceResponse = { paths: string[] };
type Quote = { currency: string; amount_usd: string | null; known_subtotal_usd: string; missing: string[] };

const words = {
  "zh-CN": {
    back: "回到聊天", title: "用量与任务记录", subtitle: "查看任务进度、耗时与模型用量。",
    refresh: "刷新", connecting: "正在连接监控服务…", retry: "重新连接", ready: "监控服务在线", failed: "监控服务暂不可用",
    allProjects: "全部项目", project: "项目", calls: "模型调用", cached: "缓存输入", uncached: "未缓存输入", output: "输出", wait: "请求往返 P50",
    coverage: (known: number, missing: number) => `${known} 次已记录${missing ? ` · ${missing} 次未知` : ""}`,
    tasks: "任务时间线", noTasks: "暂无任务记录。MonkeyHub 发起任务后会在这里出现。", task: "任务", status: "状态", elapsed: "总历时", firstVisible: "首次可见", firstCandidate: "首个候选", modelRounds: "模型轮次", toolRounds: "工具轮次",
    activity: "阶段", lane: "泳道", duration: "耗时", blocking: "阻塞", yes: "是", no: "否", details: "详情", diagnostics: "耗时诊断", warnings: "记录提示",
    usage: "调用记录", source: "来源", provider: "Provider / 模型", phase: "阶段", time: "时间", noUsage: "暂无用量记录。", showMore: "再显示 20 条",
    sources: "Codex 来源", sourcesHelp: "每行一个明确的本机 JSONL 路径。Hub 自动绑定的会话不需要手填；这里仅用于诊断补充。", apply: "应用来源", applied: "来源已更新",
    calculator: "费用估算", calculatorHelp: "按你明确选择的费率计算 API 等价值；不是订阅实际扣款，也不自动猜计费档位。", rate: "费率", chooseRate: "选择参考费率", input: "总输入", cacheRead: "缓存读取", cacheWrite: "缓存写入", cacheWrite1h: "其中 1 小时写入", reasoning: "其中推理输出", calculate: "计算", resetTotals: "恢复当前汇总", estimated: "估算费用", knownSubtotal: "已知小计", missing: "仍缺少",
    serviceDetail: "技术详情", updated: "更新于", download: "下载 Trace JSON", raw: "原始记录", endNotObserved: "结束时间未观测",
  },
  en: {
    back: "Back to chat", title: "Usage and task records", subtitle: "Follow task progress, timing and model usage.",
    refresh: "Refresh", connecting: "Connecting to monitoring service…", retry: "Reconnect", ready: "Monitoring service online", failed: "Monitoring service unavailable",
    allProjects: "All projects", project: "Project", calls: "Model calls", cached: "Cached input", uncached: "Uncached input", output: "Output", wait: "Request round-trip P50",
    coverage: (known: number, missing: number) => `${known} recorded${missing ? ` · ${missing} unknown` : ""}`,
    tasks: "Task timeline", noTasks: "No task records yet. Tasks started from MonkeyHub will appear here.", task: "Task", status: "Status", elapsed: "Elapsed", firstVisible: "First visible", firstCandidate: "First candidate", modelRounds: "Model rounds", toolRounds: "Tool rounds",
    activity: "Stage", lane: "Lane", duration: "Duration", blocking: "Blocking", yes: "Yes", no: "No", details: "Details", diagnostics: "Timing diagnostics", warnings: "Record notices",
    usage: "Call records", source: "Source", provider: "Provider / model", phase: "Phase", time: "Time", noUsage: "No usage records yet.", showMore: "Show 20 more",
    sources: "Codex sources", sourcesHelp: "One explicit local JSONL path per line. Hub-bound sessions do not need to be entered here; this is only for diagnostic supplements.", apply: "Apply sources", applied: "Sources updated",
    calculator: "Cost estimate", calculatorHelp: "Calculates an API-rate equivalent from the rate you explicitly select. It is not a subscription charge and no billing tier is guessed.", rate: "Rate", chooseRate: "Choose reference rate", input: "Total input", cacheRead: "Cache read", cacheWrite: "Cache write", cacheWrite1h: "Of which 1-hour write", reasoning: "Of which reasoning output", calculate: "Calculate", resetTotals: "Use current totals", estimated: "Estimated cost", knownSubtotal: "Known subtotal", missing: "Still missing",
    serviceDetail: "Technical details", updated: "Updated", download: "Download Trace JSON", raw: "Raw record", endNotObserved: "End not observed",
  },
} as const;

const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...init, headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof body?.detail === "string" ? body.detail : typeof body?.error === "string" ? body.error : `HTTP ${response.status}`;
    throw new Error(message);
  }
  return body as T;
}

function numeric(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string {
  return typeof value === "string" && value ? value : "—";
}

function dateText(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function MonitorPage({ preferences, active, onClose }: Props) {
  const t = words[preferences.language];
  const [base, setBase] = useState<string | null>(null);
  const [events, setEvents] = useState<MonitorEvent[]>([]);
  const [traces, setTraces] = useState<MonitorTrace[]>([]);
  const [rates, setRates] = useState<Rate[]>([]);
  const [sourcePaths, setSourcePaths] = useState("");
  const [sourceDirty, setSourceDirty] = useState(false);
  const [sourceStatus, setSourceStatus] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [project, setProject] = useState("");
  const [traceId, setTraceId] = useState("");
  const [visible, setVisible] = useState(20);
  const [rateIndex, setRateIndex] = useState("");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoteError, setQuoteError] = useState<string | null>(null);
  const [quoteDraft, dispatchQuoteDraft] = useReducer(quoteDraftReducer, { projectId: "", values: {}, automatic: true });
  const usageDraft = quoteDraft.values;
  const quoteRequest = useRef(0);
  const rateKey = JSON.stringify(rateIndex === "" ? null : rates[Number(rateIndex)] ?? null);
  const invalidateQuote = useCallback(() => {
    quoteRequest.current += 1;
    setQuote(null); setQuoteError(null);
  }, []);
  useEffect(() => { invalidateQuote(); }, [project, usageDraft, rateIndex, rateKey, invalidateQuote]);
  useEffect(() => () => { quoteRequest.current += 1; }, []);
  const loadingRef = useRef(false);

  const baseRef = useRef(base);
  const sourceDirtyRef = useRef(sourceDirty);
  baseRef.current = base;
  sourceDirtyRef.current = sourceDirty;

  const ensureService = useCallback(async () => {
    const settings = await jsonRequest<ApplicationSettingsDto>("/api/settings/apps");
    const monitorBase = `http://127.0.0.1:${settings.monitorPort}`;
    let statuses = await jsonRequest<AppStatus[]>("/api/apps");
    let monitor = statuses.find((item) => item.appId === "monkeymonitor");
    if (!monitor || !["running", "starting"].includes(monitor.state)) {
      await jsonRequest<AppStatus>("/api/apps/monkeymonitor/start", { method: "POST", body: "{}" });
    }
    const deadline = Date.now() + 20_000;
    while (Date.now() < deadline) {
      statuses = await jsonRequest<AppStatus[]>("/api/apps");
      monitor = statuses.find((item) => item.appId === "monkeymonitor");
      if (monitor?.state === "running") return monitorBase;
      if (monitor?.state === "error" || monitor?.state === "unavailable") throw new Error(monitor.error?.detail ?? monitor.state);
      await sleep(150);
    }
    throw new Error("MonkeyMonitor did not become ready in time.");
  }, []);

  const readAll = useCallback(async (includeSources = false) => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true); setError(null);
    try {
      const monitorBase = baseRef.current ?? await ensureService();
      setBase(monitorBase);
      const [diagnostics, rateResult, sourceResult] = await Promise.all([
        (async () => {
          // These two views read the same rotation-protected journal. Parallel
          // reads contend on its process lock and turn every refresh into 503.
          const eventResult = await jsonRequest<EventResponse>(`${monitorBase}/api/events`);
          const traceResult = await jsonRequest<TraceResponse>(`${monitorBase}/api/traces`);
          return { eventResult, traceResult };
        })(),
        jsonRequest<Rates>(`${monitorBase}/api/rates`),
        includeSources ? jsonRequest<SourceResponse>(`${monitorBase}/api/sources/codex`) : null,
      ]);
      const { eventResult, traceResult } = diagnostics;
      setEvents(eventResult.events ?? []); setTraces(traceResult.traces ?? []); setRates(rateResult.rates ?? []);
      setWarnings([...new Set([...(eventResult.warnings ?? []), ...(traceResult.warnings ?? [])])]);
      if (sourceResult && !sourceDirtyRef.current) setSourcePaths(sourceResult.paths.join("\n"));
      setUpdatedAt(new Date());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setBase(null);
    } finally { setLoading(false); loadingRef.current = false; }
  }, [ensureService]);

  useEffect(() => {
    if (!active) return;
    void readAll(true);
    const timer = window.setInterval(() => { if (!document.hidden) void readAll(false); }, 5000);
    const onVisible = () => { if (!document.hidden) void readAll(false); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [active, readAll]);

  const projects = useMemo(() => projectIds(events, traces), [events, traces]);
  const filteredEvents = useMemo(() => project ? events.filter((event) => event.project_id === project) : events, [events, project]);
  const filteredTraces = useMemo(() => project ? traces.filter((trace) => trace.project_id === project) : traces, [traces, project]);
  const summary = useMemo(() => summarizeUsage(filteredEvents), [filteredEvents]);
  const selectedTrace = filteredTraces.find((trace) => trace.trace_id === traceId) ?? filteredTraces[0] ?? null;

  useEffect(() => {
    if (!selectedTrace) { if (traceId) setTraceId(""); return; }
    if (traceId !== selectedTrace.trace_id) setTraceId(selectedTrace.trace_id);
  }, [selectedTrace?.trace_id, traceId]);
  useEffect(() => { setVisible(20); }, [project]);

  useEffect(() => {
    dispatchQuoteDraft({ type: "refresh", projectId: project, tokens: aggregateTokens(filteredEvents) });
  }, [filteredEvents, project]);

  const resetEstimate = () => {
    invalidateQuote();
    dispatchQuoteDraft({ type: "reset", projectId: project, tokens: aggregateTokens(filteredEvents) });
  };

  const applySources = async (event: FormEvent) => {
    event.preventDefault(); if (!base) return;
    setSourceStatus(""); setError(null);
    try {
      const paths = sourcePaths.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
      const saved = await jsonRequest<SourceResponse>(`${base}/api/sources/codex`, { method: "PUT", body: JSON.stringify({ paths }) });
      setSourcePaths(saved.paths.join("\n")); setSourceDirty(false); setSourceStatus(t.applied); await readAll(false);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };

  const calculate = async (event: FormEvent) => {
    event.preventDefault(); if (!base || rateIndex === "" || quoteDraft.projectId !== project) return;
    invalidateQuote();
    const request = quoteRequest.current;
    const rate = rates[Number(rateIndex)];
    if (!rate) return;
    const count = (key: string) => {
      const value = usageDraft[key]?.trim();
      if (!value) return null;
      const parsed = Number(value);
      if (!Number.isInteger(parsed) || parsed < 0) throw new Error("Token counts must be non-negative whole numbers.");
      return parsed;
    };
    try {
      const usage = {
        input_tokens: count("input_tokens"), output_tokens: count("output_tokens"), cached_input_tokens: count("cached_input_tokens"),
        cache_write_input_tokens: count("cache_write_input_tokens"), cache_write_1h_input_tokens: count("cache_write_1h_input_tokens"),
        reasoning_output_tokens: count("reasoning_output_tokens"),
      };
      // Bind this estimate to the displayed inputs, not the next polling tick.
      dispatchQuoteDraft({ type: "freeze" });
      const result = await jsonRequest<Quote>(`${base}/api/quote`, { method: "POST", body: JSON.stringify({ usage, rate }) });
      if (quoteRequest.current === request) setQuote(result);
    } catch (cause) {
      if (quoteRequest.current === request) setQuoteError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const timeline = numeric(selectedTrace?.summary?.timeline_ms) ?? numeric(selectedTrace?.summary?.elapsed_ms) ?? 0;
  const spans = selectedTrace?.spans ?? [];
  const selectedDiagnostics = selectedTrace?.diagnostics ?? [];
  const selectedWarnings = selectedTrace?.warnings ?? [];

  return <div className="monitor-page">
    <header className="toolbar monitor-toolbar"><button className="btn" type="button" onClick={onClose}>{t.back}</button><strong className="wordmark">MonkeyMonitor</strong><span className={`monitor-health ${error ? "monitor-health--error" : ""}`}>{loading ? t.connecting : error ? t.failed : t.ready}</span><button className="btn" type="button" onClick={() => void readAll(true)} disabled={loading}>{t.refresh}</button></header>
    <main className="monitor-shell">
      <div className="monitor-heading"><div><p className="monitor-eyebrow">MonkeyMonitor</p><h1>{t.title}</h1><p>{t.subtitle}</p></div><label>{t.project}<select value={project} onChange={(event) => { invalidateQuote(); setProject(event.target.value); }}><option value="">{t.allProjects}</option>{projects.map((id) => <option key={id}>{id}</option>)}</select></label></div>
      {error && <div className="error-message" role="alert"><strong>{t.failed}</strong><p>{error}</p><button className="btn" type="button" onClick={() => void readAll(true)}>{t.retry}</button></div>}
      <section className="monitor-stats" aria-label={t.usage}>
        {[
          [t.calls, summary.calls.toLocaleString(), ""],
          [t.cached, formatCount(summary.cachedInput.value), t.coverage(summary.cachedInput.recorded, summary.cachedInput.missing)],
          [t.uncached, formatCount(summary.uncachedInput.value), t.coverage(summary.uncachedInput.recorded, summary.uncachedInput.missing)],
          [t.output, formatCount(summary.output.value), t.coverage(summary.output.recorded, summary.output.missing)],
          [t.wait, formatDuration(summary.requestP50Ms.value), t.coverage(summary.requestP50Ms.recorded, summary.requestP50Ms.missing)],
        ].map(([label, value, note]) => <div className="monitor-stat" key={label}><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</div>)}
      </section>

      <section className="monitor-section">
        <div className="monitor-section__head"><div><p className="monitor-eyebrow">TurnTrace@1</p><h2>{t.tasks}</h2></div>{filteredTraces.length > 0 && <label>{t.task}<select value={selectedTrace?.trace_id ?? ""} onChange={(event) => setTraceId(event.target.value)}>{filteredTraces.map((trace) => <option key={trace.trace_id} value={trace.trace_id}>{dateText(trace.started_at)} · {trace.status ?? "unknown"}</option>)}</select></label>}</div>
        {!selectedTrace ? <p className="monitor-empty">{t.noTasks}</p> : <>
          <div className="monitor-trace-summary">
            <span><small>{t.status}</small><strong>{text(selectedTrace.status)}</strong></span>
            <span><small>{t.elapsed}</small><strong>{formatDuration(numeric(selectedTrace.summary?.elapsed_ms))}</strong></span>
            <span><small>{t.firstVisible}</small><strong>{formatDuration(numeric(selectedTrace.summary?.first_visible_ms))}</strong></span>
            <span><small>{t.firstCandidate}</small><strong>{formatDuration(selectedTrace.summary?.first_candidate_ms)}</strong></span>
            <span><small>{t.modelRounds}</small><strong>{formatCount(numeric(selectedTrace.summary?.model_rounds))}</strong></span>
            <span><small>{t.toolRounds}</small><strong>{formatCount(numeric(selectedTrace.summary?.tool_rounds))}</strong></span>
            <a className="btn" href={base ? `${base}/api/traces/export?trace_id=${encodeURIComponent(selectedTrace.trace_id)}` : undefined} target="_blank" rel="noreferrer">{t.download}</a>
          </div>
          <div className="monitor-timeline" style={{ "--timeline-ms": timeline } as CSSProperties}>
            {spans.map((span, index) => {
              const status = span.status === "incomplete" ? t.endNotObserved : text(span.status);
              const offset = numeric(span.offset_ms) ?? 0, duration = numeric(span.duration_ms) ?? 0;
              const left = timeline > 0 ? Math.max(0, Math.min(100, offset / timeline * 100)) : 0;
              const width = timeline > 0 ? Math.max(0.7, Math.min(100 - left, duration / timeline * 100)) : 1;
              return <details className="monitor-span" key={span.span_id ?? span.event_id ?? index}>
                <summary><span className="monitor-span__label" title={status}>{span.label ?? span.phase ?? "stage"}{span.status === "incomplete" ? ` · ${status}` : ""}</span><span className="monitor-span__lane">{span.lane ?? "—"}</span><span className="monitor-span__track"><i data-lane={span.lane ?? "unknown"} data-blocking={span.blocking === true} style={{ left: `${left}%`, width: `${width}%` }} /></span><span>{formatDuration(span.duration_ms)}</span></summary>
                <dl><dt>{t.status}</dt><dd>{status}</dd><dt>{t.source}</dt><dd>{text(span.source)}</dd><dt>{t.provider}</dt><dd>{[span.provider, span.model].filter(Boolean).join(" · ") || "—"}</dd><dt>{t.blocking}</dt><dd>{span.blocking == null ? "—" : span.blocking ? t.yes : t.no}</dd></dl>
                {span.details && Object.keys(span.details).length > 0 && <pre>{JSON.stringify(span.details, null, 2)}</pre>}
              </details>;
            })}
          </div>
          {(selectedDiagnostics.length > 0 || selectedWarnings.length > 0) && <details className="monitor-diagnostics"><summary>{t.diagnostics} · {selectedDiagnostics.length + selectedWarnings.length}</summary>{selectedDiagnostics.map((item, index) => <div key={index}><strong>{text(item.label ?? item.code)}</strong><p>{text(item.note)}</p></div>)}{selectedWarnings.map((warning) => <p key={warning}>{warning}</p>)}</details>}
          <details className="monitor-raw"><summary>{t.raw}</summary><pre>{JSON.stringify(selectedTrace, null, 2)}</pre></details>
        </>}
      </section>

      <section className="monitor-section">
        <div className="monitor-section__head"><div><p className="monitor-eyebrow">UsageLog</p><h2>{t.usage}</h2></div></div>
        {!filteredEvents.length ? <p className="monitor-empty">{t.noUsage}</p> : <><div className="monitor-table-wrap"><table className="monitor-table"><thead><tr><th>{t.time}</th><th>{t.source}</th><th>{t.provider}</th><th>{t.phase}</th><th>{t.cached}</th><th>{t.uncached}</th><th>{t.output}</th><th>{t.duration}</th></tr></thead><tbody>{filteredEvents.slice(0, visible).map((event) => {
          const cache = event.tokens.cached_input_tokens;
          const input = event.tokens.input_tokens;
          const uncached = input != null && cache != null && input >= cache ? input - cache : null;
          return <tr key={event.event_id}><td>{dateText(event.started_at)}</td><td>{event.source}</td><td>{event.provider}<small>{event.model}</small></td><td>{event.phase}<small>{event.status}</small></td><td>{formatCount(cache)}</td><td>{formatCount(uncached)}</td><td>{formatCount(event.tokens.output_tokens)}</td><td>{formatDuration(event.duration_ms)}</td></tr>;
        })}</tbody></table></div>{visible < filteredEvents.length && <button className="btn monitor-more" type="button" onClick={() => setVisible((value) => value + 20)}>{t.showMore}</button>}</>}
      </section>

      <div className="monitor-lower-grid">
        <section className="monitor-section"><div className="monitor-section__head"><h2>{t.sources}</h2></div><p className="monitor-help">{t.sourcesHelp}</p><form onSubmit={(event) => void applySources(event)}><textarea className="monitor-sources" rows={6} spellCheck={false} value={sourcePaths} onChange={(event) => { setSourcePaths(event.target.value); setSourceDirty(true); setSourceStatus(""); }} /><div className="monitor-actions"><button className="btn btn--primary" type="submit" disabled={!base}>{t.apply}</button><span role="status">{sourceStatus}</span></div></form></section>
        <section className="monitor-section"><div className="monitor-section__head"><h2>{t.calculator}</h2></div><p className="monitor-help">{t.calculatorHelp}</p><form onSubmit={(event) => void calculate(event)} className="monitor-calculator"><label>{t.rate}<select value={rateIndex} onChange={(event) => { invalidateQuote(); setRateIndex(event.target.value); }} required><option value="">{t.chooseRate}</option>{rates.map((rate, index) => <option value={index} key={`${rate.provider}:${rate.model}:${index}`}>{rate.label ?? `${rate.provider} · ${rate.model}`}</option>)}</select></label><div className="monitor-token-grid">{[
          ["input_tokens", t.input], ["cached_input_tokens", t.cacheRead], ["cache_write_input_tokens", t.cacheWrite],
          ["cache_write_1h_input_tokens", t.cacheWrite1h], ["output_tokens", t.output], ["reasoning_output_tokens", t.reasoning],
        ].map(([key, label]) => <label key={key}>{label}<input inputMode="numeric" value={usageDraft[key] ?? ""} onChange={(event) => { invalidateQuote(); dispatchQuoteDraft({ type: "edit", field: key, value: event.target.value }); }} placeholder="—" /></label>)}</div><div className="monitor-actions"><button className="btn btn--primary" type="submit" disabled={!base || rateIndex === "" || quoteDraft.projectId !== project}>{t.calculate}</button><button className="btn" type="button" onClick={resetEstimate}>{t.resetTotals}</button></div></form>
          {quoteError && <div className="error-message"><p>{quoteError}</p></div>}
          {quote && <div className="monitor-quote"><span>{t.estimated}</span><strong>{quote.amount_usd === null ? "—" : `$${quote.amount_usd} ${quote.currency}`}</strong><small>{t.knownSubtotal}: ${quote.known_subtotal_usd}{quote.missing.length ? ` · ${t.missing}: ${quote.missing.join(", ")}` : ""}</small></div>}
        </section>
      </div>

      {warnings.length > 0 && <details className="monitor-global-warnings"><summary>{t.warnings} · {warnings.length}</summary>{warnings.map((warning) => <p key={warning}>{warning}</p>)}</details>}
      <footer>{updatedAt ? `${t.updated}: ${updatedAt.toLocaleTimeString()}` : t.connecting}</footer>
    </main>
  </div>;
}

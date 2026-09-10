import { appearanceFromSearch, applyAppearance } from "/shared/appearance.js";
import { translateMessage } from "/shared/i18n.js";

const appearance = appearanceFromSearch(window.location.search);
applyAppearance(appearance);
const locale = appearance.language;
const english = {
  "MonkeyMonitor · 用量": "MonkeyMonitor · Usage",
  "跳到调用列表": "Skip to calls",
  "正在连接": "Connecting",
  "刷新": "Refresh",
  "费用估算": "Cost estimate",
  "调用用量": "Call usage",
  "用量来源": "Usage source",
  "全部": "All",
  "未缓存输入": "Uncached input",
  "输出": "Output",
  "耗时中位数": "Median duration",
  "记录说明": "About these records",
  "调用列表": "Calls",
  "排序": "Sort",
  "总输入": "Total input",
  "耗时": "Duration",
  "最近": "Latest",
  "暂无调用记录": "No calls yet",
  "接入的用量会显示在这里。": "Recorded usage will appear here.",
  "再显示 10 条": "Show 10 more",
  "按模型汇总": "By model",
  "模型用量汇总": "Usage by model",
  "模型": "Model",
  "次数": "Calls",
  "等待首次读取": "Waiting for first update",
  "页面可见时每 30 秒刷新": "Refreshes every 30 seconds while visible",
  "关闭费用估算": "Close cost estimate",
  "关闭": "Close",
  "费率": "Rates",
  "请选择，或填写自定义单价": "Select rates or enter your own",
  "修改用量和单价": "Edit usage and rates",
  "Token 用量": "Token usage",
  "未知项目留空，确认没有发生的项目填 0。": "Leave unknown values blank. Enter 0 only when you know there was no usage.",
  "总输出": "Total output",
  "缓存读取": "Cache read",
  "缓存写入总量": "Total cache write",
  "其中：1 小时写入": "Of which: 1-hour write",
  "其中：推理输出": "Of which: reasoning output",
  "单价 · 美元 / 百万 Token": "Rates · USD per million tokens",
  "普通输入": "Regular input",
  "普通缓存写入": "Regular cache write",
  "1 小时缓存写入": "1-hour cache write",
  "输出（含推理）": "Output (incl. reasoning)",
  "未知": "Unknown",
  "估算费用": "Estimated cost",
  "计算依据": "Calculation details",
  "填写用量并选择费率后计算。": "Enter usage and select rates to calculate.",
  "请确认模型、上下文长度和服务档位。": "Check the model, context length and service tier.",
  "总输入包含缓存读取和写入，推理输出包含在总输出中，不重复计费。普通缓存写入中，Anthropic 可填写 5 分钟档位。": "Total input includes cache reads and writes. Total output includes reasoning; neither is charged twice. For Anthropic, use the 5-minute tier for regular cache writes.",
  "订阅用量按单价换算的费用等值，不代表实际扣费。": "For subscription usage, this is a rate-based equivalent, not an actual charge.",
  "已完成": "Completed",
  "成功": "Succeeded",
  "失败": "Failed",
  "已取消": "Cancelled",
  "进行中": "Running",
  "已记录": "Recorded",
  "已观测": "Observed",
  "已编译": "Compiled",
  "待补充": "Needs input",
  "不支持": "Unsupported",
  "仅末次用量": "Last usage only",
  "历史不完整": "Partial history",
  "计数不连续": "Counter discontinuity",
  "计数重置未知": "Unknown counter reset",
  "API 用量": "API usage",
  "订阅用量 · 仅等值估算": "Subscription usage · equivalent estimate only",
  "计费方式未记录": "Billing mode not recorded",
  "普通输入用量": "regular input usage",
  "缓存读取用量": "cache read usage",
  "普通缓存写入用量": "regular cache write usage",
  "1 小时缓存写入用量": "1-hour cache write usage",
  "输出用量": "output usage",
  "普通输入单价": "regular input rate",
  "缓存读取单价": "cache read rate",
  "普通缓存写入单价": "regular cache write rate",
  "1 小时缓存写入单价": "1-hour cache write rate",
  "输出单价": "output rate",
  "未记录": "Not recorded",
  "模型未记录": "Model not recorded",
  "时间": "Time",
  "时间未知": "Unknown time",
  "查看 / 估算": "View / estimate",
  "输入中 {percent}% 命中缓存": "{percent}% of input was cached",
  "部分记录不完整": "Some records are incomplete",
  "来源归属未核实": "Source attribution unverified",
  "未缓存输入：{inputRecorded} 次已记录，{inputMissing} 次未知；输出：{outputRecorded} 次已记录，{outputMissing} 次未知。": "Uncached input: {inputRecorded} recorded, {inputMissing} unknown. Output: {outputRecorded} recorded, {outputMissing} unknown.",
  "未缓存输入为总输入减去缓存读取，包含新写入缓存的输入。只在两项均已知时计算；此排序用于比较用量，不代表费用排名。": "Uncached input is total input minus cache reads, including newly written cache input. It is calculated only when both values are known. This order compares usage, not cost.",
  "缓存比例使用输入和缓存读取同时已知的 {recorded} 次调用，{missing} 次缺失。缓存读取仍可能计费。": "The cache ratio uses {recorded} calls with both input and cache reads known; {missing} calls are missing those values. Cache reads may still be charged.",
  "耗时：{recorded} 次已记录，{missing} 次未知。": "Duration: {recorded} recorded, {missing} unknown.",
  "尚无耗时记录。": "No duration recorded yet.",
  "{count} 条记录仅保留部分历史或计数不连续，不能用于完整账单核对。": "{count} records contain partial history or counter discontinuities and cannot verify a complete bill.",
  "读取时有 {count} 条提示。": "Reading produced {count} notices.",
  "{count} 次未知": "{count} unknown",
  "查看 / 估算：{model}": "View / estimate: {model}",
  "显示 {shown} / {total} 条": "Showing {shown} / {total}",
  "请自行选择与模型、上下文长度及服务档位匹配的费率。": "Choose rates that match the model, context length and service tier.",
  "更新于 {time}": "Updated at {time}",
  "本地服务在线": "Local service online",
  "连接未确认": "Connection unconfirmed",
  "刷新失败，仍显示上次成功读取的数据。请稍后重试。": "Refresh failed. The last loaded data is still shown. Try again later.",
  "暂时无法读取用量。确认本地服务已启动后点击刷新。": "Usage could not be loaded. Check that the local service is running, then refresh.",
  "参考费率暂不可用，可手动填写单价；刷新后会重试读取。": "Reference rates are unavailable. Enter rates manually or refresh to retry.",
  "填写用量并选择费率后计算，未知项目保留为空。": "Enter usage and select rates to calculate. Leave unknown values blank.",
  "手动填写用量，并选择或填写单价。": "Enter usage manually, then select or enter rates.",
  "用量已修改": "Usage edited",
  "缓存写入": "Cache write",
  "日期未记录": "Date not recorded",
  "费率来源": "Rate source",
  "来源未记录": "Source not recorded",
  "。仅应用所选费率，不自动判断上下文长度或服务档位。": ". Only the selected rates apply; context length and service tier are not detected automatically.",
  "Token 数量请填写非负整数；未知项目保留空白。": "Enter non-negative whole numbers for tokens. Leave unknown values blank.",
  "总输入必须包含缓存读取与缓存写入，不能小于两者之和。": "Total input must include cache reads and writes and cannot be less than their sum.",
  "1 小时缓存写入不能超过缓存写入总量。": "1-hour cache writes cannot exceed total cache writes.",
  "推理输出不能超过总输出。": "Reasoning output cannot exceed total output.",
  "单价请填写非负十进制数，单位为美元 / 百万 Token；未知单价保留空白。": "Enter non-negative decimal rates in USD per million tokens. Leave unknown rates blank.",
  "正在计算": "Calculating",
  "正在按各项用量与单价计算。": "Calculating from each usage component and its rate.",
  "费用尚不完整": "Cost is incomplete",
  "其他用量或单价": "other usage or rates",
  "还缺少 {count} 项": "{count} items missing",
  "已知部分：${subtotal} USD。仍缺少：{missing}。": "Known subtotal: ${subtotal} USD. Still missing: {missing}.",
  "完整用量或单价": "complete usage or rates",
  "估算费用 · USD": "Estimated cost · USD",
  "本条为订阅用量，结果为单价换算的费用等值。": "This is subscription usage. The result is a rate-based cost equivalent.",
  "按填写的用量和单价计算。": "Calculated from the entered usage and rates.",
  "暂时无法计算": "Calculation unavailable",
  "请确认本地服务连接与输入内容后重试。": "Check the local connection and entered values, then retry.",
  "已修改为自定义单价。请确认它们适用于本次用量。": "Using custom rates. Check that they apply to this usage.",
  "项目": "Project",
  "全部项目": "All projects",
  "项目未记录": "Project not recorded",
  "模型调用耗时中位数": "Median model call duration",
  "操作与耗时": "Operations and timing",
  "各范围分别展示，不相加。候选生成耗时已包含几何导出；人工停留不计为模型调用。": "Timing scopes are shown separately, never added together. Candidate duration includes geometry export. Human dwell is not model call time.",
  "分组": "Group by",
  "候选轮次": "Candidate run",
  "精确来源": "Exact source",
  "会话家族": "Session family",
  "再显示 10 组": "Show 10 more groups",
  "模型调用": "Model call",
  "服务执行": "Service execution",
  "客户端等待": "Client wait",
  "代理整轮": "Agent turn",
  "耗时范围未知": "Unknown timing scope",
  "{count} 条 · 中位数 {duration}": "{count} records · median {duration}",
  "{count} 条耗时未知": "{count} unknown durations",
  "候选生成（含几何导出）": "Candidate generation (includes geometry export)",
  "几何导出": "Geometry export",
  "模型加载": "Model load",
  "Stage 保存": "Stage save",
  "意图理解": "Intent interpretation",
  "代理调用": "Agent call",
  "无模型调用": "No model call",
  "调用状态未知": "Model call status unknown",
  "候选轮次未记录": "Candidate run not recorded",
  "会话未记录": "Session not recorded",
  "父会话": "Parent session",
  "子会话": "Child session",
  "会话": "Session",
  "轮次": "Turn",
  "开始": "Started",
  "结束": "Ended",
  "关联的意图调用": "Linked intent call",
  "来源与记录详情": "Source and record details",
  "记录 ID": "Event ID",
  "关联记录": "Related event",
  "无操作记录": "No operations recorded",
  "{count} 条操作": "{count} operations",
  "Token 汇总仅计模型调用；模型耗时中位数只使用明确记录为 model_call 的时间。": "Token totals include model calls only. The model duration median uses only explicitly recorded model_call intervals.",
  "Codex 来源": "Codex sources",
  "会话 JSONL 文件": "Session JSONL files",
  "每行一个本地绝对路径；只读取明确列出的文件。留空并应用可清除来源，选择仅用于当前服务。": "One local absolute path per line. Only listed files are read. Apply an empty list to clear the selection. This selection lasts for the current service only.",
  "应用来源": "Apply sources",
  "读取当前来源": "Read current sources",
  "正在读取来源": "Reading sources",
  "已读取 {count} 个来源": "Loaded {count} sources",
  "正在应用来源": "Applying sources",
  "已应用 {count} 个来源": "Applied {count} sources",
  "来源读取失败，可重试。": "Could not read sources. Retry when the service is available.",
  "来源未更改：{error}": "Sources unchanged: {error}",
  "请求失败": "Request failed"
};
const catalog = locale === "en" ? english : {};
const t = (key, parameters) => translateMessage(catalog, key, parameters);

for (const element of document.querySelectorAll("[data-i18n]")) {
  const content = element.firstChild;
  content.nodeValue = t(element.dataset.i18n) + (content.nodeValue.endsWith(" ") ? " " : "");
}
for (const attribute of ["aria-label", "placeholder"]) {
  for (const element of document.querySelectorAll(`[data-i18n-${attribute}]`)) {
    element.setAttribute(attribute, t(element.getAttribute(`data-i18n-${attribute}`)));
  }
}

(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("quote-form");
  const usageFields = ["input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens", "cache_write_1h_input_tokens", "reasoning_output_tokens"];
  const rateFields = ["input", "cached_input", "cache_write_input", "cache_write_1h_input", "output"];
  const state = { events: [], source: "all", project: "", group: "run", visibleGroups: 10, openGroups: new Set(), sourcesLoaded: false, sourceBusy: false, sort: "uncached_input", loaded: false, loading: false, rates: [], ratesLoaded: false, selectedRate: null, quoteEvent: null, warnings: [], excluded: 0, visibleEvents: 10, quoteVersion: 0 };
  const controllers = new Set();
  const integerFormat = new Intl.NumberFormat(locale);
  const timeFormat = new Intl.DateTimeFormat(locale, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  const sourceLabels = { studio: "Studio", codex: "Codex" };
  const statusLabels = { completed: "已完成", succeeded: "成功", success: "成功", failed: "失败", error: "失败", cancelled: "已取消", running: "进行中", recorded: "已记录", observed: "已观测", compiled: "已编译", question: "待补充", unsupported: "不支持", last_only: "仅末次用量", token_count: "已记录", partial_history: "历史不完整", counter_discontinuity: "计数不连续", counter_reset_unknown: "计数重置未知" };
  const partialStatuses = new Set(["partial_history", "counter_discontinuity", "counter_reset_unknown", "last_only"]);
  const scopeLabels = { model_call: "模型调用", service: "服务执行", client_wait: "客户端等待", agent_turn: "代理整轮", unknown: "耗时范围未知" };
  const phaseLabels = { candidate: "候选生成（含几何导出）", geometry_export: "几何导出", model_load: "模型加载", stage_save: "Stage 保存", intent: "意图理解", agent: "代理调用", agent_turn: "代理整轮" };
  const billingLabels = { api_estimate: "API 用量", subscription_equivalent: "订阅用量 · 仅等值估算", unknown: "计费方式未记录" };
  const missingLabels = { "tokens.input": "普通输入用量", "tokens.cached_input": "缓存读取用量", "tokens.cache_write_input": "普通缓存写入用量", "tokens.cache_write_1h_input": "1 小时缓存写入用量", "tokens.output": "输出用量", "rate.input": "普通输入单价", "rate.cached_input": "缓存读取单价", "rate.cache_write_input": "普通缓存写入单价", "rate.cache_write_1h_input": "1 小时缓存写入单价", "rate.output": "输出单价", rate_card: "费率" };

  function text(id, value, parameters) { $(id).textContent = t(value, parameters); }
  function node(tag, className, value) { const element = document.createElement(tag); if (className) element.className = className; if (value !== undefined) element.textContent = value; return element; }
  function known(value) { return Number.isSafeInteger(value) && value >= 0; }
  function count(value) { return known(value) ? integerFormat.format(value) : "—"; }
  function label(value, fallback = "未记录") { return typeof value === "string" && value.trim() && value !== "unknown" ? value : t(fallback); }
  function duration(value) { if (!known(value)) return "—"; return value < 1000 ? `${integerFormat.format(value)} ms` : `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value / 1000)} s`; }
  function timingScope(event) { return Object.hasOwn(scopeLabels, event.timing_scope) ? event.timing_scope : "unknown"; }
  function isModelCall(event) { return event.model_call === true || (event.model_call == null && event.phase !== "agent_turn" && ["intent", "agent"].includes(event.phase)); }
  function modelDuration(event) { return timingScope(event) === "model_call" && isModelCall(event) ? event.duration_ms : null; }
  function median(values) { const sorted = values.filter(known).sort((a, b) => a - b); const middle = Math.floor(sorted.length / 2); return sorted.length ? (sorted.length % 2 ? sorted[middle] : Math.round((sorted[middle - 1] + sorted[middle]) / 2)) : null; }
  function projectKey(event) { return event.project_id || "__unrecorded__"; }
  function aggregate(events, field) { let total = 0n; let recorded = 0; for (const event of events) { const value = metric(event, field); if (known(value)) { total += BigInt(value); recorded += 1; } } return { total, recorded, missing: events.length - recorded }; }
  function aggregateText(value) { return value.recorded ? integerFormat.format(value.total) : "—"; }
  function tokenStat(id, summary) {
    let display = aggregateText(summary);
    if (summary.recorded && summary.total >= (locale === "en" ? 1000n : 10000n)) {
      const [unit, suffix] = locale === "en"
        ? summary.total >= 1000000000000n ? [1000000000000n, "T"] : summary.total >= 1000000000n ? [1000000000n, "B"] : summary.total >= 1000000n ? [1000000n, "M"] : [1000n, "K"]
        : summary.total >= 1000000000000n ? [1000000000000n, "万亿"] : summary.total >= 100000000n ? [100000000n, "亿"] : [10000n, "万"];
      const hundredths = (summary.total * 100n + unit / 2n) / unit;
      display = `${hundredths / 100n}.${String(hundredths % 100n).padStart(2, "0")}${suffix}`;
    }
    text(id, display);
    const exact = summary.recorded ? `${integerFormat.format(summary.total)} Token` : t("未记录");
    $(id).title = exact; $(id).setAttribute("aria-label", exact);
  }

  async function request(path, options = {}) {
    const controller = new AbortController();
    controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(path, { ...options, signal: controller.signal, cache: "no-store" });
      if (!response.ok) { const problem = await response.json().catch(() => ({})); throw new Error(typeof problem.error === "string" ? problem.error : t("请求失败")); }
      return await response.json();
    } finally { clearTimeout(timeout); controllers.delete(controller); }
  }

  function metric(event, field) {
    if (field === "time") { const value = Date.parse(event.started_at); return Number.isFinite(value) ? value : null; }
    if (field === "uncached_input") {
      const usage = event.tokens;
      if (![usage?.input_tokens, usage?.cached_input_tokens].every(known)) return null;
      const value = usage.input_tokens - usage.cached_input_tokens;
      return known(value) ? value : null;
    }
    const value = field === "duration_ms" ? modelDuration(event) : event.tokens?.[field];
    return known(value) ? value : null;
  }

  function descending(left, right) {
    if (left === null || right === null) return Number(right !== null) - Number(left !== null);
    return left === right ? 0 : left > right ? -1 : 1;
  }

  function renderOverview() {
    const sourceEvents = state.events.filter((event) => state.source === "all" || event.source === state.source);
    const projects = [...new Set(sourceEvents.map(projectKey))].sort();
    if (state.project && !projects.includes(state.project)) state.project = "";
    const allProjects = node("option", "", t("全部项目")); allProjects.value = "";
    $("project-filter").replaceChildren(allProjects, ...projects.map((value) => { const option = node("option", "", value === "__unrecorded__" ? t("项目未记录") : value); option.value = value; return option; }));
    $("project-filter").value = state.project;
    const operations = sourceEvents.filter((event) => !state.project || projectKey(event) === state.project);
    const events = operations.filter(isModelCall);
    const input = aggregate(events, "uncached_input");
    const output = aggregate(events, "output_tokens");
    tokenStat("stat-input", input); tokenStat("stat-output", output);
    const durations = events.map(modelDuration).filter(known);
    text("stat-duration", duration(median(durations)));
    const paired = events.filter((event) => known(event.tokens?.input_tokens) && known(event.tokens?.cached_input_tokens));
    const pairedInput = aggregate(paired, "input_tokens");
    const pairedCache = aggregate(paired, "cached_input_tokens");
    const ratioTenths = pairedInput.total > 0n ? (pairedCache.total * 1000n + pairedInput.total / 2n) / pairedInput.total : null;
    text("cache-summary", ratioTenths === null ? "" : t("输入中 {percent}% 命中缓存", { percent: `${ratioTenths / 10n}.${ratioTenths % 10n}` }));
    $("cache-summary").hidden = ratioTenths === null;
    const partial = events.filter((event) => partialStatuses.has(event.status)).length;
    const incomplete = input.missing || output.missing || partial || state.warnings.length || state.excluded;
    const attributionUnknown = state.warnings.some((warning) => warning.includes("继承"));
    text("coverage-title", attributionUnknown ? "来源归属未核实" : incomplete ? "部分记录不完整" : "记录说明");
    const coverage = [
      "Token 汇总仅计模型调用；模型耗时中位数只使用明确记录为 model_call 的时间。",
      t("未缓存输入：{inputRecorded} 次已记录，{inputMissing} 次未知；输出：{outputRecorded} 次已记录，{outputMissing} 次未知。", { inputRecorded: input.recorded, inputMissing: input.missing, outputRecorded: output.recorded, outputMissing: output.missing }),
      "未缓存输入为总输入减去缓存读取，包含新写入缓存的输入。只在两项均已知时计算；此排序用于比较用量，不代表费用排名。",
      t("缓存比例使用输入和缓存读取同时已知的 {recorded} 次调用，{missing} 次缺失。缓存读取仍可能计费。", { recorded: paired.length, missing: events.length - paired.length }),
      durations.length ? t("耗时：{recorded} 次已记录，{missing} 次未知。", { recorded: durations.length, missing: events.length - durations.length }) : "尚无耗时记录。"
    ];
    if (partial) coverage.push(t("{count} 条记录仅保留部分历史或计数不连续，不能用于完整账单核对。", { count: partial }));
    if (state.warnings.length || state.excluded) coverage.push(t("读取时有 {count} 条提示。", { count: state.warnings.length + state.excluded }));
    coverage.push(...state.warnings);
    $("coverage-content").replaceChildren(...coverage.map((value) => node("p", "", t(value))));
    renderOperations(operations); renderEvents(events); renderBreakdown(events);
  }

  function renderOperations(events) {
    text("operation-count", `(${integerFormat.format(events.length)})`);
    const scopes = Object.entries(scopeLabels).map(([scope, title]) => {
      const group = events.filter((event) => timingScope(event) === scope);
      if (!group.length) return null;
      const item = node("div", "scope-stat"); item.dataset.scope = scope;
      item.append(node("strong", "", t(title)), node("span", "", t("{count} 条 · 中位数 {duration}", { count: group.length, duration: duration(median(group.map((event) => event.duration_ms))) })));
      const missing = group.filter((event) => !known(event.duration_ms)).length;
      if (missing) item.append(node("span", "secondary-text", t("{count} 条耗时未知", { count: missing })));
      return item;
    }).filter(Boolean);
    $("timing-scopes").replaceChildren(...scopes);
    // Session ancestry comes only from explicit metadata, never path or id spelling.
    const parents = new Map();
    for (const event of state.events) if (event.session_id && event.parent_session_id) parents.set(event.session_id, event.parent_session_id);
    function family(session) {
      const seen = new Set();
      while (parents.has(session) && !seen.has(session)) { seen.add(session); session = parents.get(session); }
      return seen.has(session) ? [...seen].sort()[0] : session;
    }
    const groups = new Map();
    for (const event of events) {
      const parts = state.group === "source" ? [event.source, event.source_ref || null]
        : state.group === "session" ? [event.source, event.session_id ? family(event.session_id) : null]
        : [event.project_id || null, event.run_id || null];
      const key = JSON.stringify(parts);
      if (!groups.has(key)) groups.set(key, { parts, events: [] });
      groups.get(key).events.push(event);
    }
    const byId = new Map(state.events.map((event) => [event.event_id, event]));
    const fragment = document.createDocumentFragment();
    for (const [key, group] of [...groups].slice(0, state.visibleGroups)) {
      const detail = node("details", "operation-group"); detail.dataset.group = key;
      const openKey = `${state.group}:${key}`;
      detail.open = state.openGroups.has(openKey);
      detail.addEventListener("toggle", () => { if (!detail.isConnected) return; if (detail.open) state.openGroups.add(openKey); else state.openGroups.delete(openKey); });
      const title = state.group === "run" ? `${label(group.parts[0], "项目未记录")} · ${label(group.parts[1], "候选轮次未记录")}`
        : `${sourceLabels[group.parts[0]]} · ${label(group.parts[1], state.group === "session" ? "会话未记录" : "来源未记录")}`;
      const heading = node("summary"); heading.append(node("strong", "", title), node("span", "secondary-text", t("{count} 条操作", { count: group.events.length })));
      detail.append(heading);
      const rows = [...group.events]; const linked = new Set();
      if (state.group === "run") {
        for (const event of group.events) {
          const related = byId.get(event.related_event_id);
          if (event.phase === "candidate" && related?.phase === "intent" && !rows.some((row) => row.event_id === related.event_id)) { rows.push(related); linked.add(related.event_id); }
        }
      }
      rows.sort((a, b) => (Date.parse(a.started_at) || 0) - (Date.parse(b.started_at) || 0));
      // A family still shows individual sessions, so child usage stays attributable.
      const sessions = new Map();
      for (const event of rows) {
        const session = state.group === "session" ? event.session_id || "" : "";
        if (!sessions.has(session)) sessions.set(session, []);
        sessions.get(session).push(event);
      }
      for (const [session, entries] of sessions) {
        if (state.group === "session") {
          const sessionHeading = node("h3", "session-heading", session ? `${t(parents.has(session) ? "子会话" : "会话")} · ${session}` : t("会话未记录"));
          detail.append(sessionHeading);
        }
        for (const event of entries) detail.append(operationRow(event, linked.has(event.event_id)));
      }
      fragment.append(detail);
    }
    if (!groups.size) fragment.append(node("p", "field-help", t("无操作记录")));
    $("operation-groups").replaceChildren(fragment);
    $("more-operations").hidden = state.visibleGroups >= groups.size;
  }

  function operationRow(event, linked) {
    const row = node("article", "operation-row"); row.dataset.eventId = event.event_id;
    const title = node("div", "operation-heading");
    title.append(node("strong", "", t(phaseLabels[event.phase] || (event.phase?.startsWith("geometry_export.") ? "几何导出" : event.phase))), node("span", ["failed", "error", "cancelled"].includes(event.status) ? "failure-text" : "secondary-text", t(statusLabels[event.status] || event.status)));
    row.append(title);
    if (linked) row.append(node("p", "secondary-text", t("关联的意图调用")));
    const callState = event.model_call === false ? "无模型调用" : isModelCall(event) ? "模型调用" : "调用状态未知";
    const scopeLabel = scopeLabels[timingScope(event)];
    row.append(node("p", "operation-timing", [t(scopeLabel), duration(event.duration_ms), ...(scopeLabel === callState ? [] : [t(callState)])].join(" · ")));
    if (isModelCall(event)) row.append(node("p", "secondary-text", `${label(event.model, "模型未记录")} · ${t("总输入")} ${known(event.tokens?.input_tokens) ? count(event.tokens.input_tokens) : t("未知")} · ${t("输出")} ${known(event.tokens?.output_tokens) ? count(event.tokens.output_tokens) : t("未知")}`));
    const detail = node("details", "event-diagnostics"); detail.append(node("summary", "", t("来源与记录详情")));
    const values = node("dl");
    const fields = [["开始", event.started_at], ["结束", event.ended_at], ["项目", event.project_id], ["候选轮次", event.run_id], ["精确来源", event.source_ref], ["记录 ID", event.event_id], ["关联记录", event.related_event_id], ["会话", event.session_id], ["父会话", event.parent_session_id], ["轮次", event.turn_id], ["phase", event.phase], ["Token 用量", event.model_call === false ? t("未知") : null]];
    if (isModelCall(event)) {
      for (const [field, name] of [["cached_input_tokens", "缓存读取"], ["cache_write_input_tokens", "缓存写入总量"], ["cache_write_1h_input_tokens", "其中：1 小时写入"], ["reasoning_output_tokens", "其中：推理输出"]]) fields.push([name, known(event.tokens?.[field]) ? count(event.tokens[field]) : t("未知")]);
    }
    for (const [name, value] of fields) {
      if (value == null && !["结束", "精确来源"].includes(name)) continue;
      const item = node("div"); item.append(node("dt", "", t(name)), node("dd", "", value == null ? t("未知") : value)); values.append(item);
    }
    detail.append(values); row.append(detail); return row;
  }

  async function readSources() {
    if (state.sourceBusy) return;
    state.sourceBusy = true; $("reload-sources").disabled = true; $("apply-sources").disabled = true;
    text("source-status", "正在读取来源");
    try {
      const result = await request("/api/sources/codex");
      if (!Array.isArray(result.paths) || !result.paths.every((path) => typeof path === "string")) throw new Error("invalid_sources");
      $("source-paths").value = result.paths.join("\n"); state.sourcesLoaded = true;
      text("source-status", "已读取 {count} 个来源", { count: result.paths.length });
    } catch { text("source-status", "来源读取失败，可重试。"); }
    finally { state.sourceBusy = false; $("source-paths").disabled = !state.sourcesLoaded; $("reload-sources").disabled = false; $("apply-sources").disabled = !state.sourcesLoaded; }
  }

  async function applySources(event) {
    event.preventDefault(); if (state.sourceBusy || !state.sourcesLoaded) return;
    const paths = $("source-paths").value.split(/\r?\n/).map((path) => path.trim()).filter(Boolean);
    state.sourceBusy = true; $("source-paths").disabled = true; $("apply-sources").disabled = true; $("reload-sources").disabled = true;
    text("source-status", "正在应用来源");
    try {
      const result = await request("/api/sources/codex", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paths }) });
      if (!Array.isArray(result.paths) || !result.paths.every((path) => typeof path === "string")) throw new Error("invalid_sources");
      $("source-paths").value = result.paths.join("\n");
      text("source-status", "已应用 {count} 个来源", { count: result.paths.length });
      await refresh();
    } catch (error) { text("source-status", "来源未更改：{error}", { error: error.message }); }
    finally { state.sourceBusy = false; $("source-paths").disabled = false; $("apply-sources").disabled = false; $("reload-sources").disabled = false; }
  }

  function renderBreakdown(events) {
    const groups = new Map();
    for (const event of events) {
      const key = JSON.stringify([event.provider, event.model]);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(event);
    }
    $("model-summary").hidden = groups.size <= 1;
    if (groups.size <= 1) { $("breakdown-body").replaceChildren(); return; }
    const ordered = [...groups.values()].map((group) => ({ group, input: aggregate(group, "uncached_input") }));
    ordered.sort((a, b) => descending(a.input.recorded ? a.input.total : null, b.input.recorded ? b.input.total : null));
    const fragment = document.createDocumentFragment();
    for (const { group, input } of ordered) {
      const row = node("tr"); const model = node("td", "", label(group[0].model, "模型未记录"));
      const inputCell = node("td", "number", aggregateText(input));
      if (input.missing) inputCell.title = t("{count} 次未知", { count: input.missing });
      row.append(model, node("td", "number", integerFormat.format(group.length)), inputCell, node("td", "number", aggregateText(aggregate(group, "output_tokens"))));
      fragment.append(row);
    }
    $("breakdown-body").replaceChildren(fragment); text("group-count", `(${groups.size})`);
  }

  function renderEvents(events) {
    const titles = { uncached_input: "未缓存输入", input_tokens: "总输入", output_tokens: "输出", duration_ms: "耗时" };
    const displayMetric = state.sort === "time" ? "input_tokens" : state.sort;
    const ordered = [...events].sort((a, b) => descending(metric(a, state.sort), metric(b, state.sort)));
    const shown = ordered.slice(0, state.visibleEvents);
    const maxMetric = shown.reduce((max, event) => Math.max(max, metric(event, displayMetric) ?? 0), 0);
    const showSource = state.source === "all" && new Set(events.map((event) => event.source)).size > 1;
    const extras = [];
    if (displayMetric !== "output_tokens" && events.some((event) => known(event.tokens?.output_tokens))) extras.push("output_tokens");
    if (displayMetric !== "duration_ms" && events.some((event) => known(modelDuration(event)))) extras.push("duration_ms");
    const header = node("tr");
    for (const [name, className] of [["时间", "time-cell"], ["模型", "model-cell"], [titles[displayMetric], "number"], ...extras.map((field) => [titles[field], "number extra-cell"]), ["", "action-cell"]]) { const cell = node("th", className, t(name)); cell.scope = "col"; header.append(cell); }
    $("events-head").replaceChildren(header);
    const fragment = document.createDocumentFragment();
    for (const event of shown) {
      const row = node("tr"); const timestamp = new Date(event.started_at);
      const timeCell = node("td", "time-cell");
      const time = node("time", "", Number.isNaN(timestamp.getTime()) ? t("时间未知") : timeFormat.format(timestamp));
      if (!Number.isNaN(timestamp.getTime())) { time.dateTime = timestamp.toISOString(); time.title = timestamp.toLocaleString(locale); }
      timeCell.append(time);
      if (showSource) timeCell.append(node("span", "secondary-text", sourceLabels[event.source]));
      const modelCell = node("td", "model-cell", label(event.model, "模型未记录"));
      if (["failed", "error", "cancelled", "unsupported"].includes(event.status) || partialStatuses.has(event.status)) {
        modelCell.append(node("span", "secondary-text failure-text", t(statusLabels[event.status] || "已记录")));
      }
      const value = metric(event, displayMetric);
      const metricCell = node("td", "metric-cell");
      metricCell.append(node("span", "metric-caption", t(titles[displayMetric])), node("span", "", displayMetric === "duration_ms" ? duration(value) : count(value)));
      metricCell.title = value === null ? t("未记录") : `${integerFormat.format(value)}${displayMetric === "duration_ms" ? " ms" : " Token"}`;
      if (value !== null && maxMetric > 0) { const track = node("div", "metric-track"); track.setAttribute("aria-hidden", "true"); const bar = node("span"); bar.style.width = `${Math.min(100, value / maxMetric * 100)}%`; track.append(bar); metricCell.append(track); }
      row.append(timeCell, modelCell, metricCell);
      for (const field of extras) row.append(node("td", "number extra-cell", field === "duration_ms" ? duration(modelDuration(event)) : count(event.tokens?.[field])));
      const actionCell = node("td", "action-cell"); const button = node("button", "button event-quote", t("查看 / 估算"));
      button.type = "button"; button.setAttribute("aria-label", t("查看 / 估算：{model}", { model: label(event.model, "模型未记录") }));
      button.addEventListener("click", () => fillEvent(event)); actionCell.append(button); row.append(actionCell); fragment.append(row);
    }
    $("events-body").replaceChildren(fragment); $("events-empty").hidden = Boolean(events.length); $("events-wrap").hidden = !events.length;
    text("event-count", state.loaded ? `(${integerFormat.format(events.length)})` : "");
    text("visible-count", events.length ? t("显示 {shown} / {total} 条", { shown: shown.length, total: integerFormat.format(events.length) }) : "");
    $("more-events").hidden = shown.length >= events.length;
  }

  function populateRates(payload) {
    if (!Array.isArray(payload.rates)) throw new Error("invalid_rates");
    state.rates = payload.rates.filter((rate) => rate && typeof rate.provider === "string" && typeof rate.model === "string");
    const select = $("rate-preset");
    for (const [index, rate] of state.rates.entries()) { const option = node("option", "", label(rate.label, `${rate.provider} / ${rate.model}`)); option.value = String(index); select.append(option); }
    state.ratesLoaded = true;
    if (!state.selectedRate) text("rate-source", "请自行选择与模型、上下文长度及服务档位匹配的费率。");
  }

  async function refresh() {
    if (state.loading || document.visibilityState === "hidden") return;
    state.loading = true; $("refresh").disabled = true; $("overview").setAttribute("aria-busy", "true");
    const shouldLoadRates = !state.ratesLoaded;
    const requests = [request("/api/events"), request("/api/health")];
    if (shouldLoadRates) requests.push(request("/api/rates"));
    const results = await Promise.allSettled(requests);
    const [eventsResult, healthResult, ratesResult] = results;
    let eventsValid = false;
    if (eventsResult.status === "fulfilled" && Array.isArray(eventsResult.value.events)) {
      const unique = new Map(); let excluded = 0;
      for (const event of eventsResult.value.events) {
        if (!event || typeof event.event_id !== "string" || !event.event_id || !["studio", "codex"].includes(event.source)) { excluded += 1; continue; }
        unique.set(event.event_id, event);
      }
      state.events = [...unique.values()].sort((a, b) => (Date.parse(b.started_at) || 0) - (Date.parse(a.started_at) || 0));
      state.warnings = Array.isArray(eventsResult.value.warnings) ? eventsResult.value.warnings.filter((warning) => typeof warning === "string" && warning.trim()) : [];
      state.excluded = excluded; state.loaded = true; eventsValid = true;
      text("sync-time", t("更新于 {time}", { time: new Date().toLocaleTimeString(locale, { hour12: false }) }));
    }
    const online = healthResult.status === "fulfilled" && healthResult.value.status === "ok";
    text("health", online ? "本地服务在线" : "连接未确认"); $("health").classList.toggle("online", online);
    if (shouldLoadRates && ratesResult?.status === "fulfilled") { try { populateRates(ratesResult.value); } catch { /* Manual prices remain usable. */ } }
    text("load-notice", state.loaded ? "刷新失败，仍显示上次成功读取的数据。请稍后重试。" : "暂时无法读取用量。确认本地服务已启动后点击刷新。");
    $("load-notice").hidden = eventsValid;
    if (!state.ratesLoaded) text("rate-source", "参考费率暂不可用，可手动填写单价；刷新后会重试读取。");
    renderOverview(); state.loading = false; $("refresh").disabled = false; $("overview").setAttribute("aria-busy", "false");
  }

  function invalidateQuote() {
    state.quoteVersion += 1;
    text("quote-label", "估算费用"); text("quote-amount", "—"); text("quote-detail-title", "计算依据"); text("quote-detail", "填写用量并选择费率后计算，未知项目保留为空。");
    $("form-error").hidden = true;
    for (const element of form.querySelectorAll("[aria-invalid]")) element.removeAttribute("aria-invalid");
  }

  function quoteContext(edited = false) {
    const event = state.quoteEvent;
    if (!event) { text("quote-context", "手动填写用量，并选择或填写单价。"); $("event-usage").hidden = true; return; }
    text("quote-context", `${label(event.model, "模型未记录")} · ${sourceLabels[event.source]} · ${t(statusLabels[event.status] || "已记录")} · ${t(billingLabels[event.billing_mode] || billingLabels.unknown)}${edited ? ` · ${t("用量已修改")}` : ""}`);
    const summary = [];
    for (const [field, title] of [["input_tokens", "总输入"], ["output_tokens", "总输出"], ["cached_input_tokens", "缓存读取"], ["cache_write_input_tokens", "缓存写入"]]) {
      const raw = form.elements.namedItem(field).value.trim();
      const value = raw !== "" && /^\d+$/.test(raw) && known(Number(raw)) ? Number(raw) : null;
      const item = node("div"); item.append(node("dt", "", t(title)), node("dd", "", count(value))); summary.push(item);
    }
    $("event-usage").replaceChildren(...summary); $("event-usage").hidden = false;
  }

  function fillEvent(event = null) {
    state.quoteEvent = event; form.reset(); state.selectedRate = null; selectRate();
    for (const field of usageFields) form.elements.namedItem(field).value = known(event?.tokens?.[field]) ? String(event.tokens[field]) : "";
    $("advanced-fields").open = !event; $("estimate-details").open = false;
    quoteContext();
    if (!$("calculator").open) $("calculator").showModal();
    if (event) $("rate-preset").focus(); else form.elements.namedItem("input_tokens").focus();
  }

  function selectRate() {
    const value = $("rate-preset").value;
    state.selectedRate = value === "" ? null : state.rates[Number(value)];
    for (const field of rateFields) form.elements.namedItem(`rate_${field}`).value = state.selectedRate?.[field] ?? "";
    const source = $("rate-source"); source.replaceChildren();
    if (!state.selectedRate) { source.textContent = t("请自行选择与模型、上下文长度及服务档位匹配的费率。"); }
    else {
      const rate = state.selectedRate;
      source.append(document.createTextNode(`${label(rate.effective_date, "日期未记录")} · `));
      try { const url = new URL(rate.source_url); if (url.protocol !== "https:") throw new Error("invalid_source"); const link = node("a", "", t("费率来源")); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; source.append(link); } catch { source.append(document.createTextNode(t("来源未记录"))); }
      source.append(document.createTextNode(t("。仅应用所选费率，不自动判断上下文长度或服务档位。")));
    }
    invalidateQuote();
  }

  function fieldError(name, message) { $("advanced-fields").open = true; const element = form.elements.namedItem(name); element.setAttribute("aria-invalid", "true"); element.focus(); text("form-error", message); $("form-error").hidden = false; return null; }

  function quotePayload() {
    const usage = {}; const rate = { provider: state.selectedRate?.provider || state.quoteEvent?.provider || "custom", model: state.selectedRate?.model || state.quoteEvent?.model || "custom" };
    for (const field of usageFields) {
      const raw = form.elements.namedItem(field).value.trim();
      if (raw !== "" && (!/^\d+$/.test(raw) || !Number.isSafeInteger(Number(raw)))) return fieldError(field, "Token 数量请填写非负整数；未知项目保留空白。");
      usage[field] = raw === "" ? null : Number(raw);
    }
    if (usage.input_tokens !== null && (usage.cached_input_tokens ?? 0) + (usage.cache_write_input_tokens ?? 0) > usage.input_tokens) return fieldError("input_tokens", "总输入必须包含缓存读取与缓存写入，不能小于两者之和。");
    if (usage.cache_write_1h_input_tokens !== null && usage.cache_write_input_tokens !== null && usage.cache_write_1h_input_tokens > usage.cache_write_input_tokens) return fieldError("cache_write_1h_input_tokens", "1 小时缓存写入不能超过缓存写入总量。");
    if (usage.reasoning_output_tokens !== null && usage.output_tokens !== null && usage.reasoning_output_tokens > usage.output_tokens) return fieldError("reasoning_output_tokens", "推理输出不能超过总输出。");
    for (const field of rateFields) {
      const raw = form.elements.namedItem(`rate_${field}`).value.trim();
      if (raw !== "" && !/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(raw)) return fieldError(`rate_${field}`, "单价请填写非负十进制数，单位为美元 / 百万 Token；未知单价保留空白。");
      rate[field] = raw === "" ? null : raw;
    }
    return { usage, rate };
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault(); if ($("calculate").disabled) return;
    const payload = quotePayload(); if (!payload) return;
    const version = state.quoteVersion; $("calculate").disabled = true;
    text("quote-label", "正在计算"); text("quote-amount", "—"); text("quote-detail", "正在按各项用量与单价计算。");
    try {
      const result = await request("/api/quote", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (version !== state.quoteVersion) return;
      const validAmount = (value) => typeof value === "string" && /^\d+(?:\.\d+)?$/.test(value);
      if (result.currency !== "USD" || !validAmount(result.known_subtotal_usd) || (result.amount_usd !== null && !validAmount(result.amount_usd)) || !Array.isArray(result.missing)) throw new Error("invalid_quote");
      if (result.amount_usd === null) {
        text("quote-label", "费用尚不完整"); text("quote-amount", "—");
        const missing = [...new Set(result.missing.map((key) => t(missingLabels[key] || "其他用量或单价")))];
        text("quote-detail-title", t("还缺少 {count} 项", { count: missing.length }));
        text("quote-detail", t("已知部分：${subtotal} USD。仍缺少：{missing}。", { subtotal: result.known_subtotal_usd, missing: missing.join(locale === "en" ? ", " : "、") || t("完整用量或单价") }));
      } else {
        text("quote-label", "估算费用 · USD"); text("quote-amount", `$${result.amount_usd}`); text("quote-detail-title", "计算依据");
        text("quote-detail", state.quoteEvent?.billing_mode === "subscription_equivalent" ? "本条为订阅用量，结果为单价换算的费用等值。" : "按填写的用量和单价计算。");
      }
    } catch { if (version === state.quoteVersion) { text("quote-label", "暂时无法计算"); text("quote-amount", "—"); text("quote-detail", "请确认本地服务连接与输入内容后重试。"); } }
    finally { $("calculate").disabled = false; }
  });

  form.addEventListener("input", (event) => {
    if (event.target === $("rate-preset")) return;
    if (rateFields.some((field) => event.target.name === `rate_${field}`) && state.selectedRate) { state.selectedRate = null; $("rate-preset").value = ""; text("rate-source", "已修改为自定义单价。请确认它们适用于本次用量。"); }
    if (usageFields.includes(event.target.name)) quoteContext(true);
    invalidateQuote();
  });
  $("rate-preset").addEventListener("change", selectRate);
  $("event-sort").addEventListener("change", () => { state.sort = $("event-sort").value; state.visibleEvents = 10; renderOverview(); });
  $("project-filter").addEventListener("change", () => { state.project = $("project-filter").value; state.visibleEvents = 10; state.visibleGroups = 10; renderOverview(); });
  $("operation-group").addEventListener("change", () => { state.group = $("operation-group").value; state.visibleGroups = 10; renderOverview(); });
  $("more-operations").addEventListener("click", () => { state.visibleGroups += 10; renderOverview(); });
  $("codex-sources").addEventListener("toggle", () => { if ($("codex-sources").open && !state.sourcesLoaded) readSources(); });
  $("reload-sources").addEventListener("click", readSources);
  $("source-form").addEventListener("submit", applySources);
  for (const button of document.querySelectorAll("[data-source]")) button.addEventListener("click", () => { state.source = button.dataset.source; state.visibleEvents = 10; for (const filter of document.querySelectorAll("[data-source]")) filter.setAttribute("aria-pressed", String(filter === button)); renderOverview(); });
  $("open-calculator").addEventListener("click", () => fillEvent());
  $("close-calculator").addEventListener("click", () => $("calculator").close());
  $("refresh").addEventListener("click", refresh);
  $("more-events").addEventListener("click", () => { state.visibleEvents += 10; renderOverview(); });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refresh(); });
  const timer = setInterval(() => { if (document.visibilityState === "visible") refresh(); }, 30000);
  window.addEventListener("pagehide", () => { clearInterval(timer); for (const controller of controllers) controller.abort(); }, { once: true });
  refresh();
})();

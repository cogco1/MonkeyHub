"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("quote-form");
  const usageFields = ["input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens", "cache_write_1h_input_tokens", "reasoning_output_tokens"];
  const rateFields = ["input", "cached_input", "cache_write_input", "cache_write_1h_input", "output"];
  const state = { events: [], source: "all", sort: "time", loaded: false, loading: false, rates: [], ratesLoaded: false, selectedRate: null, quoteEvent: null, warnings: 0, excluded: 0, visibleEvents: 100, quoteVersion: 0 };
  const controllers = new Set();
  const integerFormat = new Intl.NumberFormat("zh-CN");
  const timeFormat = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  const sourceLabels = { studio: "Studio", codex: "Codex" };
  const phaseLabels = { intent: "意图解析", planning: "计划生成", execute: "执行", execution: "执行", propose: "方案生成", proposal: "方案生成", repair: "修复", validation: "验证", response: "响应", turn: "对话", agent: "代理", agent_loop: "代理循环" };
  const statusLabels = { completed: "已完成", succeeded: "成功", success: "成功", failed: "失败", error: "失败", cancelled: "已取消", running: "进行中", recorded: "已记录", observed: "已观测", compiled: "已编译", question: "待补充", unsupported: "不支持", last_only: "仅末次用量", token_count: "已记录", partial_history: "历史不完整", counter_discontinuity: "计数不连续", counter_reset_unknown: "计数重置未知" };
  const partialStatuses = new Set(["partial_history", "counter_discontinuity", "counter_reset_unknown", "last_only"]);
  const billingLabels = { api_estimate: "API 用量", subscription_equivalent: "订阅用量 · 仅等值估算", unknown: "计费方式未记录" };
  const missingLabels = { "tokens.input": "普通输入用量", "tokens.cached_input": "缓存读取用量", "tokens.cache_write_input": "普通缓存写入用量", "tokens.cache_write_1h_input": "1 小时缓存写入用量", "tokens.output": "输出用量", "rate.input": "普通输入单价", "rate.cached_input": "缓存读取单价", "rate.cache_write_input": "普通缓存写入单价", "rate.cache_write_1h_input": "1 小时缓存写入单价", "rate.output": "输出单价", rate_card: "费率" };

  function text(id, value) { $(id).textContent = value; }
  function node(tag, className, value) { const element = document.createElement(tag); if (className) element.className = className; if (value !== undefined) element.textContent = value; return element; }
  function known(value) { return Number.isSafeInteger(value) && value >= 0; }
  function count(value) { return known(value) ? integerFormat.format(value) : "—"; }
  function label(value, fallback = "未记录") { return typeof value === "string" && value.trim() && value !== "unknown" ? value : fallback; }
  function phase(value) { return phaseLabels[value] || label(value, "阶段未记录"); }
  function duration(value) { if (!known(value)) return "—"; return value < 1000 ? `${integerFormat.format(value)} ms` : `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(value / 1000)} s`; }
  function aggregate(events, field) { let total = 0n; let recorded = 0; for (const event of events) { const value = event.tokens?.[field]; if (known(value)) { total += BigInt(value); recorded += 1; } } return { total, recorded, missing: events.length - recorded }; }
  function aggregateText(value) { return value.recorded ? integerFormat.format(value.total) : "—"; }
  function tokenStat(id, summary) {
    let display = aggregateText(summary);
    if (summary.recorded && summary.total >= 10000n) {
      const [unit, suffix] = summary.total >= 1000000000000n ? [1000000000000n, "万亿"] : summary.total >= 100000000n ? [100000000n, "亿"] : [10000n, "万"];
      const hundredths = (summary.total * 100n + unit / 2n) / unit;
      display = `${hundredths / 100n}.${String(hundredths % 100n).padStart(2, "0")}${suffix}`;
    }
    text(id, display);
    const exact = summary.recorded ? `${integerFormat.format(summary.total)} Token` : "未记录";
    $(id).title = exact; $(id).setAttribute("aria-label", exact);
  }
  function completeness(value, size) { return size ? `${value.recorded} 次已记录 · ${value.missing} 次缺失` : "尚无记录"; }
  function setEmpty(id, isEmpty) { $(id).hidden = !isEmpty; }

  async function request(path, options = {}) {
    const controller = new AbortController();
    controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(path, { ...options, signal: controller.signal, cache: "no-store" });
      if (!response.ok) throw new Error("request_failed");
      return await response.json();
    } finally { clearTimeout(timeout); controllers.delete(controller); }
  }

  function renderOverview() {
    const events = state.events.filter((event) => state.source === "all" || event.source === state.source);
    const input = aggregate(events, "input_tokens");
    const output = aggregate(events, "output_tokens");
    const durations = events.map((event) => event.duration_ms).filter(known).sort((a, b) => a - b);
    const middle = Math.floor(durations.length / 2);
    const median = durations.length ? (durations.length % 2 ? durations[middle] : Math.round((durations[middle - 1] + durations[middle]) / 2)) : null;
    text("stat-events", state.loaded ? integerFormat.format(events.length) : "—");
    text("stat-events-note", state.loaded ? (state.source === "all" ? "Studio + Codex" : sourceLabels[state.source]) : "等待数据连接");
    tokenStat("stat-input", input); text("stat-input-note", completeness(input, events.length));
    tokenStat("stat-output", output); text("stat-output-note", completeness(output, events.length));
    text("stat-duration", duration(median)); text("stat-duration-note", events.length ? `${durations.length} 次已记录 · ${events.length - durations.length} 次缺失` : "尚无记录");
    const paired = events.filter((event) => known(event.tokens?.input_tokens) && known(event.tokens?.cached_input_tokens));
    const pairedInput = aggregate(paired, "input_tokens");
    const pairedCache = aggregate(paired, "cached_input_tokens");
    const ratioTenths = pairedInput.total > 0n ? (pairedCache.total * 1000n + pairedInput.total / 2n) / pairedInput.total : null;
    const ratio = ratioTenths === null ? "—" : `${ratioTenths / 10n}.${ratioTenths % 10n}%`;
    text("cache-summary", `配对记录的缓存读取 ${aggregateText(pairedCache)} · 输入占比 ${ratio} · ${events.length ? `${paired.length} 次同时记录，${events.length - paired.length} 次缺失` : "尚无记录"}`);
    $("cache-summary").title = "缓存读取总量与占比仅使用输入和缓存读取均有记录的同一批调用；输入为 0 时占比不可计算。";
    const partial = events.filter((event) => partialStatuses.has(event.status)).length;
    const notices = [];
    if (state.warnings || state.excluded) notices.push(`数据读取有 ${state.warnings + state.excluded} 条提示，部分记录可能不可用。`);
    if (partial) notices.push(`${partial} 条记录存在历史缺失或计数中断，汇总不能用于完整账单核对。`);
    text("data-notice", notices.join(" ")); $("data-notice").hidden = notices.length === 0;
    renderBreakdown(events);
    renderEvents(events);
  }

  function tokenCell(summary) {
    const cell = node("td", "number", aggregateText(summary));
    if (summary.missing) cell.append(node("small", "secondary-text", `${summary.missing} 次缺失`));
    return cell;
  }

  function renderBreakdown(events) {
    const groups = new Map();
    for (const event of events) {
      const key = JSON.stringify([event.provider, event.model, event.phase]);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(event);
    }
    const fragment = document.createDocumentFragment();
    const ordered = [...groups.values()].map((group) => ({ group, input: aggregate(group, "input_tokens") }));
    ordered.sort((a, b) => {
      if (!a.input.recorded || !b.input.recorded) return Number(Boolean(b.input.recorded)) - Number(Boolean(a.input.recorded));
      return a.input.total === b.input.total ? 0 : a.input.total > b.input.total ? -1 : 1;
    });
    for (const { group, input } of ordered) {
      const event = group[0];
      const row = node("tr");
      const model = node("td");
      model.append(node("span", "primary-text", label(event.model, "模型未记录")), node("span", "secondary-text", `${label(event.provider, "提供方未记录")} · ${phase(event.phase)}`));
      row.append(model, node("td", "number", integerFormat.format(group.length)), tokenCell(input), tokenCell(aggregate(group, "output_tokens")));
      fragment.append(row);
    }
    $("breakdown-body").replaceChildren(fragment);
    setEmpty("breakdown-empty", !events.length); $("breakdown-wrap").hidden = !events.length;
    text("group-count", groups.size ? `${groups.size} 个分组 · 输入降序` : "");
  }

  function renderEvents(events) {
    const fragment = document.createDocumentFragment();
    const metric = (event) => {
      if (state.sort === "time") { const value = Date.parse(event.started_at); return Number.isFinite(value) ? value : null; }
      const value = state.sort === "duration_ms" ? event.duration_ms : event.tokens?.[state.sort];
      return known(value) ? value : null;
    };
    const ordered = [...events].sort((a, b) => {
      const left = metric(a); const right = metric(b);
      if (left === null || right === null) return Number(right !== null) - Number(left !== null);
      return right - left;
    });
    const shown = ordered.slice(0, state.visibleEvents);
    const maxDuration = shown.reduce((max, event) => known(event.duration_ms) ? Math.max(max, event.duration_ms) : max, 0);
    for (const event of shown) {
      const row = node("tr");
      const timestamp = new Date(event.started_at);
      const timeCell = node("td");
      const time = node("time", "primary-text", Number.isNaN(timestamp.getTime()) ? "时间未记录" : timeFormat.format(timestamp));
      if (!Number.isNaN(timestamp.getTime())) { time.dateTime = timestamp.toISOString(); time.title = timestamp.toLocaleString("zh-CN"); }
      timeCell.append(time, node("span", "secondary-text", sourceLabels[event.source] || "来源未记录"));
      const modelCell = node("td");
      modelCell.append(node("span", "primary-text", label(event.model, "模型未记录")), node("span", "secondary-text", phase(event.phase)));
      const statusCell = node("td");
      const status = node("span", `status-pill${partialStatuses.has(event.status) ? " attention" : ["failed", "error", "cancelled"].includes(event.status) ? " muted" : ""}`, statusLabels[event.status] || "已记录");
      status.title = billingLabels[event.billing_mode] || billingLabels.unknown;
      statusCell.append(status, node("span", "secondary-text", event.billing_mode === "subscription_equivalent" ? "订阅等值" : event.billing_mode === "api_estimate" ? "API 估算" : "计费未记录"));
      const durationCell = node("td", "number duration-cell", duration(event.duration_ms));
      if (known(event.duration_ms) && maxDuration > 0) { const track = node("div", "duration-track"); track.setAttribute("aria-hidden", "true"); const bar = node("span"); bar.style.width = `${Math.min(100, event.duration_ms / maxDuration * 100)}%`; track.append(bar); durationCell.append(track); }
      const tokensCell = node("td", "number");
      tokensCell.append(node("span", "primary-text", count(event.tokens?.input_tokens)), node("span", "secondary-text", count(event.tokens?.output_tokens)));
      const quoteCell = node("td");
      const quoteButton = node("button", "button secondary event-quote", "计价");
      quoteButton.type = "button"; quoteButton.setAttribute("aria-label", `计价：${label(event.model, "模型未记录")} · ${phase(event.phase)}`);
      quoteButton.addEventListener("click", () => fillEvent(event)); quoteCell.append(quoteButton);
      row.append(timeCell, modelCell, statusCell, durationCell, tokensCell, quoteCell); fragment.append(row);
    }
    $("events-body").replaceChildren(fragment);
    setEmpty("events-empty", !events.length); $("events-wrap").hidden = !events.length;
    text("event-count", events.length ? `显示 ${shown.length} / ${events.length} 条` : "尚无记录");
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
      state.warnings = Array.isArray(eventsResult.value.warnings) ? eventsResult.value.warnings.length : 0;
      state.excluded = excluded; state.loaded = true; eventsValid = true;
      text("sync-time", `更新于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`);
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
    text("quote-label", "费用估算"); text("quote-amount", "—"); text("quote-detail", "输入已更改，请重新计算。未知项目不会自动计为 0。");
    $("form-error").hidden = true;
    for (const element of form.querySelectorAll("[aria-invalid]")) element.removeAttribute("aria-invalid");
  }

  function quoteContext(edited = false) {
    const event = state.quoteEvent;
    if (!event) return;
    text("quote-context", `${sourceLabels[event.source]} · ${label(event.model, "模型未记录")} · ${phase(event.phase)}。${billingLabels[event.billing_mode] || billingLabels.unknown}。${edited ? "带入的用量已手动修改。" : "已带入这条记录的用量，请重新选择或填写单价。"}`);
    $("quote-context").hidden = false;
  }

  function fillEvent(event) {
    state.quoteEvent = event;
    $("rate-preset").value = ""; selectRate();
    for (const field of usageFields) form.elements.namedItem(field).value = known(event.tokens?.[field]) ? String(event.tokens[field]) : "";
    quoteContext();
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    $("calculator").scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
    form.elements.namedItem("input_tokens").focus({ preventScroll: true });
  }

  function selectRate() {
    const value = $("rate-preset").value;
    state.selectedRate = value === "" ? null : state.rates[Number(value)];
    for (const field of rateFields) form.elements.namedItem(`rate_${field}`).value = state.selectedRate?.[field] ?? "";
    const source = $("rate-source"); source.replaceChildren();
    if (!state.selectedRate) { source.textContent = "请自行选择与模型、上下文长度及服务档位匹配的费率。"; }
    else {
      const rate = state.selectedRate;
      source.append(document.createTextNode(`${label(rate.effective_date, "日期未记录")} · `));
      try { const url = new URL(rate.source_url); if (url.protocol !== "https:") throw new Error("invalid_source"); const link = node("a", "", "费率来源"); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; source.append(link); } catch { source.append(document.createTextNode("来源未记录")); }
      source.append(document.createTextNode("。仅应用所选费率，不自动判断上下文长度或服务档位。"));
    }
    invalidateQuote();
  }

  function fieldError(name, message) { const element = form.elements.namedItem(name); element.setAttribute("aria-invalid", "true"); element.focus(); text("form-error", message); $("form-error").hidden = false; return null; }

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
        const missing = [...new Set(result.missing.map((key) => missingLabels[key] || "其他用量或单价"))];
        text("quote-detail", `已知部分：$${result.known_subtotal_usd} USD。仍缺少：${missing.join("、") || "完整用量或单价"}。`);
      } else {
        text("quote-label", "估算费用 · USD"); text("quote-amount", `$${result.amount_usd}`);
        text("quote-detail", "按填写的单价计算；缓存与推理子集不重复计费。订阅用量的费用等值不代表实际扣费。");
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
  $("event-sort").addEventListener("change", () => { state.sort = $("event-sort").value; state.visibleEvents = 100; renderOverview(); });
  for (const button of document.querySelectorAll("[data-source]")) button.addEventListener("click", () => { state.source = button.dataset.source; state.visibleEvents = 100; for (const filter of document.querySelectorAll("[data-source]")) filter.setAttribute("aria-pressed", String(filter === button)); renderOverview(); });
  $("refresh").addEventListener("click", refresh);
  $("more-events").addEventListener("click", () => { state.visibleEvents += 100; renderOverview(); });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refresh(); });
  const timer = setInterval(() => { if (document.visibilityState === "visible") refresh(); }, 30000);
  window.addEventListener("pagehide", () => { clearInterval(timer); for (const controller of controllers) controller.abort(); }, { once: true });
  refresh();
})();

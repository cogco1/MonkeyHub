"""Read-only turn views over UsageLog; no second trace store or inferred billing."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import re

from .pricing import RateCard, match_rate, quote
from .usage import TokenUsage


LANES = [{"id": key, "label": label} for key, label in (
    ("agent", "Agent"), ("hub", "Hub"), ("studio", "Studio"), ("cad", "CAD"), ("client", "Client"))]
_LABELS = {
    "hub_turn": "用户请求", "context_build": "构建上下文", "provider_round": "Agent 活动区间",
    "model_request": "模型请求", "tool_call": "工具调用", "first_visible": "首次可见",
    "first_response": "首段回复到达",
    "final_response": "最终回复", "verified": "结果验证", "validation": "候选验证",
    "candidate_readback": "候选读回", "step_readback": "STEP 读回", "preview_readback": "预览读回",
    "model_install": "模型安装", "model_projection": "视图投影", "model_load": "模型加载",
    "model_download": "模型下载", "geometry_build": "几何构建", "agent": "原生模型用量",
    "agent_turn": "原生 Agent 回合",
    "permission_wait": "等待权限决定",
}


def _time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Legacy timestamps without an offset cannot establish a clock binding.
        return parsed.timestamp() * 1000 if parsed.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _interval(row, now):
    start, end = _time(row["started_at"]), _time(row.get("ended_at"))
    if start is None:
        return None
    if row.get("duration_ms") is not None:
        end = start + row["duration_ms"]
    if end is None and row["status"] == "running":
        end = max(start, now)
    return (start, max(start, end)) if end is not None else (start, start)


def _wall_interval(row, now):
    start, end = _time(row["started_at"]), _time(row.get("ended_at"))
    if start is not None and end is not None:
        return start, end
    return _interval(row, now)


def _code(value):
    """Diagnostics can include retained filesystem refs; exports cannot."""
    if value is None:
        return None
    if (isinstance(value, str) and len(value) <= 480 and re.fullmatch(r"[A-Za-z0-9_:.@+ /{},?=-]+", value)
            and not re.search(r"(?:^[ /]|[A-Za-z]:[/\\]|file:|https?://)", value)):
        return value
    return "redacted"


def _safe_details(details):
    safe = {}
    codes = {"tool_name", "request_kind", "billing_plan", "provider_timing_basis", "native_turn_id",
             "cache_status", "cache_reason", "execution_path", "retry_reason", "wait_reason", "task_type",
             "validator_scope", "duplicate_status", "duplicate_reason", "reuse_opportunity"}
    counts = {"first_token_ms", "model_inference_ms", "input_bytes", "output_bytes", "retry_attempt", "http_status"}
    for key in codes & details.keys():
        safe[key] = _code(details[key])
    for key in (counts | {"blocking", "success", "validator_pass", "escalation", "input_equivalent",
                          "missing_observations"}) & details.keys():
        safe[key] = details[key]
    if details.get("context_budget"):
        safe["context_budget"] = details["context_budget"]
    identity = details.get("input_identity", {})
    hashes = {key: value for key, value in identity.items()
              if key.endswith(("_digest", "_sha256", "_fingerprint")) and isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value)}
    if hashes:
        safe["input_identity"] = hashes
    return safe


def _lane(row):
    phase = row["phase"]
    if phase in {"provider_round", "model_request", "agent", "agent_turn"} or row.get("model_call") is True:
        return "agent"
    if phase in {"model_load", "model_download", "model_install", "model_projection", "document_load", "first_visible"} or phase.startswith("preview_install"):
        return "client"
    if phase in {"step_readback", "preview_readback", "tessellation", "element_production"} or phase.startswith(("geometry", "cad", "occt")):
        return "cad"
    return "hub" if row["source"] == "hub" else "studio"


def _groups(rows, now, warnings):
    roots = [row for row in rows if row["phase"] == "hub_turn"]
    by_id = {row["event_id"]: row for row in rows}
    membership = {root["event_id"]: root["event_id"] for root in roots}
    for row in rows:
        if row["event_id"] in membership:
            continue
        matches = [root for root in roots if row.get("turn_id") and row["turn_id"] == root.get("turn_id")
                   and row.get("project_id") == root.get("project_id")]
        if not matches and row["source"] == "codex" and row.get("session_id"):
            candidates = [root for root in roots if root.get("session_id") == row["session_id"]
                          and (not row.get("project_id") or root.get("project_id") == row.get("project_id"))]
            native = [root for root in candidates if row.get("turn_id") and
                      root["details"].get("native_turn_id") == row["turn_id"]]
            interval = _wall_interval(row, now)
            matches = native or [root for root in candidates if interval and (bound := _wall_interval(root, now))
                                 and bound[0] <= interval[0] <= interval[1] <= bound[1]]
            if len(matches) > 1:
                warnings.append("原生用量与多个 Hub 回合时间窗重叠；该记录保留在原生回合，未猜测归属。")
        if len(matches) == 1:
            membership[row["event_id"]] = matches[0]["event_id"]

    def resolve(row, visited):
        key = row["event_id"]
        if key in membership:
            return membership[key]
        if key in visited:
            return None
        for ref in (row.get("parent_event_id"), row.get("related_event_id")):
            if ref not in by_id:
                continue
            project = row.get("project_id")
            linked_project = by_id[ref].get("project_id")
            if project and linked_project and project != linked_project:
                warnings.append("跨项目的父阶段或关联记录未合并；该阶段保留在其自身项目。")
                continue
            group = resolve(by_id[ref], visited | {key})
            if group:
                root_project = by_id[group].get("project_id")
                if project and root_project and project != root_project:
                    warnings.append("跨项目的父阶段或关联记录未合并；该阶段保留在其自身项目。")
                    continue
                membership[key] = group
                return group
        return None

    result = defaultdict(list)
    for row in rows:
        group = resolve(row, set())
        if group is None:
            identity = row.get("turn_id") or row.get("operation_id") or row["event_id"]
            group = f"{row['source']}:{row.get('project_id') or 'unknown'}:{row.get('session_id') or 'unknown'}:{identity}"
        result[group].append(row)
    return result


def _usage(rows):
    # Codex JSONL is the sole token authority for Hub Codex rounds. The Hub
    # records agent activity intervals, which are not additional model calls.
    native = [row for row in rows if row["source"] == "codex" and row.get("model_call") is True]
    sessions = {row.get("session_id") for row in native if row.get("session_id")}
    measured, excluded = [], []
    for row in rows:
        if row.get("model_call") is not True:
            continue
        if row["source"] == "hub" and row.get("session_id") in sessions:
            excluded.append(row["event_id"])
        else:
            measured.append(row)
    fields = TokenUsage().to_dict()
    totals, subtotal = {}, {}
    for name in fields:
        known = [row["tokens"].get(name) for row in measured if row["tokens"].get(name) is not None]
        subtotal[name] = sum(known)
        totals[name] = sum(known) if measured and len(known) == len(measured) else None
    return measured, {"tokens": totals, "known_subtotal_tokens": subtotal, "events_count": len(measured),
                      "missing_events_count": sum(any(value is None for value in row["tokens"].values()) for row in measured),
                      "excluded_duplicate_event_ids": [_code(value) for value in excluded]}


def _model_rounds(rows, measured):
    """Usage updates are not request boundaries; every usage source must bind."""
    by_id = {row["event_id"]: row for row in rows}
    requests = {row["event_id"] for row in rows if row["phase"] == "model_request" and row.get("model_call") is True}
    if not requests:
        return None
    for row in measured:
        pending, seen, bound = [row["event_id"]], set(), False
        while pending:
            key = pending.pop()
            if key in requests:
                bound = True
                break
            if key in seen or key not in by_id:
                continue
            seen.add(key)
            item = by_id[key]
            pending.extend(ref for ref in (item.get("parent_event_id"), item.get("related_event_id")) if ref)
        if not bound:
            # An observed Studio subrequest cannot establish a complete count
            # for an external Agent whose metadata lacks request identities.
            return None
    return len(requests)


def _event_price(row, rates):
    if row.get("rate_match_status") is not None:
        rate = RateCard.from_dict(row["rate_snapshot"]) if row.get("rate_snapshot") else None
        status, basis = row["rate_match_status"], "recorded_snapshot"
    else:
        rate, status = match_rate(row["provider"], row["model"], row["details"].get("billing_plan"), row["started_at"], rates)
        basis = "catalog_at_read_time"
    result = quote(TokenUsage.from_dict(row["tokens"]), rate)
    # Caller rates remain explicitly sourced. Provider metadata cannot turn an
    # equivalent API price into subscription debits or an invoiced API charge.
    result.update(rate=rate.to_dict() if rate else None, status=status, basis=basis,
                  billing_mode=row["billing_mode"], label="订阅 API 等价值（非实际扣款）" if row["billing_mode"] == "subscription_equivalent" else "API 费用估算（非账单）")
    return result


def _prices(rows, rates):
    items = [(row, _event_price(row, rates)) for row in rows]
    modes = sorted({row["billing_mode"] for row in rows})
    subtotal = sum((Decimal(item["known_subtotal_usd"]) for _, item in items), Decimal(0))
    missing = sorted({part for _, item in items for part in item["missing"]})
    if not items:
        missing.append("provider_usage")
    return {"currency": "USD", "amount_usd": None if missing else format(subtotal, "f"),
            "known_subtotal_usd": format(subtotal, "f"), "missing": missing, "billing_modes": modes,
            "label": "订阅 API 等价值与 API 估算（均非实际扣款）" if "subscription_equivalent" in modes else "API 费用估算（非账单）",
            "rate_snapshots": [{"event_id": _code(row["event_id"]), "rate": price["rate"], "status": price["status"], "basis": price["basis"]} for row, price in items]}


def _diagnostics(rows):
    result = []
    def add(code, label, selected, note, count=None):
        if selected:
            result.append({"code": code, "label": label, "count": len(selected) if count is None else count,
                           "event_ids": [_code(row["event_id"]) for row in selected], "note": note})
    for kind, label in (("schema_read", "重复 schema 查询"), ("state_read", "重复状态查询")):
        identities = defaultdict(list)
        for row in rows:
            digest = row["details"].get("input_identity", {}).get("context_digest")
            if row["phase"] == "tool_call" and row["details"].get("request_kind") == kind and digest:
                identities[digest].append(row)
        selected = [row for values in identities.values() if len(values) > 1 for row in values]
        if selected:
            add(kind, label, selected, "工具名和请求参数散列相同；状态是否改变、查询是否必要仍需核对。",
                sum(len(values) - 1 for values in identities.values()))
    tools = defaultdict(list)
    for row in rows:
        if row["phase"] == "tool_call" and row["details"].get("tool_name"):
            tools[row["details"]["tool_name"]].append(row)
    repeated = [row for values in tools.values() if len(values) > 1 for row in values]
    add("repeated_tools", "重复工具活动", repeated, "同名工具调用可能作用于不同输入，不能据此断言可以省略。")
    add("retries", "已记录重试", [row for row in rows if (row["details"].get("retry_attempt") or 0) > 0],
        "仅统计生产者明确标记的重试；失败后恢复不等于无效工作。")
    rounds = [row for row in rows if row["phase"] == "provider_round"]
    if len(rounds) > 1:
        add("agent_resumes", "Agent 续行", rounds[1:], "宿主记录的 Agent 活动区间恢复，可能包含等待；不能据此证明模型被重新唤醒。")
    contexts = [row for row in rows if row["details"].get("context_budget", {}).get("exceeded")]
    add("context_budget", "上下文估算超预算", contexts, "文字估算仅用于上下文诊断，真实 token 仍以 provider 元数据为准。")
    repeated_input = [row for row in rows if row["details"].get("duplicate_status") in {"same_input_request", "repeated_execution", "same_asset_request"}]
    add("repeated_inputs", "相同已记录输入再次执行", repeated_input, "需要结合结果绑定、版本和缓存条件判断是否能复用。")
    return result


def _timing(spans, root, elapsed):
    if root is None or elapsed is None:
        return [], {"basis": "unavailable", "segments": [], "duration_ms": None, "unattributed_ms": None,
                    "note": "缺少完整请求根区间，不能推定关键路径。"}, None, None, None
    by_id = {span["event_id"]: span for span in spans}
    def ancestors(span):
        seen = set()
        while span.get("parent_event_id") in by_id and span["parent_event_id"] not in seen:
            seen.add(span["parent_event_id"])
            span = by_id[span["parent_event_id"]]
        return seen
    intervals = []
    for span in spans:
        if span["event_id"] == root or span["offset_ms"] is None or span["duration_ms"] is None or span["phase"] == "agent_turn":
            continue
        start, end = max(0, span["offset_ms"]), min(elapsed, span["offset_ms"] + span["duration_ms"])
        if end > start:
            intervals.append((span, start, end))
    boundaries = sorted({0, elapsed, *(value for _, start, end in intervals for value in (start, end))})
    assigned = defaultdict(int)
    segments, blocking_ms, background_ms, unknown = [], 0, 0, 0
    ambiguous = 0
    for start, end in zip(boundaries, boundaries[1:]):
        active = [span for span, lo, hi in intervals if lo <= start and end <= hi]
        blocking = [span for span in active if span["blocking"] is True]
        if any(span["blocking"] is False for span in active):
            background_ms += end - start
        if blocking:
            blocking_ms += end - start
            # Descendants are more specific observations of the same interval.
            parents = set().union(*(ancestors(span) for span in blocking))
            choices = [span for span in blocking if span["event_id"] not in parents]
            if len(choices) == 1:
                chosen = choices[0]
                assigned[chosen["lane"]] += end - start
                if segments and segments[-1]["event_id"] == chosen["event_id"] and segments[-1]["offset_ms"] + segments[-1]["duration_ms"] == start:
                    segments[-1]["duration_ms"] += end - start
                else:
                    segments.append({"event_id": chosen["event_id"], "offset_ms": start, "duration_ms": end - start})
                continue
            ambiguous += end - start
        unknown += end - start
    note = "按明确阻塞事件的依赖层级分配非重叠区间；未观测部分保留未知，不能视为完整执行 DAG。"
    if ambiguous:
        note += "并行阻塞分支没有等待先后证据，其重叠区间未指定关键分支。"
    return ([{"lane": lane["id"], "duration_ms": assigned[lane["id"]]} for lane in LANES],
            {"basis": "observed_blocking_intervals" if segments else "unavailable", "segments": segments,
             "duration_ms": elapsed - unknown, "unattributed_ms": unknown, "note": note}, blocking_ms, background_ms, unknown)


def build_traces(rows: list[dict], *, rates: tuple[RateCard, ...] = (), now: datetime | None = None) -> dict:
    """Aggregate current event revisions; all totals are views, never persisted."""
    clock = (now or datetime.now(timezone.utc)).timestamp() * 1000
    rows = list({row["event_id"]: row for row in rows}.values())
    warnings, traces = [], []
    for identity, group in _groups(rows, clock, warnings).items():
        group.sort(key=lambda row: row["started_at"])
        root = next((row for row in group if row["phase"] == "hub_turn"), None)
        if root is None:
            root = next((row for row in group if row["timing_scope"] in {"agent_turn", "interaction"}), None)
        reference = root or group[0]
        root_interval = _interval(root, clock) if root else None
        origin = root_interval[0] if root_interval else min((_time(row["started_at"]) for row in group if _time(row["started_at"]) is not None), default=None)
        elapsed = round(root_interval[1] - root_interval[0]) if root_interval else None
        if root and root["status"] != "running" and root.get("duration_ms") is None and root.get("ended_at") is None:
            elapsed = None
        ids = {row["event_id"] for row in group}
        spans = []
        for row in group:
            interval = _interval(row, clock)
            parent = row.get("parent_event_id") or row.get("related_event_id")
            if parent not in ids:
                parent = root["event_id"] if root and root != row else None
            duration = round(interval[1] - interval[0]) if interval and (row.get("ended_at") or row.get("duration_ms") is not None or row["status"] == "running") else None
            details = _safe_details(row["details"])
            label = _LABELS.get(row["phase"], _code(row["phase"]))
            if row["phase"] == "tool_call" and details.get("tool_name"):
                label += " · " + details["tool_name"]
            spans.append({**{name: _code(row.get(name)) for name in ("event_id", "source", "provider", "model", "phase", "status", "operation_id", "run_id", "source_ref")},
                          "parent_event_id": _code(parent), "label": label, "lane": _lane(row),
                          "started_at": row["started_at"], "ended_at": row.get("ended_at"),
                          "offset_ms": round(interval[0] - origin) if interval and origin is not None else None,
                          "duration_ms": duration, "blocking": details.get("blocking"), "model_call": row.get("model_call"),
                          "details": details, "tokens": row["tokens"],
                          "price": _event_price(row, rates) if row.get("model_call") is True else None})
        attribution, critical, blocking, background, unknown = _timing(spans, _code(root["event_id"]) if root else None, elapsed)
        timeline = max([elapsed or 0, *(span["offset_ms"] + (span["duration_ms"] or 0) for span in spans if span["offset_ms"] is not None)])
        measured, usage = _usage(group)
        model_rounds = _model_rounds(group, measured)
        first_response = [span["offset_ms"] for span in spans if span["phase"] == "first_response" and span["offset_ms"] is not None]
        visible = [span["offset_ms"] for span in spans if span["phase"] == "first_visible" and span["offset_ms"] is not None]
        visible.extend(span["offset_ms"] + span["duration_ms"] for span in spans
                       if span["phase"] == "model_projection" and span["source"] == "studio" and span["status"] == "succeeded"
                       and span["offset_ms"] is not None and span["duration_ms"] is not None and span["ended_at"] is not None)
        verified = [span["offset_ms"] + (span["duration_ms"] or 0) for span in spans if span["offset_ms"] is not None and
                    (span["phase"] == "verified" or span["details"].get("validator_pass") is True) and span["status"] != "running"]
        provider_rounds = sum(row["phase"] == "provider_round" for row in group)
        traces.append({"schema": "TurnTrace@1", "trace_id": _code(identity), "turn_id": _code(reference.get("turn_id")), "project_id": _code(reference.get("project_id")),
                       "session_id": _code(reference.get("session_id")), "started_at": reference["started_at"], "ended_at": reference.get("ended_at"),
                       "status": _code(reference["status"]), "summary": {"elapsed_ms": elapsed, "timeline_ms": timeline,
                       "elapsed_basis": "hub_turn" if root and root["phase"] == "hub_turn" else "recorded_root" if root else "unavailable",
                       "first_visible_ms": min(visible, default=None), "first_response_ms": min(first_response, default=None),
                       "verified_ms": min(verified, default=None), "provider_rounds": provider_rounds, "model_rounds": model_rounds,
                       "usage_events": usage["events_count"],
                       "tool_rounds": sum(row["phase"] == "tool_call" for row in group), "agent_resumes": max(0, provider_rounds - 1),
                       "blocking_ms": blocking, "background_ms": background, "unattributed_ms": unknown},
                       "spans": spans, "attribution": attribution, "critical_path": critical, "usage": usage,
                       "price": _prices(measured, rates), "diagnostics": _diagnostics(group),
                       "warnings": ([] if root else ["缺少请求根区间；阶段记录仍可查看，总耗时保持未知。"])
                       + (["该记录的写入方此前在日志繁忙时跳过过诊断观测；受影响的回合、数量与时长都未知，此处不代表本回合缺失。"]
                          if any(row["details"].get("missing_observations") for row in group) else [])
                       + (["已关联的客户端活动超出聊天根区间；时间轴保留完整尾部，根耗时没有叠加这些阶段。"] if elapsed is not None and timeline > elapsed else [])
                       + (["部分墙钟跨度与单调时钟耗时不一致；阶段时长采用生产者测量值，跨进程位置仍按记录时间。"]
                          if any(row.get("duration_ms") is not None and _time(row.get("ended_at")) is not None and _time(row["started_at"]) is not None
                                 and abs((_time(row["ended_at"]) - _time(row["started_at"])) - row["duration_ms"]) > 1000 for row in group) else [])})
    return {"traces": sorted(traces, key=lambda trace: trace["started_at"], reverse=True),
            "warnings": list(dict.fromkeys(warnings)), "lanes": LANES}

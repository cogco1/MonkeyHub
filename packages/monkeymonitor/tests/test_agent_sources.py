"""Agent usage sources: one declared interface, Claude Code and UsageEvent@1 files beside Codex.

Every Claude Code row here is synthetic. The bodies carry a marker that must
never reach a usage event, a report or a warning.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from monkeymonitor.__main__ import main
from monkeymonitor.claude_code import iter_claude_code_events
from monkeymonitor.codex import iter_codex_events
from monkeymonitor.server import MonitorData
from monkeymonitor.sources import AGENT_SOURCES
from monkeymonitor.store import UsageLog
from monkeymonitor.trace import build_traces
from monkeymonitor.usage import TokenUsage, UsageEvent
from monkeymonitor.usage_file import iter_usage_file_events

SESSION = "0f1e2d3c-4b5a-4968-8776-655443322110"
AGENT = "a1b2c3d4e5f60718"
SECRET = "PRIVATE-BODY-MARKER"


def counts(*, ordinary=10, write=200, read=3000, output=50, hour=200, thinking=None, **tier):
    value = {"input_tokens": ordinary, "cache_creation_input_tokens": write, "cache_read_input_tokens": read,
             "output_tokens": output,
             "cache_creation": {"ephemeral_1h_input_tokens": hour, "ephemeral_5m_input_tokens": write - hour},
             "service_tier": "standard", "speed": "standard", "inference_geo": "not_available",
             "server_tool_use": {"web_search_requests": 0}}
    if thinking is not None:
        value["output_tokens_details"] = {"thinking_tokens": thinking}
    value.update(tier)
    return value


def at(seconds):
    return (datetime(2026, 10, 1, 10, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def private(row, agent, session=SESSION):
    """The fields a real row carries beside its metadata, each holding the marker."""
    row.update(cwd=f"/work/{SECRET}", gitBranch=SECRET, slug=SECRET, version="2.1.0",
               isSidechain=agent is not None, sessionId=session)
    if agent is not None:
        row["agentId"] = agent
    return row


def user(uuid, prompt, *, parent=None, second=0, agent=None, session=SESSION):
    return private({"type": "user", "uuid": uuid, "parentUuid": parent, "promptId": prompt, "timestamp": at(second),
                    "message": {"role": "user", "content": f"please {SECRET}"}}, agent, session)


def assistant(uuid, parent, message_id, usage, *, second=1, agent=None, model="claude-test-model", session=SESSION):
    return private({"type": "assistant", "uuid": uuid, "parentUuid": parent, "requestId": f"req_{message_id}",
                    "timestamp": at(second),
                    "message": {"id": message_id, "type": "message", "role": "assistant", "model": model,
                                "content": [{"type": "text", "text": f"answer {SECRET}"},
                                            {"type": "tool_use", "name": "Bash", "input": {"command": SECRET}}],
                                "usage": usage}}, agent, session)


def lines(rows, trailing=""):
    return "".join(json.dumps(row) + "\n" for row in rows) + trailing


class ClaudeHome:
    """A disposable Claude home: projects/<folder>/<session>.jsonl and its subagents/."""

    def __init__(self, case):
        self.temp = TemporaryDirectory()
        case.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "claude-home"
        self.folder = self.root / "projects" / "a-project-folder"
        self.folder.mkdir(parents=True)
        patcher = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root)})
        patcher.start()
        case.addCleanup(patcher.stop)

    def session(self, rows, *, name=SESSION, trailing=""):
        path = self.folder / f"{name}.jsonl"
        path.write_text(lines(rows, trailing), encoding="utf-8")
        return path

    def subagent(self, rows, *, session=SESSION, agent=AGENT, trailing=""):
        directory = self.folder / session / "subagents"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"agent-{agent}.jsonl"
        path.write_text(lines(rows, trailing), encoding="utf-8")
        return path


def read(paths):
    warnings = []
    return list(iter_claude_code_events(paths, warnings=warnings)), warnings


class AgentSourceRegistryTests(unittest.TestCase):
    def test_each_agent_declares_its_reader_conversion_connection_and_authority(self):
        self.assertEqual(list(AGENT_SOURCES), ["codex", "claude-code", "usage-file"])
        for key, source in AGENT_SOURCES.items():
            with self.subTest(agent=key):
                self.assertEqual(source.agent, key)
                self.assertTrue(callable(source.read))
                self.assertTrue(callable(source.tokens))
                self.assertIn(source.token_authority, {"agent", "host"})
                self.assertIn(source.existing_ids, {"merge", "reject"})
        self.assertEqual(AGENT_SOURCES["claude-code"].provider, "claude")
        # Codex prices by the provider each session records; a file by the one each event records.
        self.assertIsNone(AGENT_SOURCES["codex"].provider)
        self.assertIsNone(AGENT_SOURCES["usage-file"].provider)
        self.assertEqual(AGENT_SOURCES["codex"].token_authority, "agent")
        self.assertEqual(AGENT_SOURCES["claude-code"].token_authority, "agent")
        self.assertEqual(AGENT_SOURCES["usage-file"].token_authority, "host")
        self.assertEqual(AGENT_SOURCES["usage-file"].existing_ids, "reject")
        self.assertIs(AGENT_SOURCES["codex"].read, iter_codex_events)

    def test_claude_code_conversion_makes_input_inclusive_and_keeps_unknowns(self):
        convert = AGENT_SOURCES["claude-code"].tokens
        self.assertEqual(convert(counts(thinking=7)), TokenUsage(
            input_tokens=3210, output_tokens=50, cached_input_tokens=3000, cache_write_input_tokens=200,
            cache_write_1h_input_tokens=200, reasoning_output_tokens=7))
        partial = convert({"input_tokens": 10, "cache_creation_input_tokens": 5, "output_tokens": 3})
        self.assertEqual(partial, TokenUsage(input_tokens=None, output_tokens=3, cached_input_tokens=None,
                                             cache_write_input_tokens=5, cache_write_1h_input_tokens=None,
                                             reasoning_output_tokens=None))

    def test_source_takes_studio_hub_or_a_registered_agent(self):
        for source in ("studio", "hub", "codex", "claude-code", "usage-file"):
            UsageEvent("id", source, "none", "none", "tool_call", "completed", at(0), TokenUsage(), model_call=False)
        with self.assertRaises(ValueError):
            UsageEvent("id", "some-other-agent", "none", "none", "tool_call", "completed", at(0), TokenUsage(), model_call=False)
        # A retained Codex row written before this interface still reads.
        legacy = {"event_id": "codex:old:token", "source": "codex", "provider": "openai", "model": "m", "phase": "agent",
                  "status": "observed", "started_at": at(0), "tokens": TokenUsage(1, 1).to_dict()}
        self.assertEqual(UsageEvent.from_dict(legacy).source, "codex")


class ClaudeCodeTests(unittest.TestCase):
    def setUp(self):
        self.home = ClaudeHome(self)

    def test_rows_of_one_message_count_once_with_each_count_at_its_maximum(self):
        path = self.home.session([
            user("u1", "prompt-1"),
            assistant("m1a", "u1", "msg_one", counts(output=5), second=1),
            assistant("m1b", "m1a", "msg_one", counts(output=120, thinking=30), second=4),
            assistant("m1c", "m1b", "msg_one", counts(output=5), second=6),
            user("u2", "prompt-1", parent="m1c", second=7),
            assistant("m2", "u2", "msg_two", counts(ordinary=1, write=0, read=3200, output=9, hour=0), second=9),
        ])
        events, warnings = read([path])
        self.assertEqual(warnings, [])
        self.assertEqual(len(events), 2)
        first, second = sorted(events, key=lambda event: event.started_at)
        self.assertEqual(first.tokens, TokenUsage(input_tokens=3210, output_tokens=120, cached_input_tokens=3000,
                                                  cache_write_input_tokens=200, cache_write_1h_input_tokens=200,
                                                  reasoning_output_tokens=30))
        self.assertEqual((first.started_at, first.ended_at), (at(1), at(6)))
        self.assertEqual(first.event_id, "claude-code:msg_one")
        self.assertEqual((first.source, first.provider, first.model), ("claude-code", "claude", "claude-test-model"))
        self.assertEqual((first.session_id, first.parent_session_id, first.turn_id), (SESSION, None, "prompt-1"))
        self.assertEqual(first.details, {"billing_plan": "api-standard"})
        self.assertEqual(first.billing_mode, "subscription_equivalent")
        self.assertIs(first.model_call, True)
        self.assertEqual(second.tokens.input_tokens, 3201)
        self.assertEqual(second.tokens.cache_write_1h_input_tokens, 0)
        # Reading the same selection again gives the same identities.
        again, _ = read([path, path])
        self.assertEqual(sorted(event.event_id for event in again), ["claude-code:msg_one", "claude-code:msg_two"])

    def test_subagents_of_the_selected_session_attach_to_it_and_nothing_else_is_read(self):
        path = self.home.session([user("u1", "prompt-1"), assistant("m1", "u1", "msg_main", counts())])
        self.home.subagent([user("s1", "prompt-1", agent=AGENT),
                            assistant("s2", "s1", "msg_child", counts(output=7), second=3, agent=AGENT)])
        (self.home.folder / SESSION / "subagents" / f"agent-{AGENT}.meta.json").write_text("{}", encoding="utf-8")
        # Another session's file and its subagent sit beside the selection and are not read.
        other = "99999999-8888-4777-8666-555555555555"
        self.home.session([user("x1", "prompt-x"), assistant("x2", "x1", "msg_other", counts())], name=other)
        self.home.subagent([assistant("y1", None, "msg_other_child", counts(), agent="bbbb")], session=other, agent="bbbb")
        events, warnings = read([path])
        self.assertEqual(warnings, [])
        by_id = {event.event_id: event for event in events}
        self.assertEqual(set(by_id), {"claude-code:msg_main", "claude-code:msg_child"})
        child = by_id["claude-code:msg_child"]
        self.assertEqual((child.session_id, child.parent_session_id), (AGENT, SESSION))
        self.assertEqual(child.source_ref, f"claude-code:{SESSION}")
        self.assertEqual(child.tokens.output_tokens, 7)

    def test_a_trailing_partial_line_and_a_malformed_line_are_skipped_with_warnings(self):
        path = self.home.session([user("u1", "prompt-1"), assistant("m1", "u1", "msg_kept", counts())],
                                 trailing='{"type": "assistant", "message": {"id": "msg_cut"')
        events, warnings = read([path])
        self.assertEqual([event.event_id for event in events], ["claude-code:msg_kept"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("半行", warnings[0])
        broken = self.home.session([user("u1", "prompt-1"), assistant("m1", "u1", "msg_before", counts())])
        with broken.open("a", encoding="utf-8") as stream:
            stream.write("{not json}\n" + json.dumps(assistant("m2", "m1", "msg_after", counts(), second=3)) + "\n")
        events, warnings = read([broken])
        self.assertEqual(sorted(event.event_id for event in events), ["claude-code:msg_after", "claude-code:msg_before"])
        self.assertTrue(any("无法解析" in warning for warning in warnings))

    def test_missing_fields_stay_unknown_and_rows_without_identity_are_skipped(self):
        bare = {"input_tokens": 4, "cache_creation_input_tokens": 6, "output_tokens": 2}
        no_id = assistant("m2", "m1", "msg_x", counts(), second=2)
        del no_id["message"]["id"]
        path = self.home.session([user("u1", "prompt-1"), assistant("m1", "u1", "msg_bare", bare), no_id])
        events, warnings = read([path])
        (event,) = events
        self.assertEqual(event.tokens, TokenUsage(input_tokens=None, output_tokens=2, cached_input_tokens=None,
                                                  cache_write_input_tokens=6, cache_write_1h_input_tokens=None,
                                                  reasoning_output_tokens=None))
        self.assertTrue(any("message.id" in warning for warning in warnings))

    def test_a_reported_non_standard_tier_withholds_the_plan(self):
        rows = [user("u1", "prompt-1")]
        for index, tier in enumerate(({"service_tier": "priority"}, {"speed": "fast"}, {"inference_geo": "us"})):
            rows.append(assistant(f"m{index}", "u1", f"msg_tier_{index}", counts(**tier), second=index + 1))
        rows.append(assistant("m9", "u1", "msg_standard", counts(), second=9))
        events, _ = read([self.home.session(rows)])
        plans = {event.event_id: event.details.get("billing_plan") for event in events}
        self.assertEqual(plans, {"claude-code:msg_tier_0": None, "claude-code:msg_tier_1": None,
                                 "claude-code:msg_tier_2": None, "claude-code:msg_standard": "api-standard"})

    def test_no_message_tool_or_workspace_text_reaches_events_or_warnings(self):
        odd = assistant("m2", "m1", f"msg {SECRET}", counts(), second=2)
        named = assistant("m3", "m1", "msg_named", counts(), second=3, model=f"model {SECRET}")
        path = self.home.session([user("u1", "prompt-1"),
                                  {"type": "last-prompt", "lastPrompt": SECRET, "sessionId": SESSION},
                                  assistant("m1", "u1", "msg_one", counts()), odd, named])
        events, warnings = read([path])
        dumped = json.dumps([event.to_dict() for event in events] + warnings, ensure_ascii=False)
        self.assertNotIn(SECRET, dumped)
        self.assertNotIn("/work/", dumped)
        self.assertEqual({event.event_id for event in events}, {"claude-code:msg_one", "claude-code:msg_named"})
        self.assertEqual(next(event.model for event in events if event.event_id == "claude-code:msg_named"), "unknown")

    def test_paths_outside_the_claude_home_are_refused(self):
        with TemporaryDirectory() as outside:
            stray = Path(outside) / f"{SESSION}.jsonl"
            stray.write_text(lines([user("u1", "prompt-1"), assistant("m1", "u1", "msg_stray", counts())]), encoding="utf-8")
            events, warnings = read([stray])
        self.assertEqual(events, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("Claude home", warnings[0])
        self.assertNotIn(outside, warnings[0])
        escaped = self.home.folder / ".." / ".." / ".." / "elsewhere.jsonl"
        events, warnings = read([escaped])
        self.assertEqual((events, len(warnings)), ([], 1))

    def test_local_notices_are_not_model_calls_and_copies_across_sessions_count_once(self):
        first = self.home.session([user("u1", "prompt-1"), assistant("m1", "u1", "msg_shared", counts()),
                                   assistant("n1", "m1", "msg_notice", counts(ordinary=0, write=0, read=0, output=0, hour=0),
                                             second=2, model="<synthetic>")])
        fork = "77777777-6666-4555-8444-333333333333"
        second = self.home.session([user("u1", "prompt-1", session=fork),
                                    assistant("m1", "u1", "msg_shared", counts(output=80), session=fork),
                                    user("u2", "prompt-2", parent="m1", second=5, session=fork),
                                    assistant("m2", "u2", "msg_new", counts(), second=6, session=fork)], name=fork)
        events, warnings = read([first, second])
        self.assertEqual(warnings, [])
        by_id = {event.event_id: event for event in events}
        self.assertEqual(sorted(by_id), ["claude-code:msg_new", "claude-code:msg_shared"])
        # The copy is the same call: attributed to the first selected session, its counts at their maximum.
        self.assertEqual(by_id["claude-code:msg_shared"].session_id, SESSION)
        self.assertEqual(by_id["claude-code:msg_shared"].tokens.output_tokens, 80)
        self.assertEqual(by_id["claude-code:msg_new"].session_id, fork)


class UsageFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data_dir = self.root / "diagnostics"

    def event(self, event_id, *, source="usage-file", provider="anthropic", second=0, **extra):
        values = dict(event_id=event_id, source=source, provider=provider, model="m", phase="agent", status="observed",
                      started_at=at(second), tokens=TokenUsage(100, 10, 40, 0, 0, 2), model_call=True,
                      timing_scope="model_call", billing_mode="subscription_equivalent",
                      details={"billing_plan": "api-standard"}, session_id="other-agent-session")
        values.update(extra)
        return UsageEvent(**values)

    def document(self, name, events, *, schema="UsageEvent@1"):
        path = self.root / name
        path.write_text(json.dumps({"schema": schema, "events": [event.to_dict() for event in events]}), encoding="utf-8")
        return path

    def snapshot(self, *paths):
        return MonitorData(self.data_dir, agent_sessions={"usage-file": list(paths)}).snapshot()

    def test_a_document_and_appended_lines_both_read_as_their_recorded_agent(self):
        document = self.document("export.json", [self.event("tool-a:1"), self.event("claude-code:msg_far", source="claude-code")])
        appended = self.root / "appended.jsonl"
        appended.write_text(json.dumps(self.event("tool-b:1").to_dict()) + "\n" + '{"event_id": "tool-b:2"', encoding="utf-8")
        warnings = []
        events = list(iter_usage_file_events([document, appended], warnings=warnings))
        self.assertEqual({(event.event_id, event.source) for event in events},
                         {("tool-a:1", "usage-file"), ("claude-code:msg_far", "claude-code"), ("tool-b:1", "usage-file")})
        self.assertTrue(any("半行" in warning for warning in warnings))

    def test_conflicting_ids_and_host_records_are_refused(self):
        UsageLog(self.data_dir).append(self.event("evt-1", source="studio", provider="none", model_call=False,
                                                   tokens=TokenUsage(), details={}, billing_mode="unknown"))
        first = self.document("first.json", [
            self.event("evt-1", source="claude-code"),          # takes an id the journal already holds
            self.event("hub:turn:1", source="hub"),             # would impersonate a Hub record
            self.event("tool-a:same"), self.event("tool-a:clash"), self.event("tool-a:kept"),
        ])
        second = self.document("second.json", [self.event("tool-a:same"), self.event("tool-a:clash", second=9)])
        snapshot = self.snapshot(first, second)
        ids = sorted(row["event_id"] for row in snapshot["events"])
        self.assertEqual(ids, ["evt-1", "tool-a:kept", "tool-a:same"])
        journal = next(row for row in snapshot["events"] if row["event_id"] == "evt-1")
        self.assertEqual(journal["source"], "studio")
        joined = " ".join(snapshot["warnings"])
        self.assertIn("已有记录", joined)
        self.assertIn("hub", joined)
        self.assertIn("内容不同", joined)

    def test_a_plan_is_kept_only_when_its_connection_gives_it(self):
        path = self.document("plans.json", [self.event("tool-a:priced"),
                                            self.event("tool-a:unknown-connection", provider="some-endpoint")])
        warnings = []
        plans = {event.event_id: event.details.get("billing_plan")
                 for event in iter_usage_file_events([path], warnings=warnings)}
        self.assertEqual(plans, {"tool-a:priced": "api-standard", "tool-a:unknown-connection": None})
        self.assertTrue(warnings)

    def test_another_schema_is_refused_whole(self):
        path = self.document("future.json", [self.event("tool-a:1")], schema="UsageEvent@2")
        warnings = []
        self.assertEqual(list(iter_usage_file_events([path], warnings=warnings)), [])
        self.assertTrue(any("UsageEvent@1" in warning for warning in warnings))


class CombinedSourceTests(unittest.TestCase):
    def setUp(self):
        self.home = ClaudeHome(self)
        self.claude = self.home.session([user("u1", "prompt-1"), assistant("m1", "u1", "msg_one", counts()),
                                         user("u2", "prompt-2", parent="m1", second=3600),
                                         assistant("m2", "u2", "msg_two", counts(output=70), second=3601)])
        self.codex = self.home.root.parent / "rollout.jsonl"
        total = {"input_tokens": 100, "cached_input_tokens": 40, "cache_write_input_tokens": 0,
                 "output_tokens": 10, "reasoning_output_tokens": 5}
        self.codex.write_text(lines([
            {"type": "session_meta", "payload": {"id": "codex-session", "model_provider": "openai"}},
            {"type": "turn_context", "payload": {"model": "codex-model"}},
            {"type": "event_msg", "timestamp": at(1800), "payload": {"type": "token_count", "info": {
                "total_token_usage": total, "last_token_usage": total}}},
        ]), encoding="utf-8")
        self.data_dir = self.home.root.parent / "diagnostics"

    def report(self, *arguments):
        output = StringIO()
        with redirect_stdout(output):
            code = main(["report", *arguments])
        self.assertEqual(code, 0)
        return json.loads(output.getvalue())

    def test_codex_and_claude_code_read_together_as_they_read_alone(self):
        alone = {event.event_id: event.to_dict() for event in iter_codex_events([self.codex])}
        alone.update({event.event_id: event.to_dict() for event in read([self.claude])[0]})
        together = MonitorData(None, (self.codex,), agent_sessions={"claude-code": [self.claude]}).snapshot()
        self.assertEqual({row["event_id"]: row for row in together["events"]}, alone)
        self.assertEqual({row["source"] for row in together["events"]}, {"codex", "claude-code"})
        self.assertEqual(together["warnings"], [])

    def test_agent_session_flags_and_the_codex_alias_reach_the_same_sources(self):
        by_alias = self.report("--codex-session", str(self.codex), "--agent-session", f"claude-code={self.claude}")
        by_agent = self.report("--agent-session", f"codex={self.codex}", "--agent-session", f"claude-code={self.claude}")
        self.assertEqual(by_alias, by_agent)
        self.assertEqual(by_alias["schema"], "UsageEvent@1")
        self.assertEqual(len(by_alias["events"]), 3)
        for bad in ("nobody=" + str(self.claude), str(self.claude)):
            with self.subTest(value=bad), self.assertRaises(SystemExit), redirect_stdout(StringIO()), patch("sys.stderr", StringIO()):
                main(["report", "--agent-session", bad])

    def test_since_and_until_slice_by_started_at(self):
        both = ["--codex-session", str(self.codex), "--agent-session", f"claude-code={self.claude}"]
        window = self.report(*both, "--since", "2026-10-01T10:00:30+00:00", "--until", "2026-10-01T11:00:01Z")
        self.assertEqual([row["source"] for row in window["events"]], ["codex"])
        self.assertEqual(window["window"], {"since": "2026-10-01T10:00:30+00:00", "until": "2026-10-01T11:00:01Z"})
        later = self.report(*both, "--since", "2026-10-01T11:00:01Z")
        self.assertEqual([row["event_id"] for row in later["events"]], ["claude-code:msg_two"])
        for bad in (["--since", "2026-10-01T10:00:00"], ["--since", "yesterday"],
                    ["--since", "2026-10-01T12:00:00Z", "--until", "2026-10-01T11:00:00Z"]):
            with self.subTest(value=bad), self.assertRaises(SystemExit), patch("sys.stderr", StringIO()):
                main(["report", *both, *bad])
        with self.assertRaises(SystemExit), patch("sys.stderr", StringIO()):
            main(["serve", "--since", "2026-10-01T10:00:00Z"])

    def test_report_export_reads_back_as_a_usage_file_without_counting_twice(self):
        target = self.home.root.parent / "exports"
        summary = self.report("--agent-session", f"claude-code={self.claude}", "--output-dir", str(target))
        exported = target / "usage-events.json"
        self.assertEqual(summary, {"output": exported.resolve().as_posix(), "events": 2, "warnings": 0})
        document = json.loads(exported.read_text(encoding="utf-8"))
        self.assertEqual(document["schema"], "UsageEvent@1")
        self.assertNotIn(SECRET, exported.read_text(encoding="utf-8"))
        imported = MonitorData(None, agent_sessions={"usage-file": [exported]}).snapshot()
        self.assertEqual(sorted(row["event_id"] for row in imported["events"]), ["claude-code:msg_one", "claude-code:msg_two"])
        both = MonitorData(None, agent_sessions={"claude-code": [self.claude], "usage-file": [exported]}).snapshot()
        self.assertEqual(len(both["events"]), 2)
        self.assertTrue(any("已有记录" in warning for warning in both["warnings"]))


class TokenAuthorityTests(unittest.TestCase):
    START = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def row(self, key, phase, start, end, **extra):
        values = dict(event_id=key, source="hub", provider="claude", model="m", phase=phase, status="completed",
                      started_at=(self.START + timedelta(milliseconds=start)).isoformat(),
                      ended_at=(self.START + timedelta(milliseconds=end)).isoformat(), tokens=TokenUsage(),
                      model_call=False, project_id="project", session_id=SESSION, turn_id="hub-turn",
                      timing_scope="service")
        values.update(extra)
        return UsageEvent(**values).to_dict()

    def usage(self, rows):
        (trace,) = build_traces(rows, now=self.START + timedelta(seconds=30))["traces"]
        return trace["usage"]

    def test_claude_code_records_replace_hub_usage_for_the_same_native_session(self):
        rows = [self.row("root", "hub_turn", 0, 10000, timing_scope="agent_turn"),
                self.row("hub-usage", "model_usage", 3000, 3000, parent_event_id="root", model_call=True,
                         tokens=TokenUsage(100, 10), timing_scope="unknown"),
                self.row("claude-code:msg_one", "agent", 2000, 2500, source="claude-code", model_call=True,
                         tokens=TokenUsage(300, 30), turn_id="prompt-1", project_id=None, timing_scope="unknown")]
        usage = self.usage(rows)
        self.assertEqual(usage["tokens"]["input_tokens"], 300)
        self.assertEqual(usage["excluded_duplicate_event_ids"], ["hub-usage"])

    def test_an_imported_file_defers_to_the_hub_record_of_its_session(self):
        rows = [self.row("root", "hub_turn", 0, 10000, timing_scope="agent_turn"),
                self.row("hub-usage", "model_usage", 3000, 3000, parent_event_id="root", model_call=True,
                         tokens=TokenUsage(100, 10), timing_scope="unknown"),
                self.row("tool-a:1", "agent", 3000, 3000, source="usage-file", model_call=True,
                         tokens=TokenUsage(300, 30), timing_scope="model_call")]
        usage = self.usage(rows)
        self.assertEqual(usage["tokens"]["input_tokens"], 100)
        self.assertEqual(usage["excluded_duplicate_event_ids"], ["tool-a:1"])


if __name__ == "__main__":
    unittest.main()

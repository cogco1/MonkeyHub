"""Agent usage sources: one declared interface, Claude Code and UsageEvent@1 files beside Codex.

Every Claude Code row here is synthetic. The bodies carry a marker that must
never reach a usage event, a report or a warning.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
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
from monkeymonitor.pricing import RateCard
from monkeymonitor.server import MonitorData
from monkeymonitor.sources import AGENT_SOURCES, defer_to_hub
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


def assistant(uuid, parent, message_id, usage, *, second=1, agent=None, model="claude-test-model", session=SESSION,
              stop="tool_use"):
    """``stop=None`` is a row written before the message's stream said its stop reason."""
    return private({"type": "assistant", "uuid": uuid, "parentUuid": parent, "requestId": f"req_{message_id}",
                    "timestamp": at(second),
                    "message": {"id": message_id, "type": "message", "role": "assistant", "model": model,
                                "content": [{"type": "text", "text": f"answer {SECRET}"},
                                            {"type": "tool_use", "name": "Bash", "input": {"command": SECRET}}],
                                "stop_reason": stop, "usage": usage}}, agent, session)


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

    def subagent(self, rows, *, session=SESSION, agent=AGENT, workflow=None, trailing=""):
        directory = self.folder / session / "subagents"
        if workflow is not None:
            directory = directory / "workflows" / workflow
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
        self.assertEqual(AGENT_SOURCES["claude-code"].token_authority, "host")
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

    def test_workflow_agents_of_the_selected_session_are_its_subagents_too(self):
        path = self.home.session([user("u1", "prompt-1"), assistant("m1", "u1", "msg_main", counts())])
        self.home.subagent([user("w1", "prompt-1", agent=AGENT),
                            assistant("w2", "w1", "msg_workflow", counts(output=11), second=3, agent=AGENT)],
                           workflow="wf_0001")
        run = self.home.folder / SESSION / "subagents" / "workflows" / "wf_0001"
        # The workflow's own journal is not an agent record and is not read.
        (run / "journal.jsonl").write_text(lines([assistant("j1", None, "msg_journal", counts(), agent=AGENT)]),
                                           encoding="utf-8")
        events, warnings = read([path])
        self.assertEqual(warnings, [])
        by_id = {event.event_id: event for event in events}
        self.assertEqual(set(by_id), {"claude-code:msg_main", "claude-code:msg_workflow"})
        child = by_id["claude-code:msg_workflow"]
        self.assertEqual((child.session_id, child.parent_session_id, child.turn_id), (AGENT, SESSION, "prompt-1"))
        self.assertEqual(child.tokens.output_tokens, 11)

    def test_a_message_whose_final_counts_were_never_written_keeps_its_output_unknown(self):
        # Claude Code writes a row as each content block completes. When no row of a
        # message carries its stop reason, the stream's closing counts were never
        # written: the output there is the opening value of a few tokens.
        path = self.home.session([
            user("u1", "prompt-1"),
            assistant("m1a", "u1", "msg_open", counts(output=3, thinking=1), stop=None),
            assistant("m1b", "m1a", "msg_open", counts(output=3, thinking=1), second=2, stop=None),
            assistant("m2a", "m1b", "msg_done", counts(output=4), second=3, stop=None),
            assistant("m2b", "m2a", "msg_done", counts(output=260), second=5),
        ])
        events, warnings = read([path])
        by_id = {event.event_id: event for event in events}
        self.assertEqual(by_id["claude-code:msg_open"].tokens, TokenUsage(
            input_tokens=3210, output_tokens=None, cached_input_tokens=3000, cache_write_input_tokens=200,
            cache_write_1h_input_tokens=200, reasoning_output_tokens=None))
        self.assertEqual(by_id["claude-code:msg_done"].tokens.output_tokens, 260)
        self.assertEqual(len(warnings), 1)
        self.assertIn("1 条", warnings[0])
        self.assertIn("输出", warnings[0])

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

    def test_an_undecodable_byte_spoils_only_its_own_line(self):
        path = self.home.session([user("u1", "prompt-1"), assistant("m1", "u1", "msg_before", counts())])
        with path.open("ab") as stream:
            stream.write(b'{"type": "user", "message": "\xff\xfe broken"}\n')
            stream.write((json.dumps(assistant("m2", "m1", "msg_after", counts(), second=3)) + "\n").encode("utf-8"))
        events, warnings = read([path])
        self.assertEqual(sorted(event.event_id for event in events), ["claude-code:msg_after", "claude-code:msg_before"])
        self.assertEqual(warnings, [])

    def test_an_unreadable_subagent_folder_still_reads_the_session(self):
        path = self.home.session([user("u1", "prompt-1"), assistant("m1", "u1", "msg_main", counts())])
        self.home.subagent([assistant("s1", None, "msg_child", counts(), agent=AGENT)])
        with patch.object(Path, "is_dir", side_effect=PermissionError("denied")):
            events, warnings = read([path])
        self.assertEqual([event.event_id for event in events], ["claude-code:msg_main"])
        self.assertEqual(len(warnings), 1)

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

    def test_a_recorded_rate_does_not_travel_with_an_imported_event(self):
        # A file reports counts; this machine's catalog prices them.
        rate = RateCard(provider="anthropic", model="m", billing_plan="api-standard", input="2", output="10",
                        effective_date="2026-09-01", source_url="https://provider.example/pricing")
        path = self.document("priced.json", [self.event("tool-a:snapshot", rate_snapshot=rate.to_dict(),
                                                        rate_match_status="matched")])
        (event,) = iter_usage_file_events([path])
        self.assertEqual((event.rate_snapshot, event.rate_match_status), (None, None))
        self.assertEqual(event.details, {"billing_plan": "api-standard"})

    def test_free_text_paths_and_addresses_are_refused(self):
        path = self.document("text.json", [
            self.event("tool-a:prompt", session_id="please rewrite the facade"),
            self.event("tool-a:path", source_ref="C:/Users/someone/project"),
            self.event("tool-a:address", model="https://provider.example/model"),
            self.event("tool-a:detail", details={"billing_plan": "api-standard", "tool_name": "read the brief"}),
            self.event("tool-a:kept"),
        ])
        warnings = []
        self.assertEqual([event.event_id for event in iter_usage_file_events([path], warnings=warnings)], ["tool-a:kept"])
        self.assertTrue(any("4 条" in warning for warning in warnings))

    def test_a_malformed_file_or_record_is_a_warning_never_an_exception(self):
        nested = self.root / "nested.json"
        nested.write_text("[" * 100000, encoding="utf-8")
        lines_file = self.root / "nested.jsonl"
        lines_file.write_text("[" * 100000 + "\n" + json.dumps(self.event("tool-a:after").to_dict()) + "\n", encoding="utf-8")
        record = self.event("tool-a:odd-rate").to_dict()
        record.update(rate_match_status="matched", rate_snapshot={"provider": "anthropic", "model": "m", "source_url": 5})
        odd = self.root / "odd.json"
        odd.write_text(json.dumps({"schema": "UsageEvent@1", "events": [record, {"event_id": 5}, "text"]}), encoding="utf-8")
        warnings = []
        events = list(iter_usage_file_events([nested, lines_file, odd], warnings=warnings))
        self.assertEqual(sorted(event.event_id for event in events), ["tool-a:after", "tool-a:odd-rate"])
        self.assertIsNone(next(event for event in events if event.event_id == "tool-a:odd-rate").rate_snapshot)
        self.assertTrue(any("2 条" in warning for warning in warnings))

    def test_a_reader_that_fails_leaves_the_snapshot_and_other_sources_standing(self):
        def broken(paths, *, warnings):
            raise RuntimeError("reader bug")
            yield
        failing = replace(AGENT_SOURCES["usage-file"], read=broken)
        UsageLog(self.data_dir).append(self.event("evt-kept", source="studio", provider="none", model_call=False,
                                                  tokens=TokenUsage(), details={}, billing_mode="unknown"))
        with patch("monkeymonitor.server.AGENT_SOURCES", {**AGENT_SOURCES, "usage-file": failing}):
            snapshot = self.snapshot(self.document("any.json", [self.event("tool-a:1")]))
        self.assertEqual([row["event_id"] for row in snapshot["events"]], ["evt-kept"])
        self.assertTrue(any("usage-file" in warning and "读取失败" in warning for warning in snapshot["warnings"]))

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
    """The snapshot keeps one row per model call: the Hub's, where it counted the call itself."""
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
        """The input the traces total over what the snapshot keeps, and the rows it left out."""
        kept, _ = defer_to_hub(rows)
        traces = build_traces(kept, now=self.START + timedelta(seconds=30))["traces"]
        held = {row["event_id"] for row in kept}
        return (sum(trace["usage"]["known_subtotal_tokens"]["input_tokens"] for trace in traces),
                sorted(row["event_id"] for row in rows if row["event_id"] not in held))

    def hub_turn(self, *messages):
        """A Hub turn and one usage row per native message the Hub counted, as its observer names them."""
        return [self.row("hub:turn:hub-turn", "hub_turn", 0, 10000, timing_scope="agent_turn"),
                *(self.row(f"hub:turn:hub-turn:usage:{message}", "model_usage", 3000, 3000,
                           parent_event_id="hub:turn:hub-turn", model_call=True, tokens=TokenUsage(100, 10),
                           timing_scope="unknown") for message in messages)]

    def claude(self, key, start, **extra):
        values = dict(source="claude-code", model_call=True, tokens=TokenUsage(300, 30), turn_id="prompt-1",
                      project_id=None, timing_scope="unknown")
        values.update(extra)
        return self.row(key, "agent", start, start + 500, **values)

    def test_the_hub_stays_the_token_authority_of_each_call_it_counted(self):
        # The Hub counts each message from its stream's closing usage on the connection it
        # ran. A Claude Code record of the same message is not counted again, whether it sits
        # in the Hub's session, in a fork that copied it, or in a subagent. A message the Hub
        # never counted (the session continued outside the Hub, or a turn that ended before
        # its usage was written) counts from the record.
        fork = "77777777-6666-4555-8444-333333333333"
        for session in (SESSION, fork):
            with self.subTest(session=session):
                rows = [*self.hub_turn("msg_one", "msg_child"),
                        self.claude("claude-code:msg_one", 2000, session_id=session),
                        self.claude("claude-code:msg_child", 2600, session_id=AGENT, parent_session_id=SESSION),
                        self.claude("claude-code:msg_later", 20000)]
                self.assertEqual(self.usage(rows), (500, ["claude-code:msg_child", "claude-code:msg_one"]))

    def test_a_claude_session_the_hub_never_ran_counts_from_its_own_records(self):
        other = "99999999-8888-4777-8666-555555555555"
        rows = [*self.hub_turn("msg_one"), self.claude("claude-code:msg_far", 2000, session_id=other),
                self.claude("claude-code:msg_far_child", 2600, session_id=AGENT, parent_session_id=other)]
        self.assertEqual(self.usage(rows), (700, []))

    def test_an_imported_file_defers_to_the_hub_by_call_or_else_by_session(self):
        # An event that names no native call defers for the Hub's whole session.
        for turn in ("hub-turn", "another-turn"):
            with self.subTest(turn=turn):
                rows = [*self.hub_turn("msg_one"),
                        self.row("tool-a:1", "agent", 3000, 3000, source="usage-file", model_call=True, turn_id=turn,
                                 tokens=TokenUsage(300, 30), timing_scope="model_call")]
                self.assertEqual(self.usage(rows), (100, ["tool-a:1"]))
        # An exported Claude Code event keeps its source and is matched call by call.
        rows = [*self.hub_turn("msg_one"), self.claude("claude-code:msg_one", 2000, session_id="from-elsewhere"),
                self.claude("claude-code:msg_two", 2600)]
        self.assertEqual(self.usage(rows), (400, ["claude-code:msg_one"]))

    def test_left_out_rows_are_counted_per_source_and_hub_and_studio_rows_always_stay(self):
        rows = [*self.hub_turn("msg_one"), self.claude("claude-code:msg_one", 2000),
                self.row("tool-a:1", "agent", 3000, 3000, source="usage-file", model_call=True,
                         tokens=TokenUsage(300, 30), timing_scope="model_call"),
                self.row("studio-call", "model_request", 3100, 3200, source="studio", provider="anthropic",
                         model_call=True, tokens=TokenUsage(50, 5), timing_scope="model_call")]
        kept, left_out = defer_to_hub(rows)
        self.assertEqual(left_out, {"claude-code": 1, "usage-file": 1})
        self.assertEqual({row["event_id"] for row in kept},
                         {"hub:turn:hub-turn", "hub:turn:hub-turn:usage:msg_one", "studio-call"})

    def test_an_agent_row_outside_any_model_call_phase_is_drawn_in_the_agent_lane(self):
        rows = [self.row("tool-a:step", "tool_call", 0, 100, source="usage-file", session_id="elsewhere")]
        (trace,) = build_traces(rows, now=self.START + timedelta(seconds=30))["traces"]
        self.assertEqual([span["lane"] for span in trace["spans"]], ["agent"])


class SnapshotAuthorityTests(unittest.TestCase):
    """/api/events, report and traces all read the snapshot, so each lists a call once."""

    def setUp(self):
        self.home = ClaudeHome(self)
        self.data_dir = self.home.root.parent / "diagnostics"
        # The Hub ran this Claude session and counted msg_one from its stream.
        UsageLog(self.data_dir).append(UsageEvent(
            event_id="hub:turn:t1:usage:msg_one", source="hub", provider="claude", model="claude-test-model",
            phase="model_usage", status="succeeded", started_at=at(1), tokens=TokenUsage(3210, 120, 3000, 200, 200),
            model_call=True, session_id=SESSION, timing_scope="unknown", billing_mode="subscription_equivalent",
            details={"billing_plan": "api-standard"}))
        # Its record also holds msg_one, and msg_two from a turn the Hub never saw.
        self.session = self.home.session([user("u1", "prompt-1"), assistant("m1", "u1", "msg_one", counts(output=120)),
                                          user("u2", "prompt-2", parent="m1", second=60),
                                          assistant("m2", "u2", "msg_two", counts(output=70), second=61)])

    def test_the_snapshot_report_and_traces_hold_each_call_once(self):
        data = MonitorData(self.data_dir, agent_sessions={"claude-code": [self.session]})
        snapshot = data.snapshot()
        self.assertEqual(sorted(row["event_id"] for row in snapshot["events"]),
                         ["claude-code:msg_two", "hub:turn:t1:usage:msg_one"])
        self.assertTrue(any("1 条 claude-code" in warning for warning in snapshot["warnings"]))
        traces = data.traces()["traces"]
        self.assertEqual(sum(trace["usage"]["tokens"]["output_tokens"] or 0 for trace in traces), 190)
        output = StringIO()
        with redirect_stdout(output):
            main(["report", "--data-dir", str(self.data_dir), "--agent-session", f"claude-code={self.session}"])
        self.assertEqual(sorted(row["event_id"] for row in json.loads(output.getvalue())["events"]),
                         ["claude-code:msg_two", "hub:turn:t1:usage:msg_one"])


if __name__ == "__main__":
    unittest.main()

"""One prompt contract for A-D, answer validation, and the codex wrapper on a scripted child process."""

import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from .environment import Environment
from .strategies import (
    CODEX_FIXED_ARGUMENTS,
    RATIONALE_LIMIT,
    STRATEGIES,
    CodexRunner,
    ProposalError,
    answer_schema,
    parse_codex_events,
)

# Speaks `codex exec --json -o <answer>` on the wire: prompt bytes on stdin,
# JSONL events on stdout, the final answer in the -o file.
SCRIPTED_CODEX = r'''
import hashlib, json, sys, time
workdir, schema_path, answer_path, mode = sys.argv[1:5]
prompt = sys.stdin.buffer.read()
if mode == "sleep":
    time.sleep(60)
digest = hashlib.sha256(prompt).hexdigest()
schema = json.load(open(schema_path, encoding="utf-8"))
first = schema["properties"]["plan"]["items"]["enum"][0]
with open(answer_path, "w", encoding="utf-8") as stream:
    json.dump({"plan": [first], "ranking": [], "rationale": digest}, stream)
print(json.dumps({"type": "thread.started", "thread_id": "thread-" + digest[:8]}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 90, "cached_input_tokens": 30,
                                                      "output_tokens": 12, "reasoning_output_tokens": 4}}))
'''


class ScriptedCodex(CodexRunner):
    def __init__(self, script: Path, mode: str = "answer") -> None:
        super().__init__(executable=sys.executable)
        self.script, self.mode = script, mode

    def command(self, workdir, schema_path, answer_path):
        return [sys.executable, str(self.script), str(workdir), str(schema_path), str(answer_path), self.mode]


class PromptContractTests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()

    def requests(self, case_id):
        snapshot = self.env.reset(case_id)
        return snapshot, {strategy_id: strategy.request(self.env, snapshot, horizon=3)
                          for strategy_id, strategy in STRATEGIES.items()}

    def test_every_strategy_gets_the_same_snapshot_schema_and_contract(self):
        for case_id in self.env.case_ids:
            snapshot, requests = self.requests(case_id)
            block = json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=1, sort_keys=True)
            self.assertEqual({request.schema_json for request in requests.values()},
                             {json.dumps(answer_schema(snapshot.action_ids), sort_keys=True)})
            self.assertEqual({request.prompt_contract for request in requests.values()}, {"strategy-allocation-prompt@1"})
            for request in requests.values():
                self.assertEqual(request.snapshot_digest, snapshot.digest)
                self.assertIn(block, request.prompt)
                self.assertTrue(request.prompt.startswith("You are one arm of a controlled experiment."))

    def test_policies_differ_only_in_their_own_sections(self):
        _, requests = self.requests("protected-dependency")
        self.assertEqual({strategy_id for strategy_id, request in requests.items()
                          if "## Legal actions compiled" in request.prompt}, {"C"})
        self.assertEqual({strategy_id for strategy_id, request in requests.items()
                          if "## One-step previews" in request.prompt}, {"D"})
        self.assertIn("1. GATHER", requests["B"].prompt)
        self.assertIn("- Repair: not legal now (there is no reported violation to repair)", requests["C"].prompt)
        self.assertIn("R-7 check: violated", requests["D"].prompt)
        self.assertNotIn("R-7 check: violated", requests["A"].prompt)

    def test_a_request_is_a_pure_function_of_the_snapshot(self):
        _, first = self.requests("open-direction")
        _, second = self.requests("open-direction")
        self.assertEqual({key: value.prompt_sha256 for key, value in first.items()},
                         {key: value.prompt_sha256 for key, value in second.items()})
        self.assertEqual(len({value.prompt_sha256 for value in first.values()}), 4)

    def test_answers_are_validated_into_proposals(self):
        _, requests = self.requests("local-conflict")
        free = STRATEGIES["A"]
        proposal = free.proposal(requests["A"], {"plan": ["Inspect", "Repair"], "ranking": [],
                                                 "rationale": "x" * (RATIONALE_LIMIT + 50)})
        self.assertEqual(proposal.plan, ("Inspect", "Repair"))
        self.assertEqual(len(proposal.rationale), RATIONALE_LIMIT)
        for answer in (None, {"plan": ["Repair"]}, {"plan": [], "ranking": [], "rationale": ""},
                       {"plan": ["Teleport"], "ranking": [], "rationale": ""},
                       {"plan": ["Repair"], "ranking": [{"action": "Repair", "score": float("nan")}], "rationale": ""},
                       {"plan": ["Repair"], "ranking": [{"action": "Repair", "score": 1}, {"action": "Repair", "score": 0}],
                        "rationale": ""}):
            with self.subTest(answer=answer), self.assertRaises(ProposalError) as caught:
                free.proposal(requests["A"], answer)
            self.assertEqual(caught.exception.code, "malformed")

    def test_state_ranked_plans_start_with_their_top_ranked_action(self):
        _, requests = self.requests("local-conflict")
        ranked = STRATEGIES["C"]
        ranking = [{"action": "Inspect", "score": 0.4}, {"action": "Repair", "score": 0.9}]
        self.assertEqual(ranked.proposal(requests["C"], {"plan": ["Repair"], "ranking": ranking, "rationale": ""}).plan,
                         ("Repair",))
        for answer in ({"plan": ["Repair"], "ranking": [], "rationale": ""},
                       {"plan": ["Inspect", "Repair"], "ranking": ranking, "rationale": ""}):
            with self.subTest(answer=answer), self.assertRaises(ProposalError) as caught:
                ranked.proposal(requests["C"], answer)
            self.assertEqual(caught.exception.code, "policy_violation")


class CodexRunnerTests(unittest.TestCase):
    def setUp(self):
        env = Environment()
        self.request = STRATEGIES["A"].request(env, env.reset("open-direction"), horizon=3)
        self.temporary = tempfile.TemporaryDirectory(prefix="sa268-test-")
        self.root = Path(self.temporary.name)
        self.script = self.root / "scripted_codex.py"
        self.script.write_text(SCRIPTED_CODEX, encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_the_command_is_fresh_ephemeral_read_only_and_reads_stdin(self):
        command = CodexRunner(executable="codex").command(Path("w"), Path("s.json"), Path("a.json"))
        self.assertEqual(command[1:1 + len(CODEX_FIXED_ARGUMENTS)], list(CODEX_FIXED_ARGUMENTS))
        for flag in ("--json", "--ephemeral", "--ignore-user-config", "--output-schema", "-o"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("-s") + 1], "read-only")
        self.assertEqual(command[-1], "-")
        self.assertFalse({"resume", "fork", "-m", "-c"} & set(command))
        pinned = CodexRunner(executable="codex", model="some-model", reasoning_effort="low")
        tail = pinned.command(Path("w"), Path("s.json"), Path("a.json"))[-5:]
        self.assertEqual(tail, ["-m", "some-model", "-c", "model_reasoning_effort=low", "-"])

    def test_events_give_usage_session_and_tool_calls_from_metadata_only(self):
        stdout = "\n".join(json.dumps(event) for event in (
            {"type": "thread.started", "thread_id": "t-1"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": '{"output_tokens": 99999}'}},
            {"type": "item.completed", "item": {"type": "command_execution", "command": "dir"}},
            {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                                 "cache_write_input_tokens": 0, "output_tokens": 30,
                                                 "reasoning_output_tokens": 10}},
        )) + "\nnot json\n"
        usage, model, session, tools = parse_codex_events(stdout)
        self.assertEqual(usage, {"input_tokens": 100, "cached_input_tokens": 40, "cache_write_input_tokens": 0,
                                 "output_tokens": 30, "reasoning_output_tokens": 10})
        self.assertEqual((model, session, tools), (None, "t-1", 1))

    def test_a_call_feeds_the_exact_prompt_bytes_and_keeps_raw_files(self):
        raw = self.root / "raw"
        result = ScriptedCodex(self.script).run(self.request, seed=1, timeout_s=60, raw_dir=raw)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.answer["rationale"], self.request.prompt_sha256)  # the child read these bytes
        self.assertEqual(result.session_id, "thread-" + self.request.prompt_sha256[:8])
        self.assertEqual((result.usage["output_tokens"], result.provider_tool_calls), (12, 0))
        self.assertEqual(sorted(path.name for path in raw.iterdir()),
                         ["answer.json", "events.jsonl", "prompt.txt", "schema.json", "stderr.txt"])
        self.assertEqual(hashlib.sha256((raw / "prompt.txt").read_bytes()).hexdigest(), self.request.prompt_sha256)

    def test_a_timeout_kills_the_child_and_is_reported(self):
        started = time.monotonic()
        result = ScriptedCodex(self.script, mode="sleep").run(self.request, seed=1, timeout_s=1.0)
        self.assertEqual((result.status, result.answer), ("timeout", None))
        self.assertLess(time.monotonic() - started, 30)

    def test_a_missing_executable_is_a_provider_error(self):
        result = CodexRunner(executable=str(self.root / "no-such-codex")).run(self.request, seed=1, timeout_s=5)
        self.assertEqual(result.status, "provider_error")


if __name__ == "__main__":
    unittest.main()

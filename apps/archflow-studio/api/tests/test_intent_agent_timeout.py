"""What a call to codex leaves behind: a receipt, and no grandchild.

``codex`` on this machine is a shim — ``codex.cmd`` → ``cmd.exe`` → ``node``
— and a timeout that kills only the shim leaves a grandchild holding the
pipes, so the compile would sit past the timeout it promised. The executable
here is a real shim of the same shape (``tests/support.write_codex_shim``): a
script whose Python grandchild either answers or holds stdout open and sleeps
far longer than the timeout. The compiler must answer 502 within the timeout,
leave no grandchild behind, and — either way — sign the call with a
``ModelInvocationReceipt@2`` of the shared contract.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

# Importing the API package first puts this repository's root on ``sys.path``,
# so ``archflow`` below is this checkout's kernel and not one installed into
# the environment.
import archflow_studio_api  # noqa: F401

from archflow.contracts.canonical import canonical_digest
from archflow.ports.model import ModelInvocationStatus, ModelPhase

from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.intent_agent import (
    AGENT_FAILED,
    CODEX_DEFAULT_MODEL_ID,
    CODEX_PROVIDER_ID,
    CodexCompiler,
    IntentAgentFailed,
    Selection,
)
from archflow_studio_api.application.projection import project_state
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.errors import StudioError

from .support import CODEX_SHIM_VERSION, PROJECT_ID, make_project, write_codex_shim

TIMEOUT_S = 1.0
GRANDCHILD_SLEEP_S = 20.0

MESSAGE = "make the west portico a little taller"
SELECTION = Selection(component_id="portico", element_id=None)

# What the shim answers with: one object in ``RESPONSE_SCHEMA``, compiled.
ANSWER = json.dumps(
    {
        "status": "compiled",
        "targetComponentId": "portico",
        "elementId": "portico-base",
        "utterance": "increase height by 10 %",
        "why": "a little = +10 %",
        "question": None,
    }
)


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return str(pid) in listing
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class CodexReceiptTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        make_project(self.root)
        app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        self.projection = project_state(bound_project(app.state))


class CodexTimeoutTests(CodexReceiptTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.shim, self.pid_file = write_codex_shim(
            self.root, hang_seconds=GRANDCHILD_SLEEP_S
        )

    def _compile(self) -> tuple[StudioError, float]:
        compiler = CodexCompiler(executable=str(self.shim), timeout_s=TIMEOUT_S)
        started = time.perf_counter()
        with self.assertRaises(StudioError) as caught:
            compiler.compile(
                message=MESSAGE, selection=SELECTION, projection=self.projection
            )
        return caught.exception, time.perf_counter() - started

    def test_a_hung_agent_is_refused_within_its_timeout(self) -> None:
        error, elapsed = self._compile()
        self.assertEqual(error.status, 502)
        self.assertEqual(error.code, AGENT_FAILED)
        self.assertIn(f"did not answer within {TIMEOUT_S:g} s", error.detail)
        self.assertLess(
            elapsed,
            TIMEOUT_S + 5.0,
            f"the compile took {elapsed:.1f} s: the timeout did not end the call",
        )

    def test_the_agents_children_are_gone_after_the_timeout(self) -> None:
        _, elapsed = self._compile()
        # Only a prompt return makes the check below mean anything: a call
        # that waited for the grandchild to finish would find it gone too.
        self.assertLess(elapsed, TIMEOUT_S + 5.0)
        self.assertTrue(self.pid_file.is_file(), "the grandchild never started")
        pid = int(self.pid_file.read_text(encoding="utf-8"))
        deadline = time.perf_counter() + 2.0
        while _alive(pid) and time.perf_counter() < deadline:
            time.sleep(0.1)
        self.assertFalse(_alive(pid), f"grandchild {pid} outlived the timeout")

    def test_the_timeout_is_a_receipt_with_an_error_and_no_output(self) -> None:
        error, _ = self._compile()
        self.assertIsInstance(error, IntentAgentFailed)
        receipt = error.receipt
        self.assertIs(receipt.status, ModelInvocationStatus.TIMEOUT)
        self.assertEqual(receipt.error_code, "model.timeout")
        self.assertIsNone(receipt.output)
        self.assertIsNone(receipt.output_sha256)
        self.assertEqual(receipt.output_bytes, 0)
        self.assertGreater(receipt.input_bytes, 0)
        self.assertEqual(receipt.provider_id, CODEX_PROVIDER_ID)
        self.assertEqual(receipt.provider_version, CODEX_SHIM_VERSION)
        self.assertIs(receipt.request.phase, ModelPhase.INTENT_COMPILATION)
        # The receipt says what it is on the wire, and reads back as itself.
        self.assertEqual(receipt.to_dict()["schema"], "ModelInvocationReceipt@2")


class CodexSuccessTests(CodexReceiptTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.shim, _ = write_codex_shim(self.root, answer=ANSWER)

    def _compile(self, **kwargs: object):
        compiler = CodexCompiler(
            executable=str(self.shim), timeout_s=TIMEOUT_S * 30, **kwargs
        )
        return compiler.compile(
            message=MESSAGE, selection=SELECTION, projection=self.projection
        )

    def test_an_answered_call_is_a_success_receipt_bound_to_its_request(self) -> None:
        compilation = self._compile()
        self.assertEqual(compilation.utterance, "increase height by 10 %")
        receipt = compilation.receipt
        self.assertIsNotNone(receipt)
        self.assertIs(receipt.status, ModelInvocationStatus.SUCCESS)
        self.assertIsNone(receipt.error_code)
        self.assertEqual(receipt.output_bytes, len(ANSWER.encode("utf-8")))
        self.assertEqual(receipt.model_id, CODEX_DEFAULT_MODEL_ID)
        # The request the receipt carries is the one this call was made
        # against: the phase, the projection's own checkpoint, and a context
        # digest over the payload it was built from.
        request = receipt.request
        self.assertIs(request.phase, ModelPhase.INTENT_COMPILATION)
        self.assertEqual(
            request.checkpoint_digest,
            self.projection.state_digest or self.projection.record_digest,
        )
        self.assertEqual(
            request.context_digest, canonical_digest(request.payload, ascii=False)
        )
        self.assertEqual(request.payload["message"], MESSAGE)
        self.assertEqual(
            request.payload["selection"],
            {"component_id": "portico", "element_id": None, "gestures": []},
        )
        self.assertIn("elements", request.payload["record_sheet"])
        # What the agent said, decoded, is what the receipt's output carries.
        self.assertEqual(receipt.output["utterance"], "increase height by 10 %")
        self.assertEqual(receipt.output["status"], "compiled")

    def test_the_fingerprint_is_the_configurations_and_the_models(self) -> None:
        first = CodexCompiler(executable=str(self.shim), timeout_s=TIMEOUT_S)
        again = CodexCompiler(executable=str(self.shim), timeout_s=TIMEOUT_S)
        self.assertEqual(
            first.binding.fingerprint,
            again.binding.fingerprint,
            "the same configuration must fingerprint the same twice",
        )
        other_model = CodexCompiler(
            executable=str(self.shim), model="gpt-5", timeout_s=TIMEOUT_S
        )
        self.assertNotEqual(first.binding.fingerprint, other_model.binding.fingerprint)
        self.assertEqual(first.binding.version, CODEX_SHIM_VERSION)


class CodexMalformedTests(CodexReceiptTestCase):
    def test_an_unreadable_answer_is_a_receipt_of_the_bytes_that_arrived(self) -> None:
        shim, _ = write_codex_shim(self.root, answer="I would raise it a bit.")
        compiler = CodexCompiler(executable=str(shim), timeout_s=TIMEOUT_S * 30)
        with self.assertRaises(IntentAgentFailed) as caught:
            compiler.compile(
                message=MESSAGE, selection=SELECTION, projection=self.projection
            )
        receipt = caught.exception.receipt
        self.assertIs(receipt.status, ModelInvocationStatus.MALFORMED)
        self.assertEqual(receipt.error_code, "model.output_malformed")
        self.assertIsNone(receipt.output)
        # The answer did arrive: the receipt says how much of it and what it
        # hashed to, which is what tells a malformed answer from no answer.
        self.assertEqual(receipt.output_bytes, len("I would raise it a bit."))
        self.assertIsNotNone(receipt.output_sha256)


class CodexBuildTests(unittest.TestCase):
    def test_a_codex_that_cannot_say_its_version_refuses_to_be_built(self) -> None:
        from archflow_studio_api.settings import SettingsError

        with self.assertRaises(SettingsError) as caught:
            CodexCompiler(executable=str(Path(tempfile.gettempdir()) / "no-codex-here"))
        self.assertIn("version", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

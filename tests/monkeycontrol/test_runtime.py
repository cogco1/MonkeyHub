"""The eight-step execute pipeline, against providers that are plain objects.

Nothing here touches a desktop: a ``FakeUia`` and a ``FakePresentation``
answer every provider call and append their own name to one shared log, so
order -- highlight before the click, badge after the verification -- is as
observable as the receipt the runtime writes.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from archflow.contracts.canonical import canonical_digest

from monkeycontrol.contract import ContractError
from monkeycontrol.host import HostError
from monkeycontrol.providers import FocusError, ResolutionError
from monkeycontrol.runtime import (
    ComputerUseRuntime,
    RuntimePolicy,
    RuntimeRefusal,
)
from monkeycontrol.trace import ResolvedTarget, WindowInfo

PNG = b"\x89PNG\r\n\x1a\nfake capture"
PRIMARY = (0, 0, 1920, 1080)
SECOND = (1920, 0, 3840, 1080)
NOTEPAD = WindowInfo(4242, "Untitled - Notepad", 91, "notepad", (0, 0, 800, 600))
SAVE = ResolvedTarget(
    "Button", "Save", "1", "Button", (100, 200, 200, 240), "42.7.1", "windows-uia"
)
CLICK = {
    "intent": "press save",
    "application": "notepad",
    "target": {"controlType": "Button", "name": "Save"},
    "action": {"type": "click"},
    "verification": {"expect": "window", "title": "Notepad", "timeout_ms": 0},
}


class FakeUia:
    """Every provider method the runtime uses, recorded in one shared log."""

    name = "windows-uia"

    def __init__(
        self,
        log: list[str],
        *,
        windows=(NOTEPAD,),
        target=SAVE,
        foreground=NOTEPAD,
        invoked: bool = False,
        value: str | None = None,
        focus_error: FocusError | None = None,
        fail: HostError | None = None,
    ) -> None:
        self.log = log
        self.calls: list[tuple] = []
        self._windows = list(windows)
        self._target = target
        self._foreground = foreground
        self._invoked = invoked
        self._value = value
        self._focus_error = focus_error
        self._fail = fail

    def _note(self, name: str, *args) -> None:
        self.log.append(name)
        self.calls.append((name, args))

    def windows(self, application, title=None):
        self._note("windows", application, title)
        if not title:
            return list(self._windows)
        return [
            window for window in self._windows if re.search(title, window.title)
        ]

    def resolve(self, window, target):
        self._note("resolve", window, target)
        if isinstance(self._target, ResolutionError):
            raise self._target
        return self._target

    def read(self, target):
        self._note("read", target)
        return {
            "value": self._value,
            "enabled": True,
            "toggled": None,
            "offscreen": False,
            "bounds": list(target.bounds),
        }

    def foreground(self):
        self._note("foreground")
        if self._foreground is None:
            raise HostError("WINDOW_NOT_FOUND", "no window holds the foreground")
        return self._foreground

    def focus(self, window, target):
        self._note("focus", window, target)
        if self._focus_error is not None:
            raise self._focus_error

    def invoke(self, target):
        self._note("invoke", target)
        self._raise()
        return self._invoked

    def set_value(self, target, text):
        self._note("set_value", target, text)
        self._raise()
        return self._invoked

    def click(self, point, *, button="left", count=1):
        self._note("click", point, button, count)
        self._raise()

    def move(self, point):
        self._note("move", point)
        self._raise()

    def drag(self, start, end, *, steps=20, ms=400):
        self._note("drag", start, end)
        self._raise()

    def type_text(self, text):
        self._note("type_text", text)
        self._raise()

    def keypress(self, keys):
        self._note("keypress", keys)
        self._raise()

    def scroll(self, point, delta):
        self._note("scroll", point, delta)
        self._raise()

    def launch(self, command, *, wait_window_ms=10000):
        self._note("launch", tuple(command))
        self._raise()
        return self._windows[0] if self._windows else NOTEPAD

    def inspect(self, window, *, depth=6, max_nodes=400):
        self._note("inspect", window, depth)
        return {"window": None, "nodes": [], "truncated": False}

    def _raise(self) -> None:
        if self._fail is not None:
            raise self._fail


class FakeVisual(FakeUia):
    name = "visual-fallback"

    def resolve(self, window, target):
        self._note("resolve", window, target)
        return ResolvedTarget(
            "Unknown",
            target.name or "explicit bounds",
            None,
            None,
            tuple(target.bounds),
            None,
            self.name,
        )


class FakePresentation:
    """The overlay and the grabber, with every call kept for assertion."""

    def __init__(self, log: list[str], *, monitors=None) -> None:
        self.log = log
        self.labels: list[str] = []
        self.badges: list[str] = []
        self.anchors: list = []
        self.regions: list = []
        self.shots = 0
        self._monitors = monitors or {}

    def screenshot(self, *, bounds=None):
        self.log.append("screenshot")
        self.shots += 1
        self.regions.append(tuple(bounds) if bounds else None)
        data = PNG + str(self.shots).encode("ascii")
        return {
            "png": data,
            "sha256": "0" * 64,
            "bounds": list(bounds or (0, 0, 800, 600)),
            "bytes": len(data),
        }

    def monitor(self, handle: int = 0):
        self.log.append("monitor")
        bounds = self._monitors.get(handle, PRIMARY if handle == 0 else SECOND)
        return {"bounds": tuple(bounds), "primary": handle == 0}

    def highlight(self, bounds, *, label, kind="click", ms=600, color="#FF7A00"):
        self.log.append("highlight")
        self.labels.append(label)

    def badge(self, text, *, ms=900, kind="info", anchor=None):
        self.log.append("badge")
        self.badges.append(text)
        self.anchors.append(tuple(anchor) if anchor is not None else None)

    def clear(self):
        self.log.append("clear")


class RuntimeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.trace = Path(self._temp.name) / "trace"
        self.log: list[str] = []
        self.presentation = FakePresentation(self.log)

    def store_lines(self) -> list[dict]:
        path = self.trace / "actions.ndjson"
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def runtime(self, uia=None, *, mode="demo", allowed=("notepad",), **policy):
        self.uia = uia if uia is not None else FakeUia(self.log)
        self.visual = FakeVisual(self.log)
        built = ComputerUseRuntime(
            trace_dir=self.trace,
            policy=RuntimePolicy(
                allowed_processes=tuple(allowed),
                mode=mode,
                highlight_ms=policy.pop("highlight_ms", 0),
                **policy,
            ),
            uia=self.uia,
            visual=self.visual,
            presentation=self.presentation,
        )
        self.addCleanup(built.close)
        return built


class DemoOrderTests(RuntimeTestCase):
    def test_a_demo_click_highlights_first_and_badges_after_the_verification(
        self,
    ) -> None:
        runtime = self.runtime()
        receipt = runtime.execute(CLICK)
        self.assertEqual(receipt["status"], "succeeded")
        self.assertLess(self.log.index("highlight"), self.log.index("click"))
        last_check = len(self.log) - 1 - self.log[::-1].index("windows")
        self.assertGreater(self.log.index("badge"), last_check)
        self.assertEqual(self.log[-1], "clear")
        self.assertIn("s-0001", self.presentation.labels[0])
        self.assertIn("CLICK", self.presentation.labels[0])
        self.assertIn("Save", self.presentation.labels[0])
        self.assertTrue(self.presentation.badges[0].startswith("VERIFY"))

    def test_demo_mode_moves_the_pointer_to_the_centre_before_it_clicks(self) -> None:
        runtime = self.runtime()
        runtime.execute(CLICK)
        moves = [call for call in self.uia.calls if call[0] == "move"]
        self.assertEqual(moves[0][1][0], SAVE.center)
        self.assertLess(self.log.index("move"), self.log.index("click"))


class FastModeTests(RuntimeTestCase):
    def test_fast_mode_invokes_and_never_moves_the_pointer(self) -> None:
        runtime = self.runtime(FakeUia(self.log, invoked=True), mode="fast")
        receipt = runtime.execute(CLICK)
        self.assertEqual(receipt["status"], "succeeded")
        self.assertIn("invoke", self.log)
        self.assertNotIn("move", self.log)
        self.assertNotIn("click", self.log)
        self.assertNotIn("highlight", self.log)
        self.assertIsNone(receipt["execution"]["point"])

    def test_fast_mode_falls_back_to_the_pointer_when_invoke_says_no(self) -> None:
        runtime = self.runtime(FakeUia(self.log, invoked=False), mode="fast")
        receipt = runtime.execute(CLICK)
        self.assertEqual(receipt["status"], "succeeded")
        self.assertIn("click", self.log)
        self.assertEqual(receipt["execution"]["point"], list(SAVE.center))


class SetValueTests(RuntimeTestCase):
    SET = {
        "intent": "name the file",
        "application": "notepad",
        "target": {"automationId": "1001", "className": "Edit"},
        "action": {"type": "set_value", "text": "C:\\Temp\\demo.txt"},
    }

    def test_the_value_pattern_is_preferred_when_the_element_has_one(self) -> None:
        runtime = self.runtime(FakeUia(self.log, invoked=True), mode="fast")
        self.assertEqual(runtime.execute(self.SET)["status"], "succeeded")
        self.assertIn("set_value", self.log)
        self.assertNotIn("type_text", self.log)

    def test_without_a_value_pattern_the_field_is_cleared_before_it_is_typed(
        self,
    ) -> None:
        runtime = self.runtime(FakeUia(self.log, invoked=False), mode="fast")
        self.assertEqual(runtime.execute(self.SET)["status"], "succeeded")
        chords = [call[1][0] for call in self.uia.calls if call[0] == "keypress"]
        self.assertEqual(chords, ["ctrl+a", "delete"])
        self.assertLess(self.log.index("click"), self.log.index("type_text"))
        typed = [call for call in self.uia.calls if call[0] == "type_text"]
        self.assertEqual(typed[0][1][0], "C:\\Temp\\demo.txt")

    def test_an_empty_set_value_clears_without_typing(self) -> None:
        runtime = self.runtime(FakeUia(self.log, invoked=False), mode="fast")
        payload = dict(self.SET, action={"type": "set_value", "text": ""})
        self.assertEqual(runtime.execute(payload)["status"], "succeeded")
        self.assertNotIn("type_text", self.log)


class RefusalTests(RuntimeTestCase):
    def test_an_application_outside_the_policy_is_refused(self) -> None:
        runtime = self.runtime(allowed=("explorer",))
        receipt = runtime.execute(CLICK)
        self.assertEqual(receipt["status"], "refused")
        self.assertEqual(receipt["refusal"]["code"], "APP_NOT_ALLOWED")
        self.assertNotIn("windows", self.log)

    def test_a_launch_is_allow_listed_by_its_command_not_its_application(
        self,
    ) -> None:
        runtime = self.runtime(allowed=("notepad",))
        receipt = runtime.execute(
            {
                "intent": "open the file browser",
                "application": "notepad",
                "action": {"type": "launch", "command": ["C:\\Windows\\explorer.EXE"]},
            }
        )
        self.assertEqual(receipt["status"], "refused")
        self.assertEqual(receipt["refusal"]["code"], "APP_NOT_ALLOWED")
        self.assertIn("explorer", receipt["refusal"]["message"])

    def test_no_window_is_a_refusal(self) -> None:
        runtime = self.runtime(FakeUia(self.log, windows=()))
        receipt = runtime.execute(CLICK)
        self.assertEqual(receipt["status"], "refused")
        self.assertEqual(receipt["refusal"]["code"], "WINDOW_NOT_FOUND")

    def test_an_unresolved_target_is_a_refusal_that_keeps_the_code(self) -> None:
        error = ResolutionError("TARGET_UNRESOLVED", "no element matched the name")
        runtime = self.runtime(FakeUia(self.log, target=error))
        receipt = runtime.execute(CLICK)
        self.assertEqual(receipt["status"], "refused")
        self.assertEqual(receipt["refusal"]["code"], "TARGET_UNRESOLVED")
        self.assertEqual(receipt["backend"], "none")

    def test_an_ambiguous_target_says_what_it_saw(self) -> None:
        error = ResolutionError(
            "TARGET_AMBIGUOUS",
            "3 elements matched the name",
            [{"name": "Save"}, {"name": "Save as"}],
        )
        runtime = self.runtime(FakeUia(self.log, target=error))
        receipt = runtime.execute(CLICK)
        self.assertEqual(receipt["status"], "refused")
        self.assertEqual(receipt["refusal"]["code"], "TARGET_AMBIGUOUS")
        self.assertIn("3 elements matched", receipt["refusal"]["message"])
        self.assertEqual(len(receipt["refusal"]["candidates"]), 2)

    def test_a_lost_foreground_refuses_before_any_input(self) -> None:
        other = WindowInfo(9, "Some other app", 7, "other", (0, 0, 10, 10))
        runtime = self.runtime(FakeUia(self.log, foreground=other))
        receipt = runtime.execute(CLICK)
        self.assertEqual(receipt["status"], "refused")
        self.assertEqual(receipt["refusal"]["code"], "FOCUS_LOST")
        self.assertNotIn("click", self.log)

    def test_a_refused_focus_is_a_focus_lost_receipt(self) -> None:
        runtime = self.runtime(
            FakeUia(self.log, focus_error=FocusError("it would not come forward"))
        )
        receipt = runtime.execute(CLICK)
        self.assertEqual(receipt["status"], "refused")
        self.assertEqual(receipt["refusal"]["code"], "FOCUS_LOST")

    def test_the_focus_guard_can_be_switched_off(self) -> None:
        other = WindowInfo(9, "Some other app", 7, "other", (0, 0, 10, 10))
        runtime = self.runtime(FakeUia(self.log, foreground=other), focus_guard=False)
        self.assertEqual(runtime.execute(CLICK)["status"], "succeeded")
        self.assertNotIn("foreground", self.log)

    def test_a_dead_host_fails_the_action_and_forgets_its_targets(self) -> None:
        uia = FakeUia(self.log, fail=HostError("HOST_ERROR", "the host died"))
        runtime = self.runtime(uia)
        receipt = runtime.execute(CLICK)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["refusal"]["code"], "HOST_ERROR")
        self.assertIn("the host died", receipt["refusal"]["message"])
        self.assertEqual(runtime.resolved_count, 0)

    def test_a_missing_backend_is_a_refusal(self) -> None:
        uia = FakeUia(self.log, fail=HostError("BACKEND_UNAVAILABLE", "no PowerShell"))
        receipt = self.runtime(uia).execute(CLICK)
        self.assertEqual(receipt["status"], "refused")
        self.assertEqual(receipt["refusal"]["code"], "BACKEND_UNAVAILABLE")

    def test_an_unexpected_fault_below_this_package_is_still_a_receipt(self) -> None:
        class Broken(FakeUia):
            def click(self, point, *, button="left", count=1):
                raise OSError("the display adapter went away")

        runtime = self.runtime(Broken(self.log))
        receipt = runtime.execute(CLICK)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["refusal"]["code"], "HOST_ERROR")
        self.assertIn("OSError", receipt["refusal"]["message"])
        self.assertIn("display adapter", receipt["refusal"]["message"])
        self.assertEqual(runtime.resolved_count, 0)
        self.assertEqual(len(self.store_lines()), 1)

    def test_an_invalid_action_is_the_only_thing_execute_raises(self) -> None:
        runtime = self.runtime()
        with self.assertRaises(ContractError):
            runtime.execute({"intent": "nothing", "application": "notepad"})


class FallbackTests(RuntimeTestCase):
    def test_a_visual_fallback_target_says_so_in_the_receipt(self) -> None:
        runtime = self.runtime()
        receipt = runtime.execute(
            {
                "intent": "press whatever is there",
                "application": "notepad",
                "target": {"bounds": [10, 20, 30, 40], "backend": "visual-fallback"},
                "action": {"type": "click"},
            }
        )
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["backend"], "visual-fallback")
        self.assertIs(receipt["fallback"], True)
        self.assertEqual(receipt["execution"]["point"], [20, 30])


class VerificationTests(RuntimeTestCase):
    def test_an_unmet_expectation_fails_the_action(self) -> None:
        runtime = self.runtime(FakeUia(self.log, windows=(NOTEPAD,)))
        receipt = runtime.execute(
            dict(
                CLICK,
                verification={
                    "expect": "window",
                    "title": "Save As",
                    "timeout_ms": 0,
                },
            )
        )
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["verification"]["status"], "failed")
        self.assertEqual(receipt["refusal"]["code"], "VERIFY_FAILED")
        self.assertTrue(self.presentation.badges[0].startswith("VERIFY"))

    def test_an_element_value_expectation_is_a_contains(self) -> None:
        runtime = self.runtime(FakeUia(self.log, value="MonkeyHub computer-use demo"))
        receipt = runtime.execute(
            dict(
                CLICK,
                verification={
                    "expect": "element",
                    "state": {"value": "computer-use"},
                    "timeout_ms": 0,
                },
            )
        )
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["verification"]["status"], "passed")

    def test_an_element_expectation_falls_back_to_the_actions_own_target(self) -> None:
        runtime = self.runtime()
        receipt = runtime.execute(
            dict(CLICK, verification={"expect": "element", "timeout_ms": 0})
        )
        self.assertEqual(receipt["verification"]["status"], "passed")

    def test_an_absent_window_holds_when_nothing_matches(self) -> None:
        runtime = self.runtime(FakeUia(self.log, windows=(NOTEPAD,)))
        receipt = runtime.execute(
            dict(
                CLICK,
                verification={"expect": "absent", "title": "Save As", "timeout_ms": 0},
            )
        )
        self.assertEqual(receipt["verification"]["status"], "passed")

    def test_a_file_expectation_watches_the_filesystem(self) -> None:
        runtime = self.runtime()
        wanted = Path(self._temp.name) / "saved.txt"
        expectation = {"expect": "file", "path": str(wanted), "timeout_ms": 0}
        receipt = runtime.execute(dict(CLICK, verification=expectation))
        self.assertEqual(receipt["verification"]["status"], "failed")
        wanted.write_text("here", encoding="utf-8")
        again = runtime.execute(dict(CLICK, verification=expectation))
        self.assertEqual(again["verification"]["status"], "passed")

    def test_an_action_without_a_verification_records_none(self) -> None:
        runtime = self.runtime()
        receipt = runtime.execute(
            {
                "intent": "press save",
                "application": "notepad",
                "target": {"controlType": "Button", "name": "Save"},
                "action": {"type": "click"},
            }
        )
        self.assertIsNone(receipt["verification"])
        self.assertEqual(receipt["status"], "succeeded")


class TraceTests(RuntimeTestCase):
    def test_every_action_is_appended_with_a_digest_over_the_rest(self) -> None:
        runtime = self.runtime()
        runtime.execute(CLICK)
        runtime.execute(dict(CLICK, intent="press it again"))
        lines = [
            json.loads(line)
            for line in (self.trace / "actions.ndjson")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual([line["step_id"] for line in lines], ["s-0001", "s-0002"])
        for line in lines:
            body = {key: value for key, value in line.items() if key != "digest"}
            self.assertEqual(line["digest"], canonical_digest(body))

    def test_a_capture_is_stored_and_named_in_the_receipt(self) -> None:
        runtime = self.runtime()
        receipt = runtime.execute(dict(CLICK, capture=True))
        before = receipt["screenshots"]["before"]
        after = receipt["screenshots"]["after"]
        self.assertTrue(before.startswith("shots/"))
        self.assertTrue(after.startswith("shots/"))
        self.assertTrue((self.trace / before).is_file())
        self.assertTrue((self.trace / after).is_file())

    def test_a_capture_without_a_recording_follows_the_window_monitor(self) -> None:
        # Nothing is recording, so the only thing deciding what a capture keeps
        # is the window being acted on: the other screen stays out of the trace.
        runtime = self.runtime()
        self.presentation._monitors = {0: PRIMARY, NOTEPAD.handle: SECOND}
        receipt = runtime.execute(dict(CLICK, capture=True))
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(set(self.presentation.regions), {SECOND})

    def test_a_screenshot_action_captures_without_a_target(self) -> None:
        runtime = self.runtime()
        receipt = runtime.execute(
            {
                "intent": "keep the evidence",
                "application": "notepad",
                "action": {"type": "screenshot"},
            }
        )
        self.assertEqual(receipt["status"], "succeeded")
        self.assertTrue(receipt["screenshots"]["after"].startswith("shots/"))


class SecrecyTests(RuntimeTestCase):
    def test_a_sensitive_value_reaches_neither_the_receipt_nor_the_overlay(
        self,
    ) -> None:
        runtime = self.runtime(FakeUia(self.log, invoked=True, value="hunter2"))
        receipt = runtime.execute(
            {
                "intent": "fill the password box",
                "application": "notepad",
                "target": {"controlType": "Edit", "automationId": "1001"},
                "action": {"type": "set_value", "text": "hunter2", "sensitive": True},
                "verification": {
                    "expect": "element",
                    "state": {"value": "hunter2"},
                    "timeout_ms": 0,
                },
            }
        )
        self.assertNotIn("hunter2", json.dumps(receipt))
        self.assertEqual(receipt["action"]["text"], "<redacted 7 chars>")
        for label in self.presentation.labels:
            self.assertNotIn("hunter2", label)
        for badge in self.presentation.badges:
            self.assertNotIn("hunter2", badge)

    def test_a_failed_sensitive_expectation_keeps_the_secret_out_of_the_trace(
        self,
    ) -> None:
        # The element reads back something else, so the refusal has to explain
        # a mismatch without quoting what was expected.
        runtime = self.runtime(FakeUia(self.log, invoked=True, value="something else"))
        receipt = runtime.execute(
            {
                "intent": "fill the password box",
                "application": "notepad",
                "target": {"controlType": "Edit", "automationId": "1001"},
                "action": {"type": "set_value", "text": "hunter2", "sensitive": True},
                "verification": {
                    "expect": "element",
                    "state": {"value": "hunter2"},
                    "timeout_ms": 0,
                },
            }
        )
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["refusal"]["code"], "VERIFY_FAILED")
        self.assertNotIn("hunter2", json.dumps(receipt))
        self.assertNotIn("hunter2", receipt["refusal"]["message"])
        written = (self.trace / "actions.ndjson").read_text(encoding="utf-8")
        self.assertNotIn("hunter2", written)
        for badge in self.presentation.badges:
            self.assertNotIn("hunter2", badge)


class RegionTests(RuntimeTestCase):
    def test_a_recording_starts_on_the_primary_monitor(self) -> None:
        runtime = self.runtime()
        started = runtime.record_start("demo", interval_ms=10000)
        self.assertEqual(started["region"], "window-monitor")
        self.assertEqual(started["bounds"], list(PRIMARY))
        runtime.record_stop()

    def test_the_region_follows_the_monitor_the_window_is_on(self) -> None:
        runtime = self.runtime()
        self.presentation._monitors = {0: PRIMARY, NOTEPAD.handle: SECOND}
        runtime.record_start("demo", interval_ms=10000)
        runtime.execute(CLICK)
        self.assertEqual(runtime.recording_region, SECOND)
        # Every capture after the window was observed asks for that monitor.
        self.assertIn(SECOND, self.presentation.regions)
        manifest = runtime.record_stop()
        self.assertEqual(manifest["region"], "window-monitor")
        frames = [
            json.loads(line)
            for line in (self.trace / "recordings/demo/frames.ndjson")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        # The first frame was taken before any window was known, so it carries
        # the primary monitor: a frame always says what it actually captured.
        self.assertTrue(frames)
        self.assertEqual(frames[0]["bounds"], list(PRIMARY))

    def test_the_virtual_region_captures_everything(self) -> None:
        runtime = self.runtime()
        runtime.record_start("demo", interval_ms=10000, region="virtual")
        runtime.execute(CLICK)
        self.assertEqual(runtime.record_stop()["region"], "virtual")
        self.assertEqual(set(self.presentation.regions), {None})

    def test_an_unknown_region_is_a_mistake_not_a_refusal(self) -> None:
        runtime = self.runtime()
        with self.assertRaises(ValueError):
            runtime.record_start("demo", region="everything")


class BadgeAnchorTests(RuntimeTestCase):
    def test_the_verdict_is_anchored_to_the_target_it_is_about(self) -> None:
        runtime = self.runtime()
        runtime.execute(CLICK)
        self.assertEqual(self.presentation.anchors, [SAVE.bounds])

    def test_without_a_target_the_verdict_is_anchored_to_the_window(self) -> None:
        runtime = self.runtime()
        runtime.execute(
            {
                "intent": "close it",
                "application": "notepad",
                "action": {"type": "keypress", "keys": "ctrl+s"},
                "verification": {
                    "expect": "window",
                    "title": "Notepad",
                    "timeout_ms": 0,
                },
            }
        )
        self.assertEqual(self.presentation.anchors, [NOTEPAD.bounds])


class RecordingTests(RuntimeTestCase):
    def test_a_second_recording_is_refused_while_one_runs(self) -> None:
        runtime = self.runtime()
        started = runtime.record_start("demo", interval_ms=10000)
        self.assertEqual(started["name"], "demo")
        with self.assertRaises(RuntimeRefusal) as caught:
            runtime.record_start("again", interval_ms=10000)
        self.assertEqual(caught.exception.code, "RECORDING_ACTIVE")
        manifest = runtime.record_stop()
        self.assertEqual(manifest["name"], "demo")

    def test_stopping_a_recording_that_never_started_is_refused(self) -> None:
        runtime = self.runtime()
        with self.assertRaises(RuntimeRefusal) as caught:
            runtime.record_stop()
        self.assertEqual(caught.exception.code, "RECORDING_NOT_ACTIVE")

    def test_a_recorded_action_is_listed_in_the_manifest_and_the_timeline(
        self,
    ) -> None:
        runtime = self.runtime()
        runtime.record_start("demo", interval_ms=10000)
        runtime.execute(CLICK)
        manifest = runtime.record_stop()
        self.assertEqual(manifest["actions"], ["s-0001"])
        timeline = [
            json.loads(line)
            for line in (self.trace / "recordings/demo/timeline.ndjson")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        events = [entry["event"] for entry in timeline]
        self.assertIn("target_found", events)
        self.assertIn("click", events)
        self.assertIn("verify", events)
        self.assertTrue(all(entry["step_id"] == "s-0001" for entry in timeline))


class InspectTests(RuntimeTestCase):
    def test_inspect_names_the_window_it_walked(self) -> None:
        runtime = self.runtime()
        answer = runtime.inspect("notepad", depth=3)
        self.assertEqual(answer["application"], "notepad")
        self.assertEqual(answer["windows"][0]["title"], "Untitled - Notepad")
        self.assertIn("nodes", answer)

    def test_inspect_without_a_window_answers_rather_than_raising(self) -> None:
        runtime = self.runtime(FakeUia(self.log, windows=()))
        answer = runtime.inspect("notepad")
        self.assertEqual(answer["windows"], [])
        self.assertEqual(answer["nodes"], [])


if __name__ == "__main__":
    unittest.main()

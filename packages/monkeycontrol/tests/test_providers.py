"""What the providers make of a host's answers, with no host running.

Every case here drives a ``FakeHost`` built from a dict of op -> callable, so
resolution, regex semantics, ambiguity, the visual fallback's refusals and the
screenshot digest are all observable without a Windows desktop.
"""

from __future__ import annotations

import base64
import hashlib
import unittest

from monkeycontrol.contract import ContractError, TargetSpec
from monkeycontrol.host import HostError
from monkeycontrol.providers import (
    FocusError,
    PresentationProvider,
    Provider,
    ResolutionError,
    UiaProvider,
    VisualFallbackProvider,
)
from monkeycontrol.trace import ResolvedTarget, WindowInfo

NOTEPAD = {
    "handle": 4242,
    "title": "Untitled - Notepad",
    "pid": 91,
    "process": "notepad",
    "bounds": [100, 100, 900, 700],
}
EXPLORER = {
    "handle": 77,
    "title": "Documents",
    "pid": 12,
    "process": "explorer",
    "bounds": [0, 0, 640, 480],
}
PNG = b"\x89PNG\r\n\x1a\nfake capture"


def candidate(name: str, **overrides) -> dict:
    payload = {
        "runtime_id": f"42.{len(name)}",
        "controlType": "Button",
        "name": name,
        "automationId": "",
        "className": "Button",
        "bounds": [10, 20, 60, 44],
        "enabled": True,
        "offscreen": False,
        "patterns": ["Invoke"],
    }
    payload.update(overrides)
    return payload


class FakeHost:
    """A host whose every op is a callable, recording what it was asked."""

    def __init__(self, **ops) -> None:
        self.ops = ops
        self.calls: list[tuple[str, dict]] = []

    def request(self, op: str, **args) -> dict:
        self.calls.append((op, args))
        if op not in self.ops:
            raise AssertionError(f"the provider asked for an unexpected op {op!r}")
        handler = self.ops[op]
        return handler(**args) if callable(handler) else handler

    def args(self, op: str) -> dict:
        return next(args for name, args in self.calls if name == op)


class ProtocolTests(unittest.TestCase):
    def test_both_resolving_backends_are_providers(self) -> None:
        uia = UiaProvider(FakeHost())
        for provider, name in (
            (uia, "windows-uia"),
            (VisualFallbackProvider(uia), "visual-fallback"),
        ):
            self.assertIsInstance(provider, Provider)
            self.assertEqual(provider.name, name)


class WindowTests(unittest.TestCase):
    def test_windows_asks_the_host_for_one_process(self) -> None:
        host = FakeHost(windows=lambda **args: {"windows": [NOTEPAD]})
        found = UiaProvider(host).windows("notepad", None)
        self.assertEqual(len(found), 1)
        window = found[0]
        self.assertIsInstance(window, WindowInfo)
        self.assertEqual(window.handle, 4242)
        self.assertEqual(window.pid, 91)
        self.assertEqual(window.process, "notepad")
        self.assertEqual(window.bounds, (100, 100, 900, 700))
        self.assertEqual(host.args("windows")["process"], "notepad")

    def test_the_title_regex_is_pythons_and_never_reaches_the_host(self) -> None:
        host = FakeHost(windows=lambda **args: {"windows": [NOTEPAD, EXPLORER]})
        provider = UiaProvider(host)
        self.assertEqual(
            [window.title for window in provider.windows("notepad", "(?i)untitled")],
            ["Untitled - Notepad"],
        )
        self.assertIsNone(host.args("windows")["title_regex"])
        self.assertEqual(provider.windows("notepad", "nothing here"), [])

    def test_foreground_is_one_window(self) -> None:
        host = FakeHost(foreground=lambda **args: NOTEPAD)
        self.assertEqual(UiaProvider(host).foreground().pid, 91)

    def test_process_name_is_read_from_the_host(self) -> None:
        host = FakeHost(process=lambda **args: {"name": "notepad"})
        self.assertEqual(UiaProvider(host).process_name(91), "notepad")
        self.assertEqual(host.args("process"), {"pid": 91})


class ResolutionTests(unittest.TestCase):
    def provider(self, *candidates) -> tuple[UiaProvider, FakeHost]:
        host = FakeHost(find=lambda **args: {"candidates": list(candidates)})
        return UiaProvider(host), host

    def window(self) -> WindowInfo:
        return WindowInfo(
            handle=4242,
            title="Untitled - Notepad",
            pid=91,
            process="notepad",
            bounds=(100, 100, 900, 700),
        )

    def test_one_candidate_resolves_against_the_uia_backend(self) -> None:
        provider, host = self.provider(candidate("Save"))
        resolved = provider.resolve(self.window(), TargetSpec(name="Save"))
        self.assertIsInstance(resolved, ResolvedTarget)
        self.assertEqual(resolved.backend, "windows-uia")
        self.assertEqual(resolved.name, "Save")
        self.assertEqual(resolved.control_type, "Button")
        self.assertEqual(resolved.runtime_id, "42.4")
        self.assertEqual(resolved.bounds, (10, 20, 60, 44))
        self.assertEqual(resolved.center, (35, 32))
        criteria = host.args("find")["criteria"]
        self.assertEqual(criteria, {"name": "Save"})
        self.assertEqual(host.args("find")["handle"], 4242)

    def test_every_exact_property_travels_as_snake_case_criteria(self) -> None:
        provider, host = self.provider(candidate("Save"))
        provider.resolve(
            self.window(),
            TargetSpec(
                control_type="Button",
                name="Save",
                automation_id="save-button",
                class_name="Button",
            ),
        )
        self.assertEqual(
            host.args("find")["criteria"],
            {
                "control_type": "Button",
                "name": "Save",
                "automation_id": "save-button",
                "class_name": "Button",
            },
        )

    def test_a_name_regex_is_applied_here_and_not_by_the_host(self) -> None:
        provider, host = self.provider(
            candidate("Don't Save"), candidate("Save"), candidate("Cancel")
        )
        resolved = provider.resolve(
            self.window(), TargetSpec(name_regex=r"(?i)don.?t save")
        )
        self.assertEqual(resolved.name, "Don't Save")
        self.assertEqual(host.args("find")["criteria"], {})

    def test_a_name_regex_is_a_case_insensitive_search(self) -> None:
        provider, _ = self.provider(candidate("Text Editor"))
        self.assertEqual(
            provider.resolve(self.window(), TargetSpec(name_regex="editor")).name,
            "Text Editor",
        )

    def test_two_candidates_without_an_index_are_ambiguous(self) -> None:
        provider, _ = self.provider(candidate("Save"), candidate("Save as"))
        with self.assertRaises(ResolutionError) as caught:
            provider.resolve(self.window(), TargetSpec(control_type="Button"))
        self.assertEqual(caught.exception.code, "TARGET_AMBIGUOUS")
        self.assertIn("Save as", str(caught.exception))
        self.assertEqual(len(caught.exception.candidates), 2)

    def test_an_ambiguity_the_host_truncated_says_at_least(self) -> None:
        host = FakeHost(
            find=lambda **args: {
                "candidates": [candidate(f"Row {n}") for n in range(3)],
                "truncated": True,
            }
        )
        with self.assertRaises(ResolutionError) as caught:
            UiaProvider(host).resolve(
                self.window(), TargetSpec(control_type="ListItem")
            )
        self.assertIn("at least 3 elements", str(caught.exception))

    def test_an_ambiguous_message_names_at_most_five_candidates(self) -> None:
        provider, _ = self.provider(*(candidate(f"Row {n}") for n in range(9)))
        with self.assertRaises(ResolutionError) as caught:
            provider.resolve(self.window(), TargetSpec(control_type="ListItem"))
        message = str(caught.exception)
        self.assertIn("Row 4", message)
        self.assertNotIn("Row 5", message)
        self.assertEqual(len(caught.exception.candidates), 9)

    def test_an_index_picks_one_of_several(self) -> None:
        provider, _ = self.provider(candidate("Save"), candidate("Save as"))
        resolved = provider.resolve(
            self.window(), TargetSpec(control_type="Button", index=1)
        )
        self.assertEqual(resolved.name, "Save as")

    def test_an_index_past_the_end_is_unresolved(self) -> None:
        provider, _ = self.provider(candidate("Save"))
        with self.assertRaises(ResolutionError) as caught:
            provider.resolve(self.window(), TargetSpec(control_type="Button", index=3))
        self.assertEqual(caught.exception.code, "TARGET_UNRESOLVED")

    def test_no_candidate_is_unresolved(self) -> None:
        provider, _ = self.provider()
        with self.assertRaises(ResolutionError) as caught:
            provider.resolve(self.window(), TargetSpec(name="Nowhere"))
        self.assertEqual(caught.exception.code, "TARGET_UNRESOLVED")
        self.assertEqual(caught.exception.candidates, [])

    def test_a_pattern_list_the_host_unrolled_is_read_back_as_a_list(self) -> None:
        """PowerShell renders no patterns as ``{}`` and one as a bare string.

        Left alone, a one-pattern element turns ``"Value" in patterns`` into
        substring matching, which quietly says yes to ``RangeValue``.
        """

        for sent, expected in (
            ("RangeValue", ["RangeValue"]),
            ({}, []),
            (None, []),
            (["Invoke", "Value"], ["Invoke", "Value"]),
        ):
            with self.subTest(sent=sent):
                node = candidate("Slider", patterns=sent)
                host = FakeHost(
                    find=lambda **args: {"candidates": [dict(node)]},
                    inspect=lambda **args: {
                        "window": NOTEPAD,
                        "nodes": [dict(node)],
                        "truncated": False,
                    },
                )
                provider = UiaProvider(host)
                tree = provider.inspect(self.window())
                self.assertEqual(tree["nodes"][0]["patterns"], expected)
                with self.assertRaises(ResolutionError) as caught:
                    provider.resolve(
                        self.window(), TargetSpec(control_type="Button", index=9)
                    )
                self.assertEqual(caught.exception.candidates[0]["patterns"], expected)

    def test_an_unrolled_candidate_or_window_list_is_still_a_list(self) -> None:
        one = candidate("Save")
        host = FakeHost(
            find=lambda **args: {"candidates": one},
            windows=lambda **args: {"windows": NOTEPAD},
        )
        provider = UiaProvider(host)
        self.assertEqual(
            provider.resolve(self.window(), TargetSpec(name="Save")).name, "Save"
        )
        self.assertEqual([window.pid for window in provider.windows("notepad")], [91])

    def test_read_is_the_hosts_element_state(self) -> None:
        state = {
            "value": "monkeycontrol",
            "enabled": True,
            "toggled": None,
            "offscreen": False,
            "bounds": [10, 20, 60, 44],
        }
        host = FakeHost(read=lambda **args: state)
        target = ResolvedTarget(
            control_type="Edit",
            name="Text editor",
            automation_id=None,
            class_name="RichEditD2DPT",
            bounds=(10, 20, 60, 44),
            runtime_id="42.7",
            backend="windows-uia",
        )
        self.assertEqual(UiaProvider(host).read(target), state)
        self.assertEqual(host.args("read"), {"runtime_id": "42.7"})


class ActuationTests(unittest.TestCase):
    def target(self) -> ResolvedTarget:
        return ResolvedTarget(
            control_type="Button",
            name="Save",
            automation_id=None,
            class_name="Button",
            bounds=(10, 20, 60, 44),
            runtime_id="42.7",
            backend="windows-uia",
        )

    def test_invoke_says_whether_the_pattern_existed(self) -> None:
        host = FakeHost(invoke=lambda **args: {"invoked": False})
        self.assertFalse(UiaProvider(host).invoke(self.target()))
        self.assertEqual(host.args("invoke"), {"runtime_id": "42.7"})

    def test_set_value_says_whether_the_pattern_existed(self) -> None:
        host = FakeHost(set_value=lambda **args: {"set": True})
        self.assertTrue(UiaProvider(host).set_value(self.target(), "hello"))
        self.assertEqual(
            host.args("set_value"), {"runtime_id": "42.7", "text": "hello"}
        )

    def window(self) -> WindowInfo:
        return WindowInfo(4242, "Untitled - Notepad", 91, "notepad", (0, 0, 10, 10))

    def test_focus_names_the_window_and_the_element(self) -> None:
        host = FakeHost(
            focus=lambda **args: {"foreground": True, "element_focused": True}
        )
        UiaProvider(host).focus(self.window(), self.target())
        self.assertEqual(host.args("focus"), {"handle": 4242, "runtime_id": "42.7"})

    def test_focus_without_an_element_is_the_window_alone(self) -> None:
        host = FakeHost(
            focus=lambda **args: {"foreground": True, "element_focused": None}
        )
        UiaProvider(host).focus(self.window(), None)
        self.assertEqual(host.args("focus"), {"handle": 4242, "runtime_id": None})

    def test_a_window_that_would_not_come_forward_is_raised(self) -> None:
        host = FakeHost(
            focus=lambda **args: {"foreground": False, "element_focused": None}
        )
        with self.assertRaises(FocusError) as caught:
            UiaProvider(host).focus(self.window(), None)
        self.assertEqual(caught.exception.code, "FOCUS_LOST")
        self.assertIn("Untitled - Notepad", str(caught.exception))
        self.assertIn("91", str(caught.exception))

    def test_an_element_that_would_not_take_focus_is_raised(self) -> None:
        host = FakeHost(
            focus=lambda **args: {"foreground": True, "element_focused": False}
        )
        with self.assertRaises(FocusError) as caught:
            UiaProvider(host).focus(self.window(), self.target())
        self.assertEqual(caught.exception.code, "FOCUS_LOST")
        self.assertIn("came forward", str(caught.exception))
        self.assertIn("Save", str(caught.exception))

    def test_a_host_that_says_nothing_about_focus_is_a_lost_focus(self) -> None:
        # An older or partial reply must not read as success.
        host = FakeHost(focus=lambda **args: {})
        with self.assertRaises(FocusError):
            UiaProvider(host).focus(self.window(), None)

    def test_every_input_op_reaches_the_host_verbatim(self) -> None:
        host = FakeHost(
            click=lambda **args: {},
            move=lambda **args: {},
            drag=lambda **args: {},
            type=lambda **args: {},
            keys=lambda **args: {},
            scroll=lambda **args: {},
        )
        provider = UiaProvider(host)
        provider.click((5, 6), button="right", count=2)
        provider.move((7, 8))
        provider.drag((1, 2), (3, 4), steps=5, ms=50)
        provider.type_text("monkeycontrol")
        provider.keypress("ctrl+shift+s")
        provider.scroll((9, 10), -3)
        self.assertEqual(
            host.calls,
            [
                ("click", {"x": 5, "y": 6, "button": "right", "count": 2}),
                ("move", {"x": 7, "y": 8}),
                (
                    "drag",
                    {"x": 1, "y": 2, "to_x": 3, "to_y": 4, "steps": 5, "ms": 50},
                ),
                ("type", {"text": "monkeycontrol"}),
                ("keys", {"chord": "ctrl+shift+s"}),
                ("scroll", {"x": 9, "y": 10, "delta": -3}),
            ],
        )

    def test_launch_returns_the_window_the_host_waited_for(self) -> None:
        host = FakeHost(launch=lambda **args: dict(NOTEPAD, matched_by="pid"))
        window = UiaProvider(host).launch(("notepad.exe", "note.txt"))
        self.assertEqual(window.process, "notepad")
        self.assertEqual(
            host.args("launch"),
            {"command": ["notepad.exe", "note.txt"], "wait_window_ms": 10000},
        )

    def test_inspect_is_the_hosts_tree(self) -> None:
        tree = {"window": NOTEPAD, "nodes": [], "truncated": False}
        host = FakeHost(inspect=lambda **args: tree)
        window = WindowInfo(4242, "Untitled - Notepad", 91, "notepad", (0, 0, 10, 10))
        self.assertEqual(UiaProvider(host).inspect(window, depth=3, max_nodes=50), tree)
        self.assertEqual(
            host.args("inspect"), {"handle": 4242, "depth": 3, "max_nodes": 50}
        )


class VisualFallbackTests(unittest.TestCase):
    def window(self) -> WindowInfo:
        return WindowInfo(4242, "Untitled - Notepad", 91, "notepad", (0, 0, 800, 600))

    def provider(self) -> tuple[VisualFallbackProvider, FakeHost]:
        host = FakeHost(
            windows=lambda **args: {"windows": [NOTEPAD]},
            find=lambda **args: {"candidates": []},
        )
        return VisualFallbackProvider(UiaProvider(host)), host

    def test_the_name_says_the_resolution_was_not_semantic(self) -> None:
        provider, _ = self.provider()
        self.assertEqual(provider.name, "visual-fallback")

    def test_windows_are_the_uia_providers(self) -> None:
        provider, host = self.provider()
        self.assertEqual(
            [window.pid for window in provider.windows("notepad", None)], [91]
        )
        self.assertEqual(host.args("windows")["process"], "notepad")

    def test_explicit_bounds_become_the_resolution(self) -> None:
        provider, host = self.provider()
        resolved = provider.resolve(
            self.window(),
            TargetSpec(bounds=(10, 20, 110, 60), backend="visual-fallback"),
        )
        self.assertEqual(resolved.backend, "visual-fallback")
        self.assertEqual(resolved.control_type, "Unknown")
        self.assertEqual(resolved.name, "explicit bounds")
        self.assertIsNone(resolved.runtime_id)
        self.assertEqual(resolved.bounds, (10, 20, 110, 60))
        self.assertEqual(resolved.center, (60, 40))
        self.assertEqual(host.calls, [])

    def test_a_named_fallback_keeps_the_callers_name(self) -> None:
        provider, _ = self.provider()
        resolved = provider.resolve(
            self.window(),
            TargetSpec(
                name="Toolbar", bounds=(0, 0, 10, 10), backend="visual-fallback"
            ),
        )
        self.assertEqual(resolved.name, "Toolbar")

    def test_bounds_are_required(self) -> None:
        provider, _ = self.provider()
        with self.assertRaises(ContractError) as caught:
            provider.resolve(self.window(), TargetSpec(name="Save"))
        self.assertIn("bounds", str(caught.exception))

    def test_the_backend_must_be_declared(self) -> None:
        provider, _ = self.provider()
        with self.assertRaises(ContractError) as caught:
            provider.resolve(self.window(), TargetSpec(bounds=(0, 0, 10, 10)))
        self.assertIn("visual-fallback", str(caught.exception))

    def test_read_reports_only_what_bounds_can_say(self) -> None:
        provider, host = self.provider()
        resolved = provider.resolve(
            self.window(),
            TargetSpec(bounds=(10, 20, 110, 60), backend="visual-fallback"),
        )
        self.assertEqual(
            provider.read(resolved),
            {
                "value": None,
                "enabled": True,
                "toggled": None,
                "offscreen": False,
                "bounds": [10, 20, 110, 60],
            },
        )
        self.assertEqual(host.calls, [])


class PresentationTests(unittest.TestCase):
    def host(self, **overrides) -> FakeHost:
        payload = {
            "png_base64": base64.b64encode(PNG).decode("ascii"),
            "sha256": hashlib.sha256(PNG).hexdigest(),
            "bounds": [0, 0, 1920, 1080],
            "bytes": len(PNG),
        }
        payload.update(overrides)
        return FakeHost(
            screenshot=lambda **args: payload,
            highlight=lambda **args: {},
            badge=lambda **args: {"placed": [10, 80], "excluded_from_capture": True},
            clear=lambda **args: {},
        )

    def test_a_screenshot_arrives_as_bytes_and_writes_nothing(self) -> None:
        host = self.host()
        shot = PresentationProvider(host).screenshot()
        self.assertEqual(shot["png"], PNG)
        self.assertEqual(shot["sha256"], hashlib.sha256(PNG).hexdigest())
        self.assertEqual(shot["bytes"], len(PNG))
        self.assertEqual(shot["bounds"], [0, 0, 1920, 1080])
        self.assertEqual(host.args("screenshot"), {"bounds": None})

    def test_a_region_is_asked_for_explicitly(self) -> None:
        host = self.host(bounds=[10, 10, 110, 110])
        PresentationProvider(host).screenshot(bounds=(10, 10, 110, 110))
        self.assertEqual(host.args("screenshot"), {"bounds": [10, 10, 110, 110]})

    def test_a_digest_that_does_not_match_the_bytes_is_refused(self) -> None:
        host = self.host(sha256=hashlib.sha256(b"something else").hexdigest())
        with self.assertRaises(HostError) as caught:
            PresentationProvider(host).screenshot()
        self.assertEqual(caught.exception.code, "HOST_ERROR")

    def test_the_overlay_ops_carry_their_defaults(self) -> None:
        host = self.host()
        provider = PresentationProvider(host)
        provider.highlight((1, 2, 3, 4), label="Save")
        provider.badge("done", kind="ok")
        provider.clear()
        self.assertEqual(
            host.calls[0],
            (
                "highlight",
                {
                    "bounds": [1, 2, 3, 4],
                    "label": "Save",
                    "kind": "click",
                    "ms": 600,
                    "color": "#FF7A00",
                },
            ),
        )
        self.assertEqual(
            host.calls[1],
            ("badge", {"text": "done", "ms": 900, "kind": "ok", "anchor": None}),
        )
        self.assertEqual(host.calls[2], ("clear", {}))

    def test_a_badge_carries_the_rectangle_it_is_about(self) -> None:
        host = self.host()
        reply = PresentationProvider(host).badge(
            "VERIFY OK window Save As", kind="ok", anchor=(10, 20, 110, 60)
        )
        self.assertEqual(host.args("badge")["anchor"], [10, 20, 110, 60])
        # The overlay is excluded from every capture, so the reply is the only
        # way a caller can tell where the chip went.
        self.assertEqual(reply["placed"], [10, 80])

    def test_a_monitor_is_read_back_as_bounds_and_a_primary_flag(self) -> None:
        host = FakeHost(
            monitor=lambda **args: {"bounds": [-2560, 0, 0, 1600], "primary": False}
        )
        found = PresentationProvider(host).monitor(4242)
        self.assertEqual(found, {"bounds": (-2560, 0, 0, 1600), "primary": False})
        self.assertEqual(host.args("monitor"), {"handle": 4242})
        with self.assertRaises(HostError):
            PresentationProvider(FakeHost(monitor=lambda **args: {})).monitor()

    def test_an_unknown_badge_kind_is_refused_before_the_host(self) -> None:
        host = self.host()
        with self.assertRaises(ContractError):
            PresentationProvider(host).badge("done", kind="shout")
        self.assertEqual(host.calls, [])


if __name__ == "__main__":
    unittest.main()

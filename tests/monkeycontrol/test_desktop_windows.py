"""The two hosts against the real Windows desktop, opt in only.

Nothing here runs unless ``MONKEYCONTROL_DESKTOP_TESTS=1`` on Windows, because
it moves the mouse, presses keys and closes a window on whatever machine it
runs on. It touches one application it started itself and refuses to send a
closing chord unless that application is the one in the foreground.

    MONKEYCONTROL_DESKTOP_TESTS=1 python -m unittest \
        tests.monkeycontrol.test_desktop_windows -v
"""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from monkeycontrol.contract import TargetSpec
from monkeycontrol.host import (
    EXECUTION_HOST,
    PRESENTATION_HOST,
    HostError,
    HostProcess,
)
from monkeycontrol.providers import (
    PresentationProvider,
    ResolutionError,
    UiaProvider,
    VisualFallbackProvider,
)

DESKTOP = os.name == "nt" and os.environ.get("MONKEYCONTROL_DESKTOP_TESTS") == "1"
REASON = "set MONKEYCONTROL_DESKTOP_TESTS=1 on Windows to drive the real desktop"
#: The English and Chinese spellings of the button that discards a Notepad tab.
DISCARD = r"(?i)don.?t save|\u4e0d\u4fdd\u5b58|\u4e0d\u5b58\u6a94"
TEXT = "monkeycontrol"


def settle(seconds: float = 0.4) -> None:
    time.sleep(seconds)


def until(predicate, timeout: float = 8.0, step: float = 0.25):
    """Poll ``predicate`` until it answers truthily, or give up."""

    deadline = time.monotonic() + timeout
    answer = predicate()
    while not answer and time.monotonic() < deadline:
        time.sleep(step)
        answer = predicate()
    return answer


class DesktopHostTestCase(unittest.TestCase):
    """Both hosts, started once for the class."""

    execution: HostProcess
    presentation: HostProcess

    @classmethod
    def setUpClass(cls) -> None:
        cls.execution = HostProcess(EXECUTION_HOST, apartment="mta", timeout_s=40.0)
        cls.addClassCleanup(cls.execution.stop)
        cls.execution_hello = cls.execution.start()
        cls.presentation = HostProcess(
            PRESENTATION_HOST, apartment="sta", timeout_s=40.0
        )
        cls.addClassCleanup(cls.presentation.stop)
        cls.presentation_hello = cls.presentation.start()
        cls.uia = UiaProvider(cls.execution)
        cls.overlay = PresentationProvider(cls.presentation)

    def foreground_pid(self) -> int:
        """Who would receive input right now, or -1 when nothing titled would."""

        try:
            return self.uia.foreground().pid
        except HostError:
            return -1


@unittest.skipUnless(DESKTOP, REASON)
class HostGreetingTests(DesktopHostTestCase):
    def test_both_hosts_are_dpi_aware_and_say_which_screen_they_see(self) -> None:
        for hello, name in (
            (self.execution_hello, "execution"),
            (self.presentation_hello, "presentation"),
        ):
            self.assertEqual(hello["host"], name)
            self.assertTrue(hello["dpi_aware"], f"{name} host is not DPI aware")
            width, height = hello["screen"]
            self.assertGreater(width, 0)
            self.assertGreater(height, 0)
        self.assertEqual(
            self.execution_hello["screen"], self.presentation_hello["screen"]
        )

    def test_the_foreground_window_names_its_own_process(self) -> None:
        window = self.uia.foreground()
        self.assertGreater(window.handle, 0)
        self.assertGreater(window.pid, 0)
        self.assertTrue(window.process)
        self.assertEqual(
            self.uia.process_name(window.pid).lower(), window.process.lower()
        )
        if window.title:
            # An untitled foreground window is still an answer, but it is not
            # one of the top-level windows an application is listed by.
            self.assertIn(
                window.handle,
                [found.handle for found in self.uia.windows(window.process)],
            )


@unittest.skipUnless(DESKTOP, REASON)
class PresentationTests(DesktopHostTestCase):
    def test_a_capture_is_png_bytes_this_process_never_wrote(self) -> None:
        shot = self.overlay.screenshot(bounds=(0, 0, 400, 300))
        self.assertTrue(shot["png"].startswith(b"\x89PNG"))
        self.assertEqual(shot["bytes"], len(shot["png"]))
        self.assertEqual(shot["bounds"], [0, 0, 400, 300])
        self.assertEqual(len(shot["sha256"]), 64)

    def test_a_full_screen_capture_covers_the_virtual_screen(self) -> None:
        shot = self.overlay.screenshot()
        self.assertTrue(shot["png"].startswith(b"\x89PNG"))
        left, top, right, bottom = shot["bounds"]
        self.assertGreater(right - left, 0)
        self.assertGreater(bottom - top, 0)

    def test_the_overlay_draws_and_comes_down_again(self) -> None:
        self.overlay.highlight(
            (200, 200, 640, 420), label="monkeycontrol self test", ms=250
        )
        self.overlay.badge("monkeycontrol self test", kind="ok", ms=250)
        self.overlay.clear()


@unittest.skipUnless(DESKTOP, REASON)
class NotepadTests(DesktopHostTestCase):
    """One launched Notepad, driven end to end and closed again.

    The test refuses to run at all when Notepad is already open, so it can
    never type into, clear or close a window somebody else was using.
    """

    def setUp(self) -> None:
        if self.uia.windows("notepad"):
            self.skipTest(
                "Notepad is already open; this test never drives one it did not start"
            )
        self._temp = TemporaryDirectory(prefix="monkeycontrol-")
        self.addCleanup(self._temp.cleanup)
        self.note = Path(self._temp.name) / "monkeycontrol-desktop-test.txt"
        self.note.write_text("seed\n", encoding="utf-8")
        self.window = self.uia.launch(
            ("notepad.exe", str(self.note)), wait_window_ms=20000
        )
        self.addCleanup(self.close_notepad)

    def close_notepad(self) -> None:
        """Leave no window of ours behind, and never close somebody else's."""

        for _ in range(3):
            live = [w for w in self.uia.windows("notepad") if w.pid == self.window.pid]
            if not live:
                return
            if self.foreground_pid() != self.window.pid:
                self.uia.focus(live[0], None)
                settle()
            if self.foreground_pid() != self.window.pid:
                self.fail("Notepad would not come forward, so alt+f4 was not sent")
            self.uia.keypress("alt+f4")
            settle(1.0)
            self.discard_changes()

    def discard_changes(self) -> None:
        """Answer a save prompt, if this build of Notepad shows one."""

        for window in self.uia.windows("notepad"):
            if window.pid != self.window.pid:
                continue
            try:
                button = self.uia.resolve(window, TargetSpec(name_regex=DISCARD))
            except ResolutionError:
                continue
            self.uia.invoke(button)
            settle(0.8)

    def named_window(self):
        """The Notepad window once our own file is the tab it is showing."""

        def titled():
            for window in self.uia.windows("notepad"):
                if window.pid == self.window.pid and window.title.startswith(
                    self.note.stem
                ):
                    return window
            return None

        window = until(titled, timeout=15.0)
        self.assertIsNotNone(window, "Notepad never showed the file it was given")
        return window

    def document_of(self, window):
        """The text control, named the way this build of Notepad exposes it."""

        tree = self.uia.inspect(window, depth=8, max_nodes=200)
        nodes = [
            node
            for node in tree["nodes"]
            if node["controlType"] in ("Document", "Edit")
            and ("Value" in node["patterns"] or "Text" in node["patterns"])
        ]
        self.assertTrue(nodes, "no Document or Edit control is in the Notepad tree")
        node = nodes[0]
        target = TargetSpec(
            control_type=node["controlType"], class_name=node["className"]
        )
        document = self.uia.resolve(window, target)
        self.assertEqual(document.runtime_id, node["runtime_id"])
        self.assertEqual(document.backend, "windows-uia")
        return document

    def test_notepad_is_launched_resolved_typed_into_shown_and_closed(self) -> None:
        self.assertEqual(self.window.process.lower(), "notepad")
        window = self.named_window()
        document = self.document_of(window)
        self.assertGreater(document.bounds[2], document.bounds[0])

        # Focus first, always: input goes to the foreground window, not to the
        # element that was resolved.
        self.uia.focus(window, document)
        settle()
        self.assertEqual(self.foreground_pid(), window.pid)

        self.uia.type_text(TEXT)
        typed = until(lambda: TEXT in (self.uia.read(document)["value"] or ""))
        self.assertTrue(typed, f"{TEXT!r} never reached the document")
        state = self.uia.read(document)
        self.assertTrue(state["enabled"])
        self.assertFalse(state["offscreen"])

        # The overlay and a capture of the window that is really on screen.
        self.overlay.highlight(
            document.bounds, label="Notepad document", kind="type", ms=250
        )
        shot = self.overlay.screenshot(bounds=window.bounds)
        self.assertTrue(shot["png"].startswith(b"\x89PNG"))
        self.overlay.badge("monkeycontrol typed into Notepad", kind="ok", ms=250)
        self.overlay.clear()

        # Pointer ops, aimed only inside the window this test opened.
        self.assertEqual(self.foreground_pid(), window.pid)
        left, top, right, bottom = document.bounds
        self.uia.move(document.center)
        self.uia.click(document.center)
        self.uia.drag((left + 20, top + 20), (left + 120, top + 20), steps=6, ms=90)
        self.uia.scroll(document.center, -1)
        settle(0.2)

        if self.uia.set_value(document, f"{TEXT} set"):
            written = until(
                lambda: (self.uia.read(document)["value"] or "").startswith(
                    f"{TEXT} set"
                )
            )
            self.assertTrue(written, "ValuePattern accepted a value it did not keep")

        self.uia.keypress("ctrl+a")
        settle(0.2)
        self.uia.keypress("delete")
        cleared = until(lambda: not (self.uia.read(document)["value"] or "").strip())
        self.assertTrue(cleared, "ctrl+a then delete did not empty the document")

        # The file this test made is its own; nothing was saved over it.
        self.assertEqual(self.note.read_text(encoding="utf-8"), "seed\n")

        self.close_notepad()
        gone = until(
            lambda: not [
                w for w in self.uia.windows("notepad") if w.pid == self.window.pid
            ],
            timeout=10.0,
        )
        self.assertTrue(gone, "the Notepad this test opened is still on screen")

    def test_the_visual_fallback_resolves_the_window_without_a_property(self) -> None:
        window = self.named_window()
        fallback = VisualFallbackProvider(self.uia)
        resolved = fallback.resolve(
            window,
            TargetSpec(
                name="Notepad client area",
                bounds=window.bounds,
                backend="visual-fallback",
            ),
        )
        self.assertEqual(resolved.backend, "visual-fallback")
        self.assertIsNone(resolved.runtime_id)
        self.assertEqual(resolved.bounds, window.bounds)
        self.assertEqual(
            fallback.read(resolved)["bounds"], list(window.bounds)
        )
        self.assertIn(
            window.handle, [found.handle for found in fallback.windows("notepad", None)]
        )

    def test_an_unresolvable_target_is_refused_rather_than_guessed(self) -> None:
        window = self.named_window()
        with self.assertRaises(ResolutionError) as caught:
            self.uia.resolve(
                window, TargetSpec(automation_id="no-such-automation-id-here")
            )
        self.assertEqual(caught.exception.code, "TARGET_UNRESOLVED")


if __name__ == "__main__":
    unittest.main()

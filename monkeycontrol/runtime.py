"""One action in, one receipt out: validate, show, act, verify, record.

This is the only place in MonkeyControl that decides anything. It holds the
allow-list a caller handed it, picks the backend a target declared, refuses
rather than guesses, and writes exactly one ComputerActionReceipt@1 per
attempt -- including the attempts it refused, because a refusal that leaves no
trace is indistinguishable from an action nobody asked for. It owns no default
trace directory, no permission to run and no project state: MonkeyHub composes
it and decides when it may act.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from .contract import (
    REFUSALS,
    VISUAL_FALLBACK,
    Action,
    ContractError,
    TargetSpec,
    redacted_text,
    validate_action,
)
from .host import EXECUTION_HOST, PRESENTATION_HOST, HostError, HostProcess
from .providers import (
    FocusError,
    PresentationProvider,
    Provider,
    ResolutionError,
    UiaProvider,
    VisualFallbackProvider,
)
from .record import Recorder
from .store import ActionTraceStore
from .trace import ResolvedTarget, WindowInfo, build_receipt, window_payload
from .verify import summary, target_name, verify_action

MODES = ("fast", "demo")
#: How many clicks each pointer action sends.
POINTER = {"click": 1, "double_click": 2, "right_click": 1}
#: The actions whose next keystroke depends on one element holding focus.
#: ``set_value`` is deliberately not one of them: its value pattern needs no
#: focus at all, and its fallback clicks the element, which focuses it. The
#: Windows file dialog's file name box refuses SetFocus outright, and refusing
#: to save a file over that would be a guard protecting nothing.
FOCUS_ELEMENT = frozenset({"type", "keypress"})
LAUNCH_WINDOW_MS = 10000
RECORD_INTERVAL_MS = 250
#: What a recording keeps in frame. The default follows the monitor the
#: acted-on window is on, because the other one is nobody's business here
#: and costs several megabytes a frame; "virtual" is the whole desktop.
WINDOW_MONITOR = "window-monitor"
REGIONS = (WINDOW_MONITOR, "virtual")


@dataclass(frozen=True, slots=True)
class RuntimePolicy:
    """What this runtime is allowed to touch, and how visibly it does it."""

    allowed_processes: tuple[str, ...]
    mode: str = "fast"
    highlight_ms: int = 600
    focus_guard: bool = True


class RuntimeRefusal(RuntimeError):
    """A runtime-level refusal that has no receipt to carry it; ``code`` says why."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class _Refused(Exception):
    """Internal: one step stopped, with the receipt fields that say so."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: str = "refused",
        candidates: list | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.payload: dict = {"code": code, "message": message}
        if candidates:
            self.payload["candidates"] = [dict(item) for item in candidates]


class _Step:
    """The mutable half of one execution, until the receipt is sealed."""

    def __init__(self, action: Action, step_id: str, mode: str) -> None:
        self.action = action
        self.step_id = step_id
        self.mode = mode
        self.window: WindowInfo | None = None
        self.target: ResolvedTarget | None = None
        self.fallback = False
        self.point: tuple[int, int] | None = None
        self.verification: dict | None = None
        self.status = "succeeded"
        self.refusal: dict | None = None
        self.shown = False
        self.screenshots: dict = {}


def process_name(value: str) -> str:
    """The allow-list spelling of an executable: no directory, no suffix, lower."""

    name = re.split(r"[\\/]", str(value))[-1]
    return name[:-4].lower() if name.lower().endswith(".exe") else name.lower()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _box(bounds) -> str:
    return ",".join(str(int(value)) for value in bounds)


def _centre(bounds) -> tuple[int, int]:
    left, top, right, bottom = bounds
    return ((left + right) // 2, (top + bottom) // 2)


def _refusal_for(exc: Exception) -> _Refused:
    """Translate a provider or host failure into the step's own refusal."""

    if isinstance(exc, FocusError):
        return _Refused("FOCUS_LOST", str(exc))
    if isinstance(exc, ResolutionError):
        code = exc.code if exc.code in REFUSALS else "TARGET_UNRESOLVED"
        return _Refused(code, str(exc), candidates=exc.candidates)
    code = getattr(exc, "code", "HOST_ERROR")
    if code not in REFUSALS or code == "HOST_ERROR":
        # The host died mid-action: what reached the screen is unknown, so this
        # is a failure rather than a refusal, and every runtime id dies with it.
        return _Refused("HOST_ERROR", str(exc), status="failed")
    return _Refused(code, str(exc))


class ComputerUseRuntime:
    """The composed backend: providers, policy, trace and optional recording."""

    def __init__(
        self,
        *,
        trace_dir: Path,
        policy: RuntimePolicy,
        uia: Provider | None = None,
        visual: Provider | None = None,
        presentation: PresentationProvider | None = None,
        hosts: tuple[HostProcess, HostProcess] | None = None,
        clock=time.monotonic,
    ) -> None:
        if policy.mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        self._store = ActionTraceStore(Path(trace_dir))
        self._policy = policy
        self._clock = clock
        self._uia = uia
        self._visual = visual
        self._presentation = presentation
        self._hosts: list[HostProcess | None] = list(hosts) if hosts else [None, None]
        # Only what this runtime started is what this runtime stops.
        self._owned: dict[int, HostProcess] = {}
        self._lazy: set[str] = set()
        self._resolved: dict[tuple, ResolvedTarget] = {}
        self._recorder: Recorder | None = None
        self._region = WINDOW_MONITOR
        self._counter = 0

    @property
    def store(self) -> ActionTraceStore:
        return self._store

    @property
    def policy(self) -> RuntimePolicy:
        return self._policy

    @property
    def recording(self) -> str | None:
        return None if self._recorder is None else self._recorder.name

    @property
    def recording_region(self) -> tuple[int, int, int, int] | None:
        """The rectangle the running recording is capturing, if there is one."""

        return None if self._recorder is None else self._recorder.region

    @property
    def resolved_count(self) -> int:
        """How many resolutions are cached; zero again after a host died."""

        return len(self._resolved)

    # -- composition ----------------------------------------------------
    def _host(self, index: int) -> HostProcess:
        host = self._hosts[index]
        if host is None:
            host = HostProcess(
                EXECUTION_HOST if index == 0 else PRESENTATION_HOST,
                apartment="mta" if index == 0 else "sta",
            )
            self._hosts[index] = host
            self._owned[index] = host
        if not host.alive:
            host.start()
        return host

    @property
    def uia(self) -> Provider:
        if self._uia is None:
            self._uia = UiaProvider(self._host(0))
            self._lazy.add("_uia")
        return self._uia

    @property
    def visual(self) -> Provider:
        if self._visual is None:
            self._visual = VisualFallbackProvider(self.uia)
            self._lazy.add("_visual")
        return self._visual

    @property
    def presentation(self) -> PresentationProvider:
        if self._presentation is None:
            self._presentation = PresentationProvider(self._host(1))
            self._lazy.add("_presentation")
        return self._presentation

    def close(self) -> None:
        """Stop what this runtime started; a caller's own hosts stay its own."""

        if self._recorder is not None:
            recorder, self._recorder = self._recorder, None
            try:
                recorder.stop()
            except Exception:  # closing must not raise over an unfinished film
                pass
        for name in self._lazy:
            setattr(self, name, None)
        self._lazy.clear()
        for index, host in self._owned.items():
            host.stop()
            self._hosts[index] = None
        self._owned.clear()
        self._resolved.clear()

    # -- observation ----------------------------------------------------
    def inspect(
        self, application: str, window: str | None = None, *, depth: int = 6
    ) -> dict:
        """The element tree of the first matching window, or an empty answer."""

        found = self.uia.windows(application, window)
        answer: dict = {
            "application": application,
            "window": None,
            "windows": [window_payload(item) for item in found],
            "nodes": [],
            "truncated": False,
        }
        if not found:
            return answer
        tree = self.uia.inspect(found[0], depth=depth)
        answer["window"] = tree.get("window") or window_payload(found[0])
        answer["nodes"] = list(tree.get("nodes") or ())
        answer["truncated"] = bool(tree.get("truncated"))
        return answer

    # -- recording ------------------------------------------------------
    def record_start(
        self,
        name: str,
        *,
        interval_ms: int = RECORD_INTERVAL_MS,
        region: str = WINDOW_MONITOR,
    ) -> dict:
        """Begin capturing frames beside the trace; one recording at a time."""

        if region not in REGIONS:
            raise ValueError(f"region must be one of {', '.join(REGIONS)}")
        if self._recorder is not None:
            raise RuntimeRefusal(
                "RECORDING_ACTIVE", f"{self._recorder.name!r} is already recording"
            )
        try:
            recorder = Recorder(
                self._store,
                self.presentation,
                name,
                interval_ms=interval_ms,
                clock=self._clock,
                region=region,
            )
            if region == WINDOW_MONITOR:
                # Nothing has been acted on yet, so start on the primary screen
                # rather than on everything.
                recorder.set_region(self._monitor(None))
            recorder.start()
        except HostError as exc:
            raise RuntimeRefusal(
                exc.code if exc.code in REFUSALS else "HOST_ERROR", str(exc)
            ) from exc
        self._recorder, self._region = recorder, region
        return {
            "name": recorder.name,
            "interval_ms": interval_ms,
            "region": region,
            "bounds": list(recorder.region) if recorder.region else None,
            "directory": recorder.relative_dir,
            "started_at": recorder.started_at,
        }

    def _monitor(self, window: WindowInfo | None) -> tuple[int, int, int, int] | None:
        """The screen a window is on, or the primary one, when the host can say."""

        ask = getattr(self.presentation, "monitor", None)
        if ask is None:
            return None
        try:
            return tuple(ask(window.handle if window is not None else 0)["bounds"])
        except (HostError, KeyError, TypeError, ValueError):
            return None

    def _follow(self, step: _Step) -> None:
        """Keep the recording on the screen the action is actually happening on."""

        if self._recorder is None or self._region != WINDOW_MONITOR:
            return
        if step.window is None:
            return
        bounds = self._monitor(step.window)
        if bounds is not None and bounds != self._recorder.region:
            self._recorder.set_region(bounds)

    def record_stop(self) -> dict:
        """End the recording and answer its manifest."""

        if self._recorder is None:
            raise RuntimeRefusal("RECORDING_NOT_ACTIVE", "no recording is running")
        recorder, self._recorder = self._recorder, None
        return recorder.stop()

    # -- execution ------------------------------------------------------
    def execute(self, payload: dict, *, mode: str | None = None) -> dict:
        """Run one ComputerAction@1 and return the receipt it earned.

        Only an invalid action raises: every other outcome, including every
        refusal and every unexpected fault below this package, is a receipt,
        because that is what the caller reads back.
        """

        action = validate_action(payload)
        chosen = mode or self._policy.mode
        if chosen not in MODES:
            raise ContractError(f"mode must be one of {', '.join(MODES)}")
        self._counter += 1
        step = _Step(action, action.step_id or f"s-{self._counter:04d}", chosen)
        started_at = _now()
        started = self._clock()
        try:
            self._pipeline(step)
        except _Refused as refusal:
            step.status, step.refusal = refusal.status, refusal.payload
        except (HostError, FocusError, ResolutionError) as exc:
            refusal = _refusal_for(exc)
            step.status, step.refusal = refusal.status, refusal.payload
            if refusal.payload["code"] == "HOST_ERROR":
                self._resolved.clear()
        except Exception as exc:
            # A caller reads receipts, not tracebacks: anything a provider or
            # the machine underneath it throws is recorded as a failed step
            # rather than lost with the rest of the script.
            step.status = "failed"
            step.refusal = {
                "code": "HOST_ERROR",
                "message": f"{type(exc).__name__}: {exc}",
            }
            self._resolved.clear()
        self._take_down(step)
        receipt = build_receipt(
            action=action,
            step_id=step.step_id,
            mode=step.mode,
            window=step.window,
            target=step.target,
            fallback=step.fallback,
            point=step.point,
            started_at=started_at,
            duration_ms=int((self._clock() - started) * 1000),
            verification=step.verification,
            status=step.status,
            refusal=step.refusal,
            screenshots=step.screenshots,
        )
        self._store.append(receipt)
        return receipt

    def _pipeline(self, step: _Step) -> None:
        """The eight steps, in the one order a receipt can be trusted from."""

        action = step.action
        wanted = (
            process_name(action.command[0])
            if action.type == "launch" and action.command
            else process_name(action.application)
        )
        if wanted not in self._policy.allowed_processes:
            raise _Refused(
                "APP_NOT_ALLOWED",
                f"{wanted!r} is not in this runtime's allow-list "
                f"({', '.join(self._policy.allowed_processes) or 'empty'})",
            )
        if action.type != "launch":
            found = self.uia.windows(action.application, action.window)
            if not found:
                raise _Refused(
                    "WINDOW_NOT_FOUND",
                    f"no {action.application} window matched {action.window!r}"
                    if action.window
                    else f"{action.application} has no window",
                )
            step.window = found[0]
            self._follow(step)
        if self._recorder is not None or action.capture:
            step.screenshots["before"] = self._capture()
        if action.target is not None:
            step.target = self._resolve(step.window, action.target)
            step.fallback = step.target.backend == VISUAL_FALLBACK
            self._note(
                step,
                "target_found",
                f"{_box(step.target.bounds)} {step.target.control_type} "
                f"{step.target.name!r} automationId={step.target.automation_id} "
                f"backend={step.target.backend}",
            )
        self._show(step)
        self._act(step)
        self._check(step)
        if (self._recorder is not None or action.capture) and not step.screenshots.get(
            "after"
        ):
            step.screenshots["after"] = self._capture()

    # -- the screen -----------------------------------------------------
    def _capture(self) -> str:
        """One screenshot, kept in the trace, over the recording's own region.

        A receipt's before and after shots are the same picture as a frame, so
        while a recording is following one monitor they follow it too; without
        one they are the whole desktop, which is what a caller asking for a
        single capture has always got.
        """

        bounds = self._recorder.region if self._recorder is not None else None
        png = self.presentation.screenshot(bounds=bounds)["png"]
        return self._store.save_bytes("shots", png, ".png")

    def _draw(self, step: _Step) -> None:
        """Outline what this step is about to touch and leave it up.

        The overlay is left standing rather than given a duration, because the
        presentation host answers one request at a time: a highlight that
        waited there would also hold up the recorder's next frame, and the
        frame showing the marked target is the point of the recording.
        """

        bounds = (
            step.target.bounds
            if step.target is not None
            else (step.window.bounds if step.window is not None else None)
        )
        if bounds is None:
            return
        seen = step.window.title if step.window is not None else ""
        name = step.target.name if step.target is not None else seen
        label = (
            f"{step.step_id} {step.action.type.upper()} — "
            f"{name or target_name(step.action.target)}"
        )
        self.presentation.highlight(bounds, label=label, kind=step.action.type, ms=0)
        step.shown = True
        self._note(step, "highlight", f"{_box(bounds)} {label}")
        self._pause(self._policy.highlight_ms)

    def _show(self, step: _Step) -> None:
        if step.mode != "demo" or step.action.type in ("wait", "screenshot", "launch"):
            return
        self._draw(step)

    def _take_down(self, step: _Step) -> None:
        if not step.shown or self._presentation is None:
            return
        try:
            self._presentation.clear()
        except HostError:  # a host that already died cannot leave anything up
            pass

    def _pause(self, ms: int) -> None:
        if ms > 0:
            time.sleep(ms / 1000)

    # -- resolution -----------------------------------------------------
    def _provider(self, spec: TargetSpec) -> Provider:
        return self.visual if spec.backend == VISUAL_FALLBACK else self.uia

    def _resolve(self, window: WindowInfo | None, spec: TargetSpec) -> ResolvedTarget:
        """The element the spec names, re-read rather than re-found when known.

        A cached resolution is only ever returned with the bounds the element
        reports now, and an element that no longer answers is looked up again;
        a runtime id that died with its host is dropped when the host dies.
        """

        if window is None:
            raise ResolutionError("TARGET_UNRESOLVED", "there is no window to look in")
        provider = self._provider(spec)
        key = (provider.name, window.handle, spec)
        cached = self._resolved.get(key)
        if cached is not None:
            try:
                bounds = provider.read(cached).get("bounds")
            except (HostError, ResolutionError, ContractError):
                self._resolved.pop(key, None)
            else:
                if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
                    return replace(cached, bounds=tuple(int(item) for item in bounds))
                return cached
        found = provider.resolve(window, spec)
        self._resolved[key] = found
        return found

    def _element(self, step: _Step, spec: TargetSpec) -> tuple[ResolvedTarget, dict]:
        found = self._resolve(step.window, spec)
        return found, self._provider(spec).read(found)

    # -- actuation ------------------------------------------------------
    def _focus(self, step: _Step) -> None:
        window = step.window
        element = step.target if step.action.type in FOCUS_ELEMENT else None
        self.uia.focus(window, element)
        if not self._policy.focus_guard:
            return
        front = self.uia.foreground()
        if front.pid != window.pid:
            raise _Refused(
                "FOCUS_LOST",
                f"{front.title!r} (pid {front.pid}) holds the foreground, "
                f"not {window.title!r} (pid {window.pid})",
            )

    def _aim(self, step: _Step, point: tuple[int, int]) -> None:
        """Record the point this step is aimed at, and show it in demo mode."""

        step.point = point
        if step.mode == "demo":
            self.uia.move(point)
            self._note(step, "pointer_move", f"{point[0]},{point[1]}")

    def _act(self, step: _Step) -> None:
        action, kind = step.action, step.action.type
        if kind == "wait":
            self._pause(action.ms or 0)
            return
        if kind == "screenshot":
            step.screenshots["after"] = self._capture()
            self._note(step, "screenshot", step.screenshots["after"])
            return
        if kind == "highlight":
            if step.mode != "demo":
                self._draw(step)
            return
        if kind == "launch":
            step.window = self._launch(step)
            self._follow(step)
            return
        self._focus(step)
        uia = self.uia
        point = _centre(step.target.bounds if step.target else step.window.bounds)
        if kind == "type":
            uia.type_text(action.text)
            self._note(step, "type", self._said(action))
        elif kind == "keypress":
            uia.keypress(action.keys)
            self._note(step, "keypress", action.keys)
        elif kind == "set_value":
            if step.mode == "demo":
                self._aim(step, point)
            if uia.set_value(step.target, action.text):
                self._note(step, "type", f"{self._said(action)} (value pattern)")
            else:
                # Without a value pattern the field is set the way a person
                # would: take everything that is in it, remove it, then type.
                # Typing alone would append, and "set" would stop being true.
                self._aim(step, point)
                uia.click(point)
                uia.keypress("ctrl+a")
                uia.keypress("delete")
                if action.text:
                    uia.type_text(action.text)
                self._note(step, "type", f"{self._said(action)} (typed over)")
        elif kind == "invoke":
            if step.mode == "demo":
                self._aim(step, point)
            if uia.invoke(step.target):
                self._note(step, "click", f"{step.target.name!r} invoke pattern")
            else:
                self._aim(step, point)
                uia.click(point)
                self._note(step, "click", f"{point[0]},{point[1]} left x1")
        elif kind in POINTER:
            if step.mode == "fast" and kind == "click" and uia.invoke(step.target):
                self._note(step, "click", f"{step.target.name!r} invoke pattern")
                return
            button = "right" if kind == "right_click" else action.button
            self._aim(step, point)
            uia.click(point, button=button, count=POINTER[kind])
            self._note(
                step, "click", f"{point[0]},{point[1]} {button} x{POINTER[kind]}"
            )
        elif kind == "drag":
            destination = self._resolve(step.window, action.to)
            end = _centre(destination.bounds)
            self._aim(step, point)
            uia.drag(point, end)
            self._note(step, "drag", f"{point[0]},{point[1]} {end[0]},{end[1]}")
        elif kind == "scroll":
            self._aim(step, point)
            uia.scroll(point, action.delta)
            self._note(step, "scroll", f"{point[0]},{point[1]} {action.delta:+d}")
        else:  # pragma: no cover - ACTION_TYPES is closed and covered above
            raise _Refused("ACTION_INVALID", f"{kind} has no execution here")

    def _said(self, action: Action) -> str:
        return redacted_text(action.text) if action.sensitive else action.text

    def _launch(self, step: _Step) -> WindowInfo:
        """Start the command and hand back the window the caller meant.

        A shell that hands its window to another process -- Explorer does --
        leaves the launcher pointing at whatever that process showed first, so
        when the action declared a title it is given the last word.
        """

        action = step.action
        window = self.uia.launch(action.command, wait_window_ms=LAUNCH_WINDOW_MS)
        self._note(step, "launch", f"{' '.join(action.command)} -> {window.title!r}")
        if action.window and not re.search(action.window, window.title):
            for _ in range(10):
                matched = self.uia.windows(action.application, action.window)
                if matched:
                    return matched[0]
                time.sleep(0.2)
        return window

    # -- verification ---------------------------------------------------
    def _check(self, step: _Step) -> None:
        action = step.action
        if action.verification is None:
            return
        step.verification = verify_action(
            action,
            windows=lambda title: self.uia.windows(action.application, title),
            element=lambda spec: self._element(step, spec),
            clock=self._clock,
        )
        held = step.verification["status"] == "passed"
        said = summary(action)
        self._note(step, "verify", f"{step.verification['status']} {said}")
        if not held:
            step.status = "failed"
            step.refusal = {
                "code": "VERIFY_FAILED",
                "message": f"{said} did not hold: {step.verification['detail']}",
            }
        if step.mode == "demo":
            self.presentation.badge(
                f"VERIFY {'✓' if held else '✕'} {said}",
                ms=0,
                kind="ok" if held else "fail",
                anchor=self._anchor(step),
            )
            step.shown = True
            self._pause(self._policy.highlight_ms)

    def _anchor(self, step: _Step) -> tuple[int, int, int, int] | None:
        """The rectangle a verdict is about, so it can be said next to it."""

        if step.target is not None:
            return step.target.bounds
        return step.window.bounds if step.window is not None else None

    # -- recording ------------------------------------------------------
    def _note(self, step: _Step, event: str, detail: str) -> None:
        if self._recorder is not None:
            self._recorder.note(event, detail, step_id=step.step_id)

"""One persistent Codex ACP adapter, using the upstream Python SDK transport.

Hub owns the visible chat and permission decisions. This object owns only its
adapter process and the negotiated ACP session; it never retries a prompt.
"""

from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import Future, InvalidStateError
from contextlib import suppress
import subprocess
import sys
import threading
from typing import Any, Callable

from acp import PROTOCOL_VERSION, RequestError, connect_to_agent
from acp.schema import (
    ClientCapabilities, Implementation, RequestPermissionResponse,
    SessionNotification, TextContentBlock,
)


class AcpSessionError(RuntimeError):
    """An ACP turn failed; the caller must not silently submit it again."""


class AcpCancelled(AcpSessionError):
    """The user cancelled this turn."""


class CodexAcpSession:
    def __init__(
        self,
        command: tuple[str, ...],
        cwd: str,
        environment: dict[str, str],
        mcp_servers: list[dict],
        on_update: Callable[[dict], None],
        on_permission: Callable[[dict], Future],
        default_model: str | None = None,
    ) -> None:
        if not command:
            raise ValueError("An ACP adapter command is required.")
        self._command = command
        self._cwd = cwd
        self._environment = dict(environment)
        self._mcp_servers = mcp_servers
        self._on_update = on_update
        self._on_permission = on_permission
        self._connection = None
        self._process = None
        self._stderr_task = None
        self._stderr = deque(maxlen=16)
        self._session_id: str | None = None
        self._can_load = False
        self._config_options: list[dict] = []
        self._default_model = default_model
        self._replaying = False
        self._permissions: set[Future] = set()
        self._cleanup_lock = asyncio.Lock()
        self._turn_task = None
        self._activity_timeout: asyncio.Timeout | None = None
        self._activity_timeout_s = 0.0
        self._cancel_requested = threading.Event()
        self._prompt_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._closed = False
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, name="hub-codex-acp", daemon=True)
        self._thread.start()
        self._ready.wait()

    @property
    def default_model(self) -> str | None:
        return self._default_model

    def _run_loop(self) -> None:
        # Windows subprocess pipes need Proactor even if the web host uses a
        # different loop policy. This loop belongs only to this adapter.
        self._loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    def prompt(
        self,
        text: str,
        session_id: str | None,
        model: str | None,
        on_session: Callable[[str], None],
        timeout_s: float,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("ACP inactivity timeout must be positive.")
        if not self._prompt_lock.acquire(blocking=False):
            raise AcpSessionError("An ACP turn is already running in this chat.")
        try:
            with self._lifecycle_lock:
                if self._closed:
                    raise AcpSessionError("This ACP connection has been closed.")
                self._cancel_requested.clear()
                future = asyncio.run_coroutine_threadsafe(
                    self._prompt(text, session_id, model, on_session, timeout_s), self._loop,
                )
            future.result()
        finally:
            self._prompt_lock.release()

    async def _start(self) -> None:
        if self._process is not None:
            if self._process.returncode is not None:
                raise AcpSessionError("The ACP adapter exited. This turn was not sent.")
            return
        self._stderr.clear()
        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            cwd=self._cwd,
            env=self._environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=50 * 1024 * 1024,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr(self._process.stderr))
        self._connection = connect_to_agent(self, self._process.stdin, self._process.stdout)
        initialized = await self._connection.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(),
            client_info=Implementation(name="monkeyhub", version="0.1.0"),
        )
        if initialized.protocol_version != PROTOCOL_VERSION:
            raise AcpSessionError(f"Unsupported ACP protocol version: {initialized.protocol_version}.")
        self._can_load = bool(initialized.agent_capabilities.load_session)

    async def _drain_stderr(self, reader) -> None:
        while chunk := await reader.read(4096):
            self._stderr.append(chunk.decode("utf-8", errors="replace"))

    def _check_cancelled(self) -> None:
        if self._cancel_requested.is_set():
            raise AcpCancelled("The ACP turn was cancelled.")

    def _touch_activity(self, session_id: str) -> None:
        timeout = self._activity_timeout
        if (timeout is not None and not timeout.expired() and session_id == self._session_id
                and not self._replaying and not self._cancel_requested.is_set()):
            timeout.reschedule(self._loop.time() + self._activity_timeout_s)

    async def _prompt(self, text, session_id, model, on_session, timeout_s) -> None:
        self._turn_task = asyncio.current_task()
        try:
            # Setup is bounded too. Only activity for this negotiated session
            # renews the deadline; transport keepalives never reach this hook.
            async with asyncio.timeout(timeout_s) as timeout:
                self._activity_timeout, self._activity_timeout_s = timeout, timeout_s
                self._check_cancelled()
                await self._start()
                self._check_cancelled()
                if self._session_id is None:
                    if session_id is not None:
                        if not self._can_load:
                            raise AcpSessionError("This ACP adapter does not support restoring sessions.")
                        self._replaying = True
                        try:
                            session = await self._connection.load_session(
                                session_id=session_id, cwd=self._cwd, mcp_servers=self._mcp_servers,
                            )
                        finally:
                            self._replaying = False
                        self._session_id = session_id
                    else:
                        session = await self._connection.new_session(cwd=self._cwd, mcp_servers=self._mcp_servers)
                        self._session_id = session.session_id
                    self._set_config_options(session.config_options)
                    option = self._model_option()
                    if self._default_model is None and option:
                        self._default_model = option["currentValue"]
                    # ACP identifiers are opaque. Save the negotiated default
                    # with the ID before a turn changes the selected model.
                    on_session(self._session_id)
                elif session_id is not None and session_id != self._session_id:
                    raise AcpSessionError("This adapter is already bound to another ACP session.")
                self._check_cancelled()
                await self._select_model(model)
                self._check_cancelled()
                response = await self._connection.prompt(
                    session_id=self._session_id, prompt=[TextContentBlock(type="text", text=text)],
                )
                if response.stop_reason == "cancelled":
                    raise AcpCancelled("The ACP turn was cancelled.")
                self._check_cancelled()
                if response.stop_reason != "end_turn":
                    raise AcpSessionError(f"The ACP turn stopped with {response.stop_reason}.")
        except AcpCancelled:
            raise
        except TimeoutError as exc:
            self._activity_timeout = None
            await self._send_cancel()
            await self._stop_process()
            raise AcpSessionError("The ACP turn timed out after no session activity and was cancelled; it was not resent.") from exc
        except Exception as exc:
            self._activity_timeout = None
            await self._stop_process()
            if self._cancel_requested.is_set():
                raise AcpCancelled("The ACP turn was cancelled.") from exc
            if isinstance(exc, AcpSessionError):
                raise
            message = str(exc)
            if isinstance(exc, RequestError) and isinstance(exc.data, dict):
                reason = exc.data.get("details")
                if isinstance(reason, str) and reason:
                    message += f": {reason}"
            detail = "".join(self._stderr).strip()[-2000:]
            raise AcpSessionError(f"ACP turn failed: {message}" + (f"\n{detail}" if detail else "")) from exc
        finally:
            self._activity_timeout = None
            self._turn_task = None
            self._cancel_permissions()

    def _set_config_options(self, options) -> None:
        if options is not None:
            self._config_options = [item.model_dump(by_alias=True, exclude_none=True) for item in options]

    def _model_option(self) -> dict | None:
        return next((option for option in self._config_options if option.get("category") == "model"
                     or option.get("id") == "model"), None)

    async def _select_model(self, model: str | None) -> None:
        option = self._model_option()
        requested = model or self._default_model
        if requested is None:
            return
        if option is None or option.get("type") != "select":
            raise AcpSessionError("This ACP session does not offer model selection.")
        choices = option["options"]
        values = {item["value"] for choice in choices for item in choice.get("options", [choice])}
        if requested not in values:
            raise AcpSessionError(f"The ACP adapter does not offer model {requested!r}.")
        if option["currentValue"] != requested:
            result = await self._connection.set_config_option(
                session_id=self._session_id, config_id=option["id"], value=requested,
            )
            self._set_config_options(result.config_options)

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self._touch_activity(session_id)
        if update.session_update == "config_option_update":
            self._set_config_options(update.config_options)
        if not self._replaying:
            notification = SessionNotification(session_id=session_id, update=update)
            self._on_update(notification.model_dump(by_alias=True, exclude_none=True))

    async def request_permission(self, session_id: str, tool_call: Any, options: list, **kwargs: Any):
        if self._cancel_requested.is_set() or self._replaying:
            return RequestPermissionResponse(outcome={"outcome": "cancelled"})
        self._touch_activity(session_id)
        future = self._on_permission({
            "sessionId": session_id,
            "toolCall": tool_call.model_dump(by_alias=True, exclude_none=True),
            "options": [option.model_dump(by_alias=True, exclude_none=True) for option in options],
        })
        self._permissions.add(future)
        try:
            selected = await asyncio.wrap_future(future)
        finally:
            self._permissions.discard(future)
        self._touch_activity(session_id)
        if selected is None or selected not in {option.option_id for option in options}:
            return RequestPermissionResponse(outcome={"outcome": "cancelled"})
        return RequestPermissionResponse(outcome={"outcome": "selected", "optionId": selected})

    def _cancel_permissions(self) -> None:
        for future in tuple(self._permissions):
            with suppress(InvalidStateError):
                future.set_result(None)

    async def _send_cancel(self) -> None:
        self._cancel_permissions()
        if self._connection is not None and self._session_id is not None:
            with suppress(Exception):
                await asyncio.wait_for(self._connection.cancel(session_id=self._session_id), timeout=1)

    def cancel(self) -> None:
        with self._lifecycle_lock:
            if self._closed or not self._prompt_lock.locked():
                return
            self._cancel_requested.set()
            asyncio.run_coroutine_threadsafe(self._cancel(), self._loop)

    async def _cancel(self) -> None:
        turn = self._turn_task
        await self._send_cancel()
        if turn is not None:
            try:
                await asyncio.wait_for(asyncio.shield(turn), timeout=3)
            except TimeoutError:
                await self._stop_process()
            except Exception:
                pass  # The prompt caller receives the cancellation or failure.

    async def _stop_process(self) -> None:
        # A timeout, cancel and Hub shutdown can arrive together. A caller must
        # await the ongoing cleanup before it stops the loop, even after the
        # process has been detached from the session fields.
        async with self._cleanup_lock:
            await self._dispose_process()

    async def _dispose_process(self) -> None:
        connection, process = self._connection, self._process
        self._connection, self._process, self._session_id = None, None, None
        self._cancel_permissions()
        if connection is not None:
            with suppress(Exception):
                await asyncio.wait_for(connection.close(), timeout=2)
        if process is not None and process.stdin is not None:
            process.stdin.close()
            with suppress(Exception):
                await asyncio.wait_for(process.stdin.wait_closed(), timeout=1)
        if process is not None and process.returncode is None:
            # Closing the ACP pipe lets the adapter dispose its own resources.
            # Codex ACP also gives its app-server two seconds to exit. Let that
            # cleanup finish before escalating to this adapter's own subtree.
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                if sys.platform == "win32":
                    with suppress(OSError, subprocess.TimeoutExpired):
                        await asyncio.to_thread(
                            subprocess.run, ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=3, check=False,
                        )
                else:
                    with suppress(ProcessLookupError):
                        process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stderr_task
            self._stderr_task = None

    async def _close(self) -> None:
        await self._send_cancel()
        await self._stop_process()

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            self._cancel_requested.set()
            future = asyncio.run_coroutine_threadsafe(self._close(), self._loop)
        try:
            future.result(timeout=15)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)

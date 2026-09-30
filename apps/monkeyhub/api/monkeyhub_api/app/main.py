"""The Hub command: start one Hub for one runtime root and serve it until it is told to stop.

``main`` is what apps/monkeyhub/run.py runs for the desktop window and the
browser launcher. It holds the runtime root's lease until the Hub and its owned
workers have drained, serves the application ``composition`` builds, stops on
the managed stdin's word, and after a normal quit switches the desktop entry to
a ready update.
"""

import argparse
from contextlib import contextmanager
import inspect
import os
from pathlib import Path
import sys
import threading
from uuid import UUID
import webbrowser

import uvicorn

from ..settings import credentials
from ..updates.desktop_updates import recover_failed_start
from .composition import SOURCE_ROOT, HubSettings, create_app


def complete_interrupted_connection_teardown() -> None:
    """A connection the proactor transport cannot close must not stay attached.

    CPython shuts the socket down inside the `finally` of
    `_call_connection_lost`. Windows answers WinError 10054 when the peer is
    already gone, and that error leaves the rest of that block unrun: the
    socket stays open and the connection stays attached to `asyncio.Server`.
    `Server.wait_closed()` then waits for it forever, so uvicorn logs
    `Shutting down`, never reaches the ASGI lifespan shutdown and never exits,
    leaving the desktop host waiting on a stop it already requested. Finish
    the skipped teardown exactly once and re-raise the operating system's
    error, so nothing is detached twice and nothing is hidden.
    """
    from asyncio.proactor_events import _ProactorBasePipeTransport as Transport

    interrupted = Transport._call_connection_lost
    if getattr(interrupted, "_completes_teardown", False):
        return

    def _call_connection_lost(self, exc):
        try:
            interrupted(self, exc)
        except OSError:
            # Take each field before using it, so this teardown stays single
            # use however far CPython's own block reached before it raised.
            closing, self._sock = self._sock, None
            server, self._server = self._server, None
            self._called_connection_lost = True
            if closing is not None:
                try:
                    closing.close()
                except OSError:
                    pass
            if server is not None:
                # 3.12 detaches a counted connection; 3.13 discards the transport.
                if inspect.signature(server._detach).parameters:
                    server._detach(self)
                else:
                    server._detach()
            raise

    _call_connection_lost._completes_teardown = True
    Transport._call_connection_lost = _call_connection_lost


class HubServer(uvicorn.Server):
    async def shutdown(self, sockets=None):
        # Uvicorn drains HTTP tasks before entering ASGI lifespan shutdown.
        # End subscriptions first; admitted mutations still drain normally.
        app = self.config.app
        app.state.runtimes.begin_shutdown()
        await super().shutdown(sockets=sockets)


@contextmanager
def _runtime_lease(root: Path):
    """Keep both CLI entrypoints exclusive until the actual Hub finishes draining."""
    root.mkdir(parents=True, exist_ok=True)
    # Keep the same inode after release; deleting a lock file would let another
    # process lock a replacement while a previous opener still owns the old one.
    with (root / "hub.lock").open("a+b") as lease:
        try:
            if os.name == "nt":
                import msvcrt
                lease.seek(0)
                msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError(f"Another Hub is using runtime directory {root}. Close it and wait for shutdown, or choose another --runtime-root.") from error
        try:
            yield
        finally:
            if os.name == "nt":
                lease.seek(0)
                msvcrt.locking(lease.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lease.fileno(), fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Open the local MonkeyHub without a building project.")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--hub-web-dir", type=Path)
    parser.add_argument("--web-origin", help="One explicit loopback web development origin")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--managed-stdin", action="store_true")
    parser.add_argument("--managed-instance-id", type=UUID)
    args = parser.parse_args(argv)
    if args.managed_stdin != (args.managed_instance_id is not None):
        parser.error("--managed-stdin and --managed-instance-id must be provided together")
    runtime_root = args.runtime_root
    if runtime_root is None:
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_appdata or not Path(local_appdata).is_absolute():
            parser.error("set --runtime-root to an absolute nonproject directory")
        runtime_root = Path(local_appdata) / "MonkeyHub"
    hub_web = args.hub_web_dir
    if hub_web is None and (SOURCE_ROOT / "apps/monkeyhub/web/dist/index.html").is_file():
        hub_web = SOURCE_ROOT / "apps/monkeyhub/web/dist"
    settings = HubSettings(
        runtime_root=runtime_root, port=args.port, hub_web_dir=hub_web,
        web_origin=args.web_origin,
        managed_instance_id=str(args.managed_instance_id) if args.managed_instance_id else None,
    )
    if sys.platform == "win32":
        complete_interrupted_connection_teardown()
    # Only the Hub process itself opens the account's credential store (#334);
    # an app built by a test keeps the empty default.
    credentials.use_secret_store(credentials.account_store())
    with _runtime_lease(settings.runtime_root):
        managed = settings.managed_instance_id is not None
        try:
            app = create_app(settings)
        except BaseException as error:
            recover_failed_start(SOURCE_ROOT, settings.runtime_root, managed=managed,
                                 reason=f"{type(error).__name__}: {error}"[:300])
            raise
        server = HubServer(uvicorn.Config(app, host="127.0.0.1", port=settings.port))
        if args.managed_stdin:
            def watch_stdin():
                for line in sys.stdin:
                    if line.strip() == "stop":
                        break
                app.state.chats.shutdown()
                app.state.applications.begin_shutdown()
                server.should_exit = True
            threading.Thread(target=watch_stdin, daemon=True).start()
        if not args.no_browser:
            def open_when_ready():
                while not server.started and not server.should_exit:
                    threading.Event().wait(0.1)
                if server.started:
                    webbrowser.open(f"http://127.0.0.1:{settings.port}/")
            threading.Thread(target=open_when_ready, daemon=True).start()
        try:
            server.run()
        finally:
            if not server.started:
                app.state.updates.startup_failed("the Hub server did not start")
        if server.started:
            # A normal quit, with every owned worker drained and the runtime
            # lease still held: switch the desktop entry to a ready update.
            app.state.updates.activate_on_quit()


if __name__ == "__main__":
    main()

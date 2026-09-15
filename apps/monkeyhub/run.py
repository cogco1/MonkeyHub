"""Run the local Hub or one of its fixed child services, including embedded Python."""

from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api",
                  ROOT / "apps/monkeyfab/src"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))


_CANDIDATE_REQUEST = re.compile(
    r"^/api/(proposals/[^/]+/candidate|candidates/combine|capabilities/[^/]+/run|options/[^/]+/select|program)$"
)
_CJK = re.compile(r"[\u3400-\u9fff]")
_TERMINAL_TOOL_STATES = {"completed", "failed", "cancelled", "interrupted"}


def _progress_chat_store():
    """Add a transient, factual work narrative around the retained chat store.

    The underlying ChatStore keeps owning the transcript, native provider session,
    tool receipts and attachments. This wrapper only projects what an operator can
    safely act on *now*. Progress rows live in memory, are never passed back to the
    provider, and never enter P036 or the saved chat JSON.
    """
    from monkeyhub_api import chat as chat_module
    from monkeyhub_api.models import ChatMessage

    class ProgressChatStore(chat_module.ChatStore):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._progress_rows: dict[str, dict[str, ChatMessage]] = {}

        @staticmethod
        def _zh(session) -> bool:
            user = next((row for row in reversed(session.messages) if row.role == "user"), None)
            return bool(user and _CJK.search(user.content))

        def _progress(self, session, key: str, text: str, *, append: bool = False,
                      status: str = "complete") -> None:
            """Publish one replaceable UI row without making it conversation history."""
            clean = chat_module._redact(text).strip()
            if not clean:
                return
            rows = self._progress_rows.setdefault(session.id, {})
            turn = chat_module._turn_id(session)
            identifier = f"{turn}:progress:{key}"
            row = rows.get(key)
            if row is None or row.id != identifier:
                row = ChatMessage(id=identifier, role="tool", content="", createdAt=chat_module._now(), status=status)
                rows[key] = row
            row.content = (row.content + clean if append else clean)[-2400:]
            row.status = status
            # The runtime already converts a chat change into agent/progress SSE;
            # do not call _save merely to wake a browser.
            if self.on_change is not None:
                self.on_change(session)

        def get(self, session_id: str):
            detail = super().get(session_id)
            with self._lock:
                extra = [row.model_copy(deep=True) for row in self._progress_rows.get(session_id, {}).values()]
                if detail.status != "running":
                    ending = "interrupted" if detail.status == "interrupted" else "failed" if detail.status == "failed" else "complete"
                    for row in extra:
                        if row.status == "streaming":
                            row.status = ending
            detail.messages = sorted([*detail.messages, *extra], key=lambda row: (row.createdAt, row.id))
            return detail

        def post(self, session_id, request):
            with self._lock:
                self._progress_rows.pop(session_id, None)
            return super().post(session_id, request)

        def _acp_update(self, session_id: str, event: dict, environment: dict) -> None:
            update = event.get("update") if isinstance(event.get("update"), dict) else {}
            kind = update.get("sessionUpdate")
            # The pinned Codex ACP adapter requests its provider reasoning summary
            # (summary=auto) and exposes that client-visible summary as an ACP
            # thought chunk. We project it, but never persist or replay it. This
            # does not ask for hidden/raw reasoning and makes no extra model call.
            if kind == "agent_thought_chunk" and update.get("content", {}).get("type") == "text":
                with self._lock:
                    session = self._session(session_id)
                    running = self._running.get(session_id)
                    if (running is None or running.stop.is_set()
                            or event.get("sessionId") != session.acpSessionId):
                        return
                    text = chat_module._redact(str(update["content"].get("text") or ""), environment)
                    self._progress(session, "provider-summary", text, append=True, status="streaming")
                return
            if kind == "plan":
                with self._lock:
                    session = self._session(session_id)
                    running = self._running.get(session_id)
                    if (running is None or running.stop.is_set()
                            or event.get("sessionId") != session.acpSessionId):
                        return
                    entries = update.get("entries") if isinstance(update.get("entries"), list) else []
                    current = next((row.get("content") for row in entries if isinstance(row, dict)
                                    and row.get("status") == "in_progress" and isinstance(row.get("content"), str)), None)
                    if current:
                        prefix = "正在推进：" if self._zh(session) else "Working on: "
                        self._progress(session, "plan", prefix + current, status="streaming")
                return
            super()._acp_update(session_id, event, environment)

        def _tool_message(self, session, item, kind: str, environment) -> None:
            # Let the existing fail-closed parser decide whether this call really
            # succeeded and whether a candidate is actually readable.
            super()._tool_message(session, item, kind, environment)
            if item.get("server") != "monkeyhub":
                return
            arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            method = str(arguments.get("method") or "").upper()
            raw_path = arguments.get("path") if isinstance(arguments.get("path"), str) else ""
            path = urlsplit(raw_path).path
            status = str(item.get("status") or "")
            running = kind in {"item.started", "item.updated"} and status not in _TERMINAL_TOOL_STATES
            message_id = f"{chat_module._turn_id(session)}:{item.get('id') or 'tool'}"
            message = next((row for row in session.messages if row.id == message_id), None)
            failed = message is not None and message.status == "failed"
            zh = self._zh(session)

            if running:
                if method == "POST" and _CANDIDATE_REQUEST.fullmatch(path):
                    text = ("我正在生成新的设计候选；当前版本仍然可以继续查看。" if zh else
                            "I’m generating a new design candidate; the current version remains available to review.")
                    self._progress(session, "runtime", text, status="streaming")
                elif method == "PUT" and path == "/api/board":
                    text = "我正在把这一步的结果同步到 Board…" if zh else "I’m syncing this result to Board…"
                    self._progress(session, "runtime", text, status="streaming")
                return

            if failed:
                text = ("这一步没有完成；下面的执行详情保留了失败原因，我不会把它说成成功。" if zh else
                        "That step did not complete. The activity details keep the failure reason; I won’t present it as success.")
                self._progress(session, "runtime", text, status="failed")
                return

            if message is not None and message.candidateId:
                candidate = message.candidateId
                text = (f"候选 {candidate} 已经生成，可以去建模页面审核；我这边可以继续推进后续工作。" if zh else
                        f"Candidate {candidate} is ready to review in Modeling; I can keep the remaining work moving.")
                self._progress(session, "runtime", text)
            elif method == "PUT" and path == "/api/board":
                text = ("更新已经同步到 Board，你现在可以先去审核和批注；我这边可以继续推进后续工作。" if zh else
                        "The update is synced to Board. You can review and annotate it now while I keep the remaining work moving.")
                self._progress(session, "runtime", text)
            elif method == "POST" and path.startswith("/api/documents"):
                text = ("新的图纸/文档版本已经登记；Board 上绑定的内容现在可以刷新到这个版本。" if zh else
                        "The new drawing/document revision is registered; bound Board content can now refresh to this revision.")
                self._progress(session, "runtime", text)
            elif method == "POST" and path.startswith("/api/drawings"):
                text = ("图纸产物已经生成，可以继续审核或发送到 Board。" if zh else
                        "The drawing output is ready to review or send to Board.")
                self._progress(session, "runtime", text)

    return ProgressChatStore


def _hub_main():
    """Compose the progress projection at the Hub entry without a second store."""
    from monkeyhub_api import main as hub_main
    hub_main.ChatStore = _progress_chat_store()
    return hub_main.main


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["--service"]:
        if len(args) < 2:
            raise SystemExit("--service requires studio or monitor")
        service, args = args[1], args[2:]
        if service == "studio":
            from archflow_studio_api.main import main as serve
        elif service == "monitor":
            from monkeymonitor.__main__ import main as serve
        else:
            raise SystemExit("--service must be studio or monitor")
    else:
        serve = _hub_main()
    serve(args)


if __name__ == "__main__":
    main()

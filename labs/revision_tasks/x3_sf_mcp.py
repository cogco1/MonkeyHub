"""X3 SF: a stdio MCP server over one C-task 3DM state file, and nothing else (no Hub, no project).

The CLI starts it by path with one JSON configuration file::

    python x3_sf_mcp.py CONFIG.json
    {"file": "<the previous request's state file>", "out": "<new directory for this session's files>",
     "user_message": "<the architect's words, the reviser's utterance>", "log": "<calls.jsonl>", "max_calls": 20}

Tools:

- ``state_read``: page through the state the file carries (read-only): a summary, or one section of it (parameters,
  entities, relations, obligations, context_pack) as JSON text, ``offset``/``limit`` in characters. The file is read
  with every binding check (``carrier.read_three_dm``); a file the checks refuse is still readable, marked unchecked,
  with the refusal.
- ``model_objects``: each named mesh in the file with its box (min, max) and whether it is hidden, optionally by
  name prefix, paged.
- ``submit_change``: typed changes, ``parameters`` ({key: number}) and ``elements`` ([{id, param, value}], an
  element's own numeric param, the kernel's SET_SCALAR), applied by the hostless reviser (``reviser.revise``), which
  realizes the result, runs the system's own checks and writes the next state file. With ``adopt`` (default true) that
  file becomes the current one, for later reads and changes in this session and as the session's result.

Every call is logged; calls past ``max_calls`` are refused. ``<out>/current.json`` names the current file and each
submission.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from labs.revision_tasks import _bootstrap  # noqa: F401,E402  (source roots first)

import json  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from typing import Any  # noqa: E402

SERVER = "design"
TOOLS = ("state_read", "model_objects", "submit_change")
SECTIONS = ("summary", "parameters", "entities", "relations", "obligations", "context_pack")
MAX_CALLS = 20
PAGE = 20000

TOOL_LIST = [
    {"name": "state_read", "description": (
        "Read the design state carried in the current model file (read-only). section 'summary' (default) gives its "
        "identity, the binding check and the size of each section; 'parameters', 'entities', 'relations', 'obligations' "
        "or 'context_pack' gives that section as JSON text, paged by offset and limit in characters (follow nextOffset "
        "until null)."),
     "inputSchema": {"type": "object", "properties": {
         "section": {"type": "string", "enum": list(SECTIONS)},
         "offset": {"type": "integer", "minimum": 0, "default": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": 50000, "default": PAGE}}, "additionalProperties": False}},
    {"name": "model_objects", "description": (
        "List the model file's named objects with their bounding boxes in metres (x east, y north, z up) and whether "
        "each is hidden (a void such as an opening). Optional name prefix; paged by offset and limit in objects."),
     "inputSchema": {"type": "object", "properties": {
         "prefix": {"type": "string"}, "offset": {"type": "integer", "minimum": 0, "default": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": 400, "default": 200}}, "additionalProperties": False}},
    {"name": "submit_change", "description": (
        "Change the design: set declared parameters ({key: number}) and/or an element's own numeric params "
        "([{id, param, value}], id without the 'obj-' prefix). A parameter derived by an expression, or an element "
        "param bound to a parameter, cannot be set directly: change its inputs. The change is applied to the current "
        "file's state, the model is rebuilt and the system's own checks run; the answer gives the claim (accept, "
        "reopen, unknown or refuse), the reasons and the checks. With adopt true (default) the new file becomes the "
        "current design; with adopt false it is only tried."),
     "inputSchema": {"type": "object", "properties": {
         "parameters": {"type": "object", "additionalProperties": {"type": "number"}},
         "elements": {"type": "array", "items": {"type": "object", "properties": {
             "id": {"type": "string"}, "param": {"type": "string"}, "value": {"type": "number"}},
             "required": ["id", "param", "value"], "additionalProperties": False}},
         "adopt": {"type": "boolean", "default": True},
         "summary": {"type": "string", "maxLength": 500}}, "additionalProperties": False}},
]


class StateFileServer:
    """The tools over one state file; ``call`` answers one tool call as MCP content."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.current = Path(config["file"])
        self.out = Path(config["out"])
        self.out.mkdir(parents=True, exist_ok=True)
        self.calls = 0
        self.submissions: list[dict[str, Any]] = []
        self._write_current()

    # ---- reading
    def _read(self) -> tuple[dict[str, Any] | None, dict[str, Any], str | None]:
        """(the checked state or the raw one, binding, refusal) of the current file."""

        from labs.revision_tasks import carrier

        try:
            read = carrier.read_three_dm(self.current)
            state = {"record": read.record.to_dict(), "context_pack": read.context_pack}
            return state, read.binding, None
        except carrier.CarrierRefused as exc:
            import rhino3dm

            model = rhino3dm.File3dm.Read(str(self.current))
            raw = model.Strings["archflow:state"] if model is not None else None
            try:
                state = json.loads(raw) if raw else None
            except ValueError:
                state = None
            try:
                binding = json.loads(model.Strings["archflow:binding"]) if model is not None else {}
            except (KeyError, ValueError, TypeError):
                binding = {}
            return state, binding, f"{exc.code}: {exc.detail}"[:600]

    def state_read(self, section: str = "summary", offset: int = 0, limit: int = PAGE) -> dict[str, Any]:
        state, binding, refusal = self._read()
        record = (state or {}).get("record") or {}
        head = {"file": self.current.name, "checked": refusal is None, "refusal": refusal}
        if section == "summary":
            entities: dict[str, int] = {}
            for row in record.get("entities", ()):
                entities[row.get("schema", "?")] = entities.get(row.get("schema", "?"), 0) + 1
            sizes = {name: len(json.dumps(self._section(state, name), ensure_ascii=False)) for name in SECTIONS[1:]}
            return {**head, "project_id": record.get("project_id"), "run_id": record.get("run_id"),
                    "binding": {k: binding.get(k) for k in ("record_digest", "state_digest", "parent_file_sha256")},
                    "entities_by_schema": entities, "counts": {k: len(record.get(k, ())) for k in ("parameters", "relations", "obligations")},
                    "section_chars": sizes}
        if section not in SECTIONS:
            raise ValueError(f"section must be one of {', '.join(SECTIONS)}")
        text = json.dumps(self._section(state, section), indent=1, ensure_ascii=False)
        limit = max(1, min(int(limit), 50000))
        offset = max(0, int(offset))
        end = min(len(text), offset + limit)
        return {**head, "section": section, "offset": offset, "limit": limit, "total": len(text),
                "nextOffset": end if end < len(text) else None, "text": text[offset:end]}

    @staticmethod
    def _section(state: dict[str, Any] | None, section: str) -> Any:
        if state is None:
            return None
        if section == "context_pack":
            return state.get("context_pack")
        return (state.get("record") or {}).get(section, [])

    def model_objects(self, prefix: str = "", offset: int = 0, limit: int = 200) -> dict[str, Any]:
        import rhino3dm

        rows = []
        for obj in rhino3dm.File3dm.Read(str(self.current)).Objects:
            name = obj.Attributes.Name
            mesh = obj.Geometry
            if not name or not isinstance(mesh, rhino3dm.Mesh) or not name.startswith(prefix):
                continue
            box = mesh.GetBoundingBox()
            rows.append({"name": name, "min": [round(box.Min.X, 4), round(box.Min.Y, 4), round(box.Min.Z, 4)],
                         "max": [round(box.Max.X, 4), round(box.Max.Y, 4), round(box.Max.Z, 4)],
                         "hidden": not obj.Attributes.Visible})
        rows.sort(key=lambda r: r["name"])
        offset, limit = max(0, int(offset)), max(1, min(int(limit), 400))
        end = min(len(rows), offset + limit)
        return {"file": self.current.name, "total": len(rows), "offset": offset, "nextOffset": end if end < len(rows) else None,
                "objects": rows[offset:end]}

    # ---- changing
    def submit_change(self, parameters: dict[str, float] | None = None, elements: list[dict[str, Any]] | None = None,
                      adopt: bool = True, summary: str | None = None) -> dict[str, Any]:
        from labs.revision_tasks import reviser

        deltas = [{"target": f"parameter:{key}", "parameter": key, "to": value} for key, value in (parameters or {}).items()]
        deltas += [{"target": f"entity:{row['id'].removeprefix('obj-')}", "parameter": row["param"], "to": row["value"]}
                   for row in (elements or ())]
        if not deltas:
            raise ValueError("give at least one parameter or element change")
        n = len(self.submissions) + 1
        started = time.perf_counter()
        revision = reviser.revise(self.current, fmt="3dm", arm="C-task", deltas=deltas, out_dir=self.out, stem=f"state-{n:03d}",
                                  context_root=self.out / f"context-{n:03d}", utterance=self.config.get("user_message") or summary or "change")
        written = revision.written.path if revision.written is not None else None
        adopted = bool(adopt and written is not None)
        row = {"submission": n, "deltas": deltas, "claim": revision.claim, "reasons": revision.reasons,
               "file": str(written) if written else None, "adopted": adopted, "summary": summary,
               "seconds": round(time.perf_counter() - started, 1)}
        self.submissions.append(row)
        if adopted:
            self.current = written
        self._write_current()
        violated = [c["relation_id"] for c in revision.declared if c.get("status") == "violated"]
        return {"claim": revision.claim, "reasons": revision.reasons, "file": written.name if written else None, "adopted": adopted,
                "current_file": self.current.name, "changed_targets": [d["target"] for d in deltas],
                "closure_size": len(revision.closure), "declared_checks": {"total": len(revision.declared), "violated": violated},
                "obligations_violated": [o["obligation_id"] for o in revision.obligations if o.get("status") == "violated"],
                "new_interference": revision.new_interference}

    def _write_current(self) -> None:
        (self.out / "current.json").write_text(json.dumps({"start": str(self.config["file"]), "current": str(self.current),
                                                           "submissions": self.submissions}, indent=1, default=str) + "\n",
                                               encoding="utf-8", newline="\n")

    # ---- one MCP tool call
    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        row: dict[str, Any] = {"call": self.calls, "name": name, "arguments": {k: v for k, v in arguments.items() if k != "summary"}}
        try:
            if self.calls > int(self.config.get("max_calls", MAX_CALLS)):
                raise PermissionError(f"This request's {self.config.get('max_calls', MAX_CALLS)} tool calls are spent. Answer the "
                                      "architect now, without further tools.")
            if name not in TOOLS:
                raise ValueError(f"unknown tool {name}")
            value = getattr(self, name)(**arguments)
            text = json.dumps(value, ensure_ascii=False)
            row.update(is_error=False, chars=len(text))
            return {"content": [{"type": "text", "text": text}]}
        except Exception as exc:  # noqa: BLE001 - every failure is the tool's answer, never the server's end
            row.update(is_error=True, error=f"{type(exc).__name__}: {exc}"[:600])
            return {"isError": True, "content": [{"type": "text", "text": json.dumps({"code": type(exc).__name__, "detail": str(exc)[:1500]})}]}
        finally:
            log = self.config.get("log")
            if log:
                with Path(log).open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **row}, ensure_ascii=False, default=str) + "\n")


def handle(server: StateFileServer, message: dict[str, Any]) -> dict[str, Any] | None:
    """One JSON-RPC message; None for a notification."""

    if "id" not in message:
        return None
    method, params = message.get("method"), message.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}},
                  "serverInfo": {"name": SERVER, "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOL_LIST}
    elif method == "tools/call":
        result = server.call(str(params.get("name", "")), params.get("arguments") or {})
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Unknown method"}}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def serve(config: dict[str, Any], stdin=None, stdout=None) -> None:
    server = StateFileServer(config)
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        answer = handle(server, message)
        if answer is not None:
            stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
            stdout.flush()


def server_config(config_file: Path, python: str = sys.executable) -> dict[str, Any]:
    """The CLI's MCP entry for this server."""

    return {"command": python, "args": [str(Path(__file__).resolve()), str(config_file)], "alwaysLoad": True}


if __name__ == "__main__":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    serve(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))

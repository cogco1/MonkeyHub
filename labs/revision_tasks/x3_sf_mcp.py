"""X3 SF: a stdio MCP server over one C-task 3DM state file, and nothing else (no Hub, no project).

The CLI starts it by path with one JSON configuration file::

    python x3_sf_mcp.py CONFIG.json
    {"file": "<the previous request's state file>", "out": "<new directory for this session's files>",
     "user_message": "<the architect's words, the reviser's utterance>", "log": "<calls.jsonl>", "max_calls": 30}

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
  ``rebuild_from_state`` (default false; X3 protocol decision, harness-added ``sf_rebuild_from_state``): for a file whose
  only failed check is its geometry (``GEOMETRY_BINDING_STALE``: objects changed outside the design flow, the state and
  its digests intact), the carried state is first realized again into a file of its own (``state-NNN-rebuilt``, its
  parent the current file), the change is applied to that, and every outside change to the geometry is discarded and
  listed (object, box in the file and as the state realizes it, centre shift). Any other refusal stands as without it.
  ``REBUILD_FROM_STATE_ALLOWED`` turns the option off.

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
MAX_CALLS = 30
PAGE = 20000
# rebuild_from_state on submit_change: decided on (2026-10-14); False refuses the option outright.
REBUILD_FROM_STATE_ALLOWED = True
GEOMETRY_STALE = "GEOMETRY_BINDING_STALE"

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
        "current design; with adopt false it is only tried. rebuild_from_state (default false): only for a current file whose "
        "check says its geometry no longer matches the state it carries (GEOMETRY_BINDING_STALE: objects changed outside "
        "the design) while the state itself is intact. The change is then applied to the carried state and the whole model "
        "rebuilt from it: every outside change to the geometry is discarded, not adopted, and the answer lists each one "
        "(the object and how far it moved or changed size). Report them to the architect as a proposal to confirm (e.g. "
        "'<object> was moved <distance> outside the design; shall I adopt that as a decision?'). Any other refusal of the "
        "file stands."),
     "inputSchema": {"type": "object", "properties": {
         "parameters": {"type": "object", "additionalProperties": {"type": "number"}},
         "elements": {"type": "array", "items": {"type": "object", "properties": {
             "id": {"type": "string"}, "param": {"type": "string"}, "value": {"type": "number"}},
             "required": ["id", "param", "value"], "additionalProperties": False}},
         "adopt": {"type": "boolean", "default": True},
         "rebuild_from_state": {"type": "boolean", "default": False},
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
                      adopt: bool = True, summary: str | None = None, rebuild_from_state: bool = False) -> dict[str, Any]:
        from labs.revision_tasks import reviser

        deltas = [{"target": f"parameter:{key}", "parameter": key, "to": value} for key, value in (parameters or {}).items()]
        deltas += [{"target": f"entity:{row['id'].removeprefix('obj-')}", "parameter": row["param"], "to": row["value"]}
                   for row in (elements or ())]
        if not deltas:
            raise ValueError("give at least one parameter or element change")
        if rebuild_from_state is not False and not REBUILD_FROM_STATE_ALLOWED:
            raise PermissionError("rebuild_from_state is not available in this session")
        n = len(self.submissions) + 1
        started = time.perf_counter()
        utterance = self.config.get("user_message") or summary or "change"
        source, rebuilt = self.current, None
        if rebuild_from_state is True:
            rebuilt = self._rebuild(n, utterance)
            if rebuilt.get("refused"):
                row = {"submission": n, "deltas": deltas, "claim": "refuse", "reasons": [rebuilt["refused"]], "file": None,
                       "adopted": False, "summary": summary, "rebuild": rebuilt, "seconds": round(time.perf_counter() - started, 1)}
                self.submissions.append(row)
                self._write_current()
                return {"claim": "refuse", "reasons": [rebuilt["refused"]], "file": None, "adopted": False, "current_file": self.current.name,
                        "changed_targets": [d["target"] for d in deltas], "rebuild_from_state": _public(rebuilt)}
            if rebuilt.get("file"):
                source = Path(rebuilt["file"])
        revision = reviser.revise(source, fmt="3dm", arm="C-task", deltas=deltas, out_dir=self.out, stem=f"state-{n:03d}",
                                  context_root=self.out / f"context-{n:03d}", utterance=utterance)
        written = revision.written.path if revision.written is not None else None
        adopted = bool(adopt and written is not None)
        row = {"submission": n, "deltas": deltas, "claim": revision.claim, "reasons": revision.reasons,
               "file": str(written) if written else None, "adopted": adopted, "summary": summary,
               "seconds": round(time.perf_counter() - started, 1)}
        if rebuilt is not None:
            row["rebuild"] = rebuilt
        self.submissions.append(row)
        if adopted:
            self.current = written
        self._write_current()
        violated = [c["relation_id"] for c in revision.declared if c.get("status") == "violated"]
        answer = {"claim": revision.claim, "reasons": revision.reasons, "file": written.name if written else None, "adopted": adopted,
                  "current_file": self.current.name, "changed_targets": [d["target"] for d in deltas],
                  "closure_size": len(revision.closure), "declared_checks": {"total": len(revision.declared), "violated": violated},
                  "obligations_violated": [o["obligation_id"] for o in revision.obligations if o.get("status") == "violated"],
                  "new_interference": revision.new_interference}
        if rebuilt is not None:
            answer["rebuild_from_state"] = _public(rebuilt)
        return answer

    def _rebuild(self, n: int, utterance: str) -> dict[str, Any]:
        """The current file realized again from the state it carries, when its geometry alone fails the binding check.

        Returns ``{"needed": False}`` for a file every check passes and ``{"needed": False, "refusal": ...}`` for one refused
        for any other reason (the reviser then refuses it as it always does); otherwise the rebuilt file and each outside
        change it discards. A rebuild whose record or state differs from the carried one is refused (never expected).
        """

        from labs.revision_tasks import carrier, reviser

        try:
            read, stale = carried_state(self.current)
        except carrier.CarrierRefused as exc:
            return {"needed": False, "refusal": f"{exc.code}: {exc.detail}"[:600]}
        if stale is None:
            return {"needed": False}
        written = reviser.write_state_file(read.record, arm=read.arm, fmt="3dm", out_dir=self.out, stem=f"state-{n:03d}-rebuilt",
                                           parent_sha256=read.file_sha256, context_root=self.out / f"context-{n:03d}-rebuilt",
                                           utterance=utterance, context_pack=read.context_pack)
        same = {key: written.binding.get(key) == read.binding.get(key) for key in ("record_digest", "state_digest", "sufficient_digest")}
        if not all(same.values()):
            return {"needed": True, "file": str(written.path), "same": same,
                    "refused": {"code": "REBUILD_STATE_DIFFERS", "detail": f"the rebuilt file does not carry the same state: {same}"}}
        recorded = read.binding.get("element_geometry_digests") or {}
        realized = written.binding.get("element_geometry_digests") or {}
        outside, inside = object_boxes(self.current), object_boxes(written.path)
        discarded = []
        for name in stale["changed"]:
            discarded.append({"object": name, "change": "geometry changed outside", "box_in_file": outside.get(name),
                              "box_from_state": inside.get(name), "centre_shift": _shift(outside.get(name), inside.get(name), "centre"),
                              "size_change": _shift(outside.get(name), inside.get(name), "size")})
        for name in stale["added"]:
            discarded.append({"object": name, "change": "added outside (dropped)", "box_in_file": outside.get(name)})
        for name in stale["removed"]:
            discarded.append({"object": name, "change": "removed outside (restored)", "box_from_state": inside.get(name)})
        return {"needed": True, "file": str(written.path), "file_name": written.path.name, "sha256": carrier.sha256_bytes(written.path.read_bytes()),
                "parent": self.current.name, "parent_sha256": read.file_sha256, "binding_refusal": f"{stale['code']}: {stale['detail']}"[:600],
                "discarded_outside_changes": discarded,
                "realized_equals_recorded": realized == recorded,
                "realized_differs_from_recorded": sorted(k for k in set(recorded) | set(realized) if recorded.get(k) != realized.get(k))[:20]}

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


REBUILD_NOTE = ("These changes were made to the file outside the design and are not adopted: the change was applied to the state the "
                "file carries and the model rebuilt from it. Report each to the architect as a proposal to confirm.")


def _public(rebuilt: dict[str, Any]) -> dict[str, Any]:
    """What the agent is told about a rebuild (no local paths)."""

    if not rebuilt.get("needed"):
        return {"needed": False, **({"refusal": rebuilt["refusal"]} if rebuilt.get("refusal") else {})}
    keys = ("file_name", "parent", "binding_refusal", "discarded_outside_changes", "realized_equals_recorded", "refused")
    return {"needed": True, **{k: rebuilt[k] for k in keys if k in rebuilt}, "note": REBUILD_NOTE}


def carried_state(path: Path):
    """``(StateFile, None)`` for a file every binding check passes; ``(StateFile, stale)`` for one whose only failed check is its
    geometry (``GEOMETRY_BINDING_STALE``): the state then passes every other check (state, record, context and design-state
    digests) and ``stale`` names what differs (changed, added, removed objects). Any other refusal is raised as it is."""

    from labs.revision_tasks import carrier

    try:
        return carrier.read_three_dm(path), None
    except carrier.CarrierRefused as exc:
        if exc.code != GEOMETRY_STALE:
            raise
        refusal = exc
    geometry, strings = carrier.three_dm_geometry(path)
    state, bound = json.loads(strings["archflow:state"]), json.loads(strings["archflow:binding"])
    recorded = dict(bound.get("element_geometry_digests") or {})
    # Every check again with the geometry the binding records standing in for the file's: only the geometry may fail.
    read = carrier._checked(state, bound, carrier.sha256_bytes(path.read_bytes()), recorded,
                            strings.get("archflow:design_state_digest"), "3dm")
    return read, {"code": refusal.code, "detail": refusal.detail,
                  "changed": sorted(n for n in recorded if n in geometry and geometry[n] != recorded[n]),
                  "added": sorted(set(geometry) - set(recorded)), "removed": sorted(set(recorded) - set(geometry))}


def object_boxes(path: Path) -> dict[str, dict[str, list[float]]]:
    """Each named mesh's box in a model file (as ``model_objects`` reads it)."""

    import rhino3dm

    boxes = {}
    for obj in rhino3dm.File3dm.Read(str(path)).Objects:
        if obj.Attributes.Name and isinstance(obj.Geometry, rhino3dm.Mesh):
            box = obj.Geometry.GetBoundingBox()
            boxes[obj.Attributes.Name] = {"min": [round(box.Min.X, 4), round(box.Min.Y, 4), round(box.Min.Z, 4)],
                                          "max": [round(box.Max.X, 4), round(box.Max.Y, 4), round(box.Max.Z, 4)]}
    return boxes


def _shift(outside: dict[str, Any] | None, inside: dict[str, Any] | None, what: str) -> list[float] | None:
    """The file's box less the box the state realizes: its centre's shift or its size's change, per axis in metres."""

    if not outside or not inside:
        return None
    if what == "centre":
        return [round((outside["min"][i] + outside["max"][i] - inside["min"][i] - inside["max"][i]) / 2, 4) + 0.0 for i in range(3)]
    return [round((outside["max"][i] - outside["min"][i]) - (inside["max"][i] - inside["min"][i]), 4) + 0.0 for i in range(3)]


def handle(server: StateFileServer, message: dict[str, Any]) -> dict[str, Any] | None:
    """One JSON-RPC message; None for a notification."""

    if "id" not in message:
        return None
    method, params = message.get("method"), message.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}},
                  "serverInfo": {"name": SERVER, "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{**tool, "_meta": {"anthropic/maxResultSizeChars": 400000}} for tool in TOOL_LIST]}
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

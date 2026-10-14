"""State-bearing files: the design state written into (or beside) a model file, and read back with its binding checked.

Four arms carry the same state and differ only in where it lives and what binds it:

| arm       | where the state is                                   | binding |
|-----------|------------------------------------------------------|---------|
| C-full    | in the file: the whole record                        | full    |
| C-task    | in the file: the task view of the record + ContextPack@1 | full |
| C-unbound | in the file: the whole record                        | no per-object geometry digests |
| D         | beside the file (sidecar JSON); the file keeps only the existing ``archflow:*`` references | full |

The full binding is ``{record_digest, state_digest, sufficient_digest, parent_file_sha256,
element_geometry_digests}``. Its location follows the state.

3DM: the state goes into the document string ``archflow:state`` through
``execute_occt_export(provenance=...)`` and is verified by the exporter's own
cold read. The per-object geometry digests exist only once the file does, so a
second step reads the file with rhino3dm, adds the ``archflow:binding`` string and
writes it again (object geometry is unchanged by that rewrite; ``test_carrier``
checks it). GLB: converted from that 3DM by ``InProcessMeshProvider`` (meshes only);
the state and binding go into ``asset.extras.archflow``, identities into
``node.extras.archflow``.

Reading checks, in order:
1. the state and binding are present;
2. the record matches its digest;
3. the pack matches its digest;
4. the file's design-state reference matches the record;
5. every object's geometry digest matches.

A file that fails any check is refused with a code; it is never repaired.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from archflow.contracts.canonical import canonical_json
from archflow.state.state_record import StateRecord

from labs.revision_tasks.context_pack import pack_digest

SCHEMA = "ArchFlowStateFile@1"
ARMS = ("C-full", "C-task", "C-unbound", "D")
FORMATS = ("3dm", "glb")
TASK_VIEW_SCHEMAS = frozenset({"Element@1", "Level@1", "GridAxis@1", "Component@1"})


class CarrierRefused(ValueError):
    """The state-bearing file cannot be read as a bound design state."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class StateFile:
    record: StateRecord
    context_pack: dict[str, Any] | None
    binding: dict[str, Any]
    file_sha256: str
    arm: str
    fmt: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def task_view(record: StateRecord) -> StateRecord:
    """The rows a revision needs: elements and what they stand on, parameters, relations among kept entities, obligations."""

    from dataclasses import replace

    kept = tuple(e for e in record.entities if e.schema in TASK_VIEW_SCHEMAS)
    ids = {e.entity_id for e in kept}
    relations = tuple(r for r in record.relations if r.subject in ids and r.object in ids)
    return replace(record, entities=kept, relations=relations, evidence_refs=record.evidence_refs[:1], basis_refs=())


def payload(record: StateRecord, arm: str, context_pack: dict[str, Any] | None) -> dict[str, Any]:
    return {"schema": SCHEMA, "arm": arm, "record": record.to_dict(), "context_pack": context_pack if arm == "C-task" else None}


def binding(record: StateRecord, bound_state_digest: str, arm: str, context_pack: dict[str, Any] | None,
            parent_sha256: str | None, geometry: dict[str, str] | None) -> dict[str, Any]:
    return {"schema": SCHEMA, "arm": arm, "record_digest": record.digest, "state_digest": bound_state_digest,
            "sufficient_digest": pack_digest(context_pack) if arm == "C-task" else None,
            "parent_file_sha256": parent_sha256,
            "element_geometry_digests": None if arm == "C-unbound" else dict(sorted((geometry or {}).items()))}


# ---------------------------------------------------------------- 3DM

def three_dm_geometry(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """(object name -> encoded-geometry SHA-256, archflow document strings) from the read-only inspector."""

    from monkeycad.formats.three_dm_inspector import inspect_three_dm

    inspection = inspect_three_dm(path)
    geometry = {str(row["name"]): str(row["geometry_sha256"]) for row in inspection.object_geometry_sha256 if row["name"]}
    strings = {str(r["key"]): str(r["value"]) for r in inspection.document_user_strings if str(r["key"]).startswith("archflow:")}
    return geometry, strings


def seal_three_dm(exported: Path, final: Path, binding_value: dict[str, Any] | None) -> None:
    """Copy the exported model to ``final``, adding the binding string when one is given (harness step)."""

    import rhino3dm

    if binding_value is None:
        final.write_bytes(exported.read_bytes())
        return
    model = rhino3dm.File3dm.Read(str(exported))
    model.Strings["archflow:binding"] = canonical_json(binding_value)
    if not model.Write(str(final), 8):
        raise CarrierRefused("WRITE_FAILED", f"{final.name}: rhino3dm could not write the sealed model")


def read_three_dm(path: Path, *, sidecar: Path | None = None) -> StateFile:
    """A 3DM state file (C arms) or a 3DM with its sidecar (D), with every binding check applied."""

    data = path.read_bytes()
    geometry, strings = three_dm_geometry(path)
    if sidecar is not None:
        try:
            carried = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CarrierRefused("METADATA_MISSING", f"sidecar unreadable: {exc}") from exc
        state, bound = carried.get("state"), carried.get("binding")
    else:
        try:
            state = json.loads(strings["archflow:state"])
            bound = json.loads(strings["archflow:binding"])
        except KeyError as exc:
            raise CarrierRefused("METADATA_MISSING", f"no {exc.args[0]} in the file") from exc
        except ValueError as exc:
            raise CarrierRefused("METADATA_CORRUPT", str(exc)) from exc
    return _checked(state, bound, sha256_bytes(data), geometry, strings.get("archflow:design_state_digest"), "3dm")


# ---------------------------------------------------------------- GLB

GLB_MAGIC, JSON_CHUNK = 0x46546C67, 0x4E4F534A


def glb_split(data: bytes) -> tuple[dict[str, Any], list[tuple[int, bytes]]]:
    magic, version, length = struct.unpack_from("<III", data, 0)
    if (magic, version, length) != (GLB_MAGIC, 2, len(data)):
        raise CarrierRefused("FILE_CORRUPT", "not a glTF 2.0 binary of its stated length")
    offset, chunks = 12, []
    while offset < len(data):
        n, kind = struct.unpack_from("<II", data, offset)
        chunks.append((kind, data[offset + 8: offset + 8 + n]))
        offset += 8 + n
    if not chunks or chunks[0][0] != JSON_CHUNK:
        raise CarrierRefused("FILE_CORRUPT", "no JSON chunk")
    return json.loads(chunks[0][1].decode("utf-8").rstrip(" \0")), chunks[1:]


def glb_join(document: dict[str, Any], tail: list[tuple[int, bytes]]) -> bytes:
    body = json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body += b" " * (-len(body) % 4)
    chunks = [(JSON_CHUNK, body), *tail]
    payload_bytes = b"".join(struct.pack("<II", len(c), k) + c for k, c in chunks)
    return struct.pack("<III", GLB_MAGIC, 2, 12 + len(payload_bytes)) + payload_bytes


def glb_geometry(document: dict[str, Any], tail: list[tuple[int, bytes]]) -> dict[str, str]:
    """Node name -> SHA-256 of the exact bytes of its mesh's position and index accessors."""

    binary = tail[0][1] if tail else b""

    def accessor_bytes(index: int) -> bytes:
        accessor = document["accessors"][index]
        view = document["bufferViews"][accessor["bufferView"]]
        start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        return binary[start: view.get("byteOffset", 0) + view["byteLength"]]

    digests = {}
    for node in document.get("nodes", []):
        if "mesh" not in node or not node.get("name"):
            continue
        digest = hashlib.sha256()
        for primitive in document["meshes"][node["mesh"]]["primitives"]:
            digest.update(accessor_bytes(primitive["attributes"]["POSITION"]))
            if "indices" in primitive:
                digest.update(accessor_bytes(primitive["indices"]))
        digest.update(json.dumps({k: node[k] for k in ("matrix", "translation", "rotation", "scale") if k in node}, sort_keys=True).encode())
        digests[node["name"]] = digest.hexdigest()
    return digests


def glb_from_three_dm(three_dm: Path) -> tuple[dict[str, Any], list[tuple[int, bytes]]]:
    from monkeycad.formats.conversion import InProcessMeshProvider

    converted = InProcessMeshProvider().convert(three_dm.read_bytes(), "3dm", "glb")
    return glb_split(bytes(converted.data))


def write_glb(document: dict[str, Any], tail: list[tuple[int, bytes]], final: Path, *, state: dict[str, Any] | None,
              binding_value: dict[str, Any] | None, design_state_digest: str) -> None:
    """Embed state and binding (C arms) or only the design-state reference (D), and identities on every node."""

    document = json.loads(json.dumps(document))
    archflow: dict[str, Any] = {"design_state_digest": design_state_digest}
    if state is not None:
        archflow.update({"state": state, "binding": binding_value})
    document.setdefault("asset", {}).setdefault("extras", {})["archflow"] = archflow
    for node in document.get("nodes", []):
        if node.get("name"):
            node.setdefault("extras", {})["archflow"] = {"object_ref": f"cad-object:{node['name']}"}
    final.write_bytes(glb_join(document, tail))


def read_glb(path: Path, *, sidecar: Path | None = None) -> StateFile:
    data = path.read_bytes()
    document, tail = glb_split(data)
    geometry = glb_geometry(document, tail)
    archflow = document.get("asset", {}).get("extras", {}).get("archflow")
    reference = archflow.get("design_state_digest") if isinstance(archflow, dict) else None
    if sidecar is not None:
        try:
            carried = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CarrierRefused("METADATA_MISSING", f"sidecar unreadable: {exc}") from exc
        state, bound = carried.get("state"), carried.get("binding")
    else:
        if not isinstance(archflow, dict) or "state" not in archflow or "binding" not in archflow:
            raise CarrierRefused("METADATA_MISSING", "no archflow state in asset.extras")
        state, bound = archflow["state"], archflow["binding"]
    return _checked(state, bound, sha256_bytes(data), geometry, reference, "glb")


# ---------------------------------------------------------------- the checks every arm and format share

def _checked(state: Any, bound: Any, file_sha: str, geometry: dict[str, str], reference: str | None, fmt: str) -> StateFile:
    from labs.revision_tasks.fixtures import bound as bind_to_run

    if not isinstance(state, dict) or not isinstance(bound, dict):
        raise CarrierRefused("METADATA_MISSING", "state or binding absent")
    if state.get("schema") != SCHEMA or bound.get("schema") != SCHEMA:
        raise CarrierRefused("SCHEMA_UNSUPPORTED", f"{state.get('schema')!r} / {bound.get('schema')!r}")
    try:
        record = StateRecord.from_dict(state["record"])
    except Exception as exc:  # noqa: BLE001 - any malformed record is one refusal
        raise CarrierRefused("STATE_INVALID", f"{type(exc).__name__}: {exc}") from exc
    if record.digest != bound.get("record_digest"):
        raise CarrierRefused("STATE_DIGEST_MISMATCH", "the record is not the one the binding names")
    if bind_to_run(record).state_digest != bound.get("state_digest"):
        raise CarrierRefused("STATE_DIGEST_MISMATCH", "the bound state digest does not follow from the record")
    pack = state.get("context_pack")
    if pack_digest(pack) != bound.get("sufficient_digest"):
        raise CarrierRefused("CONTEXT_DIGEST_MISMATCH", "the context pack is not the one the binding names")
    if reference is not None and reference != bound.get("state_digest"):
        raise CarrierRefused("DESIGN_STATE_REFERENCE_MISMATCH", "the model's own design-state reference names another state")
    expected = bound.get("element_geometry_digests")
    if expected is not None:
        if set(expected) != set(geometry):
            raise CarrierRefused("GEOMETRY_BINDING_STALE", f"objects differ: {sorted(set(expected) ^ set(geometry))[:6]}")
        changed = sorted(name for name in expected if expected[name] != geometry[name])
        if changed:
            raise CarrierRefused("GEOMETRY_BINDING_STALE", f"geometry differs from the binding: {changed[:6]}")
    return StateFile(record, pack, bound, file_sha, str(bound.get("arm")), fmt)

"""What studio_request and studio_schema may call, and what they answer about it.

The allow-list of Runtime actions a chat may read or change, the refusals that
name the route to use instead, action discovery from the bound Runtime's own
OpenAPI, one action's request contract compacted for a reader, and the two
requests the chat checks before the Runtime is asked: a registered page read
and a render submission.
"""

from __future__ import annotations

import json
import re
from typing import Mapping

from . import guides, providers, transport

from ..models import HubFailure


_READ = re.compile(r"^/api/(exports(?:/[A-Za-z0-9_-]+)?|project|construction(?:/model)?|domains(?:/[a-z]+/readiness)?|state/(?:frame|volumes)|semantics|program|options|board|artifacts|model-assets/[0-9a-f]{64}/index|documents|document-annotations|studies/[A-Za-z0-9][A-Za-z0-9._-]{0,79}|decisions(?:/[A-Za-z0-9_-]+)?|memory(?:/locate)?|drawings/(?:styles|model-view|plans/vector|plans/dimensions|corrections)|capabilities(?:/[A-Za-z0-9_.-]+)?|proposals/[A-Za-z0-9_-]+|jobs/[A-Za-z0-9_-]+|candidates/[A-Za-z0-9_-]+(?:/compare)?|admissions|working-source|working-draft/revision|render/(?:capabilities|jobs(?:/[A-Za-z0-9_-]+)?))$")
_POST = re.compile(r"^/api/(exports|project/modeling|intents/context|board/export|decisions(?:/[A-Za-z0-9_-]+/revisions)?|memory(?:/about|/[A-Za-z0-9_-]+/revisions)?|state/closure|capabilities/[A-Za-z0-9_.-]+/run|proposals|proposals/(?:construction|facets|hosted-opening)|proposals/[A-Za-z0-9_-]+/candidate|program|options|options/[A-Za-z0-9_-]+/select|candidates/combine|drawings/(elevations|sheets|section-perspectives|plans|plans/status)|admissions|render/jobs)$")
_WRITE = re.compile(r"^/api/(board|document-annotations|working-draft)$")
# The agent routes the construction contract replaced (#419). The Studio still
# serves them to its own web client, so a refusal names the agent's route
# instead of calling them unknown or listing look-alike actions.
_REPLACED_ACTIONS = {
    ("GET", "/api/state"): "GET /api/construction/model (add ?run=<candidateId> for a candidate)",
    **{("POST", f"/api/proposals/{name}"): "POST /api/proposals/construction with one script"
       for name in ("sketch", "transform", "push-pull", "delete", "elevation")},
}


# What an agent's semanticEdit may still carry (#419): parameters, relations,
# readings and component intents. Geometry has one route, meaning another; an
# upsert that leaves out schema is recognised by the fields only geometry has.
_GEOMETRY_SCHEMAS = frozenset({"Element@1", "Type@1"})
_GEOMETRY_FIELDS = frozenset({"producer", "params", "references", "type_ref"})
_MEANING_FIELDS = frozenset({"semantic_kind", "semanticKind", "roles", "conditions", "facets"})
_GEOMETRY_REFUSAL = ("Geometry is authored with POST /api/proposals/construction; semanticEdit carries parameters, "
                     "relations, readings and component intents.")
_MEANING_REFUSAL = "Meaning is added with POST /api/proposals/facets."


# POSTs that only read. They go to the bound Studio as a GET would, with no
# mutation admission: there is nothing to admit, recover or replay.
_POST_READS = {"/api/intents/context", "/api/drawings/plans/status", "/api/memory/about"}


def _schema_allowed(method: str, path: str) -> bool:
    """Apply the execution allow-list to a schema template, never to a write."""
    pattern = {"GET": _READ, "POST": _POST, "PUT": _WRITE}.get(method)
    return pattern is not None and any(
        pattern.fullmatch(re.sub(r"\{[^}/]+\}", sample, path))
        for sample in ("id", "0" * 64)
    )


def _semantic_edit_refusal(body) -> HubFailure | None:
    """Why an agent's semanticEdit may not be sent, or None when it carries neither geometry nor meaning.

    Studio's web client keeps authoring its own rows; this narrows only what
    the chat can ask for, and says which route does the refused part.
    """
    if not isinstance(body, Mapping):
        return None
    for edit in (body.get("semanticEdit"), body.get("semantic_edit")):
        rows = edit.get("entities") if isinstance(edit, Mapping) else None
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, Mapping):
                continue  # the Runtime's own validation answers a malformed row
            fields = row.get("fields") if isinstance(row.get("fields"), Mapping) else {}
            if row.get("schema") in _GEOMETRY_SCHEMAS or _GEOMETRY_FIELDS.intersection(fields):
                return HubFailure(422, "CHAT_TOOL_INVALID", _GEOMETRY_REFUSAL)
            if _MEANING_FIELDS.intersection(fields):
                return HubFailure(422, "CHAT_TOOL_INVALID", _MEANING_REFUSAL)
    return None


def _available_actions(document: dict) -> list[dict]:
    """A view of this Runtime and this chat's transport, not another registry."""
    return [
        {"method": method.upper(), "path": path,
         "summary": str(operation.get("summary") or operation.get("operationId") or "")[:180]}
        for path, operations in sorted(document.get("paths", {}).items())
        for method, operation in sorted(operations.items())
        if isinstance(operation, dict) and _schema_allowed(method.upper(), path)
    ]


def _discovery(arguments: dict) -> tuple[str | None, str, int, int]:
    """Check an action-discovery question before anything is started to answer it."""
    if set(arguments) - {"method", "path", "pathPrefix", "offset", "limit"}:
        raise HubFailure(422, "CHAT_TOOL_INVALID", "Action discovery takes optional method, pathPrefix, offset and limit; omit path.")
    method = arguments.get("method")
    prefix = arguments.get("pathPrefix", "/api/")
    offset, limit = arguments.get("offset", 0), arguments.get("limit", 30)
    if (method is not None and (not isinstance(method, str) or method.upper() not in {"GET", "POST", "PUT"})
            or not isinstance(prefix, str) or not re.fullmatch(r"/api(?:/[A-Za-z0-9_.{}-]*)*/?", prefix)
            or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50):
        raise HubFailure(422, "CHAT_TOOL_INVALID", "Use method GET/POST/PUT, pathPrefix under /api, offset >= 0 and limit 1..50.")
    return method, prefix, offset, limit


def _discover_actions(base: str, arguments: dict) -> dict:
    method, prefix, offset, limit = _discovery(arguments)
    rows = [row for row in _available_actions(transport._request_json(base, "/openapi.json"))
            if row["path"].startswith(prefix) and (method is None or row["method"] == method.upper())]
    result = {"actions": rows[offset:offset + limit], "total": len(rows), "offset": offset, "limit": limit,
              "note": "Current Runtime actions exposed to this chat. Choose method/path for studio_schema to read inputs; "
                      "studio_request executes. Project state, input validation and action-specific authority still apply."}
    guide = guides._guide(prefix) if offset == 0 else None
    if guide:
        # The domain's full text, once, where the upfront guide said it would be.
        result["guide"] = guide
    if offset + limit < len(rows):
        result["next"] = {"tool": "studio_schema", "arguments": {**arguments, "offset": offset + limit, "limit": limit}}
    return result


_CONTRACT_NOTE = "Request inputs only"
_NULL = {"type": "null"}
# Keys whose values are maps of names to schemas: the names are data, never
# schema keywords, so a property called "title" is kept.
_SCHEMA_MAPS = {"properties", "patternProperties", "$defs", "definitions"}


def _compact(schema):
    """A JSON Schema as a reader needs it: no generated titles, optional nulls folded.

    ``{"anyOf": [X, {"type": "null"}]}`` becomes X marked nullable, and the
    generator's per-field title (the field name again) is dropped. Everything
    that constrains a value, descriptions, enums and bounds, is kept.
    """
    if isinstance(schema, list):
        return [_compact(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    variants = schema.get("anyOf")
    if isinstance(variants, list) and len(variants) == 2 and _NULL in variants:
        rest = {key: value for key, value in schema.items() if key != "anyOf"}
        folded = next(item for item in variants if item != _NULL)
        return _compact({**folded, **rest, "nullable": True})
    return {key: ({name: _compact(item) for name, item in value.items()} if key in _SCHEMA_MAPS and isinstance(value, dict)
                  else _compact(value))
            for key, value in schema.items() if key != "title" and not (key == "additionalProperties" and value is True)}


_SHARED_MIN = 160


def _share_repeats(parts: dict, schemas: dict) -> None:
    """Name each subschema repeated verbatim, once, and point its repeats at that name.

    Producer contracts repeat whole subtrees (a wall's references appear under
    Element@1 and Type@1 alike, a level reference in every elevation), and a
    reader pays for every repeat. Largest first. Only schemas are shared: a
    map of property names is walked into, never replaced by a reference.
    """
    shared = 0
    while True:
        seen: dict[str, int] = {}

        def count(value, schema: bool):
            if isinstance(value, dict):
                if schema and "$ref" not in value:
                    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
                    if len(text) >= _SHARED_MIN:
                        seen[text] = seen.get(text, 0) + 1
                for key, item in value.items():
                    if key in _SCHEMA_MAPS and isinstance(item, dict):
                        for child in item.values():
                            count(child, True)
                    else:
                        count(item, True)
            elif isinstance(value, list):
                for item in value:
                    count(item, True)

        for value in parts.values():
            count(value, True)
        for value in schemas.values():
            count(value, False)  # already named
        repeated = [text for text, times in seen.items() if times > 1]
        if not repeated:
            return
        text = max(repeated, key=len)
        shared += 1
        name = f"Shared{shared}"
        reference = {"$ref": f"#/components/schemas/{name}"}

        def replace(value, schema: bool):
            if isinstance(value, dict):
                if schema and json.dumps(value, sort_keys=True, ensure_ascii=False) == text:
                    return dict(reference)
                return {key: ({child: replace(item[child], True) for child in item}
                              if key in _SCHEMA_MAPS and isinstance(item, dict) else replace(item, True))
                        for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item, True) for item in value]
            return value

        for key in list(parts):
            parts[key] = replace(parts[key], True)
        for key in list(schemas):
            schemas[key] = replace(schemas[key], False)
        schemas[name] = json.loads(text)


def _request_contract(document: dict, template: str, method: str, operation: dict) -> dict:
    """What one action takes: its query/path parameters, its body and the schemas they name.

    Responses are not described; the call itself answers with one. Headers are
    this adapter's to send, and the route's own prose is for its maintainers.
    """
    parameters = [{key: value for key, value in row.items() if key in {"name", "in", "required", "schema", "description"}}
                  for row in operation.get("parameters", ()) if isinstance(row, dict) and row.get("in") in {"query", "path"}]
    request = operation.get("requestBody") or {}
    body = request.get("content", {}).get("application/json", {}).get("schema", request if "$ref" in request else None)
    components = document.get("components", {}).get("schemas", {})
    schemas, pending = {}, [parameters, body]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            reference = value.get("$ref", "")
            key = reference.rsplit("/", 1)[-1]
            if reference.startswith("#/components/schemas/") and key not in schemas:
                schemas[key] = components.get(key, {})
                pending.append(schemas[key])
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    answer = {"path": template, "method": method, "summary": operation.get("summary") or operation.get("operationId")}
    parts = {key: _compact(value) for key, value in (("parameters", parameters), ("body", body)) if value}
    compacted = {key: _compact(value) for key, value in sorted(schemas.items())}
    _share_repeats(parts, compacted)
    answer.update(parts)
    if compacted:
        answer["components"] = {"schemas": compacted}
    answer["note"] = _CONTRACT_NOTE + "; each $ref names an entry of components.schemas. The call itself answers with its result."
    return answer


def _action_refusal(base: str | None, method: str, path: str) -> HubFailure:
    """Explain a refused path without choosing or executing a replacement.

    ``base`` is a Studio that is already running, or None: a refusal never
    starts a runtime just to explain itself.
    """
    discovery = "Use studio_schema with no path to list current chat actions, then select an exact method/path."
    try:
        if base is None:
            raise HubFailure(409, "CHAT_STUDIO_UNAVAILABLE", "No running Studio.")
        document = transport._request_json(base, "/openapi.json")
    except (HubFailure, OSError, ValueError):
        return HubFailure(422, "CHAT_TOOL_UNAVAILABLE",
                          f"This path is not exposed to the chat; the Runtime action list could not be read. {discovery}")
    exists = any(method.lower() in operations and re.fullmatch(re.sub(r"\{[^}]+\}", r"[^/]+", route), path)
                 for route, operations in document.get("paths", {}).items())
    if exists:
        return HubFailure(422, "CHAT_TOOL_UNAVAILABLE", f"{method} {path} exists in the Runtime but is not exposed to chat. {discovery}")
    domain = "/".join(path.split("/")[:3]) + "/"
    related = [f"{row['method']} {row['path']}" for row in _available_actions(document)
               if row["method"] == method and row["path"].startswith(domain)][:6]
    suggestions = " Related available actions: " + "; ".join(related) + "." if related else ""
    return HubFailure(422, "CHAT_ACTION_UNKNOWN",
                      f"The current Runtime has no {method} {path}; this is an unknown action, not a permission denial."
                      f"{suggestions} {discovery} Nothing was executed.")


def _read_drawing_page(base: str, body) -> dict:
    pages = body.get("pages") if isinstance(body, dict) else None
    if (not isinstance(pages, list) or len(pages) != 1 or not isinstance(pages[0], dict)
            or body.get("format") != "png" or body.get("zip", False) is not False):
        raise HubFailure(422, "CHAT_TOOL_INVALID", "Read one registered page with format=png and zip=false.")
    page = pages[0]
    if set(page) != {"runId", "assetSha256", "revisionRef", "pageIndex"}:
        raise HubFailure(422, "CHAT_TOOL_INVALID", "Copy runId, assetSha256, revisionRef (including null), and zero-based pageIndex from GET /api/documents or the generated drawing result.")
    max_edge = body.get("maxEdge", transport._PAGE_IMAGE_MAX_EDGE)
    if type(max_edge) is not int or not 1 <= max_edge <= transport._PAGE_IMAGE_MAX_EDGE:
        raise HubFailure(422, "CHAT_TOOL_INVALID", "maxEdge must be an integer from 1 to 2048.")
    # The existing owner verifies the digest, exact revision and page before
    # rasterizing in memory. This POST is a read and never enters admission.
    picture = transport._request_json(base, "/api/board/export", "POST", {**body, "maxEdge": max_edge}, png=True)
    return {**picture, "source": {"projectId": body["projectId"], **page},
            "representation": "registered-document-page", "annotationsIncluded": False}


def _render_provider_available(base: str, body) -> None:
    """Refuse a render request no configured provider of this Runtime takes, before it is admitted (#253).

    Availability is what the Runtime reports for its actually configured image
    adapters. With none, the answer says image generation is not configured;
    a placeholder or unavailable providerId never reaches the admission, the
    Runtime or a provider. The Runtime still makes every check of its own.
    """

    reported = transport._request_json(base, "/api/render/capabilities").get("providers") or []
    rows = [row for row in reported if isinstance(row, dict)]
    available = [row["providerId"] for row in rows if row.get("available") is True and isinstance(row.get("providerId"), str)]
    if isinstance(body, dict) and body.get("providerId") in available:
        return
    if not available:
        reasons = "; ".join(str(row["unavailableReason"]) for row in rows if row.get("unavailableReason"))
        raise HubFailure(503, "RENDER_UNAVAILABLE", "Image generation is not configured for this project"
                         + (f" ({providers._redact(reasons)[:300]})" if reasons else "")
                         + ", so nothing was submitted. Tell the user; do not retry or use another providerId.")
    raise HubFailure(422, "RENDER_PROVIDER_INVALID", "providerId must name an available provider from GET "
                     f"/api/render/capabilities: {', '.join(available)}. Nothing was submitted.")

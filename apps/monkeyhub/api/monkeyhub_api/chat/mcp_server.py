"""The stdio MCP server a CLI starts by this file's path, for one conversation.

It lists the chat's tools, with the guides that describe them, and answers
each call through ``tool_calls``. A Hub conversation passes its chat id; an
external host passes its project folder and source session instead and binds
them with presentation_bind.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

# A CLI starts this same file as its stdio MCP connection, which does not
# inherit the launcher's sys.path. A checkout lists where import names begin in
# its architecture policy and its roots go first (tools/dev/source_roots.py); a
# packaged interpreter ships no policy, and its python313._pth lists the roots.
if __package__ in {None, ""}:
    _source = Path(__file__).resolve().parents[5]
    _policy = _source / "governance" / "architecture_policy.json"
    if _policy.is_file():
        _roots = [str(_source / root) for root in json.loads(_policy.read_text(encoding="utf-8"))["python_source_roots"]]
        sys.path[:0] = [root for root in _roots if root not in sys.path]
    __package__ = "monkeyhub_api.chat"
    # Started as a script this module is __main__, so a module importing it by
    # name, as the store does to name this file, would execute a second copy.
    # One file is one module, whichever way it was started.
    sys.modules.setdefault("monkeyhub_api.chat.mcp_server", sys.modules[__name__])

from .. import projects
from . import guides, providers, tool_calls, transport

from ..models import ChatPresentationBindRequest, ChatPresentationRequest, HubFailure


def _mcp(hub: str, chat_id: str | None, external: ChatPresentationBindRequest | None = None) -> None:
    from .. import computer_tools

    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    presentation_binding = None
    request_fields = {
        "method": {"type": "string", "enum": ["GET", "POST", "PUT"]},
        "path": {"type": "string"},
        "body": {"type": "object", "additionalProperties": True},
    }
    # Two schemas, because the option belongs to one tool. A tool that cannot
    # wait must not advertise waiting: an ignored argument would read as a wait
    # that happened.
    input_schema = {"type": "object", "properties": dict(request_fields),
                    "required": ["method", "path"], "additionalProperties": False}
    schema_input = {**input_schema, "required": [], "properties": {
        **input_schema["properties"],
        "pathPrefix": {"type": "string", "description": "Discover actions below this API prefix (for example /api/drawings); omit path. Omit method to include reads AND writes."},
        "offset": {"type": "integer", "minimum": 0, "description": "Action discovery page offset; omit path."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Action discovery page size, default 30; omit path."},
    }}
    request_schema = {
        "type": "object", "properties": {
            **request_fields,
            "operationId": {"type": "string", "format": "uuid", "description": "Optional stable identity for this mutation. Reusing it returns the same admission/result and never executes the request twice. Different requests must use different ids."},
            "feedbackQuote": {"type": "string", "minLength": 1, "maxLength": 2000, "description": "Only for POST /api/decisions, /api/memory, their /revisions, /api/admissions or PUT /api/working-draft: select one exact, unique, continuous passage in the current user's message that carries the decision. Hub extracts these unedited words itself and retains the original message identity. Use for long messages; invented, rewritten or ambiguous passages are refused. Omit to retain the entire message when it fits."},
            "taskClass": {"type": "string", "enum": ["spatial_formal", "polish", "deterministic_edit"], "description": "Only for POST /api/admissions that admits a result: the class of the loop it closes, as visual_review names it, required unless a review of this user message already fixed it. deterministic_edit is complete once readback checks it; a spatial_formal or polish loop is admitted only after visual_review looked at its result or at an attempt the result supersedes."},
            "awaitSeconds": {
                "type": "integer", "minimum": 1, "maximum": tool_calls._AWAIT_MAX_S,
                "description": "Wait for one submitted change, in seconds; 60 suits an ordinary change. "
                               "Place beside method/path/body. Supported by POST /api/proposals/{id}/candidate "
                               "(no body; source comes from the proposal), or POST " + tool_calls._FINISHABLE +
                               " (body requires sourceRunId). Returns job, candidate and source comparison when available. "
                               "On timeout, continue the returned reads; do not resubmit the mutation. Other paths do not support waiting.",
            },
        }, "required": ["method", "path"], "additionalProperties": False,
    }
    # The review's own fields as the runtime route names them. projectId and
    # budgetState are Hub's to fill, so they are not offered at all.
    review_schema = {"type": "object", "properties": {
        "delivery": {"type": "string", "enum": ["frames", "observation"], "default": "frames",
                     "description": "frames returns exact images for you to inspect. observation uses the Runtime's separately configured structured visual provider."},
        "taskClass": {"type": "string", "enum": ["spatial_formal", "polish", "deterministic_edit"]},
        "polishRounds": {"type": "integer", "minimum": 1, "maximum": 4,
                         "description": "Only for polish: its rounds; above 2 only when the user asked to keep refining."},
        "reason": {"type": "string", "enum": ["first_bundle", "after_repair", "polish_round"]},
        "domain": {"type": "string", "enum": ["modeling", "board", "drawing", "render"]},
        "sourceRefs": {"type": "array", "minItems": 1, "maxItems": 4, "items": {
            "type": "object", "properties": {
                "kind": {"type": "string", "enum": ["model", "page"]}, "runId": {"type": "string"},
                "stateDigest": {"type": "string", "description": "model only"},
                "assetSha256": {"type": "string"},
                "revisionRef": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "page only; null when the registration has none"},
                "pageIndex": {"type": "integer", "minimum": 0, "description": "page only"},
            }, "required": ["kind", "runId", "assetSha256"], "additionalProperties": False}},
        "viewRecipe": {"type": "array", "minItems": 1, "maxItems": 4, "items": {"type": "string"}},
        "task": {"type": "string", "minLength": 1, "maxLength": 600},
        "criteria": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
            "type": "object", "properties": {"criterionId": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,39}$"},
                                             "text": {"type": "string", "minLength": 1, "maxLength": 300}},
            "required": ["criterionId", "text"], "additionalProperties": False}},
        "preserve": {"type": "array", "maxItems": 6, "items": {"type": "string", "minLength": 1, "maxLength": 300}},
        "knownFacts": {"type": "array", "maxItems": 8, "items": {"type": "string", "minLength": 1, "maxLength": 120}},
        "priorObservations": {"type": "array", "maxItems": 6, "items": {
            "type": "object", "properties": {"findingRef": {"type": "string"}, "type": {"type": "string"},
                                             "description": {"type": "string"}},
            "required": ["findingRef", "type", "description"], "additionalProperties": False}},
        "addressedFindingIds": {"type": "array", "maxItems": 8, "items": {"type": "string", "pattern": "^f[1-9][0-9]?$"}},
    }, "required": ["taskClass", "reason", "domain", "sourceRefs", "viewRecipe", "task", "criteria"],
        "additionalProperties": False}
    reviewing = chr(10).join([
        "Look once at exact project sources: by default delivery=frames returns native images with source metadata for YOU to inspect.",
        "The bound Runtime renders every frame; delivery alone supplies no findings or acceptance. Report only what you actually see.",
        "Optional delivery=observation uses the separately configured structured provider and returns findings. Use a review for a spatial or formal task (massing,",
        "proportion, relations, composition, a sheet's hierarchy) after a meaningful batch. A deterministic edit (a value, a",
        "dimension, a count) is checked by readback, not looked at. When the user asks to see a view, read",
        "GET /api/drawings/model-view through studio_request instead.",
        "taskClass spatial_formal allows a first_bundle review, then one after_repair review whose addressedFindingIds name",
        "findings of the last review your repair answered. polish allows polishRounds polish_round reviews (1-4, more than 2",
        "only when the user's words in this message ask to keep refining, such as 继续优化 or 打磨). deterministic_edit allows none.",
        "Hub holds the allowance of the user message you are answering, fixes its class once a review is spent, and starts a",
        "new one with the user's next message.",
        "Frames delivery creates no structured finding ids: after_repair is unavailable after it; never invent finding ids to get another look.",
        "A modeling review names one model {kind: 'model', runId, stateDigest, assetSha256}, the result's non-null modelSource",
        "unchanged, with viewRecipe from front, back, left, right, top, axon. Board, drawing and render reviews name registered",
        "pages {kind: 'page', runId, assetSha256, revisionRef, pageIndex} exactly as GET /api/documents lists them, with",
        "viewRecipe page-<pageIndex> of each (deduplicate repeated page numbers); different documents may each have page 0 and receive unique frame names. criteria [{criterionId, text}] say what to inspect, preserve what must not be",
        "disturbed, and knownFacts are exact readback values (levels, clear sizes) the observer should not ask about again.",
        "A finding marked escalate touches a preserve condition: ask the user about it instead of repairing and reviewing again.",
        "A look admits, continues and accepts nothing. A spatial_formal or polish loop is admitted only after a look at its",
        "result or at an attempt the result supersedes: look, then repair or stop, then admit.",
        "Refusals spend nothing: VISUAL_BUDGET_EXHAUSTED, VISUAL_REVIEW_NOT_WARRANTED, VISUAL_REVIEW_OUT_OF_ORDER,",
        "VISUAL_SOURCE_MISMATCH (read the current exact source), VISUAL_PROVIDER_UNAVAILABLE. VISUAL_PROVIDER_FAILED spends the review.",
    ])
    modelling = guides._MODELLING
    presentation_instructions = guides._PRESENTATION_INSTRUCTIONS if external else guides._NATIVE_PRESENTATION_INSTRUCTIONS
    tools = [
        {"name": "chat_present", "description": presentation_instructions, "inputSchema": {
            **ChatPresentationRequest.model_json_schema(),
            "properties": {key: ({**value, "enum": ["progress", "assistant"]} if key == "kind" and not external else
                                 {**value, "enum": ["streaming"], "default": "streaming"} if key == "status" and not external else value)
                           for key, value in ChatPresentationRequest.model_json_schema()["properties"].items()
                           if key not in {"projectId", "sourceSessionId"}},
            "required": ["turnId", "messageId", "kind"] if external else ["messageId", "kind"],
        }},
        {"name": "studio_schema", "description": "Discover current chat actions by omitting path; optional pathPrefix (such as /api/drawings) narrows the list "
         "and, for drawings, decisions, Board, exports or render, also answers that domain's guide. Omit method to include both reads and writes; "
         "follow next when paged. The list comes from the bound Runtime and chat allow-list. "
         "With an exact method/path, read the request inputs of that allowed Studio action: query parameters, body fields, enums and bounds. "
         "Responses are not described; the call itself answers with its result. "
         "Use it to clarify a field or correct a request; the bodies studio_request's description gives need no schema read. "
         "Paths may contain template segments, such as /api/proposals/{id}/candidate.",
         "inputSchema": schema_input},
        {"name": "studio_request", "description": modelling, "inputSchema": request_schema},
        {"name": "visual_review", "description": reviewing, "inputSchema": review_schema},
        {"name": "fab_request", "description": "Use MonkeyFab GET /api/fab/profiles or POST /api/fab/send for dry-run validation only. This tool never uploads or starts printing.", "inputSchema": input_schema},
        {"name": "attachment_read", "description": "Read an uploaded attachment from this conversation by its id. "
         "Returns UTF-8 text without NUL characters, or base64 for binary files. offset, limit, total and nextOffset "
         "count text characters or binary bytes; follow nextOffset until null. PDF page is 1-based and reads one page's "
         "extracted text, with totalPages for navigation. Empty PDF text does not mean the page image was inspected. "
         "This read-only tool does not start Studio or change project files.", "inputSchema": {
             "type": "object", "properties": {
                 "attachmentId": {"type": "string"}, "offset": {"type": "integer", "minimum": 0, "default": 0},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 65536, "default": 32768},
                 "page": {"type": "integer", "minimum": 1, "default": 1},
             }, "required": ["attachmentId"], "additionalProperties": False,
         }},
        # Desktop automation is advertised on every machine and permitted on
        # none: its route answers a machine whose policy file does not enable
        # it with the file that would.
        *computer_tools.tool_definitions(),
    ]
    # Only the local stdio adapter reads an explicitly selected path. HTTP takes bytes/references only.
    tools[0]["inputSchema"]["properties"]["attachments"] = {"type": "array", "maxItems": 8, "items": {"anyOf": [
        {"$ref": "#/$defs/ChatAttachmentInput"},
        {"type": "object", "properties": {"path": {"type": "string"}, "name": {"type": "string"},
                                            "mimeType": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
    ]}}
    if external:
        tools.insert(0, {"name": "presentation_bind", "description": "Connect this source session to its configured Hub project, "
                        "reusing the same conversation across turns/reconnects. Returns its URL and default presentation instructions.",
                        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}})
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            method, params = request.get("method"), request.get("params", {})
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}},
                          "serverInfo": {"name": "monkeyhub", "version": "0.1.0"}, "instructions": presentation_instructions}
            elif method == "tools/list":
                result = {"tools": tools}
            elif method == "tools/call":
                try:
                    name, arguments = params.get("name", ""), params.get("arguments", {})
                    if external and name == "presentation_bind":
                        if arguments:
                            raise HubFailure(422, "CHAT_PRESENTATION_INVALID", "The connection already fixes its project and source session.")
                        presentation_binding = transport._request_json(hub, "/api/chat/presentation/bind", "POST", external.model_dump())
                        chat_id = presentation_binding["chatId"]
                        value = {key: value for key, value in presentation_binding.items() if key != "token"}
                        value["instructions"] = guides._PRESENTATION_INSTRUCTIONS
                    elif external and presentation_binding is None:
                        raise HubFailure(409, "CHAT_PRESENTATION_BIND_REQUIRED", "Call presentation_bind before publishing or using project tools.")
                    elif external and name == "chat_present":
                        value = tool_calls._present_tool(hub, chat_id, arguments, presentation_binding["token"])
                    else:
                        value = tool_calls.call_tool(hub, chat_id, name, arguments)
                    arguments = params.get("arguments", {})
                    image_read = (params.get("name") == "studio_request"
                                  and (str(arguments.get("method", "GET")).upper(),
                                       urlsplit(arguments.get("path", "")).path) in {
                                           ("GET", "/api/drawings/model-view"), ("POST", "/api/board/export")})
                    if name == "visual_review" and value.get("delivery") == "frames":
                        metadata = {key: item for key, item in value.items() if key != "frames"}
                        content = [{"type": "text", "text": providers._redact(json.dumps(metadata, ensure_ascii=False))}]
                        for frame in value["frames"]:
                            content.extend([
                                {"type": "text", "text": providers._redact(json.dumps({key: item for key, item in frame.items() if key != "data"}, ensure_ascii=False))},
                                {"type": "image", "mimeType": frame["mimeType"], "data": frame["data"]},
                            ])
                        result = {"content": content}
                    elif image_read:
                        metadata = {key: item for key, item in value.items() if key != "data"}
                        result = {"content": [
                            {"type": "text", "text": providers._redact(json.dumps(metadata, ensure_ascii=False))},
                            {"type": "image", "mimeType": value["mimeType"], "data": value["data"]},
                        ]}
                    else:
                        result = {"content": [{"type": "text", "text": providers._redact(json.dumps(value, ensure_ascii=False))}]}
                except HubFailure as exc:
                    # Keep the Runtime's refusal class across the MCP boundary.
                    # A stale base is not an input typo and must never be blindly retried.
                    failure = {"code": exc.error.code, "detail": providers._redact(exc.error.detail)[:1200],
                               "httpStatus": exc.status}
                    result = {"isError": True, "content": [{"type": "text", "text": json.dumps(failure, ensure_ascii=False)}]}
                except Exception as exc:
                    result = {"isError": True, "content": [{"type": "text", "text": providers._redact(str(exc))[:1500]}]}
            elif method == "ping":
                result = {}
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Unknown method"}}), flush=True)
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}, ensure_ascii=False), flush=True)
        except (ValueError, TypeError):
            continue


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp", action="store_true", required=True)
    parser.add_argument("--hub-url", required=True)
    parser.add_argument("--chat-id")
    parser.add_argument("--project-dir")
    parser.add_argument("--source-session-id")
    parser.add_argument("--provider", choices=("codex", "claude", "coding-plan"), default="codex")
    parser.add_argument("--title", default="External conversation")
    args = parser.parse_args()
    if args.source_session_id and args.project_dir:
        external = ChatPresentationBindRequest(projectDir=args.project_dir, sourceSessionId=args.source_session_id,
                                               provider=args.provider, chatId=args.chat_id, title=args.title)
        _mcp(transport._url(args.hub_url), args.chat_id, external)
    elif args.chat_id and not args.source_session_id and not args.project_dir:
        _mcp(transport._url(args.hub_url), projects._identifier(args.chat_id))
    else:
        parser.error("Use --chat-id for a Hub-owned conversation, or --project-dir and --source-session-id for external presentation.")

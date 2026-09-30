"""The chat's MCP tools, each call checked and sent to what answers it.

``call_tool`` dispatches studio_schema, studio_request, fab_request,
visual_review, attachment_read, chat_present and the computer-use tools. One
submitted change can be waited for and read back without being sent twice, and
chat_present publishes a result in the bound conversation.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
import re
import time
from typing import Mapping
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID, uuid4

from .. import projects
from . import judgments, media, preparation, studio_tool, transport, visual_review

from ..models import ChatPresentationRequest, HubFailure


# The proposal routes that answer a Proposal the client has just authored; its
# generated edits and operator are left out of the immediate answer.
_AUTHORED_PROPOSALS = {"/api/proposals", "/api/proposals/construction", "/api/proposals/facets",
                       "/api/proposals/hosted-opening"}


# What each bound tool takes, exactly as its advertised input schema says.
# Anything else is refused by name rather than ignored, so a retired option
# such as a producer cannot pass for one that took effect.
_TOOL_ARGUMENTS = {
    "studio_schema": frozenset({"method", "path", "body", "pathPrefix", "offset", "limit"}),
    "studio_request": frozenset({"method", "path", "body", "operationId", "feedbackQuote", "awaitSeconds"}),
    "fab_request": frozenset({"method", "path", "body"}),
}


# The one action a caller may ask to see through in a single tool call, and the
# bounds it is held to. It is the capability route that already exists; nothing
# else is orchestrated, and nothing new executes anything.
_FINISHABLE = "/api/capabilities/candidate.modify_existing/run"
_CHECKPOINT = re.compile(r"^/api/proposals/[A-Za-z0-9_-]+/candidate$")
_AWAIT_MAX_S = 180
_POLL_S = 0.4


def _finish(base: str, started: Mapping, submitted: Mapping, deadline: float) -> dict:
    """Watch one accepted candidate to its end, and read back what it made.

    Every read here is a read the caller would otherwise make itself, in the
    order its own tool description states: the job, then the candidate and the
    comparison against the run the change was made from. What comes back is
    those answers, shortened — never a summary of something that was not read,
    and never a check reported as held when the run said it was unchecked.

    The action has already been accepted when this begins, so nothing that
    happens here may lose it: a read that times out, refuses or cannot reach
    the service answers with the job and the candidate that exist and the
    reads that will find them. Nothing here posts anything, ever.
    """

    job_id, candidate_id = started.get("jobId"), started.get("candidateId")
    against = submitted.get("sourceRunId")
    follow = [f"GET /api/jobs/{job_id}", f"GET /api/candidates/{candidate_id}"]
    if against:
        follow.append(f"GET /api/candidates/{candidate_id}/compare?against={against}")

    def left() -> float:
        return deadline - time.monotonic()

    def unfinished(status: str, detail: str, **extra) -> dict:
        return {**started, "status": status, "detail": detail, "next": follow, **extra}

    job = None
    while True:
        if left() <= 0:
            return unfinished(
                "running" if job is None or job.get("status") not in {"succeeded", "failed"} else job["status"],
                "this call's wait ran out; the action was accepted once and was not sent again.",
                waitedOut=True)
        try:
            job = transport._request_json(base, f"/api/jobs/{job_id}", timeout=left())
        except (HubFailure, OSError, TimeoutError) as cause:
            # The run is out there with a name; only this look at it failed.
            return unfinished("unknown", f"the job could not be read: {transport._reason(cause)}", readback="failed")
        if job.get("status") in {"succeeded", "failed"}:
            break
        time.sleep(min(_POLL_S, max(0.0, left())))
    if job.get("status") != "succeeded":
        # A failure is the job's own words. Deciding what to do with them is the
        # caller's, and sending the request again is never this call's decision.
        return {**started, "status": "failed", "job": job, "next": follow}
    if left() <= 0:
        return {**started, "status": "succeeded", "readback": "not attempted",
                "detail": "the run finished as this call's wait ran out; read it with the paths below.",
                "next": follow}
    # The two reads that describe a finished run do not depend on each other,
    # so they are asked at once. This is the only overlap here: the job had to
    # finish before either could be asked at all.
    reads = {"candidate": (base, f"/api/candidates/{candidate_id}")}
    if against:
        reads["compare"] = (base, f"/api/candidates/{candidate_id}/compare?against={against}")
    answers = transport._together(reads, left(), allow_partial=True)
    errors = {name: transport._reason(value) for name, value in answers.items() if isinstance(value, BaseException)}
    candidate = answers["candidate"] if "candidate" not in errors else {}
    comparison = answers.get("compare") if "compare" not in errors else None
    result = {
        **started,
        "status": "succeeded",
        "readback": "failed" if errors else "ok",
        "candidate": {
            key: candidate.get(key) for key in
            ("candidateId", "stateDigest", "changedVsProjection", "seatExecutionComplete",
             "relationChecks", "harness", "honesty")
            if key in candidate
        },
        # What the run saved, by the fields that say whether it is really there.
        "artifacts": [
            {key: row.get(key) for key in
             ("runId", "modelSource", "sourceStageRef", "fileName", "relativePath", "representation", "lengthUnit",
              "objectCount", "readbackVerified", "available", "unavailableReason", "sha256")
             if key in row}
            for row in candidate.get("artifacts", ())
        ],
        "objects": candidate.get("objects"),
        "objectReadbackError": candidate.get("objectReadbackError"),
        # The comparison's own objects, not a count of them: which object
        # changed, from which box to which box, and which ones did not move.
        # Whether that satisfies what was kept is the reader's judgement, made
        # on these facts rather than on a verdict invented here.
        "compare": {
            "against": against,
            **{key: comparison.get(key) for key in
               ("changed", "unchanged", "added", "removed", "tolerance", "why", "honesty")
               if key in comparison},
            "objects": [
                {key: row.get(key) for key in
                 ("name", "componentId", "producerOp", "status", "before", "after") if key in row}
                for row in comparison.get("objects", ())
            ],
        } if comparison is not None else None,
        # Nothing is left to do: the job finished and both readings of it are
        # above. Listing the reads that produced them would invite a second
        # round of the calls this one already made.
        "next": [],
    }
    if errors:
        # A missing comparison must not discard objects already read, nor may
        # a successful comparison stand in for a missing candidate. Keep each
        # answer once and direct recovery only to the reads still missing.
        result.update(
            detail="The run finished. Completed reads: "
                   + (", ".join(name for name in reads if name not in errors) or "none")
                   + ". Completed responses are included below and do not need another read. "
                   "Only the reads in next are missing; overall verification remains incomplete. "
                   + "; ".join(f"{name}: {reason}" for name, reason in errors.items()),
            readbackErrors=errors,
            next=[f"GET {reads[name][1]}" for name in errors],
        )
        if "candidate" in errors:
            for key in ("candidate", "artifacts", "objects", "objectReadbackError"):
                result.pop(key)
        if "compare" in errors:
            result.pop("compare")
    return result


def call_tool(hub: str, chat_id: str, name: str, arguments: dict):
    token = transport._trace_headers.set({})
    try:
        return _call_tool(hub, chat_id, name, arguments)
    finally:
        transport._trace_headers.reset(token)


def _call_tool(hub: str, chat_id: str, name: str, arguments: dict):
    from .. import computer_tools

    if name == "chat_present":
        return _present_tool(hub, chat_id, arguments, os.environ.get("MONKEYHUB_PRESENTATION_TOKEN", ""))
    if name == "attachment_read":
        if not isinstance(arguments, dict) or set(arguments) - {"attachmentId", "offset", "limit", "page"}:
            raise HubFailure(422, "CHAT_ATTACHMENT_READ_INVALID", "Use attachmentId and optional offset, limit, and page only.")
        attachment_id = arguments.get("attachmentId")
        if not isinstance(attachment_id, str):
            raise HubFailure(422, "CHAT_ATTACHMENT_READ_INVALID", "An attachmentId from this conversation is required.")
        attachment_id = projects._identifier(attachment_id)
        offset, limit, page = arguments.get("offset", 0), arguments.get("limit", 32768), arguments.get("page", 1)
        media._attachment_read_paging(offset, limit, page)
        session = transport._request_json(hub, f"/api/chat/sessions/{projects._identifier(chat_id)}")
        if session.get("status") != "running":
            raise preparation._not_running(session)
        if projects._project(session["projectDir"]) != (session["projectId"], session["projectDir"]):
            raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The conversation's project identity changed.")
        return transport._request_json(hub, f"/api/chat/sessions/{chat_id}/attachments/{attachment_id}/read?"
                                       + urlencode({"offset": offset, "limit": limit, "page": page}))
    if name in computer_tools.ROUTES:
        # Nothing is checked twice: the policy gate, the allow-list and every
        # refusal code belong to the route, so the CLI reads what an HTTP
        # caller reads. This conversation's project is not involved.
        return computer_tools.call(hub, name, arguments)
    if name == "visual_review":
        # A tool of its own rather than a studio_request path: Hub holds the
        # allowance, so the route is on no request allow-list to go around it.
        return visual_review._visual_review(hub, chat_id, arguments)
    method, path = str(arguments.get("method", "GET")).upper(), arguments.get("path", "")
    parsed = urlsplit(path)
    allowed = {"GET": studio_tool._READ, "POST": studio_tool._POST, "PUT": studio_tool._WRITE}
    if "feedbackQuote" in arguments and (name != "studio_request" or not judgments._binds_words(method, parsed.path)
                                         or not isinstance(arguments["feedbackQuote"], str)):
        raise HubFailure(422, "CHAT_FEEDBACK_QUOTE", "feedbackQuote only selects the user's words for feedback, an admission or a Continue.")
    if "operationId" in arguments and (name != "studio_request" or method == "GET" or (
            method == "POST" and parsed.path in {"/api/board/export", "/api/drawings/plans/status"})):
        raise HubFailure(422, "CHAT_TOOL_INVALID", "operationId identifies a Studio mutation request.")
    if "awaitSeconds" in arguments and name != "studio_request":
        # Only one tool can wait for anything. Quietly dropping the option here
        # would answer at once and look like the wait had happened.
        raise HubFailure(422, "CHAT_TOOL_INVALID",
                         f"{name} has nothing to wait for; awaitSeconds is studio_request's option for "
                         f"POST {_FINISHABLE}.")
    unknown = sorted(set(arguments) - _TOOL_ARGUMENTS.get(name, frozenset(arguments)))
    if unknown:
        raise HubFailure(422, "CHAT_TOOL_INVALID", f"{name} has no argument {', '.join(unknown)}; "
                         f"it takes {', '.join(sorted(_TOOL_ARGUMENTS[name]))}.")
    if name == "fab_request":
        session = transport._request_json(hub, f"/api/chat/sessions/{projects._identifier(chat_id)}")
        if session.get("status") != "running":
            raise preparation._not_running(session)
        if projects._project(session["projectDir"]) != (session["projectId"], session["projectDir"]):
            raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The conversation's project identity changed.")
        if method == "GET" and path == "/api/fab/profiles":
            return transport._request_json(hub, path)
        if method == "POST" and path == "/api/fab/send":
            body = dict(arguments.get("body") or {})
            body["dryRun"] = True
            body.pop("accessCode", None)
            return transport._request_json(hub, path, "POST", body)
        raise HubFailure(422, "CHAT_TOOL_UNAVAILABLE", "The chat can list Fab profiles and validate a prepared job; uploads remain explicit in MonkeyFab.")
    if name == "studio_schema" and not path:
        # A malformed question is refused before a runtime is prepared for it.
        studio_tool._discovery(arguments)
        return studio_tool._discover_actions(preparation._bound_studio(hub, chat_id)[0], arguments)
    if any(key in arguments for key in ("pathPrefix", "offset", "limit")):
        raise HubFailure(422, "CHAT_TOOL_INVALID", "pathPrefix, offset and limit belong to studio_schema action discovery; omit path.")
    # Asking what a documented action takes is not calling it. The path is
    # checked against the same allow-list either way, with a schema question's
    # `{id}` segments standing for the id they name, so the templates this
    # tool's own description lists can actually be read. It is checked before
    # the Studio is resolved, so a refused request never starts a runtime.
    if parsed.scheme or parsed.netloc or parsed.fragment or method not in allowed:
        raise HubFailure(422, "CHAT_TOOL_UNAVAILABLE", "This action is not exposed to the chat.")
    permitted = studio_tool._schema_allowed(method, parsed.path) if name == "studio_schema" else allowed[method].fullmatch(parsed.path)
    if not permitted:
        replacement = studio_tool._REPLACED_ACTIONS.get((method, parsed.path))
        if replacement is not None:
            raise HubFailure(422, "CHAT_TOOL_UNAVAILABLE", f"{method} {parsed.path} is not exposed to the chat; "
                             f"use {replacement}. Nothing was executed.")
        try:
            running = preparation._bound_studio(hub, chat_id, prepare=False)[0]
        except (HubFailure, OSError, ValueError):
            running = None
        raise studio_tool._action_refusal(running, method, parsed.path)
    if name == "studio_request" and method == "POST" and parsed.path == "/api/proposals":
        # Checked, like the path, before the Studio is resolved.
        refusal = studio_tool._semantic_edit_refusal(arguments.get("body"))
        if refusal is not None:
            raise refusal
    base, session = preparation._bound_studio(hub, chat_id)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if any(query[key] != [session["projectId"]] for key in ("projectId", "project_id") if key in query):
        raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "A tool cannot select another project.")
    if name == "studio_schema":
        document = transport._request_json(base, "/openapi.json")
        # A literal path and a templated one can both match the same request
        # (e.g. /api/proposals/{proposal_id} and /api/proposals/construction);
        # the literal route wins, then the template with the fewest {…}
        # segments, and only the first of those tied that actually has the
        # requested method — a template earlier in the document must not
        # shadow a literal route it merely matches but does not serve (#419).
        matches = sorted(
            (route for route in document["paths"]
             if re.fullmatch(re.sub(r"\{[^}]+\}", r"[^/]+", route), parsed.path)),
            key=lambda route: len(re.findall(r"\{[^}]+\}", route)),
        )
        template = next((route for route in matches if method.lower() in document["paths"][route]), None)
        operation = document["paths"][template][method.lower()] if template is not None else None
        if operation is None:
            raise HubFailure(422, "CHAT_ACTION_UNSUPPORTED", "The running Runtime has no matching action. Use studio_schema with no path to list this version's available actions.")
        if method == "POST" and parsed.path == "/api/exports":
            reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            schema = document["components"]["schemas"][reference.rsplit("/", 1)[-1]]
            schema["properties"].pop("upload", None)
            schema["properties"]["attachmentId"] = {
                "type": "string", "format": "uuid",
                "description": "Exact attachment ID from this conversation. Choose this OR projectRevision OR sourceArtifactId; Hub transfers the bytes.",
            }
        if method == "POST" and judgments._is_feedback(parsed.path):
            # Expose the Runtime's real schema, narrowed to the chat capability;
            # provenance is supplied by this adapter, never by the provider.
            reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            schema = document["components"]["schemas"][reference.rsplit("/", 1)[-1]]
            creating = parsed.path in judgments._FEEDBACK_PATHS
            hidden = {"rawLanguage", "messageSource", "sourceKind"} if creating else {"reason", "revisionMessageSource", "replacement"}
            schema["properties"] = {key: value for key, value in schema["properties"].items() if key not in hidden}
            schema["required"] = [key for key in schema.get("required", []) if key not in hidden]
            if not creating:
                schema["properties"]["action"]["enum"] = ["revoke"]
            elif parsed.path == "/api/decisions":
                schema["properties"]["disposition"]["enum"] = ["avoid", "keep"]
        if (method, parsed.path) in judgments._BOUND_WORDS:
            # Hub binds the user's message and words; the provider supplies neither.
            reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            schema = document["components"]["schemas"][reference.rsplit("/", 1)[-1]]
            hidden = {"messageSource", "rawLanguage"}
            schema["properties"] = {key: value for key, value in schema["properties"].items() if key not in hidden}
            schema["required"] = [key for key in schema.get("required", []) if key not in hidden]
            if method == "POST":
                task = document["components"]["schemas"]["AdmissionTaskDto"]["properties"]
                task["kind"] = {**task["kind"], "enum": ["hub-chat"]}
        return studio_tool._request_contract(document, template, method, operation)
    if name != "studio_request":
        raise HubFailure(422, "CHAT_TOOL_UNAVAILABLE", "Unknown chat tool.")
    wait = arguments.get("awaitSeconds")
    checkpoint = method == "POST" and _CHECKPOINT.fullmatch(parsed.path) is not None
    if wait is not None:
        if not isinstance(wait, int) or isinstance(wait, bool) or not 1 <= wait <= _AWAIT_MAX_S:
            raise HubFailure(422, "CHAT_TOOL_INVALID",
                             f"awaitSeconds is a whole number of seconds from 1 to {_AWAIT_MAX_S}.")
        if not (method == "POST" and (parsed.path == _FINISHABLE or checkpoint)):
            raise HubFailure(422, "CHAT_TOOL_INVALID",
                             f"awaitSeconds supports POST {_FINISHABLE} or the final POST /api/proposals/{{id}}/candidate. It is not a field of the "
                             "request body, and every other action answers as it is, without waiting.")
        if not checkpoint and (not isinstance(arguments.get("body"), dict) or not arguments["body"].get("sourceRunId")):
            raise HubFailure(422, "CHAT_TOOL_INVALID",
                             "waiting for this action means answering with the comparison against the run "
                             "it was made from, so the body must name sourceRunId — the run the description "
                             "was read against. Without it, send the request without awaitSeconds and read "
                             "the job and the candidate yourself.")
    body = arguments.get("body")
    if method == "POST" and parsed.path == "/api/project/modeling":
        # Its only field is the project, which the chat supplies, and the chat
        # always asks for the base it leaves: the guide's first proposal is
        # written against that answer rather than a second state read.
        body = {} if body is None else body
        path = parsed.path + "?" + urlencode({**{key: values[-1] for key, values in query.items()}, "base": "true"})
    if body is not None:
        if not isinstance(body, dict):
            raise HubFailure(422, "CHAT_TOOL_INVALID", "The request body must be an object.")
        body = dict(body)
        if body.get("projectId", session["projectId"]) != session["projectId"]:
            raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "A tool cannot select another project.")
        if parsed.path in {"/api/proposals", "/api/board/export", "/api/project/modeling", "/api/render/jobs"}:
            body["projectId"] = session["projectId"]
        if method == "POST" and parsed.path == "/api/drawings/plans":
            # A cut plan the Agent asks for is its reading of what the user
            # said, so its revision says so (05 §5). Only a person's own
            # request is human, and a correction suggestion counts only those,
            # so the Agent cannot claim it: the chat fills the kind, as it
            # does for feedback decisions.
            if {body.pop(key, None) for key in ("sourceKind", "source_kind")} - {None, "agent"}:
                raise HubFailure(422, "CHAT_TOOL_INVALID", "The chat marks your cut-plan requests sourceKind=agent; "
                                 "only a person's own request in Drawings is human. Send it without sourceKind.")
            body["sourceKind"] = "agent"
    if method == "POST" and parsed.path == "/api/exports" and isinstance(body, dict):
        attachment_id = body.pop("attachmentId", None)
        if attachment_id is not None:
            if any(body.get(key) is not None for key in ("upload", "projectRevision", "sourceArtifactId")):
                raise HubFailure(422, "EXPORT_SOURCE_AMBIGUOUS", "Choose the project revision or one attachment, not both.")
            body["upload"] = transport._request_json(hub, f"/api/chat/sessions/{projects._identifier(chat_id)}/attachments/{projects._identifier(attachment_id)}/model-source")
    comparison = body or {}
    if method == "POST" and judgments._is_feedback(parsed.path):
        if parsed.query or not isinstance(body, dict):
            raise HubFailure(422, "CHAT_FEEDBACK_INVALID", "Feedback takes its scope and exact source in the body, without query parameters.")
        body = judgments._feedback_body(hub, base, chat_id, session, parsed.path, body, arguments.get("feedbackQuote"))
    if (method, parsed.path) in judgments._BOUND_WORDS:
        if parsed.query or not isinstance(body, dict):
            raise HubFailure(422, "CHAT_TOOL_INVALID", f"{method} {parsed.path} takes its whole request in the body, without query parameters.")
        bind = judgments._admission_body if method == "POST" else judgments._continue_body
        body = bind(chat_id, session, body, arguments.get("feedbackQuote"))
    if method == "POST" and parsed.path == "/api/board/export":
        if parsed.query:
            raise HubFailure(422, "CHAT_TOOL_INVALID", "The registered page read takes its source in the body, without query parameters.")
        return studio_tool._read_drawing_page(base, body)
    if method == "POST" and parsed.path == "/api/render/jobs":
        # Before admission: a provider this Runtime does not offer takes nothing.
        studio_tool._render_provider_available(base, body)
    if wait is not None and checkpoint:
        proposal = transport._request_json(base, parsed.path.removesuffix("/candidate"))
        comparison = {"sourceRunId": proposal.get("sourceRunId")}
    if method in {"POST", "PUT"} and parsed.path not in studio_tool._POST_READS:
        from uuid import uuid5, NAMESPACE_URL
        runtime_id = str(uuid5(NAMESPACE_URL, f"{session['projectId']}:{os.path.normcase(str(Path(session['projectDir']).resolve()))}"))
        operation_id = arguments.get("operationId") or str(uuid4())
        try:
            operation_id = str(UUID(operation_id))
        except (ValueError, AttributeError, TypeError) as exc:
            raise HubFailure(422, "OPERATION_ID_INVALID", "operationId must be a UUID.") from exc
        started = transport._request_json(hub, f"/api/runtime/projects/{runtime_id}/studio{path}", method, body,
                                          headers={"Idempotency-Key": operation_id, "X-Monkey-Chat": chat_id})
    else:
        started = transport._request_json(base, path, method, body)
    if isinstance(started, dict) and method == "GET" and parsed.path.startswith("/api/exports/"):
        if started.get("status") == "succeeded" and started.get("downloadPath") == parsed.path + "/bytes":
            started = started | {"downloadUrl": base + started["downloadPath"]}
    if wait is None:
        if method == "GET" and parsed.path == "/api/capabilities" and isinstance(started, dict):
            started = {**started, "actionDiscovery": {
                "tool": "studio_schema", "arguments": {},
                "note": "These registered workflows are not the complete action list. studio_schema without path lists "
                        "the actual Runtime actions exposed to this chat; optional pathPrefix narrows the list.",
            }}
        if method == "POST" and parsed.path in _AUTHORED_PROPOSALS and isinstance(started, dict) and "proposalId" in started:
            # The client just authored these edits. Echoing both the edits and
            # their full operator makes each model continuation read them twice.
            # Keep the checked change, source, impact and conflicts; the ordinary
            # GET still exposes the complete proposal when inspection needs it.
            started = {key: value for key, value in started.items() if key != "decisionOperator"}
            if isinstance(started.get("change"), dict):
                started["change"] = {key: value for key, value in started["change"].items() if key != "edits"}
            # A parameterized form can have thousands of coordinate changes.
            # Bound only these repeated lists; keep every conflict, lock, keep
            # condition and coverage limitation in the immediate response.
            for section, field in (("change", "changes"), ("impact", "direct")):
                values = started.get(section, {}).get(field)
                if isinstance(values, list) and len(values) > 100:
                    started[section] = {**started[section], field: values[:100],
                                        f"{field}Count": len(values), f"{field}Omitted": len(values) - 100}
                    started["detailsPath"] = f"/api/proposals/{started['proposalId']}"
        if method == "PUT" and parsed.path == "/api/working-draft" and isinstance(started, dict):
            # The position also lists every recovery row and any local recovery
            # draft. The Continue needs only where the head now is and the
            # revision a later Continue compares against.
            started = {key: started.get(key) for key in ("projectId", "revisionSha256", "current")}
        return started
    # One POST has happened. From here on this call only reads.
    return _finish(base, started, comparison, time.monotonic() + wait)


def _present_tool(hub: str, chat_id: str, arguments: dict, token: str):
    session = transport._request_json(hub, f"/api/chat/sessions/{projects._identifier(chat_id)}")
    body = dict(arguments)
    if set(body) - {"turnId", "messageId", "revision", "kind", "content", "status", "attachments", "documents", "suggestion"}:
        raise HubFailure(422, "CHAT_PRESENTATION_INVALID", "Use only the documented presentation fields; this connection fixes its destination.")
    if not session.get("sourceSessionId"):
        # Native completion belongs to the CLI turn. A media card must remain
        # revisable until that turn settles all its streaming messages.
        body["status"] = "streaming"
        if "turnId" not in body:
            user = next((row for row in reversed(session.get("messages", [])) if row["role"] == "user"), None)
            if user:
                body["turnId"] = user["id"]
    uploads = []
    for item in body.get("attachments", []):
        item = dict(item)
        if "path" in item:
            if set(item) - {"path", "name", "mimeType"}:
                raise HubFailure(422, "CHAT_ATTACHMENT_INVALID", "A local file takes path and optional name/mimeType.")
            path = Path(item.pop("path")).resolve(strict=True)
            if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
                raise HubFailure(413, "CHAT_ATTACHMENT_TOO_LARGE", "Select one file no larger than 20 MiB.")
            item.setdefault("name", path.name)
            item.setdefault("mimeType", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            item["data"] = base64.b64encode(path.read_bytes()).decode("ascii")
        uploads.append(item)
    body.update(projectId=session["projectId"], sourceSessionId=session.get("sourceSessionId") or f"hub:{chat_id}", attachments=uploads)
    request = ChatPresentationRequest.model_validate(body)
    result = transport._request_json(hub, f"/api/chat/sessions/{chat_id}/presentation", "POST", request.model_dump(),
                                     headers={"Authorization": "Bearer " + token})
    return {"chatId": chat_id, "messageId": request.messageId, "revision": request.revision,
            "status": result["status"], "url": hub + "/?" + urlencode({"chatId": chat_id})}

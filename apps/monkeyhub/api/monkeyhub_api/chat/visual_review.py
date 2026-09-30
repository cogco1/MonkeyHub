"""The Agent's bounded look through the bound Studio (#303): one allowance per answered user message."""

from __future__ import annotations

import base64
from hashlib import sha256
import re
from io import BytesIO
from urllib.error import URLError

from . import judgments, preparation, transport

from ..models import HubFailure


# The Agent's one bounded look (#303). The runtime route keeps no loop state:
# its caller holds the allowance and sends it back with every review. For the
# Agent that caller is Hub, with one allowance for each user message the Agent
# answers, kept in this adapter while that is the message it answers. The
# stdio loop answers one call at a time, so nothing else touches it meanwhile.
_VISUAL_REVIEW_FIELDS = {"domain", "sourceRefs", "viewRecipe", "task", "criteria", "preserve",
                         "priorObservations", "knownFacts", "reason", "addressedFindingIds", "delivery"}
# The runtime's allowance for each class the Agent can declare. The route
# refuses any other number, so these can only ever agree with it.
_VISUAL_ALLOWED = {"deterministic_edit": 0, "spatial_formal": 2}
_POLISH_ROUNDS = range(1, 5)
# Polish beyond the two looks a spatial task gets needs the user's own words
# asking to keep refining; the Agent's declaration alone never buys more.
_KEEP_REFINING = re.compile(
    r"继续优化|再优化|打磨|精修|反复推敲|keep (?:refining|polishing|improving|iterating)"
    r"|refine (?:it |this |them )?further|further refinement|\bpolish", re.IGNORECASE)
# Up to four owner-rendered views and one provider look, which is bounded itself.
_VISUAL_REVIEW_WAIT_S = 300
_visual_allowances: dict[str, tuple[str, dict]] = {}


def _allowance_note(held: dict) -> str:
    return f"This message's {held['taskClass']} allowance: {held['used']} of {held['allowed']} reviews used."


def _visual_allowance(chat_id: str, message: dict, declared, rounds) -> dict:
    """The allowance of the user message the Agent answers, for the class it declares.

    The Agent declares the class and Hub records it. Until a review is spent a
    new declaration replaces it; from then on the class stays with the
    message, so declaring again never buys another look.
    """
    if declared == "polish":
        if type(rounds) is not int or rounds not in _POLISH_ROUNDS:
            raise HubFailure(422, "CHAT_TOOL_INVALID", "A polish task names polishRounds from 1 to 4.")
        if rounds > 2 and not _KEEP_REFINING.search(message.get("content") or ""):
            raise HubFailure(409, "VISUAL_POLISH_NOT_ASKED",
                             "More than two polish rounds need the user's own words in this message asking to keep "
                             "refining (继续优化, 打磨, keep refining). Declare polishRounds 1-2, or spatial_formal.")
        allowed = rounds
    elif declared in _VISUAL_ALLOWED:
        if rounds is not None:
            raise HubFailure(422, "CHAT_TOOL_INVALID", "polishRounds belongs to a polish task.")
        allowed = _VISUAL_ALLOWED[declared]
    else:
        raise HubFailure(422, "CHAT_TOOL_INVALID", "taskClass is spatial_formal, polish or deterministic_edit.")
    turn, held = _visual_allowances.get(chat_id, (None, None))
    if turn == message["id"] and held["used"]:
        if (held["taskClass"], held["allowed"]) != (declared, allowed):
            raise HubFailure(409, "VISUAL_TASK_CLASS_FIXED",
                             f"A review of this message was spent as {held['taskClass']}, which it keeps until the "
                             f"user's next message. {_allowance_note(held)}")
        return held
    held = {"taskClass": declared, "allowed": allowed, "used": 0, "lastFindingIds": []}
    _visual_allowances[chat_id] = (message["id"], held)
    return held


def _visual_review(hub: str, chat_id: str, arguments: dict) -> dict:
    """One bounded look through the bound Studio, under the answered message's allowance.

    The runtime renders every frame from the exact sources named and writes
    nothing. By default the current agent sees these images; an explicit
    observation delivery uses the configured structured provider. Hub supplies the project
    and the allowance and keeps what the answer says of it: a refusal spends
    nothing, and a call the provider may have answered is spent. A finding
    that touches a preserve condition is marked escalate: it is a question for
    the architect, which another review cannot settle.
    """
    if not isinstance(arguments, dict) or set(arguments) - _VISUAL_REVIEW_FIELDS - {"taskClass", "polishRounds"}:
        raise HubFailure(422, "CHAT_TOOL_INVALID", "visual_review takes taskClass, polishRounds for a polish task "
                         "and the review's own fields; Hub fills projectId and budgetState.")
    base, session = preparation._bound_studio(hub, chat_id)
    message = judgments._user_message(chat_id, session, "A visual review")
    held = _visual_allowance(chat_id, message, arguments.get("taskClass"), arguments.get("polishRounds"))
    body = {key: value for key, value in arguments.items() if key in _VISUAL_REVIEW_FIELDS}
    body.setdefault("delivery", "frames")
    body.update(projectId=session["projectId"], budgetState=dict(held))

    def spent(detail: str) -> str:
        held.update(used=held["used"] + 1, lastFindingIds=[])
        return f"{detail} {_allowance_note(held)}"

    try:
        answer = transport._request_json(base, "/api/visual-reviews", "POST", body, timeout=_VISUAL_REVIEW_WAIT_S)
    except HubFailure as refused:
        # Frames have no provider side effect: an HTTP refusal, including a
        # renderer failure, delivered no review. A structured provider may
        # already have been called when it returns 5xx.
        detail = refused.error.detail
        detail = spent(detail) if body["delivery"] == "observation" and refused.status >= 500 else f"{detail} {_allowance_note(held)}"
        raise HubFailure(refused.status, refused.error.code, detail) from refused
    except URLError:
        raise  # It never reached the Studio: nothing was spent.
    except (OSError, ValueError) as lost:
        raise HubFailure(504, "VISUAL_REVIEW_UNANSWERED", spent(
            f"The Studio did not answer this review ({transport._reason(lost)}), so it counts as spent.")) from lost
    observation = answer.get("observation") if isinstance(answer, dict) else None
    state = answer.get("budgetState") if isinstance(answer, dict) else None
    if (not isinstance(state, dict) or type(state.get("used")) is not int
            or state["used"] != held["used"] + 1 or state["used"] > held["allowed"]
            or (state.get("taskClass"), state.get("allowed")) != (held["taskClass"], held["allowed"])):
        raise HubFailure(502, "CHAT_TOOL_FAILED", spent("The Studio answered this review outside its contract."))
    if body["delivery"] == "frames":
        frames = answer.get("frames")
        try:
            if (answer.get("delivery") != "frames" or observation is not None or answer.get("usage") is not None
                    or state.get("lastFindingIds") != [] or not isinstance(frames, list) or not 1 <= len(frames) <= 4):
                raise ValueError("invalid frame delivery")
            seen = set()
            for frame in frames:
                if (not isinstance(frame, dict) or frame.get("sourceRef") not in body.get("sourceRefs", [])
                        or not isinstance(frame.get("viewRef"), str) or frame["viewRef"] in seen
                        or frame.get("mimeType") != "image/png" or not isinstance(frame.get("data"), str)
                        or len(frame["data"]) > 4 * ((transport._PAGE_IMAGE_MAX_BYTES + 2) // 3)):
                    raise ValueError("invalid frame source or payload")
                seen.add(frame["viewRef"])
                data = base64.b64decode(frame["data"], validate=True)
                if sha256(data).hexdigest() != frame.get("frameSha256"):
                    raise ValueError("frame digest mismatch")
                response = BytesIO(data)
                response.headers = {"Content-Type": "image/png"}
                picture = transport._page_image(response)
                if (picture["width"], picture["height"]) != (frame.get("width"), frame.get("height")):
                    raise ValueError("frame dimensions mismatch")
            if any(source not in [frame["sourceRef"] for frame in frames] for source in body["sourceRefs"]):
                raise ValueError("missing source frame")
        except (ValueError, TypeError, KeyError, HubFailure) as invalid:
            raise HubFailure(502, "CHAT_TOOL_FAILED", spent("The Studio answered invalid review frames.")) from invalid
        held.update(used=state["used"], lastFindingIds=[])
        return {"delivery": "frames", "frames": frames, "observation": None, "usage": None,
                "note": "Inspect the images against the requested criteria. Delivery alone is not an observation or acceptance. "
                        "No structured finding ids exist for an after_repair review.",
                "allowance": {key: held[key] for key in ("taskClass", "allowed", "used")}}
    if not isinstance(observation, dict):
        raise HubFailure(502, "CHAT_TOOL_FAILED", spent("The Studio answered this review outside its contract."))
    held.update(used=state["used"], lastFindingIds=list(state.get("lastFindingIds") or ()))
    findings = [{**row, "escalate": any(str(ref).startswith("preserve:") for ref in row.get("targetRefs") or ())}
                for row in observation.get("observations") or () if isinstance(row, dict)]
    return {"observation": {**observation, "observations": findings}, "usage": answer.get("usage"),
            "allowance": {key: held[key] for key in ("taskClass", "allowed", "used")}}

"""An immutable, exact state snapshot: ContextPack@1-shaped facts, exact source refs and a digest.

Every strategy arm forks from one snapshot; no rollout writes it. The digest is
the content identity every rollout record binds to, so a rerun after a restart
can prove it started from the same state. In the product the same shape comes
from ``POST /api/intents/context`` (ContextPack@1); the V0 fixtures here are
synthetic and name no real project. ``state_schema.json`` describes the JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any, Mapping

from archflow.contracts.canonical import canonical_digest

if TYPE_CHECKING:  # the environment imports this module; keep the runtime import one-way
    from .environment import EnvState, Environment


PROJECT_ID = "synthetic-strategy-allocation"
CONTEXT_PACK = "ContextPack@1"
HONESTY = (
    "Synthetic GH-268 fixture: it describes no real project and no real person.",
    "Unread sources appear as exact identities, not content; only a reading action reveals their text.",
    "Experiment arms are read-only: no rollout writes this state or any project state.",
)
_KEYS = ("snapshotId", "caseId", "caseVersion", "environmentVersion", "title", "task", "state",
         "allowedActions", "contextPack", "sourceRefs", "digest")


def _body(snapshot_id: str, case_id: str, case_version: str, environment_version: str, title: str, task: str,
          state: tuple[tuple[str, str | bool], ...], allowed_actions: tuple[tuple[str, str, bool], ...],
          context_pack_json: str, source_refs: tuple[str, ...]) -> dict[str, Any]:
    return {
        "snapshotId": snapshot_id,
        "caseId": case_id,
        "caseVersion": case_version,
        "environmentVersion": environment_version,
        "title": title,
        "task": task,
        "state": dict(state),
        "allowedActions": [{"id": action_id, "description": description, "asksHuman": asks_human}
                           for action_id, description, asks_human in allowed_actions],
        "contextPack": json.loads(context_pack_json),
        "sourceRefs": list(source_refs),
    }


@dataclass(frozen=True)
class StateSnapshot:
    """Frozen all the way down: nested JSON is held as canonical text and handed out as copies."""

    snapshot_id: str
    case_id: str
    case_version: str
    environment_version: str
    title: str
    task: str
    state: tuple[tuple[str, str | bool], ...]
    allowed_actions: tuple[tuple[str, str, bool], ...]
    context_pack_json: str
    source_refs: tuple[str, ...]
    digest: str

    def __post_init__(self) -> None:
        if canonical_digest(self.body()) != self.digest:
            raise ValueError("snapshot digest does not match its content")

    @property
    def action_ids(self) -> tuple[str, ...]:
        return tuple(action[0] for action in self.allowed_actions)

    @property
    def context_pack(self) -> dict[str, Any]:
        return json.loads(self.context_pack_json)

    def body(self) -> dict[str, Any]:
        """Everything the digest covers: the whole snapshot except the digest itself."""
        return _body(**{item.name: getattr(self, item.name) for item in fields(self) if item.name != "digest"})

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "digest": self.digest}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    @classmethod
    def create(cls, **values: Any) -> StateSnapshot:
        return cls(**values, digest=canonical_digest(_body(**values)))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StateSnapshot:
        if not isinstance(value, Mapping) or set(value) != set(_KEYS):
            raise ValueError(f"a snapshot has exactly the keys {sorted(_KEYS)}")
        state, actions, refs = value["state"], value["allowedActions"], value["sourceRefs"]
        if not isinstance(state, Mapping) or not isinstance(actions, list) or not isinstance(refs, list):
            raise ValueError("state must be an object; allowedActions and sourceRefs must be lists")
        try:
            allowed = tuple((item["id"], item["description"], item["asksHuman"]) for item in actions)
        except (KeyError, TypeError) as exc:
            raise ValueError("each allowed action has id, description and asksHuman") from exc
        return cls(
            snapshot_id=value["snapshotId"], case_id=value["caseId"], case_version=value["caseVersion"],
            environment_version=value["environmentVersion"], title=value["title"], task=value["task"],
            state=tuple(sorted(state.items())), allowed_actions=allowed,
            context_pack_json=json.dumps(value["contextPack"], sort_keys=True, ensure_ascii=False),
            source_refs=tuple(refs), digest=value["digest"],
        )

    @classmethod
    def from_json(cls, text: str) -> StateSnapshot:
        return cls.from_dict(json.loads(text))


def snapshot_of(env: Environment, state: EnvState) -> StateSnapshot:
    """Project one environment state into the exact snapshot a strategy may read."""
    case = env.case(state.case_id)
    context_pack = {
        "contextPack": CONTEXT_PACK,
        "source": {
            "projectId": PROJECT_ID,
            "runId": f"{case.case_id}@{case.version}",
            "stateDigest": state.digest,
            "exactSource": True,
            "actionable": True,
            "sourceStageRef": None,
            "readWith": "Environment.reset(case_id), or Environment.state_of(snapshot) for a saved snapshot",
            "writeWith": "none: experiment arms never write state",
        },
        "target": None,
        "keep": None,
        "request": None,
        "contextTier": "design",
        "escalation": [],
        "context": {"task": case.task, "facts": [fact.to_dict() for fact in env.facts(state)]},
        "preflight": None,
        "confirmedStage": None,
        "scopedDecisions": [],
        "studyEvidence": [],
        "honesty": list(HONESTY),
    }
    return StateSnapshot.create(
        snapshot_id=f"{case.case_id}@{case.version}/{state.digest[:12]}",
        case_id=case.case_id,
        case_version=case.version,
        environment_version=env.version,
        title=case.title,
        task=case.task,
        state=state.values,
        allowed_actions=tuple((action.action_id, action.description, action.asks_human) for action in case.actions),
        context_pack_json=json.dumps(context_pack, sort_keys=True, ensure_ascii=False),
        source_refs=case.source_refs(),
    )

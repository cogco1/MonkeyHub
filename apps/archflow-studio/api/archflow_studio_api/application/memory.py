"""Project memory: how this project works, not what it settled (#252, ADR-009).

A decision remembers the project: what the architect kept, avoided or
required (``decisions``, Project State under #185). Memory remembers how we
work: where a piece of retained content is ("项目图框在这份文件里") and where to
look first for a topic ("查材料先去 A、B,别用 C"). It is its own owner with its
own records - one immutable revision per change in the fixed ``studio-memory``
run, record kind ``studio-memory-record``, through the same P036 ports - so a
build that only knows decisions never reads one.

Every item has the shape the owner wrote (#252, 2026-09-28): a ``key``, a
``kind``, a ``scope``, ``appliesWhen``, a kind-specific ``value``, its
``authority``, its ``provenance`` (the user's own words and message), a
``version`` and a ``status``. Two kinds exist now: a ``locator`` points at
retained project content and copies none of it; a ``source_policy`` says where
to look first, and what to avoid, for a research topic. Scope is ``project``;
the wider scopes live in the library project and reach a project by pinned
import. Authority is ``explicit``: an item is saved from the user's words or a
person's own action, never inferred from behaviour. ``confidence``,
``supportCount`` and ``contradictionCount`` stay out until something computes
them.

Retrieval is deterministic: scope first, then ``appliesWhen``, then the words
(NFKC, casefold, CJK bigrams; no embeddings). A locator's target is read again
through P036 on every lookup, and one that no longer resolves is handed back
stale with its reason, never dropped and never guessed. The revision chain,
its checks and the message provenance are the decisions owner's machinery,
reused rather than copied.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Mapping, Sequence
import unicodedata
from urllib.parse import urlsplit
import uuid

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_MEMORY_RECORD
from archflow.project.refs import record_ref_from_uri
from archflow.project.repository import ProjectRepositoryError

from ..transport.errors import StudioError
from .artifacts import artifact_bytes, document_bytes
from .authentication import ActorAttribution
from .binding import ProjectBinding, retained_sources
from .boards import read_board
from .decisions import _message_source, fixed_run, revision_chains

MEMORY_RUN_ID = "studio-memory"
MEMORY_SCHEMA = "StudioMemoryRecord@1"

LOCATOR = "locator"
SOURCE_POLICY = "source_policy"
KINDS = (LOCATOR, SOURCE_POLICY)
# Named by the owner and not written yet: each needs its own evidence first
# (a recipe is still a drawing decision until it migrates; a preference or
# habit waits for #253's accept/reject evidence).
RESERVED_KINDS = ("recipe", "preference", "habit", "standard")
PROJECT = "project"
# Wider scopes belong to the library project and arrive by pinned import.
RESERVED_SCOPES = ("organization", "team", "user")
EXPLICIT = "explicit"
RESERVED_AUTHORITIES = ("observed", "inferred")
# Left out until something computes them; authority never derives from them.
RESERVED_FIELDS = ("confidence", "supportCount", "contradictionCount")

ACTIVE = "active"
SUPERSEDED = "superseded"
REVOKED = "revoked"

# The task domains a turn can name. An item applies in the domains its kind
# lists, or in every domain when it lists none; a turn that names no domain
# reads every item its words are about.
TASK_DOMAINS = ("design", "drawing", "copy", "research")
_KIND_DOMAINS = {LOCATOR: (), SOURCE_POLICY: ("research",)}
# The normalized research topics, each with the words that put a turn on it.
# An ASCII word matches a whole word; a CJK word matches where it occurs.
RESEARCH_KEYS = {
    "materials": ("材料", "材质", "建材", "性能", "material", "materials"),
    "regulations": ("规范", "法规", "标准", "条文", "规定", "regulation", "regulations", "code", "codes"),
    "products": ("产品", "厂家", "厂商", "型号", "品牌", "product", "products", "manufacturer"),
    "precedents": ("案例", "先例", "参考项目", "precedent", "precedents", "case", "cases"),
}


@dataclass(frozen=True, slots=True)
class MemoryRevision:
    """One retained revision of one memory item: the P036 ref it is at, and what it says."""

    ref: str
    payload: Mapping[str, Any]

    @property
    def memory_id(self) -> str:
        return str(self.payload["memoryId"])

    @property
    def status(self) -> str:
        return str(self.payload["status"])

    @property
    def kind(self) -> str:
        return str(self.payload["kind"])


@dataclass(frozen=True, slots=True)
class MemoryMatch:
    """One item a turn's words are about, why, and whether a locator's target still resolves."""

    revision: MemoryRevision
    matched_terms: tuple[str, ...]
    stale_reason: str | None = None


def _invalid(message: str) -> StudioError:
    return StudioError(422, "MEMORY_INVALID", message)


# ---- the fixed run ---------------------------------------------------------


def _revisions(binding: ProjectBinding) -> dict[str, Mapping[str, Any]]:
    """Every retained revision in the memory run, keyed by its exact ref."""

    run = fixed_run(binding, MEMORY_RUN_ID, create=False, noun="memory", prefix="MEMORY")
    if run is None:
        return {}
    refs = binding.repository.list_json(
        run=run,
        destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=MEMORY_RUN_ID),
        record_kind=STUDIO_MEMORY_RECORD,
    )
    revisions: dict[str, Mapping[str, Any]] = {}
    for ref in refs:
        payload = binding.repository.load_json(ref)
        if payload.get("schema") != MEMORY_SCHEMA or payload.get("projectId") != binding.project_id:
            raise StudioError(409, "MEMORY_BINDING_MISMATCH", "A retained memory item belongs to another project.")
        revisions[ref.uri] = payload
    return revisions


def _chains(binding: ProjectBinding) -> dict[str, tuple[MemoryRevision, ...]]:
    chains = revision_chains(_revisions(binding), identity="memoryId", noun="memory", prefix="MEMORY")
    return {memory_id: tuple(MemoryRevision(ref, payload) for ref, payload in chain)
            for memory_id, chain in chains.items()}


def _ordered(rows) -> tuple[MemoryRevision, ...]:
    return tuple(sorted(rows, key=lambda row: (str(row.payload["createdAt"]), row.memory_id)))


def _current(binding: ProjectBinding) -> tuple[MemoryRevision, ...]:
    return _ordered(chain[-1] for chain in _chains(binding).values())


# ---- checking what an item points at ---------------------------------------

# A URL names something outside the project; ``project://`` is a record ref.
_URL = re.compile(r"^(?!project://)[A-Za-z][A-Za-z0-9+.-]*://|^www\.", re.IGNORECASE)
# A drive, a UNC share, a POSIX root or a home directory: one machine's path.
_MACHINE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]|~[\\/]|file:)", re.IGNORECASE)
_DOMAIN_NAME = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _outside_project(value: Any) -> str | None:
    """Why this string cannot identify retained project content, or None."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    if _MACHINE_PATH.match(text):
        return ("an absolute machine path is never a stable identity (AGENTS.md): it names one computer's "
                "disk, not this project's content. Register the file as a project document first, then "
                "point at that registration.")
    if _URL.match(text):
        return ("a URL names something outside this project, and what it serves can change under it. "
                "Register the file as a project document first, then point at that registration.")
    return None


def _content_ref(target: Any, *, what: str) -> dict[str, Any]:
    """One reference to retained project content, normalized; a path or URL is refused with its reason."""

    if isinstance(target, str):
        raise StudioError(422, "LOCATOR_TARGET_INVALID", _outside_project(target) or (
            f"a {what} is retained project content: a registered document page, an artifact by its "
            "sha256, or a board revision element - not a free-text name."))
    for value in target.values():
        reason = _outside_project(value)
        if reason is not None:
            raise StudioError(422, "LOCATOR_TARGET_INVALID", reason)
    kind = target["kind"]
    if kind == "document":
        return {"kind": "document", "runId": target["runId"], "assetSha256": target["assetSha256"],
                "revisionRef": target.get("revisionRef"), "pageIndex": target.get("pageIndex")}
    if kind == "artifact":
        return {"kind": "artifact", "sha256": target["sha256"]}
    return {"kind": "board", "revisionSha256": target["revisionSha256"], "elementId": target["elementId"]}


def stale_reason(binding: ProjectBinding, target: Mapping[str, Any]) -> str | None:
    """Why retained content no longer resolves through P036, or None when it does.

    Every read asks again: the document is still registered at that exact
    revision and its bytes hash back to its digest, an artifact's bytes hash to
    its sha256, a board element is still live on that retained revision.
    """

    try:
        if target["kind"] == "document":
            document, _ = document_bytes(binding, target["runId"], target["assetSha256"], target["revisionRef"])
            if document.revision_ref != target["revisionRef"]:
                return "the document is no longer registered at that exact revision."
            if target["pageIndex"] is not None and not 0 <= target["pageIndex"] < len(document.pages):
                return "that page does not exist in this document version."
        elif target["kind"] == "artifact":
            artifact_bytes(binding, target["sha256"])
        else:
            scene = read_board(binding, target["revisionSha256"])
            if not any(element.get("id") == target["elementId"] and not element.get("isDeleted")
                       for element in scene.elements):
                return "that board revision has no live element with that id."
    except StudioError as exc:
        return f"{exc.code}: {exc.detail}"
    return None


def _resolved(binding: ProjectBinding, target: Any, *, what: str) -> dict[str, Any]:
    normalized = _content_ref(target, what=what)
    reason = stale_reason(binding, normalized)
    if reason is not None:
        raise StudioError(404, "LOCATOR_TARGET_UNKNOWN", f"that content does not resolve in this project: {reason}")
    return normalized


def _source_name(value: str) -> str:
    """A site's domain, lowercased, or a named source as written."""

    text = unicodedata.normalize("NFKC", value).strip()
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", text):
        host = urlsplit(text).hostname
        if not host:
            raise _invalid(f"{value!r} names no site; give its domain or the source's name.")
        return host
    return text.lower() if _DOMAIN_NAME.fullmatch(text.lower()) else text


def _locator_value(binding: ProjectBinding, value: Mapping[str, Any]) -> dict[str, Any]:
    label = unicodedata.normalize("NFKC", value["label"]).strip()
    if not label:
        raise _invalid("a locator's label is the short name the user calls this content by.")
    return {"label": label, "target": _resolved(binding, value["target"], what="locator's target")}


def _source_policy_value(value: Mapping[str, Any]) -> dict[str, Any]:
    topic = unicodedata.normalize("NFKC", value["topic"]).strip()
    keys = list(value["keys"])
    if not topic:
        raise _invalid("a source policy names its topic in the user's words.")
    if not keys or len(set(keys)) != len(keys) or any(key not in RESEARCH_KEYS for key in keys):
        raise _invalid(f"a source policy names one or more distinct keys of {', '.join(RESEARCH_KEYS)}.")
    prefer = [_source_name(item) for item in value.get("prefer") or ()]
    avoid = [_source_name(item) for item in value.get("avoid") or ()]
    if not prefer and not avoid:
        raise _invalid("a source policy prefers or avoids at least one source.")
    named = [item.casefold() for item in (*prefer, *avoid)]
    if any(not item for item in named) or len(set(named)) != len(named):
        raise _invalid("each source appears once: a source is either preferred, in its order, or avoided.")
    note = value.get("note")
    note = None if note is None else note.strip() or None
    return {"topic": topic, "keys": [key for key in RESEARCH_KEYS if key in keys],
            "prefer": prefer, "avoid": avoid, "note": note}


def _fold(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _applies_when(binding: ProjectBinding, kind: str, value: Mapping[str, Any],
                  requested: Mapping[str, Any] | None) -> dict[str, Any]:
    """When an item applies: its kind's domains, its topic and keys, and an optional Stage."""

    stage_ref = (requested or {}).get("stageRef")
    if stage_ref is not None:
        try:
            reference = record_ref_from_uri(stage_ref, binding.project_id)
        except (TypeError, ValueError) as exc:
            raise _invalid(f"stageRef is not a record in this project: {exc}") from exc
        # Checked against committed design history, never against what the
        # item points at.
        binding.design_stage(reference)
    if kind == LOCATOR:
        return {"domains": list(_KIND_DOMAINS[kind]), "topics": [value["label"]], "keys": [], "stageRef": stage_ref}
    return {"domains": list(_KIND_DOMAINS[kind]), "topics": [value["topic"]], "keys": list(value["keys"]),
            "stageRef": stage_ref}


def _key(kind: str, value: Mapping[str, Any]) -> str:
    return f"{kind}:{_fold(value['label'] if kind == LOCATOR else value['topic'])}"


def _content(binding: ProjectBinding, spec: Mapping[str, Any], chains: Mapping[str, Sequence[MemoryRevision]],
             *, replacing: str | None = None) -> dict[str, Any]:
    """One item's checked content: the owner's shape, with provenance bound to the user's words.

    An agent that interpreted the user's words keeps sourceKind 'agent' and
    names the message they came from; it never saves one as the user's on its
    own, and nothing here is inferred from behaviour.
    """

    kind = spec["kind"]
    if spec["sourceKind"] == "agent" and spec.get("messageSource") is None:
        raise _invalid(f"an agent saves a {kind} only from the user's own words: name the message they came "
                       "from in messageSource.")
    value = _locator_value(binding, spec["value"]) if kind == LOCATOR else _source_policy_value(spec["value"])
    applies_when = _applies_when(binding, kind, value, spec.get("appliesWhen"))
    key = _key(kind, value)
    for chain in chains.values():
        other = chain[-1]
        if (other.memory_id != replacing and other.status == ACTIVE and other.payload["key"] == key
                and other.payload["appliesWhen"]["stageRef"] == applies_when["stageRef"]):
            raise StudioError(409, "MEMORY_KEY_CONFLICT",
                              f"memory {other.memory_id} already holds {key!r} for the same reach. "
                              "Supersede it to change it.")
    evidence = [_resolved(binding, ref, what="piece of evidence") for ref in spec.get("evidenceRefs") or ()]
    return {
        "key": key,
        "kind": kind,
        "scope": spec.get("scope") or PROJECT,
        "appliesWhen": applies_when,
        "value": value,
        "authority": spec.get("authority") or EXPLICIT,
        "provenance": {
            "rawLanguage": spec["rawLanguage"],
            # A claim about which message these words came from, not a
            # credential; the Hub fills it from the actual user message.
            "messageSource": _message_source(spec.get("messageSource")),
            "sourceKind": spec["sourceKind"],
            "evidenceRefs": evidence,
        },
    }


def _retain(binding: ProjectBinding, *, memory_id: str, previous: MemoryRevision | None, content: Mapping[str, Any],
            status: str, reason: str | None, attribution: ActorAttribution,
            revision_message_source: Mapping[str, Any] | None = None) -> MemoryRevision:
    payload = {
        "schema": MEMORY_SCHEMA,
        "projectId": binding.project_id,
        "memoryId": memory_id,
        "version": 1 if previous is None else int(previous.payload["version"]) + 1,
        "previousRevisionRef": None if previous is None else previous.ref,
        "status": status,
        "reason": reason,
        "revisionMessageSource": _message_source(revision_message_source),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "attribution": {"actorId": attribution.actor_id, "authenticated": attribution.authenticated,
                        "origin": attribution.origin},
        **content,
    }
    run = fixed_run(binding, MEMORY_RUN_ID, create=True, noun="memory", prefix="MEMORY")
    try:
        ref = binding.repository.put_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=MEMORY_RUN_ID),
            record_kind=STUDIO_MEMORY_RECORD,
            payload=payload,
        )
    except (ProjectRepositoryError, OSError) as exc:
        raise StudioError(409, "MEMORY_WRITE_FAILED", "The memory item could not be retained in its project.") from exc
    return MemoryRevision(ref.uri, payload)


# ---- the public operations -------------------------------------------------


def list_memory(binding: ProjectBinding, *, kind: str | None = None) -> tuple[MemoryRevision, ...]:
    """Every item's current revision, oldest first; one kind when asked."""

    return tuple(row for row in _current(binding) if kind is None or row.kind == kind)


def memory_history(binding: ProjectBinding, memory_id: str) -> tuple[MemoryRevision, ...]:
    """One item's whole chain, oldest first; old wording is never rewritten."""

    chain = _chains(binding).get(memory_id)
    if chain is None:
        raise StudioError(404, "MEMORY_NOT_FOUND", f"{memory_id} is not a memory item this project retains.")
    return chain


@retained_sources
def save_memory(binding: ProjectBinding, spec: Mapping[str, Any], attribution: ActorAttribution) -> MemoryRevision:
    """Check one item against what it points at and retain its first revision."""

    chains = _chains(binding)
    return _retain(binding, memory_id=str(uuid.uuid4()), previous=None, content=_content(binding, spec, chains),
                   status=ACTIVE, reason=None, attribution=attribution)


@retained_sources
def revise_memory(
    binding: ProjectBinding, memory_id: str, *, expected_revision_ref: str, action: str, reason: str | None,
    replacement: Mapping[str, Any] | None, attribution: ActorAttribution,
    revision_message_source: Mapping[str, Any] | None = None,
) -> MemoryRevision:
    """Revoke or supersede one item, against the revision the caller read.

    A revocation keeps the words and value it revokes and adds the reason. A
    supersession keeps its kind: a locator moves, a policy changes its sources.
    """

    chains = _chains(binding)
    chain = chains.get(memory_id)
    if chain is None:
        raise StudioError(404, "MEMORY_NOT_FOUND", f"{memory_id} is not a memory item this project retains.")
    current = chain[-1]
    if current.ref != expected_revision_ref:
        raise StudioError(409, "MEMORY_STALE", "This memory item has a newer revision. Read it before revising it again.")
    if current.status == REVOKED:
        raise StudioError(409, "MEMORY_REVOKED", "This memory item has already been revoked.")
    if action == "revoke":
        content = {key: current.payload[key]
                   for key in ("key", "kind", "scope", "appliesWhen", "value", "authority", "provenance")}
    elif replacement is None:
        raise _invalid("superseding a memory item needs the replacement it is superseded by.")
    elif replacement["kind"] != current.kind:
        raise _invalid(f"a supersession keeps its kind: this {current.kind} is replaced by another "
                       f"{current.kind}. Revoke it and save the new one instead.")
    else:
        content = _content(binding, replacement, chains, replacing=memory_id)
    return _retain(binding, memory_id=memory_id, previous=current, content=content,
                   status=REVOKED if action == "revoke" else ACTIVE, reason=reason, attribution=attribution,
                   revision_message_source=revision_message_source)


# ---- what a turn's words are about -----------------------------------------

# Deterministic lexical lookup: NFKC folds width, casefold folds case; CJK text
# is read as character bigrams and other text as words. No embeddings.
_CJK_RUN = re.compile(r"[㐀-䶿一-鿿豈-﫿]+")
_WORD = re.compile(r"[^\W_]+")
_STOP_TERMS = frozenset({
    "在哪", "哪里", "哪儿", "放在", "在这", "这里", "那里", "这个", "那个", "这份", "那份", "一下", "什么",
    "我们", "你们", "上次", "之前", "说的",
    "a", "an", "and", "at", "for", "in", "is", "it", "of", "on", "or", "our", "the", "this", "that",
    "to", "we", "what", "where", "which",
})


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def lexical_terms(text: str) -> frozenset[str]:
    """The terms a lookup compares: CJK bigrams (a lone character as itself) and words."""

    normalized = _normalized(text)
    terms: set[str] = set()
    for run in _CJK_RUN.findall(normalized):
        terms.update([run] if len(run) == 1 else (run[index:index + 2] for index in range(len(run) - 1)))
    terms.update(word for word in _WORD.findall(_CJK_RUN.sub(" ", normalized)) if len(word) > 1)
    return frozenset(terms - _STOP_TERMS)


def _about_locator(query_terms: frozenset[str], payload: Mapping[str, Any]) -> tuple[Any, ...] | None:
    """A locator matches a shared term with its label, or two with the words it was saved from."""

    label_terms = lexical_terms(payload["value"]["label"])
    by_label = query_terms & label_terms
    by_words = (query_terms & lexical_terms(payload["provenance"]["rawLanguage"])) - label_terms
    if not by_label and len(by_words) < 2:
        return None
    return (-len(by_label), -len(by_words)), tuple(sorted(by_label | by_words))


def _about_policy(query_terms: frozenset[str], utterance: str, payload: Mapping[str, Any]) -> tuple[Any, ...] | None:
    """A source policy matches words about its topic or one of its keys."""

    applies = payload["appliesWhen"]
    matched = {term for topic in applies["topics"] for term in query_terms & lexical_terms(topic)}
    normalized = _normalized(utterance)
    matched.update(word for key in applies["keys"] for word in RESEARCH_KEYS[key]
                   if ((word in query_terms) if word.isascii() else (word in normalized)))
    if not matched:
        return None
    return (0, -len(matched)), tuple(sorted(matched))


def memory_for(
    binding: ProjectBinding, utterance: str, *, stage_ref: str | None = None, domain: str | None = None,
    kind: str | None = None,
) -> tuple[MemoryMatch, ...]:
    """The active items these words are about: scope, then appliesWhen, then the words.

    Scope is the project. An item bound to a Stage applies only under that
    Stage; one whose kind lists task domains applies only to a turn that
    named one of them or named none. Then the words: a locator by its label
    or the words it was saved from, a source policy by its topic or keys.
    Every locator found is read again (``stale_reason``); a stale one is kept
    with its reason. Locators rank before policies, closer matches first.
    """

    query_terms = lexical_terms(utterance)
    if not query_terms:
        return ()
    ranked: list[tuple[tuple[Any, ...], MemoryRevision, tuple[str, ...]]] = []
    for revision in _current(binding):
        payload = revision.payload
        if revision.status != ACTIVE or (kind is not None and revision.kind != kind):
            continue
        if payload["scope"] != PROJECT:
            continue
        applies = payload["appliesWhen"]
        if applies["stageRef"] is not None and applies["stageRef"] != stage_ref:
            continue
        if domain is not None and applies["domains"] and domain not in applies["domains"]:
            continue
        found = (_about_locator(query_terms, payload) if revision.kind == LOCATOR
                 else _about_policy(query_terms, utterance, payload))
        if found is None:
            continue
        rank, terms = found
        ranked.append(((KINDS.index(revision.kind), *rank, str(payload["createdAt"]), revision.memory_id),
                       revision, terms))
    return tuple(
        MemoryMatch(revision, terms,
                    stale_reason(binding, revision.payload["value"]["target"]) if revision.kind == LOCATOR else None)
        for _, revision, terms in sorted(ranked, key=lambda row: row[0]))


def locate(binding: ProjectBinding, query: str, *, stage_ref: str | None = None) -> tuple[MemoryMatch, ...]:
    """Where the words say X is: the locators they are about, each target read again now."""

    return memory_for(binding, query, stage_ref=stage_ref, kind=LOCATOR)

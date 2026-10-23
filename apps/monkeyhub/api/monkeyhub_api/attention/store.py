"""The attention queue, kept by the Hub: one file per project under its own runtime root.

A card's state is a person's handling of it, not a design fact, so it lives
beside the operation journal (``runtime/operations``), never in a project. The
left column counts cards across projects whether or not a project is open, so
every project's file is read on first use and kept in memory. Cards about the
Hub itself (a prepared update) are kept the same way under ``HUB_PROJECT_ID``.
"""

from collections.abc import Callable, Iterable
from contextlib import contextmanager
import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import threading
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import ValidationError

from ..models import HubFailure
from ..runtime.workers import project_key
from .models import (
    CLOSED, DRAFT_FIELDS, PENDING, UNSETTLED, Actor, ArgValue, AttentionDraft, AttentionItem, AttentionState,
    AttentionSummary, ProjectAttentionCount, Resolution, StateChange, Suppression,
)

# Every unsettled card stays; of those already closed, the newest this many.
RETAIN_CLOSED = 500
# A rule card set aside without a number of days stays quiet this long.
RULE_QUIET_DAYS = 7
# The scope of cards about the Hub itself; its folder is the Hub runtime root.
HUB_PROJECT_ID = "monkeyhub"

Listener = Callable[[str, str, tuple[str, ...]], None]
# Refuses a draft whose buttons the executor does not run as written (HubFailure).
Check = Callable[[AttentionDraft], None]


def runtime_id(project_id: str, project_dir: str) -> str:
    """The id the Hub names this project's runtime by (``Applications.runtime_id``), and its file."""

    return str(uuid5(NAMESPACE_URL, f"{project_id}:{project_key(project_dir)}"))


def _opened(item: AttentionItem) -> AttentionState:
    return "review" if item.kind == "change_list" else "open"


def _before_working(item: AttentionItem) -> AttentionState:
    started = next((row for row in reversed(item.history) if row.to == "working"), None)
    if started is not None and started.from_ in {"open", "review", "later"}:
        return started.from_
    return _opened(item)


def _decision(content: AttentionDraft) -> tuple:
    # What a person answered: the facts and the answers offered. A reworded
    # title does not bring back a card that was set aside.
    return (content.kind, content.source.model_dump(),
            [(option.id, option.action.model_dump()) for option in content.options])


@dataclass
class _Project:
    project_id: str
    project_dir: str
    items: dict[str, AttentionItem] = field(default_factory=dict)
    suppressions: list[Suppression] = field(default_factory=list)
    # The project's own "attention" conversation: where its queue's answers
    # and the new shell's input go when no other conversation is named.
    conversation: str | None = None
    # Saved rows this Hub cannot read, such as a card kind from a newer Hub:
    # written back as they were, never dropped.
    unread: list[dict] = field(default_factory=list)

    def by_key(self, dedupe_key: str) -> AttentionItem | None:
        """The newest card under a key: a card already answered stays beside the one that asks again."""

        # Cards are kept, and saved, in the order they were made.
        found = [(item.createdAt, order, item) for order, item in enumerate(self.items.values())
                 if item.dedupeKey == dedupe_key]
        return max(found, key=lambda row: row[:2])[2] if found else None


class AttentionStore:
    """Cards and how each was handled; it never writes a project."""

    def __init__(self, root: Path | None, *, clock: Callable[[], datetime] | None = None,
                 check: Check | None = None):
        # root is <runtime_root>/attention; None keeps cards in memory only.
        self.root = root
        self.check = check
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._projects: dict[str, _Project] | None = None
        self._invalid: set[str] = set()
        self._listeners: list[Listener] = []
        self._pending: list[tuple[str, str, tuple[str, ...]]] = []

    def subscribe(self, listener: Listener) -> None:
        """Call ``listener(project_id, project_dir, ids)`` after each saved change."""

        self._listeners.append(listener)

    @contextmanager
    def _locked(self):
        # Listeners hear of a change only after the lock is released.
        try:
            with self._lock:
                yield
        finally:
            with self._lock:
                pending, self._pending = self._pending, []
            for project_id, project_dir, ids in pending:
                for listener in self._listeners:
                    listener(project_id, project_dir, ids)

    # Reading -------------------------------------------------------------

    def items(self, project_dir: str | None = None, *, states: Iterable[str] | None = None,
              channels: Iterable[str] | None = None) -> list[AttentionItem]:
        """Cards newest first, across projects unless one is named; unsettled ones unless states are named."""

        wanted, channels = frozenset(states or UNSETTLED), frozenset(channels or ())
        with self._locked():
            now = self._clock()
            projects = [self._named(project_dir)] if project_dir is not None else self._all().values()
            shown = [self._shown(item, now) for project in projects for item in project.items.values()]
        shown = [item for item in shown if item.state in wanted and (not channels or item.channel in channels)]
        return sorted(shown, key=lambda item: item.updatedAt, reverse=True)

    def get(self, item_id: str) -> AttentionItem:
        with self._locked():
            project = self._holding(item_id)
            return self._shown(project.items[item_id], self._clock())

    def summary(self) -> AttentionSummary:
        with self._locked():
            now = self._clock()
            counts = []
            for project in self._all().values():
                states = [self._shown(item, now).state for item in project.items.values()]
                counts.append(ProjectAttentionCount(
                    projectId=project.project_id, projectDir=project.project_dir,
                    open=sum(state in PENDING for state in states),
                    later=states.count("later"), working=states.count("working")))
            invalid = sorted(self._invalid)
        hub = next((row for row in counts if row.projectId == HUB_PROJECT_ID), None)
        return AttentionSummary(projects=[row for row in counts if row is not hub], hub=hub,
                                open=sum(row.open for row in counts), later=sum(row.later for row in counts),
                                working=sum(row.working for row in counts), invalid=invalid)

    def suppressions(self, project_dir: str) -> list[Suppression]:
        """The rule/target pairs a person asked to stay quiet about, while they still apply."""

        with self._locked():
            now = self._clock()
            return [row.model_copy() for row in self._named(project_dir).suppressions
                    if datetime.fromisoformat(row.until) > now]

    def conversation(self, project_dir: str) -> str | None:
        """The chat session id kept as this project's attention conversation, if one was made."""

        with self._locked():
            return self._named(project_dir).conversation

    def set_conversation(self, project_id: str, project_dir: str, session_id: str) -> None:
        with self._locked():
            project = self._project(project_id, project_dir)

            def apply(now: datetime) -> list[str]:
                project.conversation = session_id
                return [session_id]

            if project.conversation != session_id:
                self._change(project, apply)

    # Producers -------------------------------------------------------------

    def upsert(self, project_id: str, project_dir: str, draft: AttentionDraft) -> AttentionItem:
        """Write a producer's card under its dedupeKey: one thing to look at is one card.

        A new key opens a card. A card that is still unsettled takes the new
        content and keeps its state; one being carried out keeps its content
        until it is done. A card its source had stopped asking for (superseded,
        expired) opens again. One a person set aside opens again only when what
        it asks has changed (its facts or its answers). One a person answered
        stays answered, with its undo, and a changed question is a new card.
        """

        self._checked([draft])
        with self._locked():
            project = self._project(project_id, project_dir)
            written: list[str] = []

            def apply(now: datetime) -> list[str]:
                item_id, changed = self._upsert(project, draft, now)
                written.append(item_id)
                return [item_id] if changed else []

            self._change(project, apply)
            return self._shown(project.items[written[0]], self._clock())

    def reconcile(self, project_id: str, project_dir: str, prefix: str,
                  drafts: Iterable[AttentionDraft]) -> list[str]:
        """One producer's whole answer: write each draft, and retire what it no longer offers.

        Every draft's dedupeKey starts with ``prefix``. An open, review or
        later card under that prefix that is missing from ``drafts`` reads as
        superseded: its facts are gone. A card being carried out is left to its
        executor. Returns the ids of the cards that changed.
        """

        drafts = list(drafts)
        if any(not draft.dedupeKey.startswith(prefix) for draft in drafts) or not prefix:
            raise ValueError("Each draft a producer reconciles carries its own nonempty prefix.")
        self._checked(drafts)
        with self._locked():
            project = self._project(project_id, project_dir)

            def apply(now: datetime) -> list[str]:
                changed = [item_id for item_id, written in (self._upsert(project, draft, now) for draft in drafts)
                           if written]
                offered = {draft.dedupeKey for draft in drafts}
                for item in list(project.items.values()):
                    if (item.dedupeKey.startswith(prefix) and item.dedupeKey not in offered
                            and item.state in {"open", "review", "later"}):
                        self._move(item, "superseded", "machine", now, note="Its source no longer asks for this.")
                        changed.append(item.id)
                return list(dict.fromkeys(changed))

            return self._change(project, apply)

    def retire(self, project_id: str, project_dir: str, dedupe_key: str, state: AttentionState = "superseded", *,
               note: str | None = None) -> AttentionItem | None:
        """Close one unsettled card because its facts changed elsewhere (superseded) or ran out (expired)."""

        if state not in {"superseded", "expired"}:
            raise ValueError("A producer retires a card only as superseded or expired.")
        with self._locked():
            project = self._project(project_id, project_dir)
            item = project.by_key(dedupe_key)
            if item is None or item.state not in UNSETTLED - {"working"}:
                return None
            self._change(project, lambda now: [self._move(item, state, "machine", now, note=note)])
            return self._shown(project.items[item.id], self._clock())

    # A person's handling -----------------------------------------------------

    def later(self, item_id: str, *, until: datetime | None = None) -> AttentionItem:
        """Put a card off: it stops counting, and comes back at ``until`` if one is given."""

        return self._handle(item_id, "later", {"open", "review"}, until=until)

    def dismiss(self, item_id: str, *, days: int | None = None) -> AttentionItem:
        """Set a card aside, for ``days`` if given. A rule's card also quiets its rule for that target."""

        def quiet(project: _Project, item: AttentionItem, now: datetime) -> None:
            if item.source.ruleId and item.source.targetRef:
                project.suppressions = [row for row in project.suppressions
                                        if (row.ruleId, row.targetRef) != (item.source.ruleId, item.source.targetRef)]
                project.suppressions.append(Suppression(
                    ruleId=item.source.ruleId, targetRef=item.source.targetRef,
                    until=(now + timedelta(days=days or RULE_QUIET_DAYS)).isoformat()))

        until = None if days is None else self._clock() + timedelta(days=days)
        return self._handle(item_id, "dismissed", {"open", "review", "later"}, until=until, also=quiet)

    def reopen(self, item_id: str) -> AttentionItem:
        """Bring back a card that was put off or set aside, and let its rule speak again."""

        def unquiet(project: _Project, item: AttentionItem, now: datetime) -> None:
            project.suppressions = [row for row in project.suppressions
                                    if (row.ruleId, row.targetRef) != (item.source.ruleId, item.source.targetRef)]

        return self._handle(item_id, _opened, {"later", "dismissed"}, also=unquiet)

    # Carrying out an answer: the executor's bookkeeping ----------------------

    def begin(self, item_id: str, option_id: str, *, by: Actor = "person") -> AttentionItem:
        """Mark a card as being carried out with one of its answers; a second click then finds it working."""

        return self._handle(item_id, "working", {"open", "review", "later"}, by=by, option_id=option_id,
                            keep_snooze=True)

    def progress(self, item_id: str, facts: dict[str, ArgValue]) -> AttentionItem:
        """Keep what the executor saw on the working step, before it writes anything."""

        with self._locked():
            project = self._holding(item_id)
            item = project.items[item_id]
            if item.state != "working":
                raise HubFailure(409, "ATTENTION_STATE", f"This card is {item.state}; that cannot be done to it now.")

            def apply(now: datetime) -> list[str]:
                item.history[-1].facts.update(facts)
                return [item.id]

            self._change(project, apply)
            return self._shown(project.items[item_id], self._clock())

    def settle(self, item_id: str, resolution: Resolution) -> AttentionItem:
        """The answer was carried out: the card is done, with what was chosen and how to take it back."""

        def record(project: _Project, item: AttentionItem, now: datetime) -> None:
            item.resolution = resolution.model_copy(deep=True)

        return self._handle(item_id, "done", {"working"}, by=resolution.by, option_id=resolution.optionId,
                            also=record)

    def release(self, item_id: str, note: str) -> AttentionItem:
        """The answer could not be carried out: the card goes back to where it was, saying why."""

        return self._handle(item_id, _before_working, {"working"}, by="machine", note=note, keep_snooze=True)

    def undone(self, item_id: str, *, by: Actor = "person", note: str | None = None) -> AttentionItem:
        """A handled card's answer was taken back: the card asks again."""

        def forget(project: _Project, item: AttentionItem, now: datetime) -> None:
            item.resolution = None

        return self._handle(item_id, _opened, {"done"}, by=by, note=note, also=forget)

    # Internals ---------------------------------------------------------------

    def _handle(self, item_id: str, to: AttentionState | Callable[[AttentionItem], AttentionState],
                allowed: set[str], *, by: Actor = "person", until: datetime | None = None,
                option_id: str | None = None, note: str | None = None, keep_snooze: bool = False,
                also: Callable[[_Project, AttentionItem, datetime], None] | None = None) -> AttentionItem:
        with self._locked():
            project = self._holding(item_id)
            item = project.items[item_id]
            shown = self._shown(item, self._clock())
            if shown.state not in allowed:
                raise HubFailure(409, "ATTENTION_STATE", f"This card is {shown.state}; that cannot be done to it now.")

            def apply(now: datetime) -> list[str]:
                # Carrying a card out, and failing to, keeps the time it was put off until.
                snoozed = item.snoozedUntil if keep_snooze else until.isoformat() if until else None
                self._move(item, to(item) if callable(to) else to, by, now, note=note, option_id=option_id,
                           snoozed_until=snoozed)
                if also is not None:
                    also(project, item, now)
                return [item.id]

            self._change(project, apply)
            return self._shown(project.items[item_id], self._clock())

    def _checked(self, drafts: list[AttentionDraft]) -> None:
        if self.check is not None:
            for draft in drafts:
                self.check(draft)

    def _upsert(self, project: _Project, draft: AttentionDraft, now: datetime) -> tuple[str, bool]:
        """The card written for ``draft`` and whether it changed."""

        stamp = now.isoformat()
        item = project.by_key(draft.dedupeKey)
        if item is not None and item.state == "working":
            return item.id, False
        current = None if item is None else AttentionDraft.model_validate(item.model_dump(include=set(DRAFT_FIELDS)))
        changed = current is not None and _decision(current) != _decision(draft)
        if item is None or (item.state == "done" and changed):
            item = AttentionItem(**draft.model_dump(), id=f"att_{uuid4().hex}", projectId=project.project_id,
                                 projectDir=project.project_dir, state="open", createdAt=stamp, updatedAt=stamp)
            item.state = _opened(item)
            item.history.append(StateChange(from_=None, to=item.state, by="machine", at=stamp))
            project.items[item.id] = item
            return item.id, True
        asked = item.state in {"superseded", "expired"} or (item.state == "dismissed" and changed)
        if (item.state in CLOSED and not asked) or (item.state in UNSETTLED and current == draft):
            return item.id, False
        for name in DRAFT_FIELDS:
            setattr(item, name, copy.deepcopy(getattr(draft, name)))
        item.updatedAt = stamp
        if asked:
            self._move(item, _opened(item), "machine", now,
                       note="What it asks has changed." if changed else "Its source asks again.")
        return item.id, True

    @staticmethod
    def _move(item: AttentionItem, to: AttentionState, by: Actor, now: datetime, *,
              note: str | None = None, option_id: str | None = None, snoozed_until: str | None = None) -> str:
        stamp = now.isoformat()
        item.history.append(StateChange(from_=item.state, to=to, by=by, at=stamp, optionId=option_id, note=note))
        item.state, item.snoozedUntil, item.updatedAt = to, snoozed_until, stamp
        return item.id

    def _woken(self, item: AttentionItem, now: datetime) -> bool:
        # A card put off until a time is open again once that time has passed.
        if item.state not in {"later", "dismissed"} or not item.snoozedUntil:
            return False
        due = datetime.fromisoformat(item.snoozedUntil)
        if due > now:
            return False
        self._move(item, _opened(item), "machine", due, note="The time it was put off for has passed.")
        return True

    def _wake(self, project: _Project, now: datetime) -> list[str]:
        return [item.id for item in project.items.values() if self._woken(item, now)]

    def _shown(self, item: AttentionItem, now: datetime) -> AttentionItem:
        shown = item.model_copy(deep=True)
        self._woken(shown, now)
        return shown

    def _change(self, project: _Project, apply: Callable[[datetime], list[str]]) -> list[str]:
        """Apply one change and save it; on a failed save nothing changed, in memory or on disk."""

        before = copy.deepcopy(project)
        now = self._clock()
        changed = list(dict.fromkeys([*self._wake(project, now), *apply(now)]))
        if changed:
            try:
                self._save(project, now)
            except HubFailure:
                project.__dict__.update(before.__dict__)
                raise
            self._pending.append((project.project_id, project.project_dir, tuple(changed)))
        return [item for item in changed if item in project.items]

    def _all(self) -> dict[str, _Project]:
        if self._projects is None:
            self._projects = {}
            for path in sorted(self.root.glob("*.json")) if self.root is not None and self.root.is_dir() else ():
                project = self._restore(path)
                if project is None:
                    self._invalid.add(path.stem)
                else:
                    self._projects[path.stem] = project
        return self._projects

    def _project(self, project_id: str, project_dir: str) -> _Project:
        key = runtime_id(project_id, project_dir)
        projects = self._all()
        if key in self._invalid:
            raise HubFailure(503, "ATTENTION_LOG_INVALID", "This project's saved cards could not be read, so none were changed.")
        if key not in projects:
            projects[key] = _Project(project_id, str(Path(project_dir).resolve()))
        return projects[key]

    def _named(self, project_dir: str) -> _Project:
        wanted = project_key(project_dir)
        found = [project for project in self._all().values() if project_key(project.project_dir) == wanted]
        if not found:
            return _Project("", project_dir)
        return found[0]

    def _holding(self, item_id: str) -> _Project:
        project = next((project for project in self._all().values() if item_id in project.items), None)
        if project is None:
            raise HubFailure(404, "ATTENTION_NOT_FOUND", "No card has that id.")
        return project

    def _restore(self, path: Path) -> _Project | None:
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            project = _Project(saved["projectId"], saved["projectDir"])
            if not isinstance(project.project_id, str) or not isinstance(project.project_dir, str) \
                    or runtime_id(project.project_id, project.project_dir) != path.stem \
                    or not isinstance(saved["items"], list) or not isinstance(saved.get("suppressions", []), list):
                return None
        except (OSError, ValueError, KeyError, TypeError):
            return None
        for row in saved["items"]:
            try:
                item = AttentionItem.model_validate(row)
            except ValidationError:
                item = None
            if (item is None or item.id in project.items
                    or (item.projectId, project_key(item.projectDir)) != (project.project_id, project_key(project.project_dir))):
                project.unread.append(row)
                continue
            if item.state == "working":
                # The Hub stopped while this answer ran: the card asks again, keeping what was seen.
                self._move(item, _before_working(item), "machine", datetime.fromisoformat(item.updatedAt),
                           note="The Hub stopped before this answer finished; nothing more was tried.",
                           snoozed_until=item.snoozedUntil)
            project.items[item.id] = item
        if isinstance(saved.get("conversationId"), str):
            project.conversation = saved["conversationId"]
        for row in saved.get("suppressions", []):
            try:
                project.suppressions.append(Suppression.model_validate(row))
            except ValidationError:
                continue  # A quiet period that cannot be read only ends early.
        return project

    def _save(self, project: _Project, now: datetime) -> None:
        closed = sorted((item for item in project.items.values() if item.state in CLOSED and not item.snoozedUntil),
                        key=lambda item: item.updatedAt, reverse=True)
        for item in closed[RETAIN_CLOSED:]:
            del project.items[item.id]
        project.suppressions = [row for row in project.suppressions if datetime.fromisoformat(row.until) > now]
        if self.root is None:
            return
        saved = {
            "projectId": project.project_id, "projectDir": project.project_dir,
            "items": [*(item.model_dump(mode="json", by_alias=True) for item in project.items.values()), *project.unread],
            "suppressions": [row.model_dump(mode="json") for row in project.suppressions],
        }
        if project.conversation is not None:
            saved["conversationId"] = project.conversation
        path = self.root / f"{runtime_id(project.project_id, project.project_dir)}.json"
        temporary = path.with_suffix(".tmp")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            try:
                with temporary.open("w", encoding="utf-8") as stream:
                    json.dump(saved, stream, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as exc:
            raise HubFailure(503, "ATTENTION_LOG_UNAVAILABLE", "The card could not be saved, so it is as it was.") from exc

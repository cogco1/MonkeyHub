"""Wake-ups: a conversation continues by itself at a time, on a repeat, or when a project job ends.

A wake-up belongs to one conversation and holds the words it is woken with.
When it is due and the conversation is idle, the Hub sends those words to it as
a message that no person typed (``origins``), and the conversation runs one turn
as it would for the architect. A conversation that is still running is woken
once its turn ends; an archived one, or one that is gone, pauses its wake-ups
and says why. Kept in ``<runtime>/config/wakeups.json``, so they outlast a Hub
restart: one that fell due while the Hub was closed runs once when it is back,
and a repeating one then counts on from that run.

``afterJob`` waits for a job of the conversation's project to end (a skill run,
a render or export job), read from that project's Studio through the Hub, and
gives up waiting after ``JOB_WAIT_LIMIT_S`` with a wake-up that says so.
"""

from __future__ import annotations

from datetime import datetime, time as day_time, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import threading
from typing import Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import transport
from ..models import ChatMessageOrigin, ChatPostRequest, HubFailure

WAKEUPS_FILE = Path("config") / "wakeups.json"
TICK_S = 15.0
MIN_REPEAT_MINUTES = 5
ACTIVE_PER_CHAT = 10
RETRY_MINUTES = 5
RETRIES = 3
JOB_WAIT_LIMIT_S = 4 * 3600
#: What a project job reads while it has not ended; any other status is its end.
_UNFINISHED = frozenset({"queued", "pending", "starting", "running", "executing", "planning", "validated",
                         "committing", "waiting", "accepted", "in_progress"})
#: The project reads a wake-up may wait on: the job routes of the bound Studio.
_JOB_PATH = re.compile(r"^/api/(skills/runs|jobs|render/jobs|exports)/[A-Za-z0-9._:-]{1,128}$")

_log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AfterJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="the job's own read on the project's Studio, e.g. /api/skills/runs/<runId>")

    @model_validator(mode="after")
    def job_route(self):
        if not _JOB_PATH.match(self.path):
            raise ValueError("afterJob.path is a job read: /api/skills/runs/<id>, /api/jobs/<id>, "
                             "/api/render/jobs/<id> or /api/exports/<id>")
        return self


class WakeupRequest(BaseModel):
    """When to wake the conversation, and with what words. Exactly one of the four timings."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=4000, description="what the conversation is woken to do")
    inMinutes: float | None = Field(default=None, ge=1, le=60 * 24 * 60)
    at: str | None = Field(default=None, description="an ISO date-time; without an offset it is this computer's local time")
    everyMinutes: int | None = Field(default=None, ge=MIN_REPEAT_MINUTES, le=60 * 24 * 31)
    dailyAt: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM local time")
    weekdays: list[int] | None = Field(default=None, min_length=1, max_length=7,
                                       description="with dailyAt: 0 Monday … 6 Sunday; omitted, every day")
    afterJob: AfterJob | None = None

    @model_validator(mode="after")
    def one_timing(self):
        given = [name for name in ("inMinutes", "at", "everyMinutes", "dailyAt", "afterJob") if getattr(self, name) is not None]
        if len(given) != 1:
            raise ValueError("give exactly one of inMinutes, at, everyMinutes, dailyAt or afterJob")
        if self.weekdays is not None and self.dailyAt is None:
            raise ValueError("weekdays go with dailyAt")
        if self.weekdays is not None and any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("weekdays are 0 (Monday) to 6 (Sunday)")
        return self


class Wakeup(BaseModel):
    id: str
    chatId: str
    prompt: str
    kind: Literal["once", "every", "daily", "job"]
    everyMinutes: int | None = None
    dailyAt: str | None = None
    weekdays: list[int] | None = None
    jobPath: str | None = None
    nextAt: str | None = None
    createdAt: str
    createdBy: Literal["agent", "person"]
    status: Literal["active", "done", "cancelled", "paused", "failed"] = "active"
    lastRunAt: str | None = None
    lastError: str | None = None
    runs: int = 0
    attempts: int = 0
    label: str = ""


def _local_zone():
    return datetime.now().astimezone().tzinfo


def _parse_at(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise HubFailure(422, "WAKEUP_INVALID", "at is an ISO date-time such as 2026-10-30T09:00.") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_local_zone())
    return moment.astimezone(timezone.utc)


def _next_daily(after: datetime, daily_at: str, weekdays: list[int] | None) -> datetime:
    hour, minute = (int(part) for part in daily_at.split(":"))
    local = after.astimezone(_local_zone())
    for offset in range(0, 8):
        day = (local + timedelta(days=offset)).date()
        candidate = datetime.combine(day, day_time(hour, minute), tzinfo=_local_zone())
        if candidate > local and (weekdays is None or candidate.weekday() in weekdays):
            return candidate.astimezone(timezone.utc)
    raise HubFailure(422, "WAKEUP_INVALID", "No day matches these weekdays.")


def _label(wakeup: Wakeup) -> str:
    """When it runs, in a few words the conversation shows and the Agent is told."""

    if wakeup.kind == "every":
        minutes = wakeup.everyMinutes or 0
        return f"every {minutes // 60} h" if minutes % 60 == 0 else f"every {minutes} min"
    if wakeup.kind == "daily":
        days = "" if wakeup.weekdays is None else " on " + ",".join(
            "Mon Tue Wed Thu Fri Sat Sun".split()[day] for day in sorted(wakeup.weekdays))
        return f"daily at {wakeup.dailyAt}{days}"
    if wakeup.kind == "job":
        return f"when {wakeup.jobPath} ends"
    return "once"


class Wakeups:
    """The wake-ups of every conversation, and the thread that runs them."""

    def __init__(self, runtime_root: Path, chats, *, hub_url: str | None = None, now: Callable[[], datetime] = _now):
        self.path = Path(runtime_root) / WAKEUPS_FILE
        self.chats = chats
        self.hub_url = hub_url
        self._now = now
        self._lock = threading.RLock()
        self._rows: list[Wakeup] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- records -------------------------------------------------------------

    def _load(self) -> list[Wakeup]:
        if self._rows is None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._rows = [Wakeup.model_validate(row) for row in raw.get("wakeups", [])]
            except FileNotFoundError:
                self._rows = []
            except (OSError, ValueError) as exc:
                raise HubFailure(503, "WAKEUPS_UNREADABLE", f"The saved wake-ups at {self.path} could not be read; "
                                 "fix or move the file, and nothing will overwrite it meanwhile.") from exc
        return self._rows

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(json.dumps({"wakeups": [row.model_dump() for row in self._rows or []]},
                                        ensure_ascii=False, indent=1) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def list(self, chat_id: str | None = None, *, active_only: bool = True) -> list[Wakeup]:
        with self._lock:
            return [row.model_copy() for row in self._load()
                    if (chat_id is None or row.chatId == chat_id) and (not active_only or row.status in {"active", "paused"})]

    def add(self, chat_id: str, request: WakeupRequest, *, created_by: Literal["agent", "person"]) -> Wakeup:
        self.chats.get(chat_id)  # a conversation that does not exist is refused as it is everywhere
        now = self._now()
        if request.inMinutes is not None:
            kind, next_at = "once", now + timedelta(minutes=request.inMinutes)
        elif request.at is not None:
            kind, next_at = "once", _parse_at(request.at)
            if next_at < now - timedelta(minutes=1):
                raise HubFailure(422, "WAKEUP_INVALID", "at is already past.")
        elif request.everyMinutes is not None:
            kind, next_at = "every", now + timedelta(minutes=request.everyMinutes)
        elif request.dailyAt is not None:
            kind, next_at = "daily", _next_daily(now, request.dailyAt, request.weekdays)
        else:
            kind, next_at = "job", now + timedelta(seconds=TICK_S)
        wakeup = Wakeup(id=str(uuid4()), chatId=chat_id, prompt=request.prompt.strip(), kind=kind,
                        everyMinutes=request.everyMinutes, dailyAt=request.dailyAt,
                        weekdays=sorted(set(request.weekdays)) if request.weekdays else None,
                        jobPath=request.afterJob.path if request.afterJob else None,
                        nextAt=next_at.isoformat(), createdAt=now.isoformat(), createdBy=created_by)
        wakeup.label = _label(wakeup)
        with self._lock:
            rows = self._load()
            if sum(row.chatId == chat_id and row.status == "active" for row in rows) >= ACTIVE_PER_CHAT:
                raise HubFailure(409, "WAKEUPS_FULL", f"This conversation already has {ACTIVE_PER_CHAT} wake-ups; cancel one first.")
            rows.append(wakeup)
            self._save()
        return wakeup

    def cancel(self, wakeup_id: str, chat_id: str | None = None) -> Wakeup:
        with self._lock:
            row = next((row for row in self._load() if row.id == wakeup_id or (len(wakeup_id) >= 8 and row.id.startswith(wakeup_id))), None)
            if row is None or (chat_id is not None and row.chatId != chat_id):
                raise HubFailure(404, "WAKEUP_NOT_FOUND", "This wake-up does not exist.")
            if row.status in {"active", "paused"}:
                row.status, row.nextAt = "cancelled", None
                self._save()
            return row.model_copy()

    # ---- running -------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="hub-wakeups", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(TICK_S):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - one bad tick never stops the next
                _log.exception("A wake-up tick failed")

    def tick(self) -> list[str]:
        """Run what is due now; returns the ids that woke their conversation."""

        now = self._now()
        with self._lock:
            paused = [row.model_copy() for row in self._load() if row.status == "paused"]
        for row in paused:
            # Restored, a conversation's wake-ups resume; one due meanwhile runs now, once.
            try:
                chat = self.chats.get(row.chatId)
            except HubFailure:
                continue
            if not chat.archived:
                self._update(row.id, status="active", lastError=None, nextAt=row.nextAt or now.isoformat())
        with self._lock:
            due = [row.model_copy() for row in self._load()
                   if row.status == "active" and row.nextAt and datetime.fromisoformat(row.nextAt) <= now]
        woke = []
        for row in due:
            if self._stop.is_set():
                break
            if self._run(row, now):
                woke.append(row.id)
        return woke

    def _update(self, wakeup_id: str, **fields) -> None:
        with self._lock:
            row = next((item for item in self._load() if item.id == wakeup_id), None)
            if row is None or row.status == "cancelled":
                return
            for key, value in fields.items():
                setattr(row, key, value)
            self._save()

    def _run(self, row: Wakeup, now: datetime) -> bool:
        try:
            chat = self.chats.get(row.chatId)
        except HubFailure:
            self._update(row.id, status="paused", nextAt=None, lastError="The conversation no longer exists.")
            return False
        if chat.archived or chat.sourceSessionId:
            self._update(row.id, status="paused", lastError="The conversation is archived; restore it to resume.")
            return False
        if chat.status == "running":
            return False  # woken once its turn ends: the next tick looks again
        prompt = row.prompt
        if row.kind == "job":
            ended = self._job_end(chat, row, now)
            if ended is None:
                return False
            prompt = f"{ended}\n\n{row.prompt}"
        origin = ChatMessageOrigin(kind="schedule", label=row.label, scheduleId=row.id)
        try:
            self.chats.post(row.chatId, ChatPostRequest(projectId=chat.projectId, content=prompt), origin=origin)
        except HubFailure as failure:
            attempts = row.attempts + 1
            if row.kind in {"once", "job"} and attempts >= RETRIES:
                self._update(row.id, status="failed", nextAt=None, attempts=attempts, lastError=failure.error.detail[:500])
            else:
                self._update(row.id, attempts=attempts, lastError=failure.error.detail[:500],
                             nextAt=(now + timedelta(minutes=RETRY_MINUTES)).isoformat())
            return False
        following = None
        if row.kind == "every":
            following = now + timedelta(minutes=row.everyMinutes or MIN_REPEAT_MINUTES)
        elif row.kind == "daily":
            following = _next_daily(now, row.dailyAt or "09:00", row.weekdays)
        self._update(row.id, status="active" if following else "done", nextAt=following.isoformat() if following else None,
                     lastRunAt=now.isoformat(), lastError=None, runs=row.runs + 1, attempts=0)
        return True

    def _job_end(self, chat, row: Wakeup, now: datetime) -> str | None:
        """The sentence a job's end is reported with, or None while it runs (and is still worth waiting for)."""

        if now - datetime.fromisoformat(row.createdAt) > timedelta(seconds=JOB_WAIT_LIMIT_S):
            return f"Stopped waiting for {row.jobPath}: it had not ended after {JOB_WAIT_LIMIT_S // 3600} hours."
        if self.hub_url is None:
            return None
        try:
            runtime = transport._request_json(self.hub_url, "/api/runtime", timeout=10)
            attached = next((item for item in runtime.get("projects", [])
                             if item.get("projectDir") == chat.projectDir and item.get("projectId") == chat.projectId), None)
            if attached is None:
                return None  # the project is not open: nothing can be read, and nothing is guessed
            job = transport._request_json(
                self.hub_url, f"/api/runtime/projects/{attached['runtimeId']}/studio{row.jobPath}", timeout=10)
        except HubFailure as failure:
            if failure.status == 404:
                return f"{row.jobPath} could not be found: {failure.error.detail[:300]}"
            return None
        except (OSError, ValueError, KeyError, TimeoutError):
            return None
        status = next((job.get(key) for key in ("status", "state") if isinstance(job.get(key), str)), None)
        if status is None or status.lower() in _UNFINISHED:
            return None
        return f"{row.jobPath} ended with status {status}."

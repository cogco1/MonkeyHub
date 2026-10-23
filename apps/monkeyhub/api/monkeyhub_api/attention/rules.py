"""Guesses from what was just done: rules that read the edit log and offer one question with a few answers.

A rule reads what the project's Runtime already keeps (the edit log,
``GET /api/edit-log``: each step of a line, nearest first, with the fields it
moved) and writes nothing but guess cards. The first rule,
``edit.same_direction``: within the line's last five steps one element field
or parameter was changed three times the same way and never the other way, so
the card asks whether to go on. Its answers send a sentence to the project's
conversation as the person (``chat.send_message``); the change itself is the
agent's to make there, under the usual checks. No answer is recommended: a
repeated direction is a reason to ask, not grounds to prefer one answer.

A person who sets a card aside quiets this rule for that field
(``AttentionStore.dismiss`` writes the suppression, seven days by default);
``guesses`` leaves quiet fields out, and ``refresh_guesses`` retires the cards
whose streak has gone.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from .models import AttentionDraft, AttentionOption, AttentionSource, CommandRef, EvidenceRow, Suppression

SAME_DIRECTION = "edit.same_direction"
#: How many of the latest steps the rule looks at, and how many same-way changes make a streak.
WINDOW = 5
REPEATS = 3
PREFIX = f"rule:{SAME_DIRECTION}:"


def guesses(steps: Sequence[Mapping], suppressions: Iterable[Suppression], now: datetime) -> list[AttentionDraft]:
    """The guess cards the latest steps call for, one per field on a streak, quiet fields left out.

    ``steps`` are the edit log's steps as the Runtime answers them, nearest
    first: ``{runId, edits: [{targetRef, componentId, field, before, after,
    direction}]}``.
    """

    quiet = {(row.ruleId, row.targetRef) for row in suppressions if datetime.fromisoformat(row.until) > now}
    changes: dict[str, list] = {}
    for step in steps[:WINDOW]:
        for edit in step.get("edits", ()):
            if edit.get("field") is None:
                continue
            target = _target(edit)
            changes.setdefault(target, []).append((step["runId"], edit))
    cards = []
    for target, rows in changes.items():
        directions = {edit.get("direction", 0) for _, edit in rows}
        moves = [(run_id, edit) for run_id, edit in rows if edit.get("direction")]
        if len(moves) < REPEATS or len(directions - {0}) != 1 or (SAME_DIRECTION, target) in quiet:
            continue
        cards.append(_card(target, moves))
    return sorted(cards, key=lambda card: card.dedupeKey)


def refresh_guesses(store, project_id: str, project_dir: str, steps: Sequence[Mapping],
                    now: datetime) -> list[str]:
    """Write this rule's cards for a project and retire those no streak asks for any more; the changed ids."""

    drafts = guesses(steps, store.suppressions(project_dir), now)
    return store.reconcile(project_id, project_dir, PREFIX, drafts)


def _target(edit: Mapping) -> str:
    """One field of one element, or a parameter's value: what a suppression names."""

    return f"{edit['targetRef']}#{edit['field']}"


def _number(value: float) -> str:
    return format(round(value, 6), ".6g")


def _card(target: str, moves: list) -> AttentionDraft:
    """The question for one streak: oldest to newest values, then go on by the same step or go back."""

    oldest_first = list(reversed(moves))
    first, last = oldest_first[0][1], oldest_first[-1][1]
    smaller = last["direction"] < 0
    ref, _, field = target.partition("#")
    name = ref.split(":", 1)[-1]
    component = last.get("componentId")
    subject = (f"{name}（{component}）" if component and component != name else name)[:60]
    word = field.rsplit(".", 1)[-1]
    step = sum(edit["after"] - edit["before"] for _, edit in oldest_first) / len(oldest_first)
    start, now_at, next_at = first["before"], last["after"], last["after"] + step
    trail = " → ".join(_number(value) for value in [start, *(edit["after"] for _, edit in oldest_first)])
    way = "调小" if smaller else "调大"
    again = f"把 {name} 的 {field} 从 {_number(now_at)} 再{way}到 {_number(next_at)}"
    back = f"把 {name} 的 {field} 改回 {_number(start)}"
    return AttentionDraft(
        channel="guess", kind="ask",
        title=f"连着 {len(moves)} 次把 {subject} 的 {word} {way}了，还要再{way}吗？",
        summary=f"最近 {WINDOW} 步里 {len(moves)} 次都在{way}：{trail}。",
        source=AttentionSource(runIds=[run_id for run_id, _ in oldest_first], ruleId=SAME_DIRECTION,
                               targetRef=target),
        dedupeKey=PREFIX + target,
        evidence=[EvidenceRow(key="change", label="改动", text=f"{field}：{trail}", status="ok",
                              refs=[ref])],
        options=[
            AttentionOption(id="again", label=f"再{way}一步", consequence=f"在对话里请 AI 改到 {_number(next_at)}",
                            action=CommandRef(commandId="chat.send_message", args={"text": again})),
            AttentionOption(id="back", label="改回最初", consequence=f"在对话里请 AI 改回 {_number(start)}",
                            action=CommandRef(commandId="chat.send_message", args={"text": back})),
        ],
    )


__all__ = ["PREFIX", "REPEATS", "SAME_DIRECTION", "WINDOW", "guesses", "refresh_guesses"]

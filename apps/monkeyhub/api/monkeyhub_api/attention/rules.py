"""Guesses from what was just done: rules that read the edit log and offer one question with a few answers.

A rule reads what the project's Runtime already keeps (the edit log,
``GET /api/edit-log``: each step of a line, nearest first, with the fields it
moved) and writes nothing but guess cards. The first rule,
``edit.same_direction``: within the line's last five steps one element field
or parameter was asked for three times the same way and never the other way,
so the card asks whether to go on. Only steps asked in words count: dragging a
face into place three times is modeling, not a pattern worth a question, and
point coordinates (a profile's vertices) are never a field it asks about. The
producer reads the log with ``limit=WINDOW``.

Its answers send a sentence to the project's conversation as the person
(``chat.send_message`` with its ``content``; the executor sends it to the
project's attention conversation); the change itself is the agent's to make
there, under the usual checks. No answer is recommended: a repeated direction
is a reason to ask, not grounds to prefer one answer.

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

#: What a field's last name is called on the card; any other is shown as written.
FIELD_WORDS = {
    "height": ("height", "高度"), "thickness": ("thickness", "厚度"), "width": ("width", "宽度"),
    "depth": ("depth", "深度"), "length": ("length", "长度"), "elevation": ("elevation", "标高"),
    "offset": ("offset", "偏移"), "radius": ("radius", "半径"), "distance": ("distance", "距离"),
}


def guesses(steps: Sequence[Mapping], suppressions: Iterable[Suppression], now: datetime, *,
            labels: Mapping[str, str] | None = None) -> list[AttentionDraft]:
    """The guess cards the latest steps call for, one per field on a streak, quiet fields left out.

    ``steps`` are the edit log's steps as the Runtime answers them, nearest
    first: ``{runId, request, edits: [{targetRef, componentId, field, before,
    after, direction}]}``. ``labels`` gives an element's or parameter's name as
    the catalogue shows it, by its id; an id without one is shown as it is.
    """

    quiet = {(row.ruleId, row.targetRef) for row in suppressions if datetime.fromisoformat(row.until) > now}
    changes: dict[str, list] = {}
    for step in steps[:WINDOW]:
        if not (isinstance(step.get("request"), str) and step["request"].strip()):
            continue
        for edit in step.get("edits", ()):
            field = edit.get("field")
            if field is None or "[" in field or edit.get("before") is None or edit.get("after") is None:
                continue
            changes.setdefault(_target(edit), []).append((step["runId"], edit))
    cards = []
    for target, rows in changes.items():
        directions = {edit.get("direction", 0) for _, edit in rows}
        moves = [(run_id, edit) for run_id, edit in rows if edit.get("direction")]
        if len(moves) < REPEATS or len(directions - {0}) != 1 or (SAME_DIRECTION, target) in quiet:
            continue
        cards.append(_card(target, moves, labels or {}))
    return sorted(cards, key=lambda card: card.dedupeKey)


def refresh_guesses(store, project_id: str, project_dir: str, steps: Sequence[Mapping], now: datetime, *,
                    labels: Mapping[str, str] | None = None) -> list[str]:
    """Write this rule's cards for a project and retire those no streak asks for any more; the changed ids."""

    drafts = guesses(steps, store.suppressions(project_dir), now, labels=labels)
    return store.reconcile(project_id, project_dir, PREFIX, drafts)


def _target(edit: Mapping) -> str:
    """One field of one element, or a parameter's value: what a suppression names."""

    return f"{edit['targetRef']}#{edit['field']}"


def _number(value: float) -> str:
    return format(round(value, 6), ".6g")


def _card(target: str, moves: list, labels: Mapping[str, str]) -> AttentionDraft:
    """The question for one streak: oldest to newest values, then go on by the same step or go back.

    Going on is offered only while it stays a value the field could hold: a
    value that has always been positive is not offered zero or less.
    """

    oldest_first = list(reversed(moves))
    first, last = oldest_first[0][1], oldest_first[-1][1]
    smaller = last["direction"] < 0
    way, way_zh = ("smaller", "调小") if smaller else ("larger", "调大")
    ref, _, field = target.partition("#")
    kind, _, name = ref.partition(":")
    shown = labels.get(name, name)[:60]
    if kind == "parameter":
        word = word_zh = what = what_zh = shown
    else:
        word, word_zh = FIELD_WORDS.get(field.rsplit(".", 1)[-1], (field, field))
        what, what_zh = f"the {word} of {shown}", f"{shown} 的{word_zh}"
    values = [first["before"], *(edit["after"] for _, edit in oldest_first)]
    start, now_at = values[0], values[-1]
    next_at = now_at + (now_at - start) / len(oldest_first)
    trail = " → ".join(_number(value) for value in values)
    count = len(moves)
    options = []
    if not (all(value > 0 for value in values) and next_at <= 0):
        options.append(AttentionOption(
            id="again", label=f"Make it {way} again", labelZh=f"再{way_zh}一步",
            consequence=f"Ask the AI in the conversation for {_number(next_at)}",
            consequenceZh=f"在对话里请 AI 改到 {_number(next_at)}",
            action=CommandRef(commandId="chat.send_message", args={
                "content": f"Change {field} of {name} from {_number(now_at)} to {_number(next_at)}"})))
    options.append(AttentionOption(
        id="back", label="Go back to the start", labelZh="改回最初",
        consequence=f"Ask the AI in the conversation for {_number(start)} again",
        consequenceZh=f"在对话里请 AI 改回 {_number(start)}",
        action=CommandRef(commandId="chat.send_message", args={
            "content": f"Change {field} of {name} back to {_number(start)}"})))
    return AttentionDraft(
        channel="guess", kind="ask",
        title=f"You made {what} {way} {count} times running: {way} again?",
        titleZh=f"连着 {count} 次把 {what_zh} {way_zh}了，还要再{way_zh}吗？",
        summary=f"Asked {count} times in the last {WINDOW} steps, each time {way}: {trail}.",
        summaryZh=f"最近 {WINDOW} 步里 {count} 次都在{way_zh}：{trail}。",
        source=AttentionSource(runIds=[run_id for run_id, _ in oldest_first], ruleId=SAME_DIRECTION,
                               targetRef=target),
        dedupeKey=PREFIX + target,
        evidence=[EvidenceRow(key="change", label="Change", labelZh="改动", text=f"{word}: {trail}",
                              textZh=f"{word_zh}：{trail}", status="ok", refs=[ref])],
        options=options,
    )


__all__ = ["FIELD_WORDS", "PREFIX", "REPEATS", "SAME_DIRECTION", "WINDOW", "guesses", "refresh_guesses"]

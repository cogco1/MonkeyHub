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
(``chat.composer.send`` with its ``content``; the executor sends it to the
project's attention conversation); the change itself is the agent's to make
there, under the usual checks. No answer is recommended: a repeated direction
is a reason to ask, not grounds to prefer one answer.

The second rule, ``drawing.recipe``, reads the Runtime's recipe suggestions
(``GET /api/drawings/corrections``: a pen or the hatch spacing a person changed
the same way on two drawings or more, which no recipe yet decides) and asks
whether the project should keep that value. One card per pen and direction,
so a further drawing joining the evidence updates the card rather than adding
one; saving it is the person's recipe decision citing the page the suggestion
names (``attention.answer_guess.save``, the Drawings page's own
save-recipe answer).

A person who sets a card aside quiets its rule for that field, or that pen and
direction (``AttentionStore.dismiss`` writes the suppression, seven days by
default); the rules leave quiet targets out, and each refresh retires the
cards its facts no longer ask for.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from .models import AttentionDraft, AttentionOption, AttentionSource, CommandRef, EvidenceRow, Suppression

SAME_DIRECTION = "edit.same_direction"
DRAWING_RECIPE = "drawing.recipe"
RECIPE_PREFIX = f"rule:{DRAWING_RECIPE}:"
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


#: A recipe pen's name, and how its smaller and larger values read, in English and Chinese.
RECIPE_WORDS = {
    "cutLineMm": ("cut line", "剖切线", ("thinner", "调细"), ("heavier", "调粗")),
    "visibleLineMm": ("visible line", "看线", ("thinner", "调细"), ("heavier", "调粗")),
    "hatchSpacingMm": ("hatch spacing", "填充间距", ("denser", "调密"), ("sparser", "调疏")),
}


def recipe_guesses(suggestions: Sequence[Mapping], suppressions: Iterable[Suppression],
                   now: datetime) -> list[AttentionDraft]:
    """One card per recipe suggestion the Runtime offers, its pen and direction not quieted.

    ``suggestions`` are ``GET /api/drawings/corrections``'s, each ``{field,
    direction, value, drawingIds, page}``.
    """

    quiet = {(row.ruleId, row.targetRef) for row in suppressions if datetime.fromisoformat(row.until) > now}
    cards = []
    for offer in suggestions:
        if offer.get("field") not in RECIPE_WORDS or offer.get("direction") not in ("increase", "decrease"):
            continue
        target = f"{offer['field']}:{offer['direction']}"
        if (DRAWING_RECIPE, target) not in quiet:
            cards.append(_recipe_card(target, offer))
    return sorted(cards, key=lambda card: card.dedupeKey)


def refresh_recipe_guesses(store, project_id: str, project_dir: str, suggestions: Sequence[Mapping],
                           now: datetime) -> list[str]:
    """Write the recipe cards for a project and retire those no suggestion asks for any more; the changed ids."""

    drafts = recipe_guesses(suggestions, store.suppressions(project_dir), now)
    return store.reconcile(project_id, project_dir, RECIPE_PREFIX, drafts)


def _recipe_card(target: str, offer: Mapping) -> AttentionDraft:
    name, name_zh, smaller, larger = RECIPE_WORDS[offer["field"]]
    way, way_zh = smaller if offer["direction"] == "decrease" else larger
    drawings = list(offer.get("drawingIds") or ())
    value, page = float(offer["value"]), offer.get("page") or {}
    shown = f"{_number(value)} mm"
    return AttentionDraft(
        channel="guess", kind="ask",
        title=f"You made the {name} {way} on {len(drawings)} drawings: keep {shown} for the project?",
        titleZh=f"{len(drawings)} 张图都把{name_zh}{way_zh}了，要把 {shown} 定成项目的出图规则吗？",
        summary=f"The {name} was made {way} the same way on {', '.join(drawings)}; no recipe decides it yet.",
        summaryZh=f"{'、'.join(drawings)} 上的{name_zh}都这样{way_zh}了，项目的出图规则还没定这一项。",
        source=AttentionSource(ruleId=DRAWING_RECIPE, targetRef=target, runIds=[page["runId"]] if page.get("runId") else []),
        dedupeKey=RECIPE_PREFIX + target,
        evidence=[EvidenceRow(key="change", label="Drawings", labelZh="图纸", text=", ".join(drawings) or "—",
                              textZh="、".join(drawings) or "—", status="ok", refs=drawings)],
        options=[AttentionOption(
            id="save", label=f"Keep {shown}", labelZh=f"定成 {shown}",
            consequence=f"New drawings of this project draw the {name} at {shown}",
            consequenceZh=f"这个项目以后出的图，{name_zh}都用 {shown}",
            action=CommandRef(commandId="attention.answer_guess.save", args={
                "field": offer["field"], "value": value, "runId": page.get("runId"),
                "assetSha256": page.get("assetSha256"), "revisionRef": page.get("revisionRef"),
                "pageIndex": page.get("pageIndex", 0)}))],
    )


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
            action=CommandRef(commandId="chat.composer.send", args={
                "content": f"Change {field} of {name} from {_number(now_at)} to {_number(next_at)}"})))
    options.append(AttentionOption(
        id="back", label="Go back to the start", labelZh="改回最初",
        consequence=f"Ask the AI in the conversation for {_number(start)} again",
        consequenceZh=f"在对话里请 AI 改回 {_number(start)}",
        action=CommandRef(commandId="chat.composer.send", args={
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


__all__ = [
    "DRAWING_RECIPE",
    "FIELD_WORDS",
    "PREFIX",
    "RECIPE_PREFIX",
    "RECIPE_WORDS",
    "REPEATS",
    "SAME_DIRECTION",
    "WINDOW",
    "guesses",
    "recipe_guesses",
    "refresh_guesses",
    "refresh_recipe_guesses",
]

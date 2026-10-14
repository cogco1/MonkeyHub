"""X10: a case study on one real design project, judged in one call from four levels of project material.

The project is a full-scale wall section designed by one person in the prototype. Each trial is one single-turn
``claude -p`` call with x2's fairness controls (``x2.command``, ``prompts/system_common.md``, the template
``prompts/request.md``); the request is the task's ``utterance``, the intro is the material's line in
``prompts/x10_materials.json``, and the materials are one of four levels built from the same project data:

| material | contents |
|---|---|
| L1 | descriptive: the project's review page data (``DATA`` read out of its single-file HTML, binaries stripped) |
| L2 | L1, then the relations (what supports what, which purchase quantities follow from which dimensions) and the decisions |
| L3 | L2, then the obligations with their checks and the evidence states |
| H  | the dated design events |

Every level is written by x2's generic renderer (``x2.render``). Items appear without their provenance (``source``,
``date``), except history, whose dates are its content; evidence keeps its ``as_of``. L1 keeps the page as built,
its notes included (the extract may classify them, for the report only). The extract's key legend, when it has one,
goes into every prompt right after the intro, the same for all four materials. 8 tasks x 4 materials x 3
repeats = 96 calls, in x2's Latin-square order. The answer is x2's JSON. Nothing is executed and no oracle runs: the
project's geometry comes from its own build scripts. Scores per trial: the verdict, strict (``x1._ok``) and in x2's
four categories (held | blocked = violated or refuse | unknown | flag_rejected); a false accept (``held`` where the gold
does not hold); affected precision and recall by component name (case-insensitive, otherwise exact); recheck recall
against the gold's obligation ids; for gold that carries ``values``, whether ``changes`` gives each within its tolerance.

Inputs are passed by path and nothing project-specific is committed: the extract (``X10Extract@1``), the tasks
(``X10Tasks@1``) and a local list of names to hide (``--deny-words``). Before any call the manifest records the checks,
all of which must pass: nesting (L1 in L2 in L3, item by item and as text), leakage (every dated item on or before
``--cutoff``), anonymity (no deny word in any material, prompt, or output file name or content; a word is named by its
line only; a ``=word`` line matches whole words only) and gold ids (every recheck id is an obligation id); it also
reports each material's size, and gold strings found verbatim in a material. ``--dry-run`` writes the materials,
prompts and manifest and scores two canned answers per trial (the gold, and one wrong on every score) with no call.
A real run needs the part ``x10`` in ``x2.CAPS``, which is a separate change.
"""

from __future__ import annotations

from labs.revision_tasks import _bootstrap  # noqa: F401  (source roots first)

import argparse
import base64
import csv
import hashlib
import json
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from archflow.contracts.canonical import canonical_json

from labs.revision_tasks import taskspec, x1, x2
from labs.revision_tasks import run as runner

PART = "x10"
MATERIALS = ("L1", "L2", "L3", "H")
CUTOFF = "2026-10-14"
INTROS = x2.PROMPTS / "x10_materials.json"
EXTRACT_SCHEMA, TASKS_SCHEMA = "X10Extract@1", "X10Tasks@1"
CATEGORY = {"held": "held", "violated": "blocked", "refuse": "blocked", "unknown": "unknown", "flag_rejected": "flag_rejected"}
# Required fields of each extract section besides ``source`` (a missing ``date`` fails the leakage check instead).
SECTIONS = {"relations": ("id", "kind", "a", "b", "statement"), "decisions": ("id", "chose", "rejected", "reason"),
            "obligations": ("id", "statement", "check", "applies_to"), "evidence": ("id", "item", "status", "as_of"),
            "history": ("event",)}
EVIDENCE_STATUS = ("verified", "unknown", "stale")
NOTE_CLASSES = ("descriptive", "relational", "decision", "obligation", "evidence", "mixed")
SOURCES = ("canned-gold", "canned-wrong")


# ---------------------------------------------------------------- the page

_CONST = re.compile(r"\b(?:const|let|var)\s+DATA\s*=\s*")
_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.S | re.I)
_JSON_TYPE = re.compile(r"""\btype\s*=\s*["']application/json["']""", re.I)
_DATA_URI = re.compile(r"data:([\w.+-]+/[\w.+-]+);base64,")
_GLB_B64 = re.compile(r"(?:data:[\w.+-]+/[\w.+-]+;base64,)?Z2xURg[A-Za-z0-9+/=]{64,}")   # "glTF" in base64


def page_data(html: str, script_id: str | None = None) -> tuple[Any, tuple[int, int]]:
    """The page's ``DATA`` and its span in the page: ``const DATA = {...};`` in a script, or a
    ``<script type="application/json">`` (the one whose id is ``script_id``, else the only one)."""

    found = None if script_id else _CONST.search(html)
    if found:
        try:
            value, end = json.JSONDecoder().raw_decode(html, found.end())
        except ValueError as exc:
            raise ValueError(f"PAGE_DATA_NOT_JSON: {exc}") from None
        return value, (found.end(), end)
    scripts = [m for m in _SCRIPT.finditer(html) if _JSON_TYPE.search(m.group(1))]
    if script_id:
        scripts = [m for m in scripts if re.search(rf"""\bid\s*=\s*["']{re.escape(script_id)}["']""", m.group(1))]
    if len(scripts) != 1:
        raise ValueError(f"PAGE_DATA_NOT_FOUND: no const DATA and {len(scripts)} matching JSON scripts")
    try:
        return json.loads(scripts[0].group(2)), scripts[0].span(2)
    except ValueError as exc:
        raise ValueError(f"PAGE_DATA_NOT_JSON: {exc}") from None


def _blob(value: Any) -> str | None:
    """The media type of a string that holds a binary: a base64 data URI, or bare base64 glTF."""

    if not isinstance(value, str):
        return None
    uri = _DATA_URI.match(value)
    if uri:
        return uri.group(1)
    if len(value) >= 64 and value.startswith("Z2xURg") and re.fullmatch(r"[A-Za-z0-9+/=\s]+", value):
        return "model/gltf-binary"
    return None


def strip_binaries(value: Any, where: str = "DATA") -> tuple[Any, list[dict[str, Any]]]:
    """The value without the strings that hold binaries (the base64 GLB, any data URI), and each one's place, type and size."""

    stripped: list[dict[str, Any]] = []

    def walk(node: Any, at: str) -> Any:
        if isinstance(node, dict):
            out = {}
            for key, item in node.items():
                if _blob(item):
                    stripped.append({"at": f"{at}.{key}", "type": _blob(item), "chars": len(item)})
                else:
                    out[key] = walk(item, f"{at}.{key}")
            return out
        if isinstance(node, list):
            kept = []
            for index, item in enumerate(node):
                if _blob(item):
                    stripped.append({"at": f"{at}[{index}]", "type": _blob(item), "chars": len(item)})
                else:
                    kept.append(walk(item, f"{at}[{index}]"))
            return kept
        return node

    return walk(value, where), stripped


def read_l1(spec: dict[str, Any], base: Path) -> tuple[Any, dict[str, Any]]:
    """L1's value and what reading it found. ``spec`` is ``{"html": <path>, "id"?: <script id>}`` (a path relative to
    the extract's folder) or ``{"data": <inline DATA>}``. The page is only read; binaries are stripped and reported by size."""

    if "html" in spec:
        raw = (base / spec["html"]).read_bytes()
        html = raw.decode("utf-8-sig")
        data, (start, end) = page_data(html, spec.get("id"))
        report = {"from": "html", "page_sha256": hashlib.sha256(raw).hexdigest(), "page_bytes": len(raw), "data_chars": end - start}
        outside = [m for m in _GLB_B64.finditer(html) if m.end() <= start or m.start() >= end]
    elif "data" in spec:
        data, outside = spec["data"], []
        report = {"from": "inline", "data_chars": len(canonical_json(data, ascii=False))}
    else:
        raise ValueError("EXTRACT_INVALID: l1 needs html or data")
    data, stripped = strip_binaries(data)
    report["stripped"] = stripped + [{"at": "page", "type": "model/gltf-binary", "chars": m.end() - m.start()} for m in outside]
    report["kept_chars"] = len(canonical_json(data, ascii=False))
    return data, report


# ---------------------------------------------------------------- inputs

def load_extract(path: Path) -> dict[str, Any]:
    extract = json.loads(path.read_text(encoding="utf-8"))
    if extract.get("schema") != EXTRACT_SCHEMA:
        raise ValueError(f"EXTRACT_INVALID: schema is not {EXTRACT_SCHEMA}")
    if not isinstance(extract.get("l1"), dict) or not ({"html", "data"} & set(extract["l1"])) or "source" not in extract["l1"]:
        raise ValueError("EXTRACT_INVALID: l1 must be {html: <path>, source, date} or {data: <object>, source, date}")
    ids: set[str] = set()
    for section, fields in SECTIONS.items():
        items = extract.get(section)
        if not isinstance(items, list):
            raise ValueError(f"EXTRACT_INVALID: {section} must be a list")
        for index, item in enumerate(items):
            missing = [f for f in (*fields, "source") if not isinstance(item, dict) or f not in item]
            if missing:
                raise ValueError(f"EXTRACT_INVALID: {section}[{index}] lacks {', '.join(missing)}")
            if "id" in fields:
                if item["id"] in ids:
                    raise ValueError(f"EXTRACT_INVALID: {section}[{index}] repeats id {item['id']}")
                ids.add(item["id"])
    for index, item in enumerate(extract["evidence"]):
        if item["status"] not in EVIDENCE_STATUS:
            raise ValueError(f"EXTRACT_INVALID: evidence[{index}] status is not one of {', '.join(EVIDENCE_STATUS)}")
    for section, field in (("decisions", "rejected"), ("obligations", "applies_to")):
        if any(not isinstance(item[field], list) for item in extract[section]):
            raise ValueError(f"EXTRACT_INVALID: every {section} {field} must be a list")
    legend = extract["l1"].get("legend")
    if legend is not None and (not isinstance(legend, dict) or "source" not in legend or not isinstance(legend.get("entries"), dict)
                               or not legend["entries"] or not all(isinstance(k, str) and isinstance(v, str) for k, v in legend["entries"].items())):
        raise ValueError("EXTRACT_INVALID: l1.legend must be {entries: {<key>: <label>}, source, date}")
    seen: set[int] = set()
    for index, entry in enumerate(extract["l1"].get("notes_classification") or []):
        if not isinstance(entry, dict) or not {"note", "class", "source", "date"} <= set(entry):
            raise ValueError(f"EXTRACT_INVALID: l1.notes_classification[{index}] must be {{note, class, source, date}}")
        if not isinstance(entry["note"], int) or isinstance(entry["note"], bool) or entry["note"] in seen:
            raise ValueError(f"EXTRACT_INVALID: l1.notes_classification[{index}] note must be a new integer index")
        if entry["class"] not in NOTE_CLASSES:
            raise ValueError(f"EXTRACT_INVALID: l1.notes_classification[{index}] class is not one of {', '.join(NOTE_CLASSES)}")
        seen.add(entry["note"])
    return extract


def notes_report(spec: dict[str, Any], l1: Any) -> dict[str, Any]:
    """How the extract classifies the page's ``DATA.notes``, for the report only: L1 keeps every note as built. Counts
    per class and the indices no entry classifies; an index outside the notes is refused."""

    entries = spec.get("notes_classification") or []
    notes = l1.get("notes") if isinstance(l1, dict) else None
    if not isinstance(notes, list):
        if entries:
            raise ValueError("EXTRACT_INVALID: l1.notes_classification is given but DATA has no notes list")
        return {"notes": None}
    outside = sorted(e["note"] for e in entries if not 0 <= e["note"] < len(notes))
    if outside:
        raise ValueError(f"EXTRACT_INVALID: l1.notes_classification notes {outside} are outside DATA.notes (0..{len(notes) - 1})")
    by_class = {c: sum(e["class"] == c for e in entries) for c in NOTE_CLASSES}
    classified = {e["note"] for e in entries}
    return {"notes": len(notes), "classified": len(classified), "by_class": by_class,
            "unclassified": [i for i in range(len(notes)) if i not in classified]}


def legend_block(spec: dict[str, Any]) -> str:
    """The key legend as the prompt carries it after the intro, the same for every material; empty without one."""

    legend = spec.get("legend")
    return x2.render({"Key legend": legend["entries"]}) if legend else ""


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def load_tasks(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != TASKS_SCHEMA or not isinstance(doc.get("tasks"), list):
        raise ValueError(f"TASKS_INVALID: schema is not {TASKS_SCHEMA} or tasks is not a list")
    seen: set[str] = set()
    for task in doc["tasks"]:
        tid, gold = task.get("task_id"), task.get("gold") or {}
        if not isinstance(tid, str) or tid in seen or not isinstance(task.get("utterance"), str):
            raise ValueError(f"TASKS_INVALID: task {tid!r} needs a unique task_id and an utterance")
        seen.add(tid)
        verdicts = gold.get("verdicts")
        if not isinstance(verdicts, list) or not verdicts or any(v not in x2.VERDICTS for v in verdicts):
            raise ValueError(f"TASKS_INVALID: {tid} gold verdicts must be a non-empty list of {', '.join(x2.VERDICTS)}")
        for field in ("affected", "recheck"):
            if not isinstance(gold.get(field), list) or not all(isinstance(v, str) for v in gold[field]):
                raise ValueError(f"TASKS_INVALID: {tid} gold {field} must be a list of strings")
        for name, want in (gold.get("values") or {}).items():
            if not isinstance(want, dict) or not _number(want.get("value")) or not _number(want.get("tol")) or want["tol"] < 0:
                raise ValueError(f"TASKS_INVALID: {tid} gold value {name} must be {{value: <number>, tol: <number >= 0>}}")
    return doc["tasks"]


def deny_words(path: Path) -> list[tuple[int, str, bool]]:
    """The words to hide: line number, word, whole words only. One per line, matched case-insensitively as a substring;
    a line ``=word`` matches only the whole word. Blank lines and lines starting with ``#`` are skipped."""

    words = []
    for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if line and not line.startswith("#"):
            whole = line.startswith("=")
            word = line[1:].strip() if whole else line
            if word:
                words.append((n, word, whole))
    return words


# ---------------------------------------------------------------- materials

def _content(item: dict[str, Any], keep_date: bool = False) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k != "source" and (keep_date or k != "date")}


def levels(extract: dict[str, Any], l1: Any) -> dict[str, dict[str, Any]]:
    """The four materials as data. L2 extends L1 and L3 extends L2 by construction."""

    def section(name: str) -> list[dict[str, Any]]:
        return [_content(item) for item in extract[name]]

    first = {"page": l1}
    second = {**first, "relations": section("relations"), "decisions": section("decisions")}
    third = {**second, "obligations": section("obligations"), "evidence": section("evidence")}
    history = sorted(extract["history"], key=lambda e: str(e.get("date") or ""))
    return {"L1": first, "L2": second, "L3": third, "H": {"history": [_content(e, keep_date=True) for e in history]}}


def prompt_for(material: str, utterance: str, text: str, legend: str = "") -> str:
    intros = json.loads(INTROS.read_text(encoding="utf-8"))
    intro = intros[material] + ("\n\n" + legend if legend else "")
    template = (x2.PROMPTS / "request.md").read_text(encoding="utf-8")
    return template.replace("{utterance}", utterance).replace("{intro}", intro).replace("{materials}", text)


# ---------------------------------------------------------------- checks

def _items(value: Any, path: tuple[Any, ...] = ()):
    """Each list element and each scalar outside a list, by its path."""

    if isinstance(value, dict):
        for key, item in value.items():
            yield from _items(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield path + (index,), canonical_json(item, ascii=False)
    else:
        yield path, canonical_json(value, ascii=False)


def _path(path: tuple[Any, ...]) -> str:
    return "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path).lstrip(".")


def nesting(data: dict[str, Any], texts: dict[str, str]) -> dict[str, Any]:
    """L1 in L2 in L3: every item of the inner level unchanged at the same place in the outer, the inner text a prefix
    of the outer; and what each level adds, counted by section."""

    out: dict[str, Any] = {"ok": True}
    for inner, outer in (("L1", "L2"), ("L2", "L3")):
        a, b = dict(_items(data[inner])), dict(_items(data[outer]))
        changed = [_path(p) for p, v in a.items() if b.get(p) != v]
        adds: dict[str, int] = {}
        for p in b:
            if p not in a:
                adds[str(p[0])] = adds.get(str(p[0]), 0) + 1
        prefix = texts[outer] == texts[inner] or texts[outer].startswith(texts[inner] + "\n")
        ok = not changed and prefix
        out[f"{inner}<{outer}"] = {"ok": ok, "changed_or_missing": len(changed), "first_changed": changed[:10], "text_prefix": prefix, "adds": adds}
        out["ok"] = out["ok"] and ok
    return out


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def leakage(extract: dict[str, Any], cutoff: str, texts: dict[str, str], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Every dated item on or before the cutoff (a later or missing ``date``, or a later ``as_of``, fails). Reported
    only: later ISO dates written inside a material, and gold strings (affected names, values) found verbatim in one."""

    limit = date.fromisoformat(cutoff)
    legend = [("l1.legend", extract["l1"]["legend"])] if extract["l1"].get("legend") else []
    entries = [("l1", extract["l1"])] + legend + [(f"{s}:{item['id']}" if "id" in item else f"{s}[{i}]", item)
                                         for s in SECTIONS for i, item in enumerate(extract[s])]
    late, undated = [], []
    for where, item in entries:
        day = _day(item.get("date")) if item.get("date") else None
        if day is None:
            undated.append(where)
        elif day > limit:
            late.append({"item": where, "date": item["date"]})
        if item.get("as_of") is not None and (_day(item["as_of"]) is None or _day(item["as_of"]) > limit):
            late.append({"item": where, "as_of": item["as_of"]})
    in_text = {m: sorted({d for d in re.findall(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", t) if _day(d) and _day(d) > limit})
               for m, t in texts.items()}
    gold: dict[str, dict[str, list[str]]] = {}
    for task in tasks:
        probes = [(n, re.compile(rf"(?<![\w-]){re.escape(n)}(?![\w-])", re.I)) for n in task["gold"]["affected"]]
        probes += [(f"{name}={_num(v['value'])}", re.compile(rf"(?<![\w.]){re.escape(_num(v['value']))}(?!\w|\.\d)"))
                   for name, v in (task["gold"].get("values") or {}).items()]
        for material, text in texts.items():
            hits = [label for label, pattern in probes if pattern.search(text)]
            if hits:
                gold.setdefault(task["task_id"], {})[material] = hits
    return {"ok": not late and not undated, "cutoff": cutoff, "dated_items": len(entries), "late": late, "undated": undated,
            "later_dates_in_text": {m: d for m, d in in_text.items() if d}, "gold_in_materials": gold}


def gold_ids(extract: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    known = {o["id"] for o in extract["obligations"]}
    unknown = {t["task_id"]: [r for r in t["gold"]["recheck"] if r not in known] for t in tasks}
    unknown = {k: v for k, v in unknown.items() if v}
    return {"ok": not unknown, "recheck_not_obligations": unknown}


def _pattern(word: str, whole: bool) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(word)}(?!\w)" if whole else re.escape(word), re.I)


def _redact(text: str, words: list[tuple[int, str, bool]]) -> str:
    for line, word, whole in words:
        text = _pattern(word, whole).sub(f"<deny:{line}>", text)
    return text


def scan(texts: dict[str, str], words: list[tuple[int, str, bool]]) -> list[dict[str, Any]]:
    """Every deny word in each named text, case-insensitive, counted as whole words and as substrings; a ``=word`` line
    is a hit only as a whole word. A word is named by its line in the list and a label by its redacted form, so the
    report cannot carry the word."""

    hits = []
    for label, text in texts.items():
        folded = text.casefold()
        for line, word, whole_only in words:
            substring = folded.count(word.casefold())
            whole = len(_pattern(word, True).findall(text)) if substring else 0
            if whole if whole_only else substring:
                hits.append({"in": _redact(label, words), "word_line": line, "whole_only": whole_only, "whole_word": whole,
                             "substring": substring})
    return hits


def scan_files(root: Path, words: list[tuple[int, str, bool]]) -> list[dict[str, Any]]:
    """The deny-word scan over every file and folder name under ``root`` and every file's content."""

    texts: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        texts[f"name {rel}"] = rel
        if path.is_file():
            texts[f"file {rel}"] = path.read_text(encoding="utf-8", errors="replace")
    return scan(texts, words)


def _tokens(chars: int) -> int:
    return -(-chars // 4)


def sizes(texts: dict[str, str], prompts: dict[tuple[str, str], str], repeats: int, legend: str = "") -> dict[str, Any]:
    """Each material's characters and estimated tokens (chars / 4), its largest prompt, the key legend every prompt
    carries, and the prompts' input cost at the manifest rate (Claude Code's own system prompt and the output not counted)."""

    out: dict[str, Any] = {m: {"chars": len(t), "est_tokens": _tokens(len(t))} for m, t in texts.items()}
    for (_, material), prompt in prompts.items():
        out[material]["max_prompt_chars"] = max(out[material].get("max_prompt_chars", 0), len(prompt))
    total = sum(_tokens(len(p)) for p in prompts.values()) * repeats
    return {"ok": True, "by_material": out, "legend": {"chars": len(legend), "est_tokens": _tokens(len(legend))} if legend else None,
            "est_prompt_input_tokens": total,
            "est_prompt_input_usd": round(total * float(x2.RATES["input"]) / 1e6, 4)}


# ---------------------------------------------------------------- score

def category(verdict: Any) -> str | None:
    return CATEGORY.get(verdict) if isinstance(verdict, str) else None


def _names(value: Any) -> list[str]:
    return sorted({str(v).casefold() for v in value}) if isinstance(value, list) else []


def value_hits(changes: Any, values: dict[str, Any] | None) -> dict[str, bool] | None:
    """For each gold value, whether ``changes`` gives it (name case-insensitive) within its tolerance."""

    if not values:
        return None
    given = {str(k).casefold(): v for k, v in changes.items()} if isinstance(changes, dict) else {}
    return {name: _number(given.get(name.casefold())) and abs(float(given[name.casefold()]) - want["value"]) <= want["tol"] + 1e-9
            for name, want in values.items()}


def score(answer: dict[str, Any] | None, task: dict[str, Any]) -> dict[str, Any]:
    verdict = (answer or {}).get("verdict")
    gold = task["gold"]
    affected = _names((answer or {}).get("affected"))
    precision, recall = x1._set_scores(affected, _names(gold["affected"]))
    _, recheck = x1._set_scores(_names((answer or {}).get("conditions")), _names(gold["recheck"]))
    hits = value_hits((answer or {}).get("changes"), gold.get("values"))
    return {"parsed": answer is not None, "verdict": verdict, "verdict_ok": verdict in x2.VERDICTS and x1._ok(verdict, task),
            "category": category(verdict), "category_ok": category(verdict) is not None and category(verdict) in {category(v) for v in gold["verdicts"]},
            "false_accept": verdict == "held" and "held" not in gold["verdicts"], "affected": affected,
            "affected_precision": precision, "affected_recall": recall, "recheck_recall": recheck,
            "value_hits": hits, "values_ok": None if hits is None else all(hits.values())}


def canned(task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The dry run's two answers: the gold itself, and one wrong on every score it can be."""

    gold = task["gold"]
    values = gold.get("values") or {}
    right = {"verdict": gold["verdicts"][0], "changes": {k: v["value"] for k, v in values.items()} or None,
             "affected": list(gold["affected"]), "conditions": list(gold["recheck"]), "reason": "canned: the gold"}
    taken = {category(v) for v in gold["verdicts"]}
    wrong = {"verdict": next((v for v in ("held", "refuse", "unknown", "flag_rejected") if category(v) not in taken), "ask"),
             "changes": {k: v["value"] + 2 * v["tol"] + 1 for k, v in values.items()} or None,
             "affected": list(gold["affected"][1:]) + ["no-such-component"], "conditions": list(gold["recheck"][:-1]),
             "reason": "canned: wrong on every score"}
    return {"canned-gold": right, "canned-wrong": wrong}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per material: verdict and category accuracy, false accepts, mean affected precision and recall and recheck recall
    (over the trials where each is defined), value hits, cost and the median input tokens."""

    cells: dict[str, dict[str, Any]] = {}
    for row in rows:
        cell = cells.setdefault(row["material"], {"trials": 0, "verdict_ok": 0, "category_ok": 0, "false_accept": 0, "unparsed": 0,
                                                  "values_trials": 0, "values_ok": 0, "cost_usd": 0.0, "_tokens": [],
                                                  "_affected_precision": [], "_affected_recall": [], "_recheck_recall": []})
        cell["trials"] += 1
        for key in ("verdict_ok", "category_ok", "false_accept"):
            cell[key] += int(bool(row[key]))
        cell["unparsed"] += int(not row["parsed"])
        for key in ("affected_precision", "affected_recall", "recheck_recall"):
            if row[key] is not None:
                cell[f"_{key}"].append(row[key])
        if row["values_ok"] is not None:
            cell["values_trials"] += 1
            cell["values_ok"] += int(row["values_ok"])
        cell["cost_usd"] = round(cell["cost_usd"] + (row.get("cost_usd") or 0.0), 4)
        if row.get("input_tokens") is not None:
            cell["_tokens"].append(row["input_tokens"])
    for cell in cells.values():
        for key in ("affected_precision", "affected_recall", "recheck_recall"):
            found = cell.pop(f"_{key}")
            cell[key] = {"mean": round(sum(found) / len(found), 3) if found else None, "n": len(found)}
        tokens = sorted(cell.pop("_tokens"))
        cell["median_input_tokens"] = tokens[len(tokens) // 2] if tokens else None
    return cells


# ---------------------------------------------------------------- the run

def refuse_real_run(cli: str | None, ledger: x2.Ledger | None) -> None:
    if PART not in x2.CAPS:
        raise x2.CapReached(f"part {PART} has no cap in x2.CAPS: a real run waits for that change")
    if not cli or ledger is None:
        raise ValueError("a real run needs --cli and --ledger")


def _file(path: Path) -> dict[str, Any]:
    return {"sha256": taskspec.file_sha256(path), "bytes": path.stat().st_size}


def prepare(extract_path: Path, tasks_path: Path, deny_path: Path, cutoff: str, materials: tuple[str, ...],
            task_ids: tuple[str, ...] | None) -> dict[str, Any]:
    """Inputs read and checked in memory, nothing written: the four materials, the prompts and the manifest."""

    extract, tasks, words = load_extract(extract_path), load_tasks(tasks_path), deny_words(deny_path)
    if task_ids:
        missing = sorted(set(task_ids) - {t["task_id"] for t in tasks})
        if missing:
            raise ValueError(f"TASKS_INVALID: no task {', '.join(missing)}")
        tasks = [t for t in tasks if t["task_id"] in task_ids]
    l1, page = read_l1(extract["l1"], extract_path.parent)
    page["notes"] = notes_report(extract["l1"], l1)
    legend = legend_block(extract["l1"])
    page["legend_entries"] = len(extract["l1"]["legend"]["entries"]) if legend else 0
    data = levels(extract, l1)
    texts = {m: x2.render(data[m]) for m in MATERIALS}
    prompts = {(t["task_id"], m): prompt_for(m, t["utterance"], texts[m], legend) for t in tasks for m in materials}
    hits = scan({**{f"material {m}": t for m, t in texts.items()}, "legend": legend,
                 **{f"prompt {t} {m}": p for (t, m), p in prompts.items()}}, words)
    checks = {"nesting": nesting(data, texts), "leakage": leakage(extract, cutoff, texts, tasks), "gold_ids": gold_ids(extract, tasks),
              "anonymity": {"ok": not hits, "words": len(words), "hits": hits}}
    manifest = {"part": PART, "cutoff": cutoff, "materials": list(materials), "tasks": [t["task_id"] for t in tasks],
                "inputs": {"extract": _file(extract_path), "tasks": _file(tasks_path), "deny_words": {**_file(deny_path), "words": len(words)},
                           "l1": page},
                "counts": {s: len(extract[s]) for s in SECTIONS}, "checks": checks}
    return {"extract": extract, "tasks": tasks, "words": words, "data": data, "texts": texts, "legend": legend, "prompts": prompts,
            "manifest": manifest}


def _write_json(path: Path, value: Any, words: list[tuple[int, str, bool]]) -> int:
    """Write JSON with every deny word redacted; the number of redactions."""

    text = json.dumps(value, indent=1, sort_keys=True, default=str) + "\n"
    clean = _redact(text, words)
    path.write_text(clean, encoding="utf-8", newline="\n")
    return sum(len(_pattern(word, whole).findall(text)) for _, word, whole in words)


def _trials(tasks: list[dict[str, Any]], materials: tuple[str, ...], repeats: int):
    """x2's Latin-square order: per task and repeat, the materials rotated by one."""

    for index, task in enumerate(tasks):
        for repeat in range(repeats):
            shift = (index + repeat) % len(materials)
            for slot, material in enumerate(materials[shift:] + materials[:shift]):
                yield task, repeat, slot, material, f"X10-{material}-{task['task_id']}-r{repeat}"


def run(out: Path, *, extract: Path, tasks: Path, deny: Path, cutoff: str = CUTOFF, materials: tuple[str, ...] = MATERIALS,
        repeats: int = 3, task_ids: tuple[str, ...] | None = None, dry_run: bool = True, ledger: x2.Ledger | None = None,
        cli: str | None = None) -> dict[str, Any]:
    if not dry_run:
        refuse_real_run(cli, ledger)
    prepared = prepare(extract, tasks, deny, cutoff, materials, task_ids)
    out.mkdir(parents=True, exist_ok=True)
    words, manifest, checks = prepared["words"], prepared["manifest"], prepared["manifest"]["checks"]
    manifest.update({"dry_run": dry_run, "repeats": repeats, "trials_planned": len(prepared["tasks"]) * len(materials) * repeats})
    checks["sizes"] = sizes(prepared["texts"], prepared["prompts"], repeats, prepared["legend"])
    summary: dict[str, Any] = {"dry_run": dry_run, "stopped": None, "checks_ok": False, "by_source": {}}
    if not checks["anonymity"]["ok"]:
        # Nothing that holds a deny word is written: no material, no prompt.
        manifest["ok"] = False
        _write_json(out / "x10_manifest.json", manifest, words)
        summary["stopped"] = "ANONYMITY: a deny word is in a material or prompt; only the manifest was written"
        _write_json(out / "x10_summary.json", summary, words)
        return summary
    config = out / "config"
    x2.write_config(config)
    identity = {"harness": runner.harness_identity(), "prompt_files_sha256": x2.prompt_files_sha256(), "model": x2.MODEL,
                "command": x2.command("claude", config)[1:], "rates": x2.RATES, "per_call_usd": x2.PER_CALL_USD, "part": PART}
    if not dry_run:
        identity["cli_version"] = subprocess.run([cli, "--version"], capture_output=True, text=True).stdout.strip()
    _write_json(out / "identity.json", identity, words)
    (out / "materials").mkdir()
    for material, text in prepared["texts"].items():
        (out / "materials" / f"{material}.txt").write_text(text, encoding="utf-8", newline="\n")
    (out / "trials").mkdir()
    for task, _, _, material, trial_id in _trials(prepared["tasks"], materials, repeats):
        (out / "trials" / f"{trial_id}.prompt.md").write_text(prepared["prompts"][(task["task_id"], material)], encoding="utf-8", newline="\n")
    files = scan_files(out, words)
    checks["anonymity"].update({"ok": not files, "file_hits": files})
    manifest["ok"] = all(checks[k]["ok"] for k in ("nesting", "leakage", "anonymity", "gold_ids"))
    if _write_json(out / "x10_manifest.json", manifest, words):
        manifest["ok"] = checks["anonymity"]["ok"] = False
        checks["anonymity"]["manifest_redacted"] = True
        _write_json(out / "x10_manifest.json", manifest, words)
    summary["checks_ok"] = manifest["ok"]
    if not manifest["ok"]:
        summary["stopped"] = "CHECKS_FAILED: " + ", ".join(k for k in ("nesting", "leakage", "anonymity", "gold_ids") if not checks[k]["ok"])
        _write_json(out / "x10_summary.json", summary, words)
        return summary
    rows: list[dict[str, Any]] = []
    for task, repeat, slot, material, trial_id in _trials(prepared["tasks"], materials, repeats):
        prompt = prepared["prompts"][(task["task_id"], material)]
        trial = out / "trials" / trial_id
        base = {"trial_id": trial_id, "task_id": task["task_id"], "material": material, "repeat_idx": repeat, "order_slot": slot,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "prompt_chars": len(prompt),
                "gold_verdicts": "|".join(task["gold"]["verdicts"])}
        if dry_run:
            answers = {}
            for source, canned_answer in canned(task).items():
                text = "```json\n" + json.dumps(canned_answer) + "\n```"
                answer = x2.parse_answer(text)
                scored = score(answer, task)
                answers[source] = {"text": text, "answer": answer, "score": scored}
                rows.append({**base, "answer_source": source, **_row(scored)})
            _write_json(trial.parent / f"{trial_id}.bundle.json", {"trial": base, "task": task, "answers": answers}, words)
            continue
        try:
            ledger.check(PART)
        except x2.CapReached as exc:
            summary["stopped"] = str(exc)
            break
        call = x2.call_model(prompt, trial, cli=cli, config=config)
        cost, priced = x2.price(call["result"])
        ledger.add({"at": datetime.now(timezone.utc).isoformat(), "experiment": "X10", "part": PART, "trial_id": trial_id,
                    "out": str(out), "cost_usd": cost, **priced})
        answer = x2.parse_answer(call["text"])
        scored = score(answer, task)
        _write_json(trial.parent / f"{trial_id}.bundle.json", {"trial": base, "task": task, "usage": priced, "cost_usd": cost,
                    "call": {k: call[k] for k in ("exit_code", "seconds", "init", "text", "stderr")}, "answer": answer, "score": scored}, words)
        rows.append({**base, "answer_source": "model", **_row(scored), "cost_usd": round(cost, 4),
                     "input_tokens": priced["tokens"]["input_tokens"], "output_tokens": priced["tokens"]["output_tokens"],
                     "seconds": round(call["seconds"], 1)})
        print(f"{trial_id:24} {str(scored['verdict']):13} ok={scored['category_ok']!s:5} fa={scored['false_accept']!s:5} "
              f"rec={scored['affected_recall']} recheck={scored['recheck_recall']} ${cost:.3f}", flush=True)
    if rows:
        fields = list(dict.fromkeys(k for row in rows for k in row))
        with (out / "x10.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, restval="", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    for source in dict.fromkeys(r["answer_source"] for r in rows):
        summary["by_source"][source] = summarize([r for r in rows if r["answer_source"] == source])
    summary["cost_usd"] = round(sum(r.get("cost_usd") or 0.0 for r in rows), 4)
    summary["spent_part_usd"] = ledger.spent(PART) if ledger else 0.0
    summary["spent_total_usd"] = ledger.spent() if ledger else 0.0
    after = scan_files(out, words)
    summary["anonymity_after"] = {"ok": not after, "hits": after}
    _write_json(out / "x10_summary.json", summary, words)
    return summary


def _row(scored: dict[str, Any]) -> dict[str, Any]:
    hits = scored["value_hits"]
    return {**{k: scored[k] for k in ("parsed", "verdict", "verdict_ok", "category", "category_ok", "false_accept", "affected_precision",
                                       "affected_recall", "recheck_recall", "values_ok")},
            "value_hits": "" if hits is None else "|".join(f"{k}={int(v)}" for k, v in hits.items())}


# ---------------------------------------------------------------- a synthetic project

def write_synthetic(root: Path) -> dict[str, Path]:
    """A small generic project for the tests and the dry run: a page with ``const DATA`` (short component keys, notes,
    and a stand-in GLB inside and outside it), an extract with a key legend and a partial notes classification, eight
    tasks and a deny-word list. Every name is generic."""

    root.mkdir(parents=True, exist_ok=True)
    glb = base64.b64encode(b"glTF" + bytes(range(2, 120))).decode("ascii")

    def part(name, material, purpose, size, version):
        return {"n": name, "m": material, "p": purpose, "s": size, "v": version}

    data = {"project": {"title": "Sample wall section", "units": "mm"},
            "components": [part("footing-1", "mat-block", "base under the posts", [900, 300, 150], "unchanged"),
                           part("post-1", "mat-timber", "frame, left", [90, 90, 1200], "unchanged"),
                           part("post-2", "mat-timber", "frame, right", [90, 90, 1200], "unchanged"),
                           part("panel-1", "mat-board", "infill, left bay", [360, 100, 1200], "changed"),
                           part("panel-2", "mat-board", "infill, right bay", [360, 100, 1200], "changed"),
                           part("cap-1", "mat-timber", "top plate", [900, 140, 40], "new")],
            "materials": [{"name": "mat-block", "note": "concrete block"}, {"name": "mat-timber", "note": "sawn softwood"},
                          {"name": "mat-board", "note": "fibre board"}],
            "procurement": [{"item": "block-unit", "qty": 6, "unit_price": 4.5}, {"item": "timber-stud", "qty": 4, "unit_price": 9.0},
                            {"item": "board-sheet", "qty": 2, "unit_price": 22.0}],
            "notes": ["The panels are fibre board between timber posts.", "cap-1 rests on both posts.",
                      "Block infill was dropped as too heavy.", "The sample must fit within 900 x 900 mm in plan.",
                      "Board price checked on 2026-10-02.", "Studs are bought with the board."],
            "model": {"glb": "data:model/gltf-binary;base64," + glb}}
    page = ("<!doctype html>\n<html><head><title>Sample</title></head><body>\n<div id=\"viewer\"></div>\n"
            f"<script>\nconst MODEL_B64 = \"{glb}\";\nconst DATA = {json.dumps(data, indent=1)};\nrender(DATA);\n</script>\n</body></html>\n")
    (root / "page.html").write_text(page, encoding="utf-8", newline="\n")

    def src(section):
        return f"notes.md#{section}"

    extract = {
        "schema": EXTRACT_SCHEMA,
        "l1": {"html": "page.html", "source": "page.html#DATA", "date": "2026-10-08",
               "legend": {"entries": {"n": "name", "m": "material", "p": "purpose", "s": "bounding size in mm (x, y, z)",
                                      "v": "version flag: new, changed or unchanged"},
                          "source": "page.html#template", "date": "2026-10-08"},
               "notes_classification": [{"note": i, "class": c, "source": "page.html#notes", "date": "2026-10-08"} for i, c in
                                        enumerate(("descriptive", "relational", "decision", "obligation", "evidence"))]},
        "relations": [
            {"id": "r-footing-post-1", "kind": "supports", "a": "footing-1", "b": "post-1", "statement": "footing-1 carries post-1"},
            {"id": "r-footing-post-2", "kind": "supports", "a": "footing-1", "b": "post-2", "statement": "footing-1 carries post-2"},
            {"id": "r-posts-panel-1", "kind": "supports", "a": "post-1", "b": "panel-1", "statement": "panel-1 spans from post-1 to the bay centre"},
            {"id": "r-posts-cap-1", "kind": "supports", "a": "post-2", "b": "cap-1", "statement": "cap-1 rests on both posts"},
            {"id": "q-board-panels", "kind": "quantity_from", "a": "board-sheet", "b": "panel-1",
             "statement": "board-sheet qty = ceil(total panel area / sheet area 1.2 m2)"},
            {"id": "q-block-footing", "kind": "quantity_from", "a": "block-unit", "b": "footing-1", "statement": "block-unit qty = footing-1 length / 150"},
            {"id": "d-cap-footing", "kind": "depends_on", "a": "cap-1", "b": "footing-1", "statement": "cap-1 length equals footing-1 length"}],
        "decisions": [
            {"id": "dec-infill", "chose": "mat-board infill", "rejected": ["mat-block infill"], "reason": "block infill is too heavy for the posts"},
            {"id": "dec-cap", "chose": "timber cap", "rejected": ["no cap"], "reason": "the panels need a top restraint"}],
        "obligations": [
            {"id": "sample-size", "statement": "the sample's plan is at most 900 x 900 mm", "check": "footing-1 length <= 900 and depth <= 900",
             "applies_to": ["footing-1", "cap-1"]},
            {"id": "local-supply", "statement": "every material is purchasable locally", "check": "each procurement item has a local supplier",
             "applies_to": ["block-unit", "timber-stud", "board-sheet"]},
            {"id": "budget-total", "statement": "the procurement total is at most 120", "check": "sum(qty * unit_price) <= 120",
             "applies_to": ["block-unit", "timber-stud", "board-sheet"]},
            {"id": "panel-bearing", "statement": "each panel bears on a post at one end", "check": "panel end within 10 mm of a post face",
             "applies_to": ["panel-1", "panel-2"]}],
        "evidence": [
            {"id": "ev-board-price", "item": "board-sheet unit_price", "status": "verified", "as_of": "2026-10-02"},
            {"id": "ev-stud-price", "item": "timber-stud unit_price", "status": "stale", "as_of": "2026-09-15"},
            {"id": "ev-block-local", "item": "block-unit local supplier", "status": "unknown", "as_of": None}],
        "history": [
            {"date": "2026-09-10", "event": "brief: a 900 mm long wall section sample on a block footing"},
            {"date": "2026-09-18", "event": "block infill proposed; rejected as too heavy for the posts"},
            {"date": "2026-09-25", "event": "board infill chosen; two panels of 360 x 1200 mm"},
            {"date": "2026-10-02", "event": "board-sheet priced at 22"},
            {"date": "2026-10-08", "event": "cap-1 added as a top restraint"}]}
    dates = {"relations": "2026-09-25", "decisions": "2026-09-25", "obligations": "2026-09-12", "evidence": "2026-10-02"}
    for section, day in dates.items():
        for item in extract[section]:
            item.update({"source": src(section), "date": day})
    for item in extract["history"]:
        item["source"] = src("history")
    (root / "extract.json").write_text(json.dumps(extract, indent=1) + "\n", encoding="utf-8", newline="\n")

    def task(tid, utterance, verdicts, affected, recheck, values=None):
        gold = {"verdicts": verdicts, "affected": affected, "recheck": recheck}
        if values:
            gold["values"] = {k: {"value": v, "tol": 0.5} for k, v in values.items()}
        return {"task_id": tid, "utterance": utterance, "gold": gold}

    tasks = {"schema": TASKS_SCHEMA, "tasks": [
        task("T1", "Make cap-1 20 mm thicker.", ["held"], ["cap-1"], ["budget-total"], {"cap-1.height": 60}),
        task("T2", "Lengthen the sample to 1000 mm.", ["violated"], ["footing-1", "cap-1", "panel-1", "panel-2"],
             ["sample-size", "budget-total"], {"footing-1.length": 1000}),
        task("T3", "Make panel-1 both 100 mm and 140 mm thick.", ["refuse"], [], []),
        task("T4", "Buy the blocks from a local supplier at today's price.", ["unknown"], [], ["local-supply"]),
        task("T5", "Use block infill for both panels.", ["flag_rejected"], ["panel-1", "panel-2"], ["budget-total"]),
        task("T6", "Make both panels 100 mm shorter.", ["held"], ["panel-1", "panel-2"], ["panel-bearing", "budget-total"],
             {"panel-1.height": 1100, "panel-2.height": 1100}),
        task("T7", "Add a third board panel and a third post.", ["violated", "refuse"], ["footing-1", "cap-1"], ["sample-size", "budget-total"]),
        task("T8", "Move post-2 50 mm toward post-1.", ["held"], ["post-2", "panel-2"], ["panel-bearing"], {"post-2.offset": -50})]}
    (root / "tasks.json").write_text(json.dumps(tasks, indent=1) + "\n", encoding="utf-8", newline="\n")
    (root / "deny.txt").write_text("# names to hide (one per line; =word for whole words only)\nzorblax\nquuxwell studio\n=vexmoor\n", encoding="utf-8", newline="\n")
    return {"extract": root / "extract.json", "tasks": root / "tasks.json", "deny": root / "deny.txt", "page": root / "page.html"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run X10 (four levels of one project's material, single calls) into an empty directory.")
    parser.add_argument("--out", type=Path, help="the run directory, new or empty")
    parser.add_argument("--extract", type=Path, help=f"the {EXTRACT_SCHEMA} file")
    parser.add_argument("--tasks", type=Path, help=f"the {TASKS_SCHEMA} file")
    parser.add_argument("--deny-words", type=Path, help="a local word list of names to hide (never committed)")
    parser.add_argument("--cutoff", default=CUTOFF, help="the last date an item may carry (ISO)")
    parser.add_argument("--materials", default=",".join(MATERIALS))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--task-ids", help="comma-separated task ids (default: every task)")
    parser.add_argument("--dry-run", action="store_true", help="write materials, prompts and manifest, score canned answers, call nothing")
    parser.add_argument("--ledger", type=Path, help="the shared cost ledger (a real run)")
    parser.add_argument("--cli", help="the claude executable (a real run)")
    parser.add_argument("--write-synthetic", type=Path, metavar="DIR", help="write the synthetic project's inputs into DIR and stop")
    args = parser.parse_args(argv)
    if args.write_synthetic:
        print(json.dumps({k: str(v) for k, v in write_synthetic(args.write_synthetic.resolve()).items()}, indent=1))
        return 0
    for name in ("out", "extract", "tasks", "deny_words"):
        if getattr(args, name) is None:
            parser.error(f"--{name.replace('_', '-')} is required")
    materials = tuple(args.materials.split(","))
    if not materials or any(m not in MATERIALS for m in materials):
        parser.error(f"--materials must name some of {','.join(MATERIALS)}")
    if args.dry_run == bool(args.cli):
        parser.error("give either --dry-run or --cli (with --ledger)")
    if not args.dry_run:
        try:
            refuse_real_run(args.cli, args.ledger)
        except (x2.CapReached, ValueError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    try:
        summary = run(out, extract=args.extract.resolve(), tasks=args.tasks.resolve(), deny=args.deny_words.resolve(), cutoff=args.cutoff,
                      materials=materials, repeats=args.repeats, task_ids=tuple(args.task_ids.split(",")) if args.task_ids else None,
                      dry_run=args.dry_run, ledger=x2.Ledger(args.ledger.resolve()) if args.ledger else None, cli=args.cli)
    except (ValueError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    words = deny_words(args.deny_words.resolve())
    manifest = json.loads((out / "x10_manifest.json").read_text(encoding="utf-8"))
    print(_redact(json.dumps({"checks": {k: v.get("ok") for k, v in manifest["checks"].items()},
                              "sizes": manifest["checks"]["sizes"]["by_material"] if "sizes" in manifest["checks"] else None,
                              "stripped": manifest["inputs"]["l1"]["stripped"]}, indent=1), words))
    print(_redact(json.dumps(summary, indent=1, sort_keys=True), words))
    ok = summary["checks_ok"] and not summary["stopped"] and summary.get("anonymity_after", {}).get("ok", False)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

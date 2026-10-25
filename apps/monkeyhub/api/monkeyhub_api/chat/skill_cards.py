"""What a chat shows of a skill or script an Agent ran, and keeping a chat's script as a skill (S23).

An Agent that finds no Hub action for what the user asks writes a small Python
script and runs it through the Hub (``POST /api/skills/scratch/run``). The
conversation shows one card per run, a skill's or a script's: what ran, its
code, what let it run (nothing needed asking, an earlier answer, or this run's
card), what it came to, and one sentence when it was stopped. A script that
helped is kept with one click: the Hub writes it as a package in the person's
own skill folder (``skill_library.write_package``), where every project finds it
and every chat lists it. A script nobody keeps stays only in its run's record
in the project and on this card.

The words a skill's question ends with once answered are here too, so the card
says in place what was allowed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from project_runtime.application.skill_library import write_package
from project_runtime.errors import StudioError

from ..models import ChatSkillRun, HubFailure

# What an answered skill question says after its title.
OUTCOME_WORDS = {
    "allowed_once": "已允许这一次 / Allowed this time",
    "allowed_always": "已允许，以后这类不再问 / Allowed from now on",
    "refused": "没有允许 / Not allowed",
    "cancelled": "没有回答 / Not answered",
}
# What let a run go ahead, in the words of its card.
ASKED_WORDS = {
    "no": "没问你：只读项目文件、只写自己的输出、不联网、不起别的程序",
    "earlier": "按你之前的允许",
    "once": "你允许了这一次",
    "always": "你允许了，以后这类不再问",
}
STATUS_WORDS = {"queued": "排队中", "running": "在跑", "succeeded": "跑完了", "failed": "没跑成"}


def card_text(run: ChatSkillRun) -> str:
    """The card in one text, for a reader without the card (an older Hub, the transcript)."""

    what = (f"临时脚本「{run.description}」" if run.kind == "scratch"
            else f"skill「{run.name}」")
    parts = [f"{what}（代码 {run.code}）", ASKED_WORDS[run.asked], STATUS_WORDS[run.status]]
    if run.words:
        parts.append(run.words)
    if run.keptAs:
        parts.append(f"已留下为 skill「{run.keptAs}」")
    return " · ".join(parts)


def package(run: ChatSkillRun, name: str, *, project_id: str) -> dict[str, Any]:
    """The skill package a kept script becomes, in the form ``skill_library.write_package`` takes."""

    script = run.script or {}
    body = (f"{run.description}\n\n"
            f"Kept from a script an Agent wrote in a MonkeyHub chat (run {run.runId}, project {project_id}). "
            "It runs through the Hub like any skill: inputs under --inputs, parameters in --params, outputs under --outputs.\n")
    return {"name": name, "description": run.description, "body": body, "files": dict(script.get("files") or {}),
            "entry": script.get("entry"), "manifest": script.get("manifest"), "access": run.access}


def keep(run: ChatSkillRun, name: str, *, project_id: str, root: Path | None = None) -> Path:
    """Write a chat's script as the package ``name`` in the person's skill folder; the folder it is in.

    Only a script that ran to the end is kept, and a name the folder already holds is refused.
    """

    if run.kind != "scratch" or not run.script:
        raise HubFailure(409, "SKILL_KEEP_INVALID", "只有聊天里现写的脚本能留下成 skill。")
    if run.status != "succeeded":
        raise HubFailure(409, "SKILL_KEEP_NOT_SUCCEEDED", "这个脚本没跑成，先不留下。")
    try:
        return write_package(package(run, name, project_id=project_id), root=root)
    except StudioError as failure:
        if failure.code == "SKILL_EXISTS":
            raise HubFailure(409, "SKILL_KEEP_EXISTS", f"已经有一个叫「{name}」的 skill 了，换个名字再留下。") from failure
        raise HubFailure(422, "SKILL_KEEP_INVALID", f"这个脚本不能留下成 skill：{failure.detail}") from failure

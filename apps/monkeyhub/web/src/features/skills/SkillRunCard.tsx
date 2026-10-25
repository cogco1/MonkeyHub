/**
 * S23: one card per skill or script the AI ran in this conversation. It says
 * what ran and its code, what let it run (nothing needed asking, an earlier
 * answer, or this run's question), what it came to, and the one sentence the
 * runner gave when it stopped the script. A script that ran to the end can be
 * kept as a skill with one click, under the name the AI suggested or one the
 * person types; every project then has it. A script nobody keeps stays only in
 * its run's record.
 */

import { useState } from "react";

import type { ChatMessage, ChatSkillRun } from "../../api/generated";
import { ChatCard } from "../../ChatCard";

const SKILL_NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export const skillRunWords = {
  "zh-CN": {
    script: (description: string) => `临时脚本「${description}」`,
    skill: (name: string) => `skill「${name}」`,
    code: (code: string) => `代码 ${code}`,
    asked: {
      no: "没问你：只读项目文件、只写自己的输出、不联网、不起别的程序",
      earlier: "按你之前的允许运行",
      once: "你允许了这一次",
      always: "你允许了，以后这类不再问",
    },
    status: { queued: "排队中", running: "在跑", succeeded: "跑完了", failed: "没跑成" },
    outputs: (names: string) => `写出：${names}`,
    keepName: "skill 名字",
    keep: "留下成 skill",
    keeping: "正在留下…",
    keepHint: "留下后，所有项目都能用它；不留下，它只在这次运行的记录里。",
    kept: (name: string) => `已留下为 skill「${name}」，所有项目都能用`,
    badName: "名字用小写字母、数字和连字符，例如 pdf-colours",
  },
  en: {
    script: (description: string) => `Script: ${description}`,
    skill: (name: string) => `Skill ${name}`,
    code: (code: string) => `code ${code}`,
    asked: {
      no: "Not asked: it reads the project, writes only its outputs, no network, no other program",
      earlier: "Ran on what you allowed before",
      once: "You allowed this run",
      always: "You allowed it; this kind is not asked again",
    },
    status: { queued: "Queued", running: "Running", succeeded: "Done", failed: "Did not run" },
    outputs: (names: string) => `Wrote: ${names}`,
    keepName: "Skill name",
    keep: "Keep as a skill",
    keeping: "Keeping…",
    keepHint: "Kept, every project can use it; otherwise it stays only in this run's record.",
    kept: (name: string) => `Kept as the skill ${name}; every project can use it`,
    badName: "Use lowercase letters, digits and hyphens, e.g. pdf-colours",
  },
} as const;

export type SkillRunWords = (typeof skillRunWords)[keyof typeof skillRunWords];

/** The card of one run; ``onKeep`` keeps a finished script under a name and resolves once the Hub answered. */
export function SkillRunCard({ run, words, onKeep, disabled }: {
  run: ChatSkillRun; words: SkillRunWords; disabled?: boolean;
  onKeep?: (name: string) => Promise<void>;
}) {
  const [name, setName] = useState(run.name ?? "");
  const [keeping, setKeeping] = useState(false);
  const scratch = run.kind === "scratch";
  const title = scratch ? words.script(run.description) : words.skill(run.name ?? run.description);
  const keepable = scratch && run.status === "succeeded" && !run.keptAs && onKeep !== undefined;
  const valid = SKILL_NAME.test(name) && name.length <= 64;
  const keep = async () => {
    if (!onKeep || !valid || keeping) return;
    setKeeping(true);
    try { await onKeep(name); } finally { setKeeping(false); }
  };
  return <ChatCard className="chat-skill-run" data-kind={run.kind} data-status={run.status} data-asked={run.asked}
    title={title} status={<span className="chat-skill-run__status">{words.status[run.status ?? "queued"]}</span>}
    summary={<span className="chat-muted">{words.code(run.code)} · {words.asked[run.asked]}</span>}
    actions={keepable ? <form className="chat-skill-run__keep" onSubmit={(event) => { event.preventDefault(); void keep(); }}>
      <label><span>{words.keepName}</span>
        <input value={name} onChange={(event) => setName(event.target.value.trim())} spellCheck={false}
          aria-invalid={!valid} aria-describedby={`keep-hint-${run.runId}`} disabled={disabled || keeping} /></label>
      <button type="submit" data-command="chat.skill_run.keep" data-command-arg={run.runId} className="chat-activity__open"
        disabled={disabled || keeping || !valid}>{keeping ? words.keeping : words.keep}</button>
      <small id={`keep-hint-${run.runId}`} className="chat-muted">{valid ? words.keepHint : words.badName}</small>
    </form> : undefined}>
    {run.words && <p className="chat-skill-run__words" role="status">{run.words}</p>}
    {run.outputs && run.outputs.length > 0 && <p className="chat-muted">{words.outputs(run.outputs.join("、"))}</p>}
    {run.keptAs && <p className="chat-skill-run__kept">{words.kept(run.keptAs)}</p>}
  </ChatCard>;
}

/** A skill's question once answered: where it was asked, with what the answer allowed. */
export function AnsweredSkillQuestion({ message }: { message: ChatMessage }) {
  return <div className="chat-activity chat-activity--permission" data-outcome={message.permissionOutcome ?? undefined}>
    <div className="chat-permission chat-permission--answered" role="group">
      <p>{message.content}</p>
    </div>
  </div>;
}

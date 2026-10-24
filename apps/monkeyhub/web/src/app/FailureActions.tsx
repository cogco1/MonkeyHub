import { useState } from "react";

import { useOptionalCommands } from "../features/commands/CommandProvider";
import { usePreferences, type Language } from "../features/settings/preferences";
import { useT } from "../i18n/useT";

/** The command whose handler undoes the last edit of the model draft; the Stage registers it while a model is open. */
export const UNDO_ACTION = "draft.tools.undo";

/**
 * What the AI is asked when a person hands a failure over: the sentence they read, and, in developer mode, the
 * service's own words after it. The AI can repeat the step and see the raw refusal itself.
 */
export function askAiText(reason: string, language: Language, technical?: string): string {
  const ask = language === "zh-CN"
    ? `刚才这一步没有完成：${reason}请你查一下原因，能修好就修好，修不了就告诉我怎么办。`
    : `That step did not finish: ${reason} Please find out why and fix it if you can; if you cannot, tell me what to do.`;
  return technical ? `${ask}\n${technical}` : ask;
}

/**
 * The next steps of a failure that can be taken with one click: hand it to the AI in the open conversation, and
 * undo the last edit when the model on screen has one to undo. A step that cannot be taken now is not shown.
 */
export function FailureActions({ reason, ai = true, undo = false, technical }: {
  reason: string;
  ai?: boolean;
  undo?: boolean;
  technical?: string;
}) {
  const t = useT();
  const { language } = usePreferences();
  const commands = useOptionalCommands();
  const [handed, setHanded] = useState(false);
  if (!commands) return null;
  const undoCommand = undo ? commands.tree.commands.find((command) => command.id === UNDO_ACTION) : undefined;
  const canUndo = undoCommand !== undefined && commands.stateOf(undoCommand).kind === "ready";
  const canAsk = ai && commands.canAskAI();
  if (!canAsk && !canUndo) return null;
  return (
    <span className="failure-actions" key={reason}>
      {canAsk && <button type="button" className="btn btn--link" data-command="view.notice.ask_ai" data-command-arg="failure"
        disabled={handed} onClick={() => { if (commands.askAI(askAiText(reason, language, technical))) setHanded(true); }}>
        {handed ? t("failure.askAiDone") : t("failure.askAi")}
      </button>}
      {canUndo && <button type="button" className="btn btn--link" data-command="view.notice.undo" data-command-arg="failure"
        onClick={() => commands.run(UNDO_ACTION, "card")}>{t("failure.undo")}</button>}
    </span>
  );
}

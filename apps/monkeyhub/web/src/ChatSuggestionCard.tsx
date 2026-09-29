import { useId, useState } from "react";
import type { ChatDetail, ChatMessage, HubError } from "./api/generated";
import { ChatCard } from "./ChatCard";
import "./ChatSuggestionCard.css";

const words = {
  "zh-CN": {
    heading: "猜你想要", available: "已有工具可做", development: "需要评估新能力",
    time: "预计时间", cost: "预计费用", unknown: "待评估", estimate: "估算",
    details: "技术细节与依据", tools: "拟用工具", timeBasis: "时间依据", costBasis: "费用依据",
    request: "将发送的请求", accept: "先做一版", assess: "先评估方案", adjust: "调整想法", dismiss: "暂时跳过",
    sending: "正在发送…", selected: "已选择，进展见后续对话", skipped: "已跳过这项建议",
    stale: "对话已有更新，请让 Agent 重新确认这项建议。", external: "请回原对话继续这项建议。",
    archived: "恢复这段对话后可继续。", pending: "等待当前回复完成后可选择。",
    incomplete: "这项建议尚未完成，请在对话中继续确认。", busy: "正在处理，请稍候。",
    failed: "请求未能发送，请重试或在对话中继续。", errorDetails: "查看错误详情",
  },
  en: {
    heading: "You might want to", available: "Existing tools", development: "New capability to assess",
    time: "Estimated time", cost: "Estimated cost", unknown: "To be assessed", estimate: "Estimate",
    details: "Technical details and basis", tools: "Proposed tools", timeBasis: "Time basis", costBasis: "Cost basis",
    request: "Request to send", accept: "Make a first version", assess: "Assess this approach", adjust: "Adjust idea", dismiss: "Skip for now",
    sending: "Sending…", selected: "Selected — follow the conversation for progress", skipped: "Suggestion skipped",
    stale: "The conversation has changed. Ask the Agent to confirm this suggestion again.", external: "Continue this suggestion in the original conversation.",
    archived: "Restore this conversation to continue.", pending: "Choose after the current reply finishes.",
    incomplete: "This suggestion is unfinished. Continue the conversation to confirm it.", busy: "An action is in progress. Please wait.",
    failed: "The request could not be sent. Retry or continue in the conversation.", errorDetails: "View error details",
  },
} as const;

type Props = {
  message: ChatMessage;
  chat: ChatDetail;
  language: keyof typeof words;
  busy: boolean;
  sending: boolean;
  error: HubError | null;
  onAccept: () => void;
  onAdjust: () => void;
};

/** A proposal is retained with its message; only an explicit choice posts a new user turn. */
export function ChatSuggestionCard({ message, chat, language, busy, sending, error, onAccept, onAdjust }: Props) {
  const statusId = useId();
  const [dismissed, setDismissed] = useState(false);
  const card = message.suggestion;
  if (!card) return null;
  const t = words[language];
  const messages = chat.messages ?? [];
  const lastUser = messages.findLast((row) => row.role === "user");
  const latest = messages.findLast((row) => row.role === "assistant" && row.suggestion && row.sourceTurnId === message.sourceTurnId);
  const selected = messages.some((row) => row.suggestionSelection?.messageId === message.id);
  const reason = selected ? t.selected : chat.sourceSessionId ? t.external : chat.archived ? t.archived
    : dismissed ? t.skipped : !lastUser || message.sourceTurnId !== (lastUser.sourceTurnId ?? lastUser.id) || latest?.id !== message.id
      || message.presentationRevision == null ? t.stale
      : sending ? t.sending : chat.status === "running" ? t.pending : chat.status !== "idle" || message.status !== "complete" ? t.incomplete : busy ? t.busy : null;
  const estimates = [
    { label: t.time, value: card.timeEstimate?.value },
    { label: t.cost, value: card.costEstimate?.value },
  ];
  return <ChatCard className="chat-suggestion" aria-busy={sending} data-suggestion-id={message.id}
    title={card.title} status={`${t.heading} · ${card.capability === "available" ? t.available : t.development}`}
    summary={card.outcome}
    details={{ label: t.details, children: <>
      <p>{card.rationale}</p>
      {Boolean(card.tools?.length) && <p><strong>{t.tools}</strong><br />{card.tools!.join(" · ")}</p>}
      {estimates.filter(({ value }) => !value).map(({ label }) => <p key={label}><strong>{label}</strong> · {t.unknown}</p>)}
      {card.timeEstimate?.basis && <p><strong>{t.timeBasis}</strong><br />{card.timeEstimate.basis}</p>}
      {card.costEstimate?.basis && <p><strong>{t.costBasis}</strong><br />{card.costEstimate.basis}</p>}
      <p><strong>{t.request}</strong><br /><span className="chat-suggestion__request">{card.prompt}</span></p>
    </> }}
    actions={!dismissed && <>
      <button type="button" className="btn btn--primary" data-suggestion-action="accept" disabled={Boolean(reason)} aria-describedby={reason ? statusId : undefined} onClick={onAccept}>
        {sending ? t.sending : card.capability === "available" ? t.accept : t.assess}
      </button>
      <button type="button" className="btn" data-suggestion-action="adjust" disabled={Boolean(reason)} onClick={onAdjust}>{t.adjust}</button>
      <button type="button" className="chat-suggestion__skip" data-suggestion-action="dismiss" disabled={Boolean(reason)} onClick={() => setDismissed(true)}>{t.dismiss}</button>
    </>}
    footer={(reason || error) && <>
      {reason && <p className="chat-suggestion__status" id={statusId} role="status">{reason}</p>}
      {error && <div className="chat-suggestion__error" role="alert"><p>{t.failed}</p><details><summary>{t.errorDetails}</summary><p>{error.detail}</p></details></div>}
    </>}>
    {Boolean(card.deliverables?.length) && <ul className="chat-suggestion__deliverables">{card.deliverables!.map((item, index) => <li key={index}>{item}</li>)}</ul>}
    {estimates.some(({ value }) => value) && <dl className="chat-suggestion__estimates">{estimates.filter(({ value }) => value).map(({ label, value }) =>
      <div key={label}><dt>{label}</dt><dd>{value}<small>{t.estimate}</small></dd></div>)}</dl>}
  </ChatCard>;
}

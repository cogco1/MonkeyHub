/**
 * Above the composer: when this conversation will continue by itself, one line per wake-up, each with Cancel.
 * Nothing shows while it has none. The Agent sets them (chat_schedule) when the architect asks for a later or
 * repeated check, or to pick up after a long job.
 */

import { useCallback, useEffect, useState } from "react";

import { useT } from "../i18n/useT";
import "./hubNotes.css";

type Wakeup = {
  id: string; chatId: string; prompt: string; kind: "once" | "every" | "daily" | "job"; everyMinutes?: number | null;
  dailyAt?: string | null; weekdays?: number[] | null; jobPath?: string | null; nextAt?: string | null;
  status: "active" | "done" | "cancelled" | "paused" | "failed"; lastError?: string | null;
};

const clock = (iso: string) => {
  const at = new Date(iso);
  const today = new Date().toDateString() === at.toDateString();
  return today ? at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : at.toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
};

export function WakeupStrip({ chatId, status }: { chatId: string | null; status: string | null }) {
  const t = useT();
  const [rows, setRows] = useState<Wakeup[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const read = useCallback(async () => {
    if (!chatId) { setRows([]); return; }
    try {
      const response = await fetch(`/api/chat/sessions/${encodeURIComponent(chatId)}/wakeups`);
      if (response.ok) setRows(await response.json() as Wakeup[]);
    } catch { /* Unread, the strip keeps what it showed. */ }
  }, [chatId]);
  // Read again when the conversation changes or a turn ends (an Agent may just have set one), and once a minute.
  useEffect(() => {
    void read();
    const timer = window.setInterval(() => void read(), 60_000);
    return () => window.clearInterval(timer);
  }, [read, status]);

  if (!chatId || rows.length === 0) return null;
  const days = t("wakeup.days").split(",");
  const when = (row: Wakeup) => {
    if (row.kind === "every") {
      const minutes = row.everyMinutes ?? 0;
      return minutes % 60 === 0 ? t("wakeup.everyHours", { hours: minutes / 60 }) : t("wakeup.every", { minutes });
    }
    if (row.kind === "daily") return row.weekdays?.length && row.weekdays.length < 7
      ? t("wakeup.weekly", { days: row.weekdays.map((day) => days[day] ?? day).join(" "), time: row.dailyAt ?? "" })
      : t("wakeup.daily", { time: row.dailyAt ?? "" });
    if (row.kind === "job") return t("wakeup.job");
    return t("wakeup.once", { time: row.nextAt ? clock(row.nextAt) : "" });
  };
  const cancel = async (id: string) => {
    setBusy(id);
    try {
      await fetch(`/api/chat/sessions/${encodeURIComponent(chatId)}/wakeups/${encodeURIComponent(id)}/cancel`, { method: "POST" });
    } finally { setBusy(null); await read(); }
  };
  return <ul className="hub-wakeups" aria-label={t("wakeup.title")}>{rows.map((row) => <li key={row.id} data-status={row.status}>
    <span className="hub-wakeups__when">{when(row)}</span>
    <span className="hub-wakeups__prompt" title={row.prompt}>{row.prompt}</span>
    <small className="hub-wakeups__next">{row.status === "paused" ? t("wakeup.paused", { reason: row.lastError ?? "" })
      : row.kind !== "once" && row.kind !== "job" && row.nextAt ? t("wakeup.next", { time: clock(row.nextAt) }) : ""}</small>
    <button type="button" className="hub-notes__action" data-command="chat.wakeup.cancel" data-command-arg={row.id}
      disabled={busy !== null} onClick={() => void cancel(row.id)}>{t("wakeup.cancel")}</button>
  </li>)}</ul>;
}

/**
 * Find conversations (side panel): every conversation of this Hub, archived ones included, by its title or by what
 * was said in it. Each result names its project and shows where the words were said; choosing one opens it.
 */

import { useEffect, useRef, useState } from "react";

import { useT } from "../i18n/useT";
import "./hubNotes.css";

export type ChatSearchHit = {
  chatId: string; title: string; projectId: string; projectDir: string; status: string; archived: boolean; updatedAt: string;
  matches: { messageId: string; role: string; createdAt: string; snippet: string }[];
};

export interface ChatFinderProps {
  projectName(projectDir: string): string | null;
  onOpen(hit: ChatSearchHit): void;
}

export function ChatFinder({ projectName, onOpen }: ChatFinderProps) {
  const t = useT();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<ChatSearchHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const asked = useRef(0);

  useEffect(() => {
    const words = query.trim();
    if (!words) { setHits(null); setError(null); return; }
    const ticket = ++asked.current;
    const timer = window.setTimeout(() => {
      fetch(`/api/chat/search?${new URLSearchParams({ q: words, limit: "30" })}`)
        .then(async (response) => {
          const value = await response.json();
          if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : `HTTP ${response.status}`);
          if (ticket === asked.current) { setHits(value as ChatSearchHit[]); setError(null); }
        })
        .catch((cause: unknown) => { if (ticket === asked.current) setError(cause instanceof Error ? cause.message : String(cause)); });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  return <div className="hub-finder">
    <input type="search" className="hub-finder__input" value={query} placeholder={t("finder.placeholder")} aria-label={t("finder.placeholder")}
      autoFocus onChange={(event) => setQuery(event.target.value)} />
    {!query.trim() && <p className="hub-notes__empty">{t("shell.side.find.empty")}</p>}
    {hits && hits.length === 0 && <p className="hub-notes__empty">{t("finder.none")}</p>}
    {hits && hits.length > 0 && <ul className="hub-finder__results">{hits.map((hit) => <li key={hit.chatId}>
      <button type="button" className="hub-finder__hit" data-command="chat.open_conversation" data-command-arg={hit.chatId} onClick={() => onOpen(hit)}>
        <span className="hub-finder__title"><span className="chat-thread__dot" data-status={hit.status} />{hit.title}
          {hit.archived && <small className="hub-notes__scope">{t("finder.archived")}</small>}</span>
        <small className="hub-finder__project">{projectName(hit.projectDir) ?? hit.projectId} · {new Date(hit.updatedAt).toLocaleDateString()}</small>
        {hit.matches.slice(0, 2).map((match) => <span key={match.messageId} className="hub-finder__snippet">{match.snippet}</span>)}
      </button>
    </li>)}</ul>}
    {error && <p className="hub-notes__error" role="alert">{error}</p>}
  </div>;
}

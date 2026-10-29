/**
 * Settings › Conversations › Other tools (gap 3): the MCP servers a conversation may use beside MonkeyHub's own.
 * One switch takes the servers already set up in Claude Code; each listed server can then be turned off. Keys in a
 * server's environment or headers are never sent to this page, only their names. A change reaches the next turn.
 */

import { useEffect, useState } from "react";

import type { Language } from "../../../../../../packages/web-shared/src/appearance.js";

type Server = { name: string; source: "hub" | "claude"; transport: "stdio" | "http" | "sse"; target: string; envKeys: string[]; enabled: boolean };
type View = { fromClaude: boolean; servers: Server[]; claudeProblem?: string | null };

const WORDS = {
  "zh-CN": {
    title: "其他工具（MCP）",
    intro: "让对话用上你已经接好的工具，比如 Drive、Blender、Rhino、浏览器。改动从下一轮起生效；只读的对话不用这些工具。",
    fromClaude: "用 Claude Code 里配好的 MCP 服务器", fromClaudeHint: "读取 Claude Code 的用户配置（~/.claude.json），每轮重新读。",
    none: "还没有可用的服务器。", keys: (names: string) => `带密钥：${names}（不在这里显示）`, failed: "没能读取或保存：",
    source: { claude: "Claude Code", hub: "Hub" },
  },
  en: {
    title: "Other tools (MCP)",
    intro: "Let conversations use tools you already connected, such as Drive, Blender, Rhino or a browser. Changes apply from the next turn; a read-only conversation does not use them.",
    fromClaude: "Use the MCP servers set up in Claude Code", fromClaudeHint: "Reads Claude Code's user configuration (~/.claude.json) again at each turn.",
    none: "No server to use yet.", keys: (names: string) => `Keys: ${names} (never shown here)`, failed: "Could not read or save: ",
    source: { claude: "Claude Code", hub: "Hub" },
  },
} as const;

async function call(method: "GET" | "PUT", body?: unknown): Promise<View> {
  const response = await fetch("/api/chat/mcp-servers", {
    method, headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error((data && (data.detail || data.code)) || `HTTP ${response.status}`);
  return data as View;
}

export function McpServersSettings({ language }: { language: Language }) {
  const words = WORDS[language];
  const [view, setView] = useState<View | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => { call("GET").then(setView, (cause) => setProblem(String(cause?.message ?? cause))); }, []);
  const change = async (body: { fromClaude?: boolean; disabled?: string[] }) => {
    setBusy(true); setProblem(null);
    try { setView(await call("PUT", body)); } catch (cause) { setProblem(String((cause as Error)?.message ?? cause)); }
    finally { setBusy(false); }
  };
  const disabled = (view?.servers ?? []).filter((row) => !row.enabled && (row.source === "hub" || view?.fromClaude)).map((row) => row.name);
  return <>
    <h3 className="settings-subhead">{words.title}</h3>
    <p className="settings-intro">{words.intro}</p>
    <div className="settings-row"><div className="settings-row__text"><label htmlFor="mcp-from-claude">{words.fromClaude}</label>
      <p className="settings-row__hint">{view?.claudeProblem ?? words.fromClaudeHint}</p></div>
      <input data-command="preference.chat_mcp.from_claude" id="mcp-from-claude" type="checkbox" checked={Boolean(view?.fromClaude)}
        disabled={!view || busy} onChange={(event) => void change({ fromClaude: event.target.checked })} /></div>
    {view && !view.servers.length && <p className="settings-row__hint">{words.none}</p>}
    {view?.servers.map((row) => {
      const usable = row.source === "hub" || view.fromClaude;
      return <div className="settings-row" key={`${row.source}:${row.name}`}><div className="settings-row__text">
        <label htmlFor={`mcp-${row.source}-${row.name}`}>{row.name} <small>· {words.source[row.source]}</small></label>
        <p className="settings-row__hint">{row.target}{row.envKeys.length ? ` · ${words.keys(row.envKeys.join(", "))}` : ""}</p></div>
        <input data-command="preference.chat_mcp.server" data-command-arg={row.name} id={`mcp-${row.source}-${row.name}`} type="checkbox"
          checked={row.enabled} disabled={busy || !usable}
          onChange={(event) => void change({ disabled: event.target.checked ? disabled.filter((name) => name !== row.name) : [...disabled, row.name] })} /></div>;
    })}
    {problem && <p className="settings-row__hint" role="alert">{words.failed}{problem}</p>}
  </>;
}

/**
 * Settings › Conversations › Other tools (gap 3): the MCP servers a conversation may use beside MonkeyHub's own.
 * One switch takes the servers already set up in Claude Code (its own and each folder's); each listed server can be
 * turned off; a server can be added to the Hub's own list, or taken off it. Keys in a server's environment or
 * headers are never sent to this page, only their names. A change reaches the next turn.
 */

import { useEffect, useState, type FormEvent } from "react";

import type { Language } from "../../../../../../packages/web-shared/src/appearance.js";

type Server = { name: string; source: "hub" | "claude"; transport: "stdio" | "http" | "sse"; target: string; envKeys: string[]; enabled: boolean };
type View = { fromClaude: boolean; servers: Server[]; claudeProblem?: string | null };
type Change = { fromClaude?: boolean; disabled?: string[]; add?: Record<string, Record<string, unknown>>; remove?: string[] };

const WORDS = {
  "zh-CN": {
    title: "其他工具（MCP）",
    intro: "让对话用上你已经接好的工具，比如 Drive、Blender、Rhino、浏览器。改动从下一轮起生效；只读的对话不用这些工具。",
    fromClaude: "用 Claude Code 里配好的 MCP 服务器",
    fromClaudeHint: "读 Claude Code 的用户配置（~/.claude.json），也读每个文件夹自己的服务器；每轮重新读。",
    none: "还没有可用的服务器。", keys: (names: string) => `带密钥：${names}（不在这里显示）`, failed: "没能读取或保存：",
    source: { claude: "Claude Code", hub: "Hub" }, remove: "移除",
    add: "添加服务器", name: "名字", command: "命令或网址", args: "参数（空格分开）", env: "环境变量（每行一个 名字=值）", save: "添加",
    addHint: "命令在本机运行（stdio）；以 http(s):// 开头的按网址连接。密钥存在本机 Hub 配置里，保存后不再显示。",
  },
  en: {
    title: "Other tools (MCP)",
    intro: "Let conversations use tools you already connected, such as Drive, Blender, Rhino or a browser. Changes apply from the next turn; a read-only conversation does not use them.",
    fromClaude: "Use the MCP servers set up in Claude Code",
    fromClaudeHint: "Reads Claude Code's user configuration (~/.claude.json) and each folder's own servers, again at each turn.",
    none: "No server to use yet.", keys: (names: string) => `Keys: ${names} (never shown here)`, failed: "Could not read or save: ",
    source: { claude: "Claude Code", hub: "Hub" }, remove: "Remove",
    add: "Add a server", name: "Name", command: "Command or URL", args: "Arguments (space-separated)", env: "Environment (one NAME=value per line)", save: "Add",
    addHint: "A command runs on this computer (stdio); an http(s):// address is reached as a URL. Keys stay in this computer's Hub configuration and are not shown again.",
  },
} as const;

async function call(method: "GET" | "PUT", body?: Change): Promise<View> {
  const response = await fetch("/api/chat/mcp-servers", {
    method, headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error((data && (data.detail || data.code)) || `HTTP ${response.status}`);
  return data as View;
}

/** The new server as the Hub's list keeps it: a command with its arguments and environment, or a URL. */
function serverOf(target: string, args: string, env: string): Record<string, unknown> {
  const value = target.trim();
  if (/^https?:\/\//i.test(value)) return { type: "http", url: value };
  const pairs = env.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
    const at = line.indexOf("=");
    return at > 0 ? [line.slice(0, at).trim(), line.slice(at + 1).trim()] as const : null;
  }).filter((pair): pair is readonly [string, string] => pair !== null);
  return { type: "stdio", command: value, args: args.split(/\s+/).filter(Boolean), ...(pairs.length ? { env: Object.fromEntries(pairs) } : {}) };
}

export function McpServersSettings({ language }: { language: Language }) {
  const words = WORDS[language];
  const [view, setView] = useState<View | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [draft, setDraft] = useState({ name: "", target: "", args: "", env: "" });
  useEffect(() => { call("GET").then(setView, (cause) => setProblem(String(cause?.message ?? cause))); }, []);
  const change = async (body: Change) => {
    setBusy(true); setProblem(null);
    try { setView(await call("PUT", body)); return true; }
    catch (cause) { setProblem(String((cause as Error)?.message ?? cause)); return false; }
    finally { setBusy(false); }
  };
  const add = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft.name.trim() || !draft.target.trim()) return;
    if (await change({ add: { [draft.name.trim()]: serverOf(draft.target, draft.args, draft.env) } })) setDraft({ name: "", target: "", args: "", env: "" });
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
        {row.source === "hub" && <button data-command="preference.chat_mcp.remove" data-command-arg={row.name} type="button" className="btn"
          disabled={busy} onClick={() => void change({ remove: [row.name] })}>{words.remove}</button>}
        <input data-command="preference.chat_mcp.server" data-command-arg={row.name} id={`mcp-${row.source}-${row.name}`} type="checkbox"
          checked={row.enabled} disabled={busy || !usable}
          onChange={(event) => void change({ disabled: event.target.checked ? disabled.filter((name) => name !== row.name) : [...disabled, row.name] })} /></div>;
    })}
    <form className="settings-mcp-add" onSubmit={(event) => void add(event)}>
      <p className="settings-row__hint">{words.add} · {words.addHint}</p>
      <div className="settings-row"><label htmlFor="mcp-add-name">{words.name}</label>
        <input data-command="preference.chat_mcp.add" id="mcp-add-name" value={draft.name} pattern="[A-Za-z0-9_-]{1,64}" spellCheck={false}
          onChange={(event) => setDraft((value) => ({ ...value, name: event.target.value }))} /></div>
      <div className="settings-row"><label htmlFor="mcp-add-target">{words.command}</label>
        <input id="mcp-add-target" value={draft.target} spellCheck={false} placeholder="npx -y … / https://…"
          onChange={(event) => setDraft((value) => ({ ...value, target: event.target.value }))} /></div>
      <div className="settings-row"><label htmlFor="mcp-add-args">{words.args}</label>
        <input id="mcp-add-args" value={draft.args} spellCheck={false}
          onChange={(event) => setDraft((value) => ({ ...value, args: event.target.value }))} /></div>
      <div className="settings-row"><label htmlFor="mcp-add-env">{words.env}</label>
        <textarea id="mcp-add-env" rows={2} value={draft.env} spellCheck={false} autoComplete="off"
          onChange={(event) => setDraft((value) => ({ ...value, env: event.target.value }))} /></div>
      <div className="settings-row"><span />
        <button data-command="preference.chat_mcp.add" type="submit" className="btn" disabled={busy || !draft.name.trim() || !draft.target.trim()}>{words.save}</button></div>
    </form>
    {problem && <p className="settings-row__hint" role="alert">{words.failed}{problem}</p>}
  </>;
}

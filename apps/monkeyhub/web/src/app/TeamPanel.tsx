import { useEffect, useState } from "react";

type Member = { actorId: string; name: string; role: string; nodeId: string };
type Connection = { projectId: string; projectDir: string; kind: string; status: string; ownerActorId: string; role?: string; totalBytes?: number; completedBytes?: number; error?: string; members: Member[] };
async function api<T>(path: string, body?: unknown, method = body === undefined ? "GET" : "POST"): Promise<T> {
  const response = await fetch(`/api/team${path}`, { method, headers: body === undefined ? undefined : { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  let value;
  try { value = await response.json(); }
  catch { throw new Error(`The Hub returned an unreadable response (HTTP ${response.status}).`); }
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : `HTTP ${response.status}`);
  return value as T;
}

export function TeamPanel({ projectDir, language, close, openProject }: { projectDir: string | null; language: string; close: () => void; openProject: (projectId: string, projectDir: string) => void }) {
  const zh = language === "zh-CN";
  const [name, setName] = useState("");
  const [folder, setFolder] = useState("");
  const [invitation, setInvitation] = useState("");
  const [created, setCreated] = useState("");
  const [role, setRole] = useState("designer");
  const [connections, setConnections] = useState<Connection[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    const refresh = () => void api<Connection[]>("/projects").then((rows) => { if (alive) setConnections(rows); }, (cause) => { if (alive) setError(String(cause.message)); });
    refresh(); const timer = setInterval(refresh, 2000);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  const run = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setError("");
    try { await action(); setConnections(await api<Connection[]>("/projects")); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  };
  const roles = [["viewer", zh ? "只读" : "Viewer"], ["designer", zh ? "设计成员" : "Designer"], ["moderator", zh ? "可接受阶段" : "Moderator"]];
  return <section aria-labelledby="team-heading">
    <div className="chat-dialog__heading"><h2 id="team-heading">{zh ? "项目协作" : "Project team"}</h2><button type="button" className="btn" onClick={close}>{zh ? "关闭" : "Close"}</button></div>
    <p>{zh ? "每台设备使用自己的 Hub、Runtime 和本地项目。离线修改保存在本机；重连后同步候选及成员位置。" : "Each device runs its own Hub, Runtime and project replica. Offline work stays local; candidates and member positions catch up after reconnecting."}</p>
    <label>{zh ? "你的名字" : "Your name"}<input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} /></label>
    <fieldset disabled={busy}><legend>{zh ? "邀请成员" : "Invite a member"}</legend>
      <p>{projectDir ?? (zh ? "先选择一个项目" : "Select a project first")}</p>
      <label>{zh ? "权限" : "Role"}<select value={role} onChange={(event) => setRole(event.target.value)}>{roles.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
      <button className="btn" disabled={!projectDir || !name.trim()} onClick={() => void run(async () => {
        const result = await api<{ invitation: string }>("/share", { projectDir, name: name.trim(), role }); setCreated(result.invitation);
      })}>{zh ? "生成一次性邀请" : "Create one-time invitation"}</button>
      {created && <label>{zh ? "30 分钟内有效，请私下发给成员" : "Valid for 30 minutes. Share privately with the member"}<textarea readOnly value={created} onFocus={(event) => event.target.select()} /></label>}
    </fieldset>
    <fieldset disabled={busy}><legend>{zh ? "加入项目" : "Join a project"}</legend>
      <label>{zh ? "邀请" : "Invitation"}<textarea value={invitation} onChange={(event) => setInvitation(event.target.value)} /></label>
      <label>{zh ? "本机空文件夹的完整路径" : "Absolute path to an empty local folder"}<input value={folder} onChange={(event) => setFolder(event.target.value)} /></label>
      <button className="btn btn--primary" disabled={!name.trim() || !folder.trim() || !invitation.trim()} onClick={() => void run(async () => {
        await api("/join", { invitation: invitation.trim(), projectDir: folder.trim(), name: name.trim() }); setInvitation("");
      })}>{zh ? "加入并下载" : "Join and download"}</button>
    </fieldset>
    {error && <p role="alert">{error}</p>}
    {busy && <p role="status">{zh ? "正在连接…" : "Connecting…"}</p>}
    {connections.map((connection) => <article key={connection.projectId}>
      <h3>{connection.projectId}</h3><p role="status">{connection.status}{connection.role ? ` · ${connection.role}` : ""}{connection.error ? ` · ${connection.error}` : ""}</p>
      {Boolean(connection.totalBytes) && <><progress aria-label={`${connection.projectId} ${zh ? "下载进度" : "download progress"}`} value={connection.completedBytes ?? 0} max={connection.totalBytes} /><span>{Math.floor((connection.completedBytes ?? 0) / 1024)} / {Math.floor((connection.totalBytes ?? 0) / 1024)} KiB</span></>}
      <button className="btn" disabled={busy} onClick={() => void run(async () => { await api(`/projects/${encodeURIComponent(connection.projectId)}/resume`, {}); })}>{zh ? "重连 / 继续下载" : "Reconnect / resume"}</button>
      <button className="btn" disabled={!["running", "synced", "offline", "revoked"].includes(connection.status)} onClick={() => openProject(connection.projectId, connection.projectDir)}>{zh ? "打开本机项目" : "Open local project"}</button>
      {connection.members.map((member) => <div key={member.actorId}><span>{member.name} · {member.role} </span>
        <select aria-label={`${member.name} ${zh ? "权限" : "role"}`} value={member.role} disabled={busy || member.actorId === connection.ownerActorId} onChange={(event) => void run(async () => {
          await api(`/projects/${encodeURIComponent(connection.projectId)}/members/${encodeURIComponent(member.actorId)}`, { role: event.target.value }, "PUT");
        })}>{roles.map(([value, label]) => <option key={value} value={value}>{label}</option>)}<option value="revoked">{zh ? "撤销访问" : "Revoke access"}</option></select></div>)}
    </article>)}
  </section>;
}

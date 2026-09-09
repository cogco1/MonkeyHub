import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { applyAppearance, type AppearancePreferences, type Language } from "../../../shared-web/src/appearance";
import { client } from "./api/generated/client.gen";
import { getUserSettingsApiSettingsUserGet, listAppsApiAppsGet, startAppApiAppsAppIdStartPost, stopAppApiAppsAppIdStopPost, type AppStatus } from "./api/generated";
import "./styles.css";

type AppId = "monkeyarch" | "monkeydiagram" | "monkeymonitor" | "monkeyboard";

const copy = {
  en: {
    title: "MonkeyHub", subtitle: "One desktop entry for the Monkey tools.",
    waiting: "Hub service is not connected yet.", primary: "Waiting for Hub service",
    arch: ["MonkeyArch", "3D modelling and spatial change"],
    diagram: ["MonkeyDiagram", "Drawings, images and annotations"],
    monitor: ["MonkeyMonitor", "Usage and cost inspection"],
    board: ["MonkeyBoard", "Board workspace is planned, not available yet"],
    unavailable: "Not available", settings: "Display", language: "语言", theme: "Theme", size: "Size",
  },
  "zh-CN": {
    title: "MonkeyHub", subtitle: "Monkey 工具的统一桌面入口。",
    waiting: "尚未连接 Hub 服务。", primary: "等待 Hub 服务",
    arch: ["MonkeyArch", "三维建模与空间修改"], diagram: ["MonkeyDiagram", "图纸、图片与批注"],
    monitor: ["MonkeyMonitor", "用量与成本检查"], board: ["MonkeyBoard", "图版工作区正在规划，暂不可用"],
    unavailable: "暂不可用", settings: "显示", language: "Language", theme: "主题", size: "字号",
  },
} as const;

function App() {
  const [preferences, setPreferences] = useState<AppearancePreferences>({ language: navigator.language.startsWith("zh") ? "zh-CN" : "en", theme: "system", fontScale: 1 });
  useEffect(() => applyAppearance(preferences), [preferences]);
  const [apps, setApps] = useState<readonly AppStatus[] | null>(null);
  const [serviceError, setServiceError] = useState<string | null>(null);
  const [busy, setBusy] = useState<AppId | null>(null);
  const refresh = async () => {
    try {
      const [appResponse, settingsResponse] = await Promise.all([listAppsApiAppsGet(), getUserSettingsApiSettingsUserGet()]);
      if (!appResponse.data) throw new Error("Hub did not return application status.");
      setApps(appResponse.data);
      const settings = settingsResponse.data;
      if (settings?.language || settings?.theme || settings?.fontScale) setPreferences((current) => ({ language: settings.language ?? current.language, theme: settings.theme ?? current.theme, fontScale: settings.fontScale ?? current.fontScale }));
      setServiceError(null);
    } catch {
      setApps(null); setServiceError(t.waiting);
    }
  };
  useEffect(() => { client.setConfig({ baseUrl: window.location.origin }); void refresh(); }, []);
  const t = copy[preferences.language];
  const labels: Record<AppId, readonly [string, string]> = { monkeyarch: t.arch, monkeydiagram: t.diagram, monkeymonitor: t.monitor, monkeyboard: t.board };
  const cards = (apps ?? (["monkeyarch", "monkeydiagram", "monkeymonitor", "monkeyboard"] as const).map((appId) => ({ appId, title: labels[appId][0], serviceId: appId === "monkeymonitor" ? "monitor" : appId === "monkeyboard" ? "board" : "studio", available: false, state: appId === "monkeyboard" ? "unavailable" : "stopped", url: null })));
  const act = async (app: AppStatus) => { if (!app.available || app.state === "unavailable") return; setBusy(app.appId); try { const result = app.state === "running" ? await stopAppApiAppsAppIdStopPost({ path: { app_id: app.appId } }) : await startAppApiAppsAppIdStartPost({ path: { app_id: app.appId } }); if (!result.data) throw new Error(); await refresh(); } catch { setServiceError(t.waiting); } finally { setBusy(null); } };
  return <main className="hub">
    <header><div><p className="eyebrow">Monkey</p><h1>{t.title}</h1><p>{t.subtitle}</p></div><fieldset><legend>{t.settings}</legend><label>{t.language}<select value={preferences.language} onChange={(event) => setPreferences((p) => ({ ...p, language: event.target.value as Language }))}><option value="en">English</option><option value="zh-CN">简体中文</option></select></label><label>{t.theme}<select value={preferences.theme} onChange={(event) => setPreferences((p) => ({ ...p, theme: event.target.value as AppearancePreferences["theme"] }))}><option value="system">System</option><option value="dark">Dark</option><option value="light">Light</option></select></label><label>{t.size}<select value={preferences.fontScale} onChange={(event) => setPreferences((p) => ({ ...p, fontScale: Number(event.target.value) as AppearancePreferences["fontScale"] }))}><option value="0.9">90%</option><option value="1">100%</option><option value="1.1">110%</option></select></label></fieldset></header>
    <section className="service" aria-live="polite"><span className="dot" />{serviceError ?? (apps ? "Hub connected" : t.waiting)}<button type="button" onClick={() => void refresh()}>{t.primary}</button></section>
    <section className="apps" aria-label="Applications">{cards.map((app) => { const label = labels[app.appId]; const unavailable = !app.available || app.state === "unavailable"; const running = app.state === "running"; return <article key={app.appId} className="app-card"><div><p className="eyebrow">{unavailable ? t.unavailable : `${app.serviceId} · ${app.state}`}</p><h2>{label[0]}</h2><p>{label[1]}</p></div><button type="button" disabled={unavailable || busy !== null} onClick={() => void act(app)}>{unavailable ? t.unavailable : busy === app.appId ? "…" : running ? "Stop" : "Start"}</button></article>; })}</section>
  </main>;
}

const root = document.getElementById("root");
if (!root) throw new Error("MonkeyHub root is missing.");
createRoot(root).render(<StrictMode><App /></StrictMode>);

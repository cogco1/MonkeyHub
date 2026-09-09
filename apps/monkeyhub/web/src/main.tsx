import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { applyAppearance, type AppearancePreferences, type Language } from "../../../shared-web/src/appearance";
import "./styles.css";

type AppId = "arch" | "diagram" | "monitor" | "board";
type AppStatus = "waiting" | "unavailable";

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
  const t = copy[preferences.language];
  const apps: ReadonlyArray<{ id: AppId; status: AppStatus; name: string; description: string }> = [
    { id: "arch", status: "waiting", name: t.arch[0], description: t.arch[1] },
    { id: "diagram", status: "waiting", name: t.diagram[0], description: t.diagram[1] },
    { id: "monitor", status: "waiting", name: t.monitor[0], description: t.monitor[1] },
    { id: "board", status: "unavailable", name: t.board[0], description: t.board[1] },
  ];
  return <main className="hub">
    <header><div><p className="eyebrow">Monkey</p><h1>{t.title}</h1><p>{t.subtitle}</p></div><fieldset><legend>{t.settings}</legend><label>{t.language}<select value={preferences.language} onChange={(event) => setPreferences((p) => ({ ...p, language: event.target.value as Language }))}><option value="en">English</option><option value="zh-CN">简体中文</option></select></label><label>{t.theme}<select value={preferences.theme} onChange={(event) => setPreferences((p) => ({ ...p, theme: event.target.value as AppearancePreferences["theme"] }))}><option value="system">System</option><option value="dark">Dark</option><option value="light">Light</option></select></label><label>{t.size}<select value={preferences.fontScale} onChange={(event) => setPreferences((p) => ({ ...p, fontScale: Number(event.target.value) as AppearancePreferences["fontScale"] }))}><option value="0.9">90%</option><option value="1">100%</option><option value="1.1">110%</option></select></label></fieldset></header>
    <section className="service" aria-live="polite"><span className="dot" />{t.waiting}<button type="button" disabled>{t.primary}</button></section>
    <section className="apps" aria-label="Applications">{apps.map((app) => <article key={app.id} className="app-card"><div><p className="eyebrow">{app.status === "unavailable" ? t.unavailable : t.waiting}</p><h2>{app.name}</h2><p>{app.description}</p></div><button type="button" disabled aria-label={`${app.name}: ${app.status === "unavailable" ? t.unavailable : t.waiting}`}>{app.status === "unavailable" ? t.unavailable : t.primary}</button></article>)}</section>
  </main>;
}

const root = document.getElementById("root");
if (!root) throw new Error("MonkeyHub root is missing.");
createRoot(root).render(<StrictMode><App /></StrictMode>);

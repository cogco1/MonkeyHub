import { useEffect, useRef, useState, type ComponentType, type ReactNode } from "react";

import { useCommandActions, useCommands } from "../features/commands/CommandProvider";
import type { MessageKey } from "../i18n/messages.en";
import { MenuTabs } from "../features/chrome/SurfaceChrome";
import { useT } from "../i18n/useT";
import { AdvancedTools } from "./AdvancedTools";
import "./attentionShell.css";

/**
 * The attention shell (2-3a, ?shell=attention): what needs the architect in front, the Board and
 * the model one tab away, the conversation in a drawer above the composer, and the rest in a side
 * panel. It holds no state of its own beyond what is open: ChatShell keeps the projects, the
 * conversations, the open surfaces and the composer, and hands them in as the same nodes its
 * workbench layout places.
 */

export type CenterView = "attention" | "board" | "model" | "tool";
export type SidePage = "background" | "history" | "handled" | "ignored" | "tools";
const SIDE_PAGES: readonly SidePage[] = ["background", "history", "handled", "ignored", "tools"];
/** How many of a project's conversations stand under its row. */
export const RECENT_CONVERSATIONS = 3;

export interface ShellConversation {
  readonly id: string;
  readonly title: string;
  readonly status: string;
  readonly current: boolean;
  readonly needsYou: boolean;
}

export interface ShellProject {
  readonly projectDir: string;
  readonly name: string;
  readonly current: boolean;
  /** What waits on the architect in this project. */
  readonly needs: number;
  readonly working: boolean;
  readonly recent: readonly ShellConversation[];
}

export interface AttentionShellProps {
  Icon: ComponentType<{ name: string }>;
  projects: readonly ShellProject[];
  /** What waits on the architect across every project. */
  attentionCount: number;
  view: CenterView;
  /** The tool page shown in the center, when the view is "tool". */
  toolLabel: string | null;
  /** Why the Board and the model cannot open yet, if they cannot. */
  surfaceBlocked: string | null;
  onView(view: Exclude<CenterView, "tool">): void;
  history: { back: boolean; forward: boolean; onBack(): void; onForward(): void };
  onSelectProject(projectDir: string): void;
  onSelectConversation(id: string): void;
  onNewProject(): void;
  onAddProject(): void;
  onSettings(): void;
  usage: { label: string; onOpen(): void } | null;
  /** Agent work running or queued in any project, for the background line and page. */
  background: { running: number; queued: number };
  /** True while the conversation is being sent to or answers; the drawer opens for it. */
  conversationActive: boolean;
  notices: ReactNode;
  conversation: ReactNode;
  composer: ReactNode;
  /** The open surfaces; they show while the view is not the attention queue. */
  surfaces: ReactNode;
  taskItems: ReactNode;
  dialogs: ReactNode;
}

function Glyph({ path }: { path: string }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={path} /></svg>;
}
const SEARCH = "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm5-2 4 4";
const QUEUE = "M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01";

export function AttentionShell(props: AttentionShellProps) {
  const { Icon, projects, view } = props;
  const t = useT();
  const { openPalette } = useCommands();
  const [side, setSide] = useState<SidePage | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [leftOpen, setLeftOpen] = useState(false);
  const active = useRef(props.conversationActive);
  // A message being sent or answered opens the drawer; closing it again is the architect's call.
  useEffect(() => {
    if (props.conversationActive && !active.current) setDrawer(true);
    active.current = props.conversationActive;
  }, [props.conversationActive]);
  const open = (page: SidePage) => setSide((current) => (current === page ? null : page));
  const pageLabel = (page: SidePage) => t(`shell.side.${page}` as MessageKey);
  useCommandActions({ "view.tab.attention": { run: () => props.onView("attention") } });
  const backgroundCount = props.background.running + props.background.queued;
  const tabs = [
    { value: "attention", label: t("shell.view.attention") },
    { value: "board", label: t("shell.view.board") },
    { value: "model", label: t("shell.view.model") },
  ] as const;

  return <div className="attention-shell" data-side={side ?? "closed"} data-left={leftOpen ? "open" : "closed"}>
    <header className="attention-shell__top">
      <button type="button" className="chat-icon" aria-label={t("shell.back")} title={t("shell.back")} disabled={!props.history.back}
        data-command="view.conversation_history.back" onClick={props.history.onBack}><Icon name="back" /></button>
      <button type="button" className="chat-icon" aria-label={t("shell.forward")} title={t("shell.forward")} disabled={!props.history.forward}
        data-command="view.conversation_history.forward" onClick={props.history.onForward}><Icon name="forward" /></button>
      <button type="button" className="chat-icon attention-shell__left-toggle" aria-label={t("shell.projects")} aria-expanded={leftOpen}
        aria-controls="attention-shell-left" data-command="view.left_column.toggle" onClick={() => setLeftOpen(!leftOpen)}><Icon name="sidebar" /></button>
      <button type="button" className="attention-shell__search" onClick={openPalette} aria-keyshortcuts="Control+K">
        <Glyph path={SEARCH} /><span>{t("shell.search")}</span><kbd>Ctrl K</kbd>
      </button>
    </header>

    <nav className="attention-shell__left" id="attention-shell-left" aria-label={t("shell.projects")}>
      <button type="button" className="attention-shell__row attention-shell__row--attention" aria-current={view === "attention" ? "page" : undefined}
        data-command="view.tab.attention" onClick={() => { props.onView("attention"); setLeftOpen(false); }}>
        <Glyph path={QUEUE} /><span>{t("shell.view.attention")}</span>
        {props.attentionCount > 0 && <span className="attention-shell__count">{props.attentionCount}</span>}
      </button>
      <p className="attention-shell__caption">{t("shell.projects")}</p>
      {!projects.length && <p className="attention-shell__empty">{t("shell.noProjects")}</p>}
      <ul className="attention-shell__projects">{projects.map((project) => <li key={project.projectDir}>
        <button type="button" className="attention-shell__row" aria-current={project.current ? "true" : undefined} title={project.projectDir}
          onClick={() => { props.onSelectProject(project.projectDir); setLeftOpen(false); }}>
          <Icon name="folder" /><span>{project.name}</span>
          {project.needs > 0 ? <span className="attention-shell__count" aria-label={t("shell.needs", { count: project.needs })}>{project.needs}</span>
            : project.working ? <span className="attention-shell__working" aria-label={t("shell.working")} /> : null}
        </button>
        {project.recent.length > 0 && <ul className="attention-shell__recent">{project.recent.map((item) => <li key={item.id}>
          <button type="button" className="chat-thread" aria-current={item.current ? "page" : undefined} title={item.title}
            onClick={() => { props.onSelectConversation(item.id); setDrawer(true); setLeftOpen(false); }}>
            <span className="chat-thread__dot" data-status={item.status} /><span className="chat-thread__title">{item.title}</span>
            {item.needsYou && <span className="chat-needs">{t("shell.needsYou")}</span>}
          </button>
        </li>)}</ul>}
      </li>)}</ul>
      <div className="attention-shell__left-foot">
        <button type="button" className="attention-shell__row" data-command="project.create.new" onClick={props.onNewProject}><Icon name="plus" /><span>{t("shell.newProject")}</span></button>
        <button type="button" className="chat-icon" aria-label={t("shell.addProject")} title={t("shell.addProject")}
          data-command="project.create.add_existing" onClick={props.onAddProject}><Icon name="folder" /></button>
        {props.usage && <button type="button" className="attention-shell__row attention-shell__usage" data-command="view.usage.open" onClick={props.usage.onOpen}>
          <Icon name="chart" /><span>{props.usage.label}</span></button>}
        <button type="button" className="chat-icon" aria-label={t("shell.settings")} title={t("shell.settings")}
          data-command="view.settings.open" onClick={props.onSettings}><Icon name="settings" /></button>
      </div>
    </nav>
    {leftOpen && <button type="button" className="attention-shell__scrim attention-shell__scrim--left" aria-label={t("shell.close")} onClick={() => setLeftOpen(false)} />}

    <main className="attention-shell__center">
      <div className="surface-bar attention-shell__tabs">
        <MenuTabs label={t("shell.views")} value={view === "tool" ? "" : view} options={tabs}
          onChange={(value) => props.onView(value as Exclude<CenterView, "tool">)} />
        {view === "tool" && props.toolLabel && <span className="attention-shell__tool">{props.toolLabel}</span>}
        {props.surfaceBlocked && view === "attention" && <span className="surface-bar__end">{props.surfaceBlocked}</span>}
      </div>
      {props.notices}
      <div className="attention-shell__body">
        <section className="attention-shell__queue" hidden={view !== "attention"} aria-label={t("shell.view.attention")}>
          <h2>{props.attentionCount ? t("shell.queueCount", { count: props.attentionCount }) : t("shell.queueEmpty")}</h2>
          <p>{t("shell.queueHint")}</p>
        </section>
        <div className="attention-shell__surfaces" hidden={view === "attention"}>{props.surfaces}</div>
      </div>
      <div className="attention-shell__bottom">
        <div className="status-line attention-shell__status">
          <span className="status-line__start">{backgroundCount
            ? t("shell.backgroundLine", { running: props.background.running, queued: props.background.queued }) : t("shell.backgroundIdle")}</span>
          <span className="status-line__end attention-shell__status-actions">
            <button type="button" className="menu-command" aria-expanded={drawer} aria-controls="attention-shell-drawer" onClick={() => setDrawer(!drawer)}>
              {drawer ? t("shell.hideConversation") : t("shell.showConversation")}</button>
            <button type="button" className="menu-command" aria-expanded={side === "background"} onClick={() => open("background")}>{t("shell.openBackground")}</button>
          </span>
        </div>
        <section className="attention-shell__drawer" id="attention-shell-drawer" hidden={!drawer} aria-label={t("shell.conversation")}>
          {props.conversation}
        </section>
        {props.composer}
      </div>
    </main>

    <aside className="attention-shell__side" aria-label={t("shell.side")}>
      {side && <div className="attention-shell__panel">
        <div className="attention-shell__panel-head">
          <div className="menu-tabs attention-shell__side-tabs" role="tablist" aria-label={t("shell.side")}>
            {SIDE_PAGES.map((page) => <button key={page} type="button" role="tab" className="menu-tab" aria-selected={side === page} aria-pressed={side === page}
              onClick={() => setSide(page)}>{pageLabel(page)}</button>)}
          </div>
          <button type="button" className="chat-icon" aria-label={t("shell.closeSide")} title={t("shell.closeSide")} onClick={() => setSide(null)}><Icon name="close" /></button>
        </div>
        <div className="attention-shell__panel-body" role="tabpanel" aria-label={pageLabel(side)}>
          {side === "background" ? <ul className="chat-tasks attention-shell__tasks">{props.taskItems}</ul>
            : side === "tools" ? <AdvancedTools />
            : <p className="attention-shell__empty">{t(`shell.side.${side}.empty` as MessageKey)}</p>}
        </div>
      </div>}
      <div className="attention-shell__rail" role="toolbar" aria-orientation="vertical" aria-label={t("shell.side")}>
        {SIDE_PAGES.map((page) => <button key={page} type="button" className="attention-shell__rail-item" aria-pressed={side === page} onClick={() => open(page)}>
          <Icon name={page === "background" ? "tasks" : page === "history" ? "tree" : page === "handled" ? "archive" : page === "ignored" ? "restore" : "display"} />
          <span>{pageLabel(page)}</span>{page === "background" && backgroundCount > 0 && <span className="attention-shell__count">{backgroundCount}</span>}
        </button>)}
      </div>
    </aside>
    {side && <button type="button" className="attention-shell__scrim attention-shell__scrim--side" aria-label={t("shell.closeSide")} onClick={() => setSide(null)} />}
    {props.dialogs}
  </div>;
}

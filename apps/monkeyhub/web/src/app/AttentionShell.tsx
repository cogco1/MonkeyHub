import { useEffect, useRef, useState, type ComponentType, type ReactNode } from "react";

import { useCommandActions, useCommands } from "../features/commands/CommandProvider";
import type { MessageKey } from "../i18n/messages.en";
import { useT } from "../i18n/useT";
import { AttentionQueue, CardHistory, useAttentionQueue } from "../notifications/cards/AttentionQueue";
import { pendingFor } from "../notifications/cards/cardText";
import { OPEN_CARD_EVENT, type OpenCardDetail } from "../notifications/openChat";
import { showingQueue } from "../notifications/queue";
import { useNoticeRegion } from "../notifications/regions";
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
/** The command that shows each side page: its rail item and its tab carry it, and Ctrl K finds it. */
const SIDE_COMMANDS: Readonly<Record<SidePage, string>> = {
  background: "view.background.toggle", history: "view.design_history.toggle", handled: "view.handled.toggle",
  ignored: "view.ignored.toggle", tools: "view.advanced_tools.toggle",
};
/** How many of a project's conversations stand under its row. */
export const RECENT_CONVERSATIONS = 3;

export interface ShellConversation {
  readonly id: string;
  readonly title: string;
  readonly status: string;
  readonly current: boolean;
  readonly needsYou: boolean;
  readonly archived: boolean;
  /** Why it cannot be archived or restored right now, if it cannot. */
  readonly archiveBlocked: string | null;
}

export interface ShellProject {
  readonly projectDir: string;
  readonly name: string;
  /** The step its working design stands on, named as the history page names it ("V16"); none shown while nothing names it. */
  readonly step?: string | null;
  readonly current: boolean;
  /** What waits on the architect in this project. */
  readonly needs: number;
  readonly working: boolean;
  readonly recent: readonly ShellConversation[];
}

/** What just came out, beside the conclusion: a drawing with its picture and the click that opens it, or a finished job. */
export interface RecentTile {
  /** The drawing's id, when the tile is a drawing. */
  readonly id?: string;
  readonly label: string;
  readonly title: string;
  readonly caption: string;
  readonly picture?: string;
  readonly onOpen?: () => void;
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
  onNewConversation(projectDir: string): void;
  onArchiveConversation(id: string): void;
  /** The column lists archived conversations instead of the active ones. */
  archivedView: boolean;
  onArchivedView(value: boolean): void;
  onNewProject(): void;
  onAddProject(): void;
  onSettings(): void;
  usage: { label: string; onOpen(): void } | null;
  /** Agent work running or queued in any project, for the background line and page. */
  background: { running: number; queued: number };
  /** Beside the conclusion: how long the longest running work has left, and the latest finished result. */
  overview?: { wait: string | null; recent: RecentTile | null };
  /** True while the conversation is being sent to or answers; the drawer opens for it. */
  conversationActive: boolean;
  notices: ReactNode;
  conversation: ReactNode;
  composer: ReactNode;
  /** The open surfaces; they show while the view is not the attention queue. */
  surfaces: ReactNode;
  taskItems: ReactNode;
  /** The open project's 修改历史 page (2-6), bound to its runtime by the host; absent without an open project. */
  historyPage?: ReactNode;
  dialogs: ReactNode;
  /** The left column's Attention: no conversation is chosen, so the next message goes to the attention conversation. */
  onAttentionConversation(): void;
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
  useEffect(() => { showingQueue(view === "attention"); return () => showingQueue(false); }, [view]);
  const open = (page: SidePage) => setSide((current) => (current === page ? null : page));
  const pageLabel = (page: SidePage) => t(`shell.side.${page}` as MessageKey);
  useCommandActions({
    "view.tab.attention": { run: () => props.onView("attention") },
    ...Object.fromEntries(SIDE_PAGES.map((page) => [SIDE_COMMANDS[page], { run: () => open(page) }])),
  });
  const backgroundCount = props.background.running + props.background.queued;
  // The queue's own counts where the Hub serves it; until then, what the conversations say waits on the architect.
  const queue = useAttentionQueue();
  const summary = queue.available ? queue.summary : null;
  const attentionCount = summary ? summary.open : props.attentionCount;
  const needs = (project: ShellProject) => pendingFor(summary, project.projectDir) ?? project.needs;
  const projectName = (projectDir: string) => projects.find((project) => project.projectDir === projectDir)?.name ?? null;
  // The notices keep clear of the side and of the status line and composer (1-4).
  const sideRef = useRef<HTMLElement>(null), bottomRef = useRef<HTMLDivElement>(null);
  useNoticeRegion(sideRef, "right");
  useNoticeRegion(bottomRef, "bottom");
  // A card's notice shows the card here, in the queue.
  const onView = useRef(props.onView);
  onView.current = props.onView;
  useEffect(() => {
    const show = (event: Event) => {
      const { cardId } = (event as CustomEvent<OpenCardDetail>).detail;
      event.preventDefault();
      onView.current("attention");
      setLeftOpen(false);
      requestAnimationFrame(() => {
        const card = document.getElementById(`attention-card-${cardId}`);
        card?.scrollIntoView({ block: "nearest" });
        card?.focus({ preventScroll: true });
      });
    };
    window.addEventListener(OPEN_CARD_EVENT, show);
    return () => window.removeEventListener(OPEN_CARD_EVENT, show);
  }, []);
  const tabs = [
    { value: "attention", label: t("shell.view.attention") },
    { value: "board", label: t("shell.view.board") },
    { value: "model", label: t("shell.view.model") },
  ] as const;

  return <div className="attention-shell" data-view={view} data-side={side ?? "closed"} data-left={leftOpen ? "open" : "closed"}>
    <header className="attention-shell__top">
      {/* S01: the arrows are there only while there is a conversation to go back or forward to; Ctrl K has them always. */}
      {props.history.back && <button type="button" className="chat-icon" aria-label={t("shell.back")} title={t("shell.back")}
        data-command="view.conversation_history.back" onClick={props.history.onBack}><Icon name="back" /></button>}
      {props.history.forward && <button type="button" className="chat-icon" aria-label={t("shell.forward")} title={t("shell.forward")}
        data-command="view.conversation_history.forward" onClick={props.history.onForward}><Icon name="forward" /></button>}
      <button type="button" className="chat-icon attention-shell__left-toggle" aria-label={t("shell.projects")} aria-expanded={leftOpen}
        aria-controls="attention-shell-left" data-command="view.left_column.toggle" onClick={() => setLeftOpen(!leftOpen)}><Icon name="sidebar" /></button>
      <button type="button" className="attention-shell__search" onClick={openPalette} aria-keyshortcuts="Control+K">
        <Glyph path={SEARCH} /><span>{t("shell.search")}</span><kbd>Ctrl K</kbd>
      </button>
    </header>

    <nav className="attention-shell__left" id="attention-shell-left" aria-label={t("shell.projects")}>
      <button type="button" className="attention-shell__row attention-shell__row--attention" aria-current={view === "attention" ? "page" : undefined}
        data-command="view.tab.attention" onClick={() => { props.onView("attention"); props.onAttentionConversation(); setLeftOpen(false); }}>
        <Glyph path={QUEUE} /><span>{t("shell.view.attention")}</span>
        {attentionCount > 0 && <span className="attention-shell__count">{attentionCount}</span>}
      </button>
      <div className="attention-shell__caption">
        <span>{props.archivedView ? t("shell.archived") : t("shell.projects")}</span>
        <button type="button" className="chat-icon" aria-pressed={props.archivedView} aria-label={t("shell.archived")} title={t("shell.archived")}
          onClick={() => props.onArchivedView(!props.archivedView)}><Icon name="archive" /></button>
      </div>
      {!projects.length && <p className="attention-shell__empty">{t("shell.noProjects")}</p>}
      <ul className="attention-shell__projects">{projects.map((project) => <li key={project.projectDir}>
        <div className="attention-shell__project">
          <button type="button" className="attention-shell__row" aria-current={project.current ? "true" : undefined} title={project.projectDir}
            onClick={() => { props.onSelectProject(project.projectDir); setLeftOpen(false); }}>
            <Icon name="folder" /><span className="attention-shell__name"><span>{project.name}</span>
              {project.step ? <small className="attention-shell__version">{t("shell.projectStep", { step: project.step })}</small> : null}</span>
            {needs(project) > 0 ? <span className="attention-shell__count" aria-label={t("shell.needs", { count: needs(project) })}>{needs(project)}</span>
              : project.working ? <span className="attention-shell__working" aria-label={t("shell.working")} /> : null}
          </button>
          <button type="button" className="chat-icon attention-shell__new" aria-label={t("shell.newConversationIn", { project: project.name })}
            title={t("shell.newConversationIn", { project: project.name })} onClick={() => { props.onNewConversation(project.projectDir); setLeftOpen(false); }}><Icon name="plus" /></button>
        </div>
        {project.recent.length > 0 && <ul className="attention-shell__recent">{project.recent.map((item) => <li key={item.id} className="chat-thread-row">
          <button type="button" className="chat-thread" aria-current={item.current ? "page" : undefined} title={item.title}
            onClick={() => { props.onSelectConversation(item.id); setDrawer(true); setLeftOpen(false); }}>
            <span className="chat-thread__dot" data-status={item.status} /><span className="chat-thread__title">{item.title}</span>
            {item.needsYou && <span className="chat-needs">{t("shell.needsYou")}</span>}
          </button>
          <button type="button" className="chat-icon chat-thread-action" disabled={item.archiveBlocked !== null}
            aria-label={`${item.archived ? t("shell.restoreConversation") : t("shell.archiveConversation")}: ${item.title}`}
            title={item.archiveBlocked ?? (item.archived ? t("shell.restoreConversation") : t("shell.archiveConversation"))}
            onClick={() => props.onArchiveConversation(item.id)}><Icon name={item.archived ? "restore" : "archive"} /></button>
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
        {/* The Board and the model need an open project; without one their tabs say why, as Ctrl K does. */}
        <div className="menu-tabs" role="group" aria-label={t("shell.views")}>{tabs.map((tab) => {
          const blocked = tab.value !== "attention" ? props.surfaceBlocked : null;
          return <button key={tab.value} type="button" className="menu-tab" aria-pressed={view === tab.value} aria-disabled={blocked ? true : undefined}
            title={blocked ?? undefined} onClick={() => { if (!blocked) props.onView(tab.value); }}>{tab.label}</button>;
        })}</div>
        {view === "tool" && props.toolLabel && <span className="attention-shell__tool">{props.toolLabel}</span>}
        {props.surfaceBlocked && view === "attention" && <span className="surface-bar__end">{props.surfaceBlocked}</span>}
      </div>
      {props.notices}
      <div className="attention-shell__body">
        <section className="attention-shell__queue" hidden={view !== "attention"} aria-label={t("shell.view.attention")}>
          {/* The conclusion first, what runs and what just came out beside it, the cards below (ADR-011 §8). */}
          <div className="attention-overview">
            <div className="attention-overview__hero">
              <h2>{queue.available === null ? t("shell.queueReading") : attentionCount ? t("shell.queueCount", { count: attentionCount }) : t("shell.queueEmpty")}</h2>
              {!queue.available && <p>{t("shell.queueHint")}</p>}
            </div>
            {backgroundCount > 0 && <button data-command="view.background.toggle" type="button" className="attention-overview__tile" onClick={() => open("background")}>
              <span>{t("shell.overview.background")}</span>
              <b>{props.background.running ? t("shell.overview.running", { count: props.background.running }) : t("shell.overview.queued", { count: props.background.queued })}</b>
              <small>{props.overview?.wait ?? (props.background.running && props.background.queued ? t("shell.overview.queued", { count: props.background.queued }) : "")}</small>
            </button>}
            {props.overview?.recent && (() => {
              const recent = props.overview.recent;
              const body = <>
                {recent.picture && <img className="attention-overview__picture" src={recent.picture} alt="" loading="lazy" />}
                <span>{recent.label}</span><b title={recent.title}>{recent.title}</b><small>{recent.caption}</small>
              </>;
              return recent.onOpen ? <button type="button" className="attention-overview__tile attention-overview__tile--picture" data-command="view.recent_drawing.open" data-command-arg={recent.id}
                aria-label={`${recent.label}：${recent.title}`} onClick={recent.onOpen}>{body}</button>
                : <div className="attention-overview__tile">{body}</div>;
            })()}
          </div>
          {queue.available && <AttentionQueue projectName={projectName} />}
        </section>
        <div className="attention-shell__surfaces" hidden={view === "attention"}>{props.surfaces}</div>
      </div>
      <div className="attention-shell__bottom" ref={bottomRef}>
        <div className="status-line attention-shell__status">
          <button type="button" className="status-line__start menu-command attention-shell__status-open" aria-expanded={side === "background"} aria-label={t("shell.openBackground")}
            title={t("shell.openBackground")} data-command="view.background.toggle" onClick={() => open("background")}>{backgroundCount
            ? t("shell.backgroundLine", { running: props.background.running, queued: props.background.queued }) : t("shell.backgroundIdle")}</button>
          <span className="status-line__end attention-shell__status-actions">
            <button type="button" className="menu-command" aria-expanded={drawer} aria-controls="attention-shell-drawer" onClick={() => setDrawer(!drawer)}>
              {drawer ? t("shell.hideConversation") : t("shell.showConversation")}</button>
          </span>
        </div>
        <section className="attention-shell__drawer" id="attention-shell-drawer" hidden={!drawer} aria-label={t("shell.conversation")}>
          {props.conversation}
        </section>
        {props.composer}
      </div>
    </main>

    <aside className="attention-shell__side" ref={sideRef} aria-label={t("shell.side")}>
      {side && <div className="attention-shell__panel">
        <div className="attention-shell__panel-head">
          <div className="menu-tabs attention-shell__side-tabs" role="tablist" aria-label={t("shell.side")}>
            {SIDE_PAGES.map((page) => <button key={page} type="button" role="tab" className="menu-tab" aria-selected={side === page} aria-pressed={side === page}
              data-command={SIDE_COMMANDS[page]} onClick={() => setSide(page)}>{pageLabel(page)}</button>)}
          </div>
          <button type="button" className="chat-icon" aria-label={t("shell.closeSide")} title={t("shell.closeSide")} onClick={() => setSide(null)}><Icon name="close" /></button>
        </div>
        <div className="attention-shell__panel-body" role="tabpanel" aria-label={pageLabel(side)}>
          {side === "background" ? <ul className="chat-tasks attention-shell__tasks">{props.taskItems}</ul>
            : side === "tools" ? <AdvancedTools />
            : side === "history" && props.historyPage ? props.historyPage
            : queue.available && side === "handled" ? <CardHistory states={["done"]} empty="shell.side.handled.empty" projectName={projectName} />
            : queue.available && side === "ignored" ? <CardHistory states={["dismissed"]} empty="shell.side.ignored.empty" projectName={projectName} />
            : <p className="attention-shell__empty">{t(`shell.side.${side}.empty` as MessageKey)}</p>}
        </div>
      </div>}
      {/* 20 前台只剩草图、体块和少量语义描述: the rail shows the background while there is work in it; the other pages are
          one Ctrl K away (their commands), and the open panel's own tabs switch between them. Nothing to show, no rail. */}
      {(Boolean(props.historyPage) || side !== null) && <div className="attention-shell__rail" role="toolbar" aria-orientation="vertical" aria-label={t("shell.side")}>
        {SIDE_PAGES.filter((page) => side !== null || (page === "history" && Boolean(props.historyPage))).map((page) => <button key={page} type="button" className="attention-shell__rail-item" aria-pressed={side === page}
          data-command={SIDE_COMMANDS[page]} onClick={() => open(page)}>
          <Icon name={page === "background" ? "tasks" : page === "history" ? "tree" : page === "handled" ? "archive" : page === "ignored" ? "restore" : "display"} />
          <span>{pageLabel(page)}</span>{page === "background" && backgroundCount > 0 && <span className="attention-shell__count">{backgroundCount}</span>}
        </button>)}
      </div>}
    </aside>
    {side && <button type="button" className="attention-shell__scrim attention-shell__scrim--side" aria-label={t("shell.closeSide")} onClick={() => setSide(null)} />}
    {props.dialogs}
  </div>;
}

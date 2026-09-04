import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import type { ServerIdentity } from "../../api/connection";
import type { ProjectBindingDto } from "../../api/generated";
import { prepareEnglishToChinese } from "../../i18n/browserTranslator";
import { useT } from "../../i18n/useT";
import {
  usePreferences,
  type FontScale,
  type Language,
  type ThemePreference,
} from "./preferences";
import "./SettingsPanel.css";

type SettingsSection =
  | "appearance"
  | "project"
  | "model"
  | "geometry"
  | "server"
  | "diagnostics";

type SourceKind = "browser" | "server" | "unavailable";
type TranslationPreparation = "idle" | "preparing" | "ready" | "unavailable";

const SECTIONS: readonly SettingsSection[] = [
  "appearance",
  "project",
  "model",
  "geometry",
  "server",
  "diagnostics",
];

export interface SettingsPanelProps {
  open: boolean;
  onClose(): void;
  server: ServerIdentity;
  project: ProjectBindingDto | null;
}

function SourceBadge({
  source,
  label,
  title,
}: {
  source: SourceKind;
  label: string;
  title: string;
}) {
  return (
    <span
      className="settings-source"
      data-source={source}
      title={title}
    >
      {label}
    </span>
  );
}

function ReadOnlySetting({
  label,
  value,
  source,
  sourceLabel,
  sourceTitle,
}: {
  label: string;
  value: string;
  source: SourceKind;
  sourceLabel: string;
  sourceTitle: string;
}) {
  return (
    <div className="settings-row settings-row--readonly">
      <span className="settings-row__label">{label}</span>
      <output className="settings-readout" title={value}>
        {value}
      </output>
      <SourceBadge source={source} label={sourceLabel} title={sourceTitle} />
    </div>
  );
}

export function SettingsPanel({
  open,
  onClose,
  server,
  project,
}: SettingsPanelProps) {
  const t = useT();
  const {
    language,
    theme,
    fontScale,
    eventStreamVisible,
    setLanguage,
    setTheme,
    setFontScale,
    setEventStreamVisible,
  } = usePreferences();
  const [activeSection, setActiveSection] =
    useState<SettingsSection>("appearance");
  const [translationPreparation, setTranslationPreparation] =
    useState<TranslationPreparation>("idle");

  const prepareDynamicChinese = () => {
    setTranslationPreparation("preparing");
    void prepareEnglishToChinese().then((available) => {
      setTranslationPreparation(available ? "ready" : "unavailable");
    });
  };

  const id = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;

    previousFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;

    const frame = window.requestAnimationFrame(() => {
      closeButtonRef.current?.focus();
    });

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }

      if (event.key !== "Tab") return;

      const panel = panelRef.current;
      if (panel === null) return;

      const focusable = Array.from(
        panel.querySelectorAll<HTMLElement>(
          'button:not([disabled]), select:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
        ),
      ).filter(
        (element) => element.offsetParent !== null && element.tabIndex >= 0,
      );

      if (focusable.length === 0) {
        event.preventDefault();
        panel.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const current = document.activeElement;

      if (event.shiftKey && (current === first || !panel.contains(current))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && current === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", handleKeyDown);
      previousFocusRef.current?.focus();
    };
  }, [open]);

  if (!open) return null;

  const sectionLabel = (section: SettingsSection) =>
    t(`settings.sections.${section}`);
  const sourceLabel = (source: SourceKind) =>
    t(`settings.badges.${source}`);
  const sourceTitle = (source: SourceKind) =>
    t("common.sourceNamed", { name: sourceLabel(source) });
  const unavailable = t("settings.values.protocolUnavailable");
  const notBound = t("settings.values.notBound");

  const moveSectionFocus = (
    event: ReactKeyboardEvent<HTMLButtonElement>,
    section: SettingsSection,
  ) => {
    const currentIndex = SECTIONS.indexOf(section);
    let nextIndex: number | null = null;

    if (event.key === "ArrowDown" || event.key === "ArrowRight") {
      nextIndex = (currentIndex + 1) % SECTIONS.length;
    } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + SECTIONS.length) % SECTIONS.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = SECTIONS.length - 1;
    }

    if (nextIndex === null) return;
    event.preventDefault();
    const nextSection = SECTIONS[nextIndex];
    setActiveSection(nextSection);
    document.getElementById(`${id}-tab-${nextSection}`)?.focus();
  };

  const projectValue = (value: string | null | undefined) => ({
    value: project === null ? notBound : value ?? unavailable,
    source: (project !== null && value !== null && value !== undefined
      ? "server"
      : "unavailable") as SourceKind,
  });

  const projectId = projectValue(project?.projectId);
  const projectRoot = projectValue(project?.projectDir);
  const referenceRun = projectValue(project?.referenceRun.runId);
  const modelProvider = projectValue(
    project === null
      ? undefined
      : [project.intentProvider, project.intentModel]
          .filter((value): value is string => Boolean(value))
          .join(" · "),
  );
  const capabilities = server.capabilities.join(", ");

  return (
    <div
      className="settings-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        className="settings-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-description`}
        tabIndex={-1}
      >
        <header className="settings-panel__header">
          <div className="settings-panel__heading">
            <h1 id={`${id}-title`}>{t("settings.title")}</h1>
            <p id={`${id}-description`}>{t("settings.description")}</p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            className="settings-panel__close"
            onClick={onClose}
          >
            {t("common.close")}
          </button>
        </header>

        <div className="settings-panel__layout">
          <div
            className="settings-nav"
            role="tablist"
            aria-orientation="vertical"
            aria-label={t("settings.title")}
          >
            {SECTIONS.map((section) => (
              <button
                key={section}
                id={`${id}-tab-${section}`}
                type="button"
                role="tab"
                aria-selected={activeSection === section}
                aria-controls={`${id}-panel-${section}`}
                tabIndex={activeSection === section ? 0 : -1}
                onClick={() => setActiveSection(section)}
                onKeyDown={(event) => moveSectionFocus(event, section)}
              >
                {sectionLabel(section)}
              </button>
            ))}
          </div>

          <div className="settings-panel__content">
            <section
              id={`${id}-panel-appearance`}
              role="tabpanel"
              aria-labelledby={`${id}-tab-appearance`}
              hidden={activeSection !== "appearance"}
            >
              <h2>{sectionLabel("appearance")}</h2>
              <div className="settings-group">
                <div className="settings-row">
                  <label htmlFor={`${id}-language`} className="settings-row__label">
                    {t("settings.fields.language")}
                  </label>
                  <div className="settings-language-control">
                    <select
                      id={`${id}-language`}
                      value={language}
                      onChange={(event) => {
                        const nextLanguage = event.currentTarget.value as Language;
                        if (nextLanguage === "zh-CN") prepareDynamicChinese();
                        setLanguage(nextLanguage);
                      }}
                    >
                      <option value="en">{t("settings.options.en")}</option>
                      <option value="zh-CN">{t("settings.options.zhCN")}</option>
                    </select>
                    {language === "zh-CN" && (
                      <div className="settings-translation-preparation">
                        <button
                          type="button"
                          className="btn btn--small"
                          disabled={translationPreparation === "preparing"}
                          onClick={prepareDynamicChinese}
                        >
                          {translationPreparation === "unavailable"
                            ? t("settings.translation.retry")
                            : t("settings.translation.prepare")}
                        </button>
                        <span role="status" aria-live="polite">
                          {translationPreparation === "preparing"
                            ? t("settings.translation.preparing")
                            : translationPreparation === "ready"
                              ? t("settings.translation.ready")
                              : translationPreparation === "unavailable"
                                ? t("settings.translation.unavailable")
                                : t("settings.translation.optional")}
                        </span>
                      </div>
                    )}
                  </div>
                  <SourceBadge
                    source="browser"
                    label={sourceLabel("browser")}
                    title={sourceTitle("browser")}
                  />
                </div>

                <div className="settings-row">
                  <label htmlFor={`${id}-theme`} className="settings-row__label">
                    {t("settings.fields.theme")}
                  </label>
                  <select
                    id={`${id}-theme`}
                    value={theme}
                    onChange={(event) =>
                      setTheme(event.currentTarget.value as ThemePreference)
                    }
                  >
                    <option value="dark">{t("settings.options.dark")}</option>
                    <option value="light">{t("settings.options.light")}</option>
                    <option value="system">{t("settings.options.system")}</option>
                  </select>
                  <SourceBadge
                    source="browser"
                    label={sourceLabel("browser")}
                    title={sourceTitle("browser")}
                  />
                </div>

                <div className="settings-row">
                  <label htmlFor={`${id}-font-scale`} className="settings-row__label">
                    {t("settings.fields.fontScale")}
                  </label>
                  <select
                    id={`${id}-font-scale`}
                    value={fontScale}
                    onChange={(event) =>
                      setFontScale(Number(event.currentTarget.value) as FontScale)
                    }
                  >
                    <option value={0.9}>{t("settings.options.fontCompact")}</option>
                    <option value={1}>{t("settings.options.fontDefault")}</option>
                    <option value={1.1}>{t("settings.options.fontLarge")}</option>
                  </select>
                  <SourceBadge
                    source="browser"
                    label={sourceLabel("browser")}
                    title={sourceTitle("browser")}
                  />
                </div>
              </div>
            </section>

            <section
              id={`${id}-panel-project`}
              role="tabpanel"
              aria-labelledby={`${id}-tab-project`}
              hidden={activeSection !== "project"}
            >
              <h2>{sectionLabel("project")}</h2>
              <div className="settings-group">
                <ReadOnlySetting
                  label={t("settings.fields.projectId")}
                  value={projectId.value}
                  source={projectId.source}
                  sourceLabel={sourceLabel(projectId.source)}
                  sourceTitle={sourceTitle(projectId.source)}
                />
                <ReadOnlySetting
                  label={t("settings.fields.projectRoot")}
                  value={projectRoot.value}
                  source={projectRoot.source}
                  sourceLabel={sourceLabel(projectRoot.source)}
                  sourceTitle={sourceTitle(projectRoot.source)}
                />
                <ReadOnlySetting
                  label={t("settings.fields.referenceRun")}
                  value={referenceRun.value}
                  source={referenceRun.source}
                  sourceLabel={sourceLabel(referenceRun.source)}
                  sourceTitle={sourceTitle(referenceRun.source)}
                />
              </div>
            </section>

            <section
              id={`${id}-panel-model`}
              role="tabpanel"
              aria-labelledby={`${id}-tab-model`}
              hidden={activeSection !== "model"}
            >
              <h2>{sectionLabel("model")}</h2>
              <div className="settings-group">
                <ReadOnlySetting
                  label={t("settings.fields.modelProvider")}
                  value={modelProvider.value}
                  source={modelProvider.source}
                  sourceLabel={sourceLabel(modelProvider.source)}
                  sourceTitle={sourceTitle(modelProvider.source)}
                />
              </div>
            </section>

            <section
              id={`${id}-panel-geometry`}
              role="tabpanel"
              aria-labelledby={`${id}-tab-geometry`}
              hidden={activeSection !== "geometry"}
            >
              <h2>{sectionLabel("geometry")}</h2>
              <div className="settings-group">
                <ReadOnlySetting
                  label={t("settings.fields.geometryBackend")}
                  value={unavailable}
                  source="unavailable"
                  sourceLabel={sourceLabel("unavailable")}
                  sourceTitle={sourceTitle("unavailable")}
                />
              </div>
            </section>

            <section
              id={`${id}-panel-server`}
              role="tabpanel"
              aria-labelledby={`${id}-tab-server`}
              hidden={activeSection !== "server"}
            >
              <h2>{sectionLabel("server")}</h2>
              <div className="settings-group">
                <ReadOnlySetting
                  label={t("settings.fields.serverIdentity")}
                  value={`${server.server} · ${server.serverVersion} · ${server.mode}`}
                  source="server"
                  sourceLabel={sourceLabel("server")}
                  sourceTitle={sourceTitle("server")}
                />
                <ReadOnlySetting
                  label={t("settings.fields.apiVersion")}
                  value={server.protocol}
                  source="server"
                  sourceLabel={sourceLabel("server")}
                  sourceTitle={sourceTitle("server")}
                />
                <ReadOnlySetting
                  label={t("settings.fields.schemaVersion")}
                  value={unavailable}
                  source="unavailable"
                  sourceLabel={sourceLabel("unavailable")}
                  sourceTitle={sourceTitle("unavailable")}
                />
              </div>
            </section>

            <section
              id={`${id}-panel-diagnostics`}
              role="tabpanel"
              aria-labelledby={`${id}-tab-diagnostics`}
              hidden={activeSection !== "diagnostics"}
            >
              <h2>{sectionLabel("diagnostics")}</h2>
              <div className="settings-group">
                <div className="settings-row">
                  <label
                    htmlFor={`${id}-event-stream`}
                    className="settings-row__label"
                  >
                    {t("settings.fields.eventStreamVisible")}
                  </label>
                  <label className="settings-switch" htmlFor={`${id}-event-stream`}>
                    <input
                      id={`${id}-event-stream`}
                      type="checkbox"
                      role="switch"
                      checked={eventStreamVisible}
                      onChange={(event) =>
                        setEventStreamVisible(event.currentTarget.checked)
                      }
                    />
                    <span className="settings-switch__track" aria-hidden="true">
                      <span className="settings-switch__thumb" />
                    </span>
                    <span>
                      {eventStreamVisible ? t("common.on") : t("common.off")}
                    </span>
                  </label>
                  <SourceBadge
                    source="browser"
                    label={sourceLabel("browser")}
                    title={sourceTitle("browser")}
                  />
                </div>
                <ReadOnlySetting
                  label={t("settings.fields.diagnosticStatus")}
                  value={
                    capabilities.length > 0
                      ? `${t("settings.values.connected")} · ${capabilities}`
                      : t("settings.values.connected")
                  }
                  source="server"
                  sourceLabel={sourceLabel("server")}
                  sourceTitle={sourceTitle("server")}
                />
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}

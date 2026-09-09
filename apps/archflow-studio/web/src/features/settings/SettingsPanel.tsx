import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";

import type { ServerIdentity } from "../../api/connection";
import { asStudioApiError, studio, type StudioApiError } from "../../api/client";
import type { ProjectBindingDto, UserSettingsDto } from "../../api/generated";
import { ErrorPanel } from "../../app/ErrorPanel";
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

type SourceKind = "browser" | "server" | "user" | "unavailable";
type TranslationPreparation = "idle" | "preparing" | "ready" | "unavailable";

const SECTIONS: readonly SettingsSection[] = [
  "appearance",
  "project",
  "model",
  "geometry",
  "server",
  "diagnostics",
];
const DESIGN_SECTIONS: readonly SettingsSection[] = ["appearance", "model"];

export interface SettingsPanelProps {
  open: boolean;
  onClose(): void;
  server: ServerIdentity;
  project: ProjectBindingDto | null;
  modelInfo?: ReactNode;
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
  modelInfo,
}: SettingsPanelProps) {
  const t = useT();
  const {
    language,
    theme,
    fontScale,
    eventStreamVisible,
    developerMode,
    setLanguage,
    setTheme,
    setFontScale,
    setEventStreamVisible,
    setDeveloperMode,
  } = usePreferences();
  const [activeSection, setActiveSection] =
    useState<SettingsSection>("appearance");
  const [translationPreparation, setTranslationPreparation] =
    useState<TranslationPreparation>("idle");
  const userSettingsAvailable = server.mode === "local" && server.capabilities.includes("user-settings");
  const sections: readonly SettingsSection[] = developerMode ? SECTIONS : DESIGN_SECTIONS;
  const [savedDefaults, setSavedDefaults] = useState<UserSettingsDto | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<UserSettingsDto>({});
  const [timeoutDraft, setTimeoutDraft] = useState<string | null>(null);
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsSaved, setSettingsSaved] = useState(false);
  const [settingsError, setSettingsError] = useState<{
    error: StudioApiError; operation: "GET" | "PUT";
  } | null>(null);
  const settingsRequest = useRef(0);
  const settingsWrite = useRef(false);

  const readUserDefaults = useCallback(async () => {
    if (!userSettingsAvailable || settingsWrite.current) return;
    const request = ++settingsRequest.current;
    setSettingsLoading(true);
    setSettingsError(null);
    try {
      const defaults = await studio.userSettings();
      if (request === settingsRequest.current) setSavedDefaults(defaults);
    } catch (cause) {
      if (request === settingsRequest.current) {
        setSettingsError({ error: asStudioApiError(cause), operation: "GET" });
      }
    } finally {
      if (request === settingsRequest.current) setSettingsLoading(false);
    }
  }, [userSettingsAvailable]);

  useEffect(() => {
    if (userSettingsAvailable) void readUserDefaults();
    else setSavedDefaults(null);
    return () => { settingsRequest.current += 1; };
  }, [readUserDefaults, userSettingsAvailable]);

  useEffect(() => {
    if (!developerMode && !DESIGN_SECTIONS.includes(activeSection)) setActiveSection("appearance");
  }, [activeSection, developerMode]);

  const stageUserSetting = <K extends keyof UserSettingsDto>(key: K, value: UserSettingsDto[K]) => {
    if (!userSettingsAvailable) return;
    setSettingsDraft((current) => ({ ...current, [key]: value }));
    setSettingsSaved(false);
  };
  const nextLaunchDefaults: UserSettingsDto = { ...savedDefaults, ...settingsDraft };
  const timeoutValue = timeoutDraft ?? String(nextLaunchDefaults.intentTimeoutS ?? "");
  const timeoutNumber = timeoutValue === "" ? null : Number(timeoutValue);
  const invalidTimeout = timeoutNumber !== null && (!Number.isFinite(timeoutNumber) || timeoutNumber <= 0);
  const invalidModel = nextLaunchDefaults.intentModel != null && nextLaunchDefaults.intentModel.trim() === "";
  const settingsDirty = Object.keys(settingsDraft).length > 0 || timeoutDraft !== null;
  const invalidUserFile = settingsError?.operation === "GET" && settingsError.error.code === "USER_SETTINGS_INVALID";

  const saveUserDefaults = async (replaceInvalidFile = false) => {
    if (!userSettingsAvailable || settingsLoading || settingsWrite.current || !settingsDirty ||
        invalidTimeout || invalidModel || (replaceInvalidFile ? !invalidUserFile : savedDefaults === null || invalidUserFile)) return;
    settingsWrite.current = true;
    const request = ++settingsRequest.current;
    const submittedDraft = settingsDraft;
    const submittedTimeout = timeoutDraft;
    let operation: "GET" | "PUT" = "GET";
    setSettingsSaving(true);
    setSettingsSaved(false);
    if (!replaceInvalidFile) setSettingsError(null);
    try {
      // PUT replaces the whole file. Preserve fields another window changed
      // since this panel opened, while this action still submits only its draft.
      let latest: UserSettingsDto;
      try {
        latest = await studio.userSettings();
      } catch (cause) {
        const error = asStudioApiError(cause);
        if (!replaceInvalidFile || error.code !== "USER_SETTINGS_INVALID") throw error;
        // The separate replacement button explicitly discards only an unreadable
        // user file. A transport or permission error never becomes empty defaults.
        latest = {};
      }
      if (request !== settingsRequest.current) return;
      const body: UserSettingsDto = {
        ...latest, ...submittedDraft,
        ...(submittedTimeout === null ? {} : { intentTimeoutS: timeoutNumber }),
      };
      operation = "PUT";
      const defaults = await studio.saveUserSettings(body);
      if (request !== settingsRequest.current) return;
      setSavedDefaults(defaults);
      setSettingsError(null);
      // Only the submitted values were saved. Keep edits made while the PUT ran.
      setSettingsDraft((current) => {
        const remaining = { ...current };
        for (const field of Object.keys(submittedDraft) as Array<keyof UserSettingsDto>) {
          if (current[field] === submittedDraft[field]) delete remaining[field];
        }
        return remaining;
      });
      setTimeoutDraft((current) => current === submittedTimeout ? null : current);
      setSettingsSaved(true);
    } catch (cause) {
      if (request === settingsRequest.current) {
        setSettingsError({ error: asStudioApiError(cause), operation });
      }
    } finally {
      settingsWrite.current = false;
      setSettingsSaving(false);
    }
  };

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
    const currentIndex = sections.indexOf(section);
    let nextIndex: number | null = null;

    if (event.key === "ArrowDown" || event.key === "ArrowRight") {
      nextIndex = (currentIndex + 1) % sections.length;
    } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + sections.length) % sections.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = sections.length - 1;
    }

    if (nextIndex === null) return;
    event.preventDefault();
    const nextSection = sections[nextIndex];
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
  const appearanceSource = <K extends "language" | "theme" | "fontScale">(
    key: K, value: UserSettingsDto[K],
  ): SourceKind => userSettingsAvailable && savedDefaults?.[key] === value ? "user" : "browser";
  const savedAppearance = (key: "language" | "theme" | "fontScale", current: string | number) => {
    const saved = savedDefaults?.[key];
    if (!userSettingsAvailable || saved == null || saved === current) return null;
    const value = key === "language" ? t(saved === "zh-CN" ? "settings.options.zhCN" : "settings.options.en")
      : key === "theme" ? t(saved === "dark" ? "settings.options.dark" : saved === "light" ? "settings.options.light" : "settings.options.system")
        : t(saved === 0.9 ? "settings.options.fontCompact" : saved === 1.1 ? "settings.options.fontLarge" : "settings.options.fontDefault");
    return <p className="settings-row__help">{t("settings.user.savedDefault", { value })}</p>;
  };
  const defaultBadge = (key: keyof UserSettingsDto) => {
    const edited = key === "intentTimeoutS" ? timeoutDraft !== null : Object.hasOwn(settingsDraft, key);
    return savedDefaults?.[key] != null && !edited
      ? <SourceBadge source="user" label={sourceLabel("user")} title={sourceTitle("user")} />
      : null;
  };

  return (
    <div
      className="settings-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        className={`settings-panel${userSettingsAvailable ? " settings-panel--user-settings" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-description`}
        tabIndex={-1}
      >
        <header className="settings-panel__header">
          <div className="settings-panel__heading">
            <h1 id={`${id}-title`}>{t("settings.title")}</h1>
            <p id={`${id}-description`}>{t(userSettingsAvailable ? "settings.user.description" : "settings.description")}</p>
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
            {sections.map((section) => (
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
                        stageUserSetting("language", nextLanguage);
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
                    source={appearanceSource("language", language)}
                    label={sourceLabel(appearanceSource("language", language))}
                    title={sourceTitle(appearanceSource("language", language))}
                  />
                  {savedAppearance("language", language)}
                </div>

                <div className="settings-row">
                  <label htmlFor={`${id}-theme`} className="settings-row__label">
                    {t("settings.fields.theme")}
                  </label>
                  <select
                    id={`${id}-theme`}
                    value={theme}
                    onChange={(event) => {
                      const value = event.currentTarget.value as ThemePreference;
                      setTheme(value);
                      stageUserSetting("theme", value);
                    }}
                  >
                    <option value="dark">{t("settings.options.dark")}</option>
                    <option value="light">{t("settings.options.light")}</option>
                    <option value="system">{t("settings.options.system")}</option>
                  </select>
                  <SourceBadge
                    source={appearanceSource("theme", theme)}
                    label={sourceLabel(appearanceSource("theme", theme))}
                    title={sourceTitle(appearanceSource("theme", theme))}
                  />
                  {savedAppearance("theme", theme)}
                </div>

                <div className="settings-row">
                  <label htmlFor={`${id}-font-scale`} className="settings-row__label">
                    {t("settings.fields.fontScale")}
                  </label>
                  <select
                    id={`${id}-font-scale`}
                    value={fontScale}
                    onChange={(event) => {
                      const value = Number(event.currentTarget.value) as FontScale;
                      setFontScale(value);
                      stageUserSetting("fontScale", value);
                    }}
                  >
                    <option value={0.9}>{t("settings.options.fontCompact")}</option>
                    <option value={1}>{t("settings.options.fontDefault")}</option>
                    <option value={1.1}>{t("settings.options.fontLarge")}</option>
                  </select>
                  <SourceBadge
                    source={appearanceSource("fontScale", fontScale)}
                    label={sourceLabel(appearanceSource("fontScale", fontScale))}
                    title={sourceTitle(appearanceSource("fontScale", fontScale))}
                  />
                  {savedAppearance("fontScale", fontScale)}
                </div>
                <div className="settings-row">
                  <label
                    htmlFor={`${id}-developer-mode`}
                    className="settings-row__label"
                  >
                    {t("settings.fields.developerMode")}
                  </label>
                  <label className="settings-switch" htmlFor={`${id}-developer-mode`}>
                    <input
                      id={`${id}-developer-mode`}
                      type="checkbox"
                      role="switch"
                      checked={developerMode}
                      onChange={(event) => setDeveloperMode(event.currentTarget.checked)}
                    />
                    <span className="settings-switch__track" aria-hidden="true">
                      <span className="settings-switch__thumb" />
                    </span>
                    <span>{developerMode ? t("common.on") : t("common.off")}</span>
                  </label>
                  <SourceBadge
                    source="browser"
                    label={sourceLabel("browser")}
                    title={sourceTitle("browser")}
                  />
                  <p className="settings-row__help">
                    {t("settings.developerMode.help")}
                  </p>
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
              {modelInfo && <>
                <h3>{t("settings.currentModel")}</h3>
                <div className="settings-group settings-model-info">{modelInfo}</div>
              </>}
              <div className="settings-group">
                <ReadOnlySetting
                  label={t("settings.fields.modelProvider")}
                  value={modelProvider.value}
                  source={modelProvider.source}
                  sourceLabel={sourceLabel(modelProvider.source)}
                  sourceTitle={sourceTitle(modelProvider.source)}
                />
              </div>
              {userSettingsAvailable && <>
                <h3>{t("settings.user.nextLaunch")}</h3>
                <div className="settings-group">
                  <div className="settings-row">
                    <label htmlFor={`${id}-intent-provider`} className="settings-row__label">
                      {t("settings.user.provider")}
                    </label>
                    <select id={`${id}-intent-provider`} value={nextLaunchDefaults.intentProvider ?? ""}
                      onChange={(event) => stageUserSetting("intentProvider",
                        (event.currentTarget.value || null) as UserSettingsDto["intentProvider"])}>
                      <option value="">{t("settings.user.inherit")}</option>
                      <option value="deterministic">Deterministic</option>
                      <option value="codex">Codex</option>
                      <option value="anthropic">Anthropic</option>
                    </select>
                    {defaultBadge("intentProvider")}
                  </div>
                  <div className="settings-row">
                    <label htmlFor={`${id}-intent-model`} className="settings-row__label">
                      {t("settings.user.model")}
                    </label>
                    <input id={`${id}-intent-model`} type="text" value={nextLaunchDefaults.intentModel ?? ""}
                      placeholder={t("settings.user.inherit")} autoComplete="off"
                      aria-invalid={invalidModel || undefined}
                      aria-describedby={invalidModel ? `${id}-intent-model-error` : undefined}
                      onChange={(event) => stageUserSetting("intentModel", event.currentTarget.value || null)} />
                    {defaultBadge("intentModel")}
                    {invalidModel && <p id={`${id}-intent-model-error`} className="settings-row__help settings-row__error" role="alert">
                      {t("settings.user.invalidModel")}
                    </p>}
                  </div>
                  <div className="settings-row">
                    <label htmlFor={`${id}-intent-timeout`} className="settings-row__label">
                      {t("settings.user.timeout")}
                    </label>
                    <input id={`${id}-intent-timeout`} type="text" inputMode="decimal" value={timeoutValue}
                      placeholder={t("settings.user.inherit")} autoComplete="off"
                      aria-invalid={invalidTimeout || undefined}
                      aria-describedby={invalidTimeout ? `${id}-intent-timeout-error` : undefined}
                      onChange={(event) => { setTimeoutDraft(event.currentTarget.value); setSettingsSaved(false); }} />
                    {defaultBadge("intentTimeoutS")}
                    {invalidTimeout && <p id={`${id}-intent-timeout-error`} className="settings-row__help settings-row__error" role="alert">
                      {t("settings.user.invalidTimeout")}
                    </p>}
                  </div>
                </div>
              </>}
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
        {userSettingsAvailable && <footer className="settings-panel__footer">
          <p>{t("settings.user.help")}</p>
          {settingsError && <ErrorPanel error={settingsError.error}
            what={`${settingsError.operation} /api/settings/user`} />}
          <div className="settings-save-controls">
            <button className="btn btn--primary" type="button"
              disabled={savedDefaults === null || invalidUserFile || settingsLoading || settingsSaving || !settingsDirty || invalidTimeout || invalidModel}
              onClick={() => void saveUserDefaults()}>
              {t(settingsSaving ? "settings.user.saving" : "settings.user.save")}
            </button>
            {invalidUserFile && <button className="btn" type="button"
              disabled={settingsLoading || settingsSaving || !settingsDirty || invalidTimeout || invalidModel}
              onClick={() => void saveUserDefaults(true)}>
              {t("settings.user.replaceInvalid")}
            </button>}
            {settingsError?.operation === "GET" && <button className="btn" type="button"
              disabled={settingsLoading || settingsSaving} onClick={() => void readUserDefaults()}>
              {t("settings.user.retry")}
            </button>}
            <p role="status" aria-live="polite">
              {settingsLoading ? t("settings.user.loading") : settingsSaving ? t("settings.user.saving")
                : settingsDirty ? t("settings.user.unsaved") : settingsSaved ? t("settings.user.saved") : ""}
            </p>
          </div>
        </footer>}
      </div>
    </div>
  );
}

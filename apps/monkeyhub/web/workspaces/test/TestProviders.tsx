import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ProjectRuntimeProvider } from "../src/api/ProjectRuntimeContext";
import { UserPreferencesProvider as ControlledPreferences, usePreferences as useWorkspacePreferences } from "../src/features/settings/preferences";
import { applyAppearance, appearanceFromSearch, resolveAppearance, type AppearancePreferences, type Language, type ThemePreference } from "../../../../shared-web/src/appearance.js";

export { useStudio, useConnection } from "../src/api/ProjectRuntimeContext";

function storedFixturePreferences() {
  try { return JSON.parse(window.localStorage.getItem("archflow-studio.user-preferences") ?? "{}"); }
  catch { return {}; }
}

const TestAppearance = createContext<{
  setLanguage(language: Language): void;
  setTheme(theme: ThemePreference): void;
} | null>(null);

function SessionDiagnostics({ children }: { children: ReactNode }) {
  const preferences = useWorkspacePreferences();
  useEffect(() => {
    const stored = storedFixturePreferences();
    preferences.setDeveloperMode(stored.developerMode === true);
    preferences.setEventStreamVisible(stored.eventStreamVisible !== false);
  }, []);
  return children;
}

/** Test host for isolated components; production appearance always comes from Hub. */
export function UserPreferencesProvider({ children, baseUrl = "" }: {
  children: ReactNode;
  baseUrl?: string;
}) {
  const [appearance, setAppearance] = useState<AppearancePreferences>(() =>
    appearanceFromSearch(window.location.search, resolveAppearance(storedFixturePreferences(), { language: "en", theme: "light", fontScale: 1 })));
  useEffect(() => applyAppearance(appearance), [appearance]);
  const controls = useMemo(() => ({
    setLanguage: (language: Language) => setAppearance(value => ({ ...value, language })),
    setTheme: (theme: ThemePreference) => setAppearance(value => ({ ...value, theme })),
  }), []);
  return <TestAppearance.Provider value={controls}>
    <ProjectRuntimeProvider baseUrl={baseUrl}>
      <ControlledPreferences appearance={appearance}><SessionDiagnostics>{children}</SessionDiagnostics></ControlledPreferences>
    </ProjectRuntimeProvider>
  </TestAppearance.Provider>;
}

/** Legacy component fixtures can ask their test host to change its appearance. */
export function usePreferences() {
  const preferences = useWorkspacePreferences(), controls = useContext(TestAppearance);
  if (controls === null) throw new Error("The test appearance host is required.");
  return { ...preferences, ...controls };
}

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type Language = "en" | "zh-CN";
export type ThemePreference = "dark" | "light" | "system";
export type FontScale = 0.9 | 1 | 1.1;

export interface UserPreferences {
  readonly language: Language;
  readonly theme: ThemePreference;
  readonly fontScale: FontScale;
  readonly eventStreamVisible: boolean;
}

export interface UserPreferencesContextValue extends UserPreferences {
  setLanguage(language: Language): void;
  setTheme(theme: ThemePreference): void;
  setFontScale(fontScale: FontScale): void;
  setEventStreamVisible(visible: boolean): void;
  updatePreferences(patch: Partial<UserPreferences>): void;
}

const STORAGE_KEY = "archflow-studio.user-preferences";
const STORAGE_VERSION = 1 as const;

type StoredPreferences = UserPreferences & {
  readonly version: typeof STORAGE_VERSION;
};

const UserPreferencesContext = createContext<UserPreferencesContextValue | null>(
  null,
);

function isLanguage(value: unknown): value is Language {
  return value === "en" || value === "zh-CN";
}

function isTheme(value: unknown): value is ThemePreference {
  return value === "dark" || value === "light" || value === "system";
}

function isFontScale(value: unknown): value is FontScale {
  return value === 0.9 || value === 1 || value === 1.1;
}

function readStoredPreferences(): UserPreferences | null {
  if (typeof window === "undefined") return null;

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === null) return null;
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) return null;

    const stored = value as Record<string, unknown>;
    if (
      stored.version !== STORAGE_VERSION ||
      !isLanguage(stored.language) ||
      !isTheme(stored.theme) ||
      !isFontScale(stored.fontScale) ||
      typeof stored.eventStreamVisible !== "boolean"
    ) {
      return null;
    }

    return {
      language: stored.language,
      theme: stored.theme,
      fontScale: stored.fontScale,
      eventStreamVisible: stored.eventStreamVisible,
    };
  } catch {
    return null;
  }
}

function languageFromQuery(): Language | null {
  if (typeof window === "undefined") return null;
  const value = new URLSearchParams(window.location.search).get("lang");
  if (value === null) return null;
  if (value.toLowerCase() === "en") return "en";
  if (value.toLowerCase() === "zh-cn") return "zh-CN";
  return null;
}

function languageFromNavigator(): Language | null {
  if (typeof navigator === "undefined") return null;
  const candidates =
    navigator.languages.length > 0 ? navigator.languages : [navigator.language];

  for (const candidate of candidates) {
    const language = candidate.toLowerCase();
    if (language === "zh" || language.startsWith("zh-")) return "zh-CN";
    if (language === "en" || language.startsWith("en-")) return "en";
  }
  return null;
}

function initialPreferences(): UserPreferences {
  const stored = readStoredPreferences();
  return {
    language:
      languageFromQuery() ?? stored?.language ?? languageFromNavigator() ?? "en",
    theme: stored?.theme ?? "system",
    fontScale: stored?.fontScale ?? 1,
    eventStreamVisible: stored?.eventStreamVisible ?? true,
  };
}

function persistPreferences(preferences: UserPreferences): void {
  if (typeof window === "undefined") return;
  const stored: StoredPreferences = {
    version: STORAGE_VERSION,
    ...preferences,
  };

  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
  } catch {
    // Site data may be disabled; the current tab still keeps the preference.
  }
}

export function UserPreferencesProvider({ children }: { children: ReactNode }) {
  const [preferences, setPreferences] = useState<UserPreferences>(initialPreferences);

  useEffect(() => {
    if (typeof document !== "undefined") {
      const root = document.documentElement;
      root.lang = preferences.language;
      root.dataset.theme = preferences.theme;
      root.style.setProperty("--font-scale", String(preferences.fontScale));
    }
    persistPreferences(preferences);
  }, [preferences]);

  const setLanguage = useCallback((language: Language) => {
    setPreferences((current) => ({ ...current, language }));
  }, []);

  const setTheme = useCallback((theme: ThemePreference) => {
    setPreferences((current) => ({ ...current, theme }));
  }, []);

  const setFontScale = useCallback((fontScale: FontScale) => {
    setPreferences((current) => ({ ...current, fontScale }));
  }, []);

  const setEventStreamVisible = useCallback((eventStreamVisible: boolean) => {
    setPreferences((current) => ({ ...current, eventStreamVisible }));
  }, []);

  const updatePreferences = useCallback((patch: Partial<UserPreferences>) => {
    setPreferences((current) => ({ ...current, ...patch }));
  }, []);

  const value = useMemo<UserPreferencesContextValue>(
    () => ({
      ...preferences,
      setLanguage,
      setTheme,
      setFontScale,
      setEventStreamVisible,
      updatePreferences,
    }),
    [
      preferences,
      setLanguage,
      setTheme,
      setFontScale,
      setEventStreamVisible,
      updatePreferences,
    ],
  );

  return (
    <UserPreferencesContext.Provider value={value}>
      {children}
    </UserPreferencesContext.Provider>
  );
}

export function usePreferences(): UserPreferencesContextValue {
  const value = useContext(UserPreferencesContext);
  if (value === null) {
    throw new Error("usePreferences must be used inside UserPreferencesProvider.");
  }
  return value;
}

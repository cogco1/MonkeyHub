export type Language = "en" | "zh-CN";
export type ThemePreference = "dark" | "light" | "system";
export type FontScale = 0.9 | 1 | 1.1;

/** Personal display choices only. Project selection and editing bases stay in Studio. */
export interface AppearancePreferences {
  readonly language: Language;
  readonly theme: ThemePreference;
  readonly fontScale: FontScale;
}

export function applyAppearance(preferences: AppearancePreferences): void {
  const root = document.documentElement;
  root.lang = preferences.language;
  root.dataset.theme = preferences.theme;
  root.style.setProperty("--font-scale", String(preferences.fontScale));
}

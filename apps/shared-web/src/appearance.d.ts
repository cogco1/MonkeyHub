export type Language = "en" | "zh-CN";
export type ThemePreference = "dark" | "light" | "system";
export type FontScale = 0.9 | 1 | 1.1;
export interface AppearancePreferences {
  readonly language: Language;
  readonly theme: ThemePreference;
  readonly fontScale: FontScale;
}
export const DEFAULT_APPEARANCE: Readonly<AppearancePreferences>;
export function isLanguage(value: unknown): value is Language;
export function isTheme(value: unknown): value is ThemePreference;
export function isFontScale(value: unknown): value is FontScale;
export function resolveAppearance(value: unknown, fallback?: AppearancePreferences): AppearancePreferences;
export function appearanceFromSearch(search: string, fallback?: AppearancePreferences): AppearancePreferences;
export function applyAppearance(preferences: AppearancePreferences): void;
export function applicationUrl(url: string, preferences: AppearancePreferences): string;

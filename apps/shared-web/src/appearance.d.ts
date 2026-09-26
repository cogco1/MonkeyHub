export type Language = "en" | "zh-CN";
export type ThemePreference = "dark" | "light" | "system";
export type FontScale = 0.9 | 1 | 1.1;
/** How the interface is drawn (#328); classic is the original look. */
export type UiStyle = "classic" | "quiet" | "titleblock" | "night";
export interface AppearancePreferences {
  readonly language: Language;
  readonly theme: ThemePreference;
  readonly fontScale: FontScale;
  /** Absent means classic, so hosts that never offer the choice keep their look. */
  readonly uiStyle?: UiStyle;
}
export const DEFAULT_APPEARANCE: Readonly<AppearancePreferences>;
export function isLanguage(value: unknown): value is Language;
export function isTheme(value: unknown): value is ThemePreference;
export function isFontScale(value: unknown): value is FontScale;
export function isUiStyle(value: unknown): value is UiStyle;
export function resolveAppearance(value: unknown, fallback?: AppearancePreferences): AppearancePreferences;
export function appearanceFromSearch(search: string, fallback?: AppearancePreferences): AppearancePreferences;
export function applyAppearance(preferences: AppearancePreferences): void;
export function applicationUrl(url: string, preferences: AppearancePreferences): string;

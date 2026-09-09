/** Display choices only; this module never reads or writes project preferences. */
export const DEFAULT_APPEARANCE = Object.freeze({ language: "zh-CN", theme: "system", fontScale: 1 });

export function isLanguage(value) { return value === "en" || value === "zh-CN"; }
export function isTheme(value) { return value === "dark" || value === "light" || value === "system"; }
export function isFontScale(value) { return value === 0.9 || value === 1 || value === 1.1; }

export function resolveAppearance(value, fallback = DEFAULT_APPEARANCE) {
  return {
    language: isLanguage(value?.language) ? value.language : fallback.language,
    theme: isTheme(value?.theme) ? value.theme : fallback.theme,
    fontScale: isFontScale(value?.fontScale) ? value.fontScale : fallback.fontScale,
  };
}

/** An application opened from Hub inherits these explicit display choices once. */
export function appearanceFromSearch(search, fallback = DEFAULT_APPEARANCE) {
  const params = new URLSearchParams(search);
  return resolveAppearance({
    language: params.get("lang"), theme: params.get("theme"),
    fontScale: params.has("fontScale") ? Number(params.get("fontScale")) : undefined,
  }, fallback);
}

export function applyAppearance(preferences) {
  if (typeof document === "undefined") return;
  const appearance = resolveAppearance(preferences);
  document.documentElement.lang = appearance.language;
  document.documentElement.dataset.theme = appearance.theme;
  document.documentElement.style.setProperty("--font-scale", String(appearance.fontScale));
}

export function applicationUrl(url, preferences) {
  const target = new URL(url);
  const appearance = resolveAppearance(preferences);
  target.searchParams.set("lang", appearance.language);
  target.searchParams.set("theme", appearance.theme);
  target.searchParams.set("fontScale", String(appearance.fontScale));
  return target.href;
}

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { AppearancePreferences } from "../../../../../../shared-web/src/appearance.js";
export type { Language, ThemePreference, FontScale } from "../../../../../../shared-web/src/appearance.js";

export interface UserPreferences extends AppearancePreferences {
  readonly eventStreamVisible: boolean;
  readonly developerMode: boolean;
}

export interface UserPreferencesContextValue extends UserPreferences {
  setEventStreamVisible(visible: boolean): void;
  setDeveloperMode(enabled: boolean): void;
}

export interface EditingBasePreference {
  readonly runId: string;
  readonly sourceStageRef?: string | null;
  readonly branchId?: string | null;
}

const STORAGE_KEY = "archflow-studio.user-preferences";
const STORAGE_VERSION = 1 as const;

// Retain the existing record and its exact runtime/project keys. Legacy display
// fields are preserved on pointer writes, but never control the Hub's display.
type StoredPreferences = {
  readonly version: typeof STORAGE_VERSION;
  readonly editingBases?: unknown;
  readonly [key: string]: unknown;
};

const UserPreferencesContext = createContext<UserPreferencesContextValue | null>(null);

function readStoredPreferences(requireReadable = false): StoredPreferences | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === null) return null;
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      if (requireReadable) throw new Error("Invalid preference record");
      return null;
    }
    const stored = value as Record<string, unknown>;
    if (stored.version !== STORAGE_VERSION) {
      if (requireReadable) throw new Error("Unsupported preference record");
      return null;
    }
    return stored as StoredPreferences;
  } catch {
    if (requireReadable) {
      throw new Error("The saved editing choices could not be read. Retry or explicitly return to the default editing base.");
    }
    return null;
  }
}

function persistEditingBases(stored: StoredPreferences | null, editingBases: unknown): boolean {
  if (typeof window === "undefined") return false;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
      ...stored, version: STORAGE_VERSION, editingBases,
    }));
    return true;
  } catch {
    return false;
  }
}

function editingBases(stored: StoredPreferences | null): Record<string, unknown> {
  const bases = stored?.editingBases;
  if (bases === undefined) return {};
  if (typeof bases !== "object" || bases === null || Array.isArray(bases)) {
    throw new Error("The saved editing choices are unreadable. Retry or explicitly return to the default editing base.");
  }
  return bases as Record<string, unknown>;
}

function editingChoice(value: unknown): EditingBasePreference {
  const choice = typeof value === "string" ? { runId: value } : value;
  if (typeof choice !== "object" || choice === null || Array.isArray(choice)) {
    throw new Error("The saved editing choice is unreadable. Retry or explicitly return to the default editing base.");
  }
  const fields = choice as Record<string, unknown>;
  if (typeof fields.runId !== "string" || fields.runId.trim() === "" ||
    [fields.sourceStageRef, fields.branchId].some(field => field !== undefined && field !== null &&
      (typeof field !== "string" || field.trim() === ""))) {
    throw new Error("The saved editing choice is unreadable. Retry or explicitly return to the default editing base.");
  }
  return {
    runId: fields.runId,
    ...(fields.sourceStageRef === undefined ? {} : { sourceStageRef: fields.sourceStageRef as string | null }),
    ...(fields.branchId === undefined ? {} : { branchId: fields.branchId as string | null }),
  };
}

function readChoice(serverBaseUrl: string, projectId: string): EditingBasePreference | null {
  const bases = editingBases(readStoredPreferences(true));
  const key = JSON.stringify([serverBaseUrl, projectId]);
  return Object.hasOwn(bases, key) ? editingChoice(bases[key]) : null;
}

function writeChoice(serverBaseUrl: string, projectId: string, choice: EditingBasePreference | string | null): boolean {
  const stored = readStoredPreferences(true);
  const next = { ...editingBases(stored) };
  const key = JSON.stringify([serverBaseUrl, projectId]);
  if (choice === null) delete next[key];
  else {
    const valid = editingChoice(choice);
    next[key] = typeof choice === "string" ? valid.runId : valid;
  }
  return persistEditingBases(stored, next);
}

/** The same preference record owns these pointers; clearing site data clears them too. */
export const editingBasePreferences = {
  read(serverBaseUrl: string, projectId: string): string | null {
    return readChoice(serverBaseUrl, projectId)?.runId ?? null;
  },
  readChoice,
  write(serverBaseUrl: string, projectId: string, runId: string | null): boolean {
    return writeChoice(serverBaseUrl, projectId, runId);
  },
  writeChoice(serverBaseUrl: string, projectId: string, choice: EditingBasePreference | null): boolean {
    return writeChoice(serverBaseUrl, projectId, choice);
  },
};

/** Hub owns appearance; diagnostics here last only for this mounted session. */
export function UserPreferencesProvider({ appearance, children }: {
  appearance: AppearancePreferences;
  children: ReactNode;
}) {
  const [eventStreamVisible, setEventStreamVisible] = useState(true);
  const [developerMode, setDeveloperMode] = useState(false);
  const value = useMemo<UserPreferencesContextValue>(() => ({
    language: appearance.language,
    theme: appearance.theme,
    fontScale: appearance.fontScale,
    eventStreamVisible, developerMode, setEventStreamVisible, setDeveloperMode,
  }), [appearance.language, appearance.theme, appearance.fontScale, eventStreamVisible, developerMode]);
  return <UserPreferencesContext.Provider value={value}>{children}</UserPreferencesContext.Provider>;
}

export function usePreferences(): UserPreferencesContextValue {
  const value = useContext(UserPreferencesContext);
  if (value === null) throw new Error("usePreferences must be used inside UserPreferencesProvider.");
  return value;
}

/**
 * Developer mode and the event stream it shows, kept in the browser's preference record under their own
 * key: the record's legacy top-level display fields (an old `eventStreamVisible` among them) stay inert.
 */
export interface DeveloperPreferences {
  readonly mode: boolean;
  readonly eventStream: boolean;
}

export const DEVELOPER_DEFAULTS: DeveloperPreferences = { mode: false, eventStream: true };

/** The saved switches from a preference record; anything unreadable is the default. */
export function developerPreferences(record: unknown): DeveloperPreferences {
  const saved = typeof record === "object" && record !== null && !Array.isArray(record) ? (record as Record<string, unknown>).developer : undefined;
  if (typeof saved !== "object" || saved === null || Array.isArray(saved)) return DEVELOPER_DEFAULTS;
  const { mode, eventStream } = saved as Record<string, unknown>;
  return { mode: mode === true, eventStream: eventStream !== false };
}

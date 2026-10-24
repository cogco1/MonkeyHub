/**
 * The printer address MonkeyFab last reached, kept on this machine so the next upload does not ask for it again.
 * It is an address, not a secret: the LAN access code is typed each time and never kept. Like the shell choice it
 * lives in localStorage rather than in the user settings, and a browser that refuses storage just asks again.
 */

export const PRINTER_HOST_KEY = "monkeyhub.fab.printer-host";

type Store = Pick<Storage, "getItem" | "setItem">;

const storage = (): Store | null => { try { return window.localStorage; } catch { return null; } };

export function rememberedPrinterHost(store: Store | null = storage()): string {
  try { return store?.getItem(PRINTER_HOST_KEY)?.trim() ?? ""; } catch { return ""; }
}

/** Keep the address a check or an upload just reached; an empty one is not worth keeping. */
export function rememberPrinterHost(host: string, store: Store | null = storage()): void {
  const value = host.trim();
  if (!value) return;
  try { store?.setItem(PRINTER_HOST_KEY, value); } catch { /* the address is asked for again next time */ }
}

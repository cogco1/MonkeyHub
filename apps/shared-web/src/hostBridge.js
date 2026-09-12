/**
 * The one thing an embedded tool page asks its host for.
 *
 * A tool opened inside MonkeyHub can reach a capability it does not have
 * itself: the conversation bound to this project. It says so, and the host
 * decides what to do; the tool never navigates the host or sends anything on
 * the person's behalf.
 *
 * The host states its own origin in the page URL it opened (`host=`), and a
 * request is posted only to that exact origin. A page opened on its own has no
 * host, `hostOrigin()` answers null, and the tool offers what it can do alone.
 */

export const HOST_MESSAGE_SOURCE = "archflow-studio";
/** Bring the person back to this project's conversation, ready to type. */
export const START_MODELING = "start-modeling";

/** The host that embedded this page, if it named itself and is a real origin. */
export function hostOrigin(search = typeof location === "undefined" ? "" : location.search) {
  const declared = new URLSearchParams(search).get("host");
  if (!declared) return null;
  try {
    const url = new URL(declared);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.origin === declared || `${url.origin}/` === declared ? url.origin : null;
  } catch {
    return null;
  }
}

/** Ask the host for its conversation. Returns false when there is no host. */
export function requestStartModeling(origin = hostOrigin()) {
  if (!origin || typeof window === "undefined" || window.parent === window) return false;
  window.parent.postMessage({ source: HOST_MESSAGE_SOURCE, type: START_MODELING }, origin);
  return true;
}

/**
 * Read a request off a `message` event, once it is certain it came from the
 * page this host embedded: the same origin, and that page's own window.
 */
export function readHostRequest(event, expected) {
  if (!expected || event.origin !== expected.origin) return null;
  if (expected.window && event.source !== expected.window) return null;
  const data = event.data;
  if (!data || typeof data !== "object" || data.source !== HOST_MESSAGE_SOURCE) return null;
  return data.type === START_MODELING ? START_MODELING : null;
}

/**
 * The four states every panel in this shell can be in.
 *
 * `failed` exists so that a panel has somewhere to put the server's refusal.
 * Without it the only place a failed call could go is into an empty list, and
 * an empty list reads as "there is nothing", which is a different — and far
 * more confident — statement than "the server said no".
 */

import type { StudioApiError } from "../api/client";

export type Loadable<T> =
  | { readonly status: "idle" }
  | { readonly status: "loading" }
  | { readonly status: "ready"; readonly value: T }
  | { readonly status: "failed"; readonly error: StudioApiError };

export const idle = { status: "idle" } as const;
export const loading = { status: "loading" } as const;

export function ready<T>(value: T): Loadable<T> {
  return { status: "ready", value };
}

export function failed<T>(error: StudioApiError): Loadable<T> {
  return { status: "failed", error };
}

/** The value when there is one, and `null` in every other state. */
export function valueOf<T>(loadable: Loadable<T>): T | null {
  return loadable.status === "ready" ? loadable.value : null;
}

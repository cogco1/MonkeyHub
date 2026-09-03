/**
 * How this shell shortens the server's values for display, in one place.
 *
 * A digest is abbreviated for reading and never for comparing: every request
 * carries the full value the server sent, and nothing here is ever parsed back.
 */

/** The first eight characters of a digest, or an em dash when there is none. */
export function sha8(value: string | null | undefined): string {
  return value ? value.slice(0, 8) : "—";
}

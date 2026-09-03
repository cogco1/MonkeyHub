/**
 * The server's honesty lines, rendered verbatim.
 *
 * These sentences are the API saying what an answer does not cover. They are
 * not decoration on a complete answer and they are not summarised, shortened or
 * hidden behind a toggle here. An empty list is a real answer and says so.
 */

export function HonestyLines({
  lines,
  label = "honesty",
}: {
  lines: readonly string[];
  label?: string;
}) {
  return (
    <div className="honesty">
      <p className="honesty__label">{label}</p>
      {lines.length === 0 ? (
        <p className="honesty__empty">
          the server returned no honesty lines here
        </p>
      ) : (
        <ul className="honesty__lines">
          {lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

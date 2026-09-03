/**
 * Server sentences, one per line, exactly as they came. Empty renders nothing:
 * a card quotes what gates it, and an empty list gates nothing.
 */

export function Verbatim({
  lines,
  label,
}: {
  lines: readonly string[];
  label?: string;
}) {
  if (lines.length === 0) return null;
  return (
    <div className="verbatim">
      {label && <p className="label">{label}</p>}
      <ul>
        {lines.map((line, index) => (
          <li key={`${index}:${line}`}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

export function SystemLine({ text }: { text: string }) {
  return <p className="sys">{text}</p>;
}

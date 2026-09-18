/**
 * Server sentences, one per line, exactly as they came. Empty renders nothing:
 * a card quotes what gates it, and an empty list gates nothing.
 */

import { BilingualText } from "../../../i18n/BilingualText";
import type { SystemTextPart } from "../../../app/transcript";

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
          <li key={`${index}:${line}`}>
            <BilingualText source={line} showSourceToggle />
          </li>
        ))}
      </ul>
    </div>
  );
}

export function SystemLine({
  text,
  parts,
}: {
  text: string;
  parts?: readonly SystemTextPart[];
}) {
  return (
    <p className="sys">
      {parts === undefined ? (
        <span lang="en" translate="no">{text}</span>
      ) : (
        parts.map((part, index) =>
          part.kind === "prose" ? (
            <BilingualText key={`${index}:${part.text}`} source={part.text} />
          ) : (
            <span
              key={`${index}:${part.text}`}
              className={part.kind === "technical" ? "mono" : undefined}
              translate="no"
            >
              {part.text}
            </span>
          ),
        )
      )}
    </p>
  );
}

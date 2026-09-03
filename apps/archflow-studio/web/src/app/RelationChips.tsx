/**
 * The three-state chips, and the one rule that keeps them three.
 *
 * held is green only when something was actually held and nothing was violated
 * or left unchecked. violated is red. unchecked is amber and is never green:
 * "nothing was checked" is not "nothing was wrong", and a chip that blurred the
 * two would be this browser inventing a verdict the server did not issue.
 *
 * Every number here is copied off `RelationChecksDto`. Nothing is summed,
 * compared or inferred beyond choosing which of the three colours the *held*
 * chip may wear.
 */

import type { RelationChecksDto } from "../api/generated";

/** Whether the held count has earned green. Colour only; never a verdict. */
export function heldIsGreen(checks: RelationChecksDto): boolean {
  return checks.held > 0 && checks.violated === 0 && checks.unchecked === 0;
}

export function RelationChips({ checks }: { checks: RelationChecksDto }) {
  return (
    <div className="chips">
      <span
        className={`chip ${heldIsGreen(checks) ? "chip--held" : "chip--neutral"}`}
      >
        held {checks.held}
      </span>
      <span
        className={`chip ${checks.violated > 0 ? "chip--violated" : "chip--neutral"}`}
      >
        violated {checks.violated}
      </span>
      <span
        className={`chip ${checks.unchecked > 0 ? "chip--unchecked" : "chip--neutral"}`}
      >
        unchecked {checks.unchecked}
      </span>
      <span className="chip chip--flag">
        heldFlag {String(checks.heldFlag)}
      </span>
      <span className="chip chip--flag">
        fullyChecked {String(checks.fullyChecked)}
      </span>
    </div>
  );
}

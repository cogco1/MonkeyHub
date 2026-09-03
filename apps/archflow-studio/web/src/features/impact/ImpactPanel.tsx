/**
 * What the change reaches, as the kernel closed over it.
 *
 * Nothing on this screen is computed by the browser: `direct`, `propagated`,
 * `conflicts`, `locks` and `unknownCoverage` are the server's own lists.
 *
 * `unknownCoverage` is shown beside the closure rather than under it. The
 * components no dependency edge mentions are the ones this answer could not
 * speak about, and a panel that showed only the closure would render that
 * silence as safety.
 */

import type { ImpactDto } from "../../api/generated";
import { HonestyLines } from "../state/HonestyLines";

function RefList({ label, refs }: { label: string; refs: readonly string[] }) {
  return (
    <div className="impact__group">
      <p className="panel__subhead">
        {label} ({refs.length})
      </p>
      {refs.length === 0 ? (
        <p className="panel__note">the server returned none</p>
      ) : (
        <ul className="refs mono">
          {refs.map((ref) => (
            <li key={ref}>{ref}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ImpactPanel({ impact }: { impact: ImpactDto }) {
  return (
    <div className="impact">
      <RefList label="direct" refs={impact.direct} />
      <RefList label="propagated" refs={impact.propagated} />
      <RefList label="protected" refs={impact.protected} />
      <div className="impact__group">
        <p className="panel__subhead">conflicts ({impact.conflicts.length})</p>
        {impact.conflicts.length === 0 ? (
          <p className="panel__note">the server returned none</p>
        ) : (
          <ul className="refs mono">
            {impact.conflicts.map((ref) => (
              <li key={ref} className="is-conflict">
                {ref}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="impact__group">
        <p className="panel__subhead">locks ({impact.locks.length})</p>
        {impact.locks.length === 0 ? (
          <p className="panel__note">the server returned none</p>
        ) : (
          <ul className="refs mono">
            {impact.locks.map((lock) => (
              <li key={`${lock.ref}:${lock.authority}`}>
                {lock.ref} · {lock.authority}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="impact__group">
        <p className="panel__subhead">
          unknown coverage ({impact.unknownCoverage.count})
        </p>
        <p className="panel__note">
          components no dependency edge mentions: unknown impact, not zero
          impact.
        </p>
        {impact.unknownCoverage.componentIds.length > 0 && (
          <ul className="refs mono">
            {impact.unknownCoverage.componentIds.map((id) => (
              <li key={id}>{id}</li>
            ))}
          </ul>
        )}
      </div>
      <HonestyLines lines={impact.honesty} />
    </div>
  );
}

/**
 * What the selected component is made of, and what the record declares about it.
 *
 * The elements are the projection's own rows filtered by `componentId`; the
 * numbers under each are the authored producer fields, printed as they came.
 * The parameters are the record's declared parameters with the lock authority
 * the server attached — a lock is a fact about the record, not a hint.
 */

import type { StateProjectionDto } from "../../api/generated";

export function SelectionPanel({
  projection,
  selectedComponentId,
  selectedElementId,
  onSelectElement,
}: {
  projection: StateProjectionDto;
  selectedComponentId: string | null;
  selectedElementId: string | null;
  onSelectElement(elementId: string | null): void;
}) {
  if (selectedComponentId === null) {
    return (
      <p className="panel__note">
        nothing is selected. Click a component in the tree, or pick an object in
        the viewer.
      </p>
    );
  }
  const elements = projection.elements.filter(
    (element) => element.componentId === selectedComponentId,
  );
  return (
    <div className="selection">
      <p className="selection__head mono">{selectedComponentId}</p>

      <p className="panel__subhead">elements ({elements.length})</p>
      {elements.length === 0 ? (
        <p className="panel__note">
          this component declares no Element@1 rows in the projection.
        </p>
      ) : (
        <ul className="rows">
          {elements.map((element) => (
            <li key={element.elementId}>
              <button
                type="button"
                className={`row${
                  element.elementId === selectedElementId ? " is-selected" : ""
                }`}
                onClick={() =>
                  onSelectElement(
                    element.elementId === selectedElementId
                      ? null
                      : element.elementId,
                  )
                }
              >
                <span className="row__id">{element.elementId}</span>
                <span className="row__meta">{element.producer}</span>
                <span className="row__fields mono">
                  {Object.entries(element.numericFields)
                    .map(([key, value]) => `${key}=${value}`)
                    .join("  ") || "no numeric fields"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <p className="panel__subhead">
        record parameters ({projection.parameters.length})
      </p>
      {projection.parameters.length === 0 ? (
        <p className="panel__note">
          the record declares no parameters in this projection.
        </p>
      ) : (
        <ul className="rows">
          {projection.parameters.map((parameter) => (
            <li key={parameter.key} className="row row--static">
              <span className="row__id mono">{parameter.key}</span>
              <span className="row__meta">
                {parameter.value} {parameter.unit}
              </span>
              <span className="row__meta">{parameter.epistemicStatus}</span>
              {parameter.lockAuthority && (
                <span className="chip chip--lock">
                  lock {parameter.lockAuthority}
                </span>
              )}
              {parameter.expr && (
                <span className="row__fields mono">= {parameter.expr}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

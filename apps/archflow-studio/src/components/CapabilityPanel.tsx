import type { StudioCapability } from "../contracts/studio";

interface CapabilityPanelProps {
  capabilities: StudioCapability[];
  loading: boolean;
}

export function CapabilityPanel({
  capabilities,
  loading,
}: CapabilityPanelProps) {
  return (
    <section className="inspection-section" aria-labelledby="capability-heading">
      <div className="section-heading-row">
        <h2 id="capability-heading">ARCHFLOW BACKEND</h2>
        <span className="micro-label">BOUNDARIES</span>
      </div>
      {loading ? (
        <p className="panel-note">正在读取能力清单…</p>
      ) : capabilities.length === 0 ? (
        <p className="panel-note">Gateway 离线；本地 3DM 预览仍可使用。</p>
      ) : (
        <ul className="capability-list">
          {capabilities.map((capability) => (
            <li key={capability.id}>
              <span
                className={`availability availability--${capability.availability}`}
                aria-label={capability.availability}
              />
              <span>
                <strong>{capability.label}</strong>
                <small>{capability.detail}</small>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}


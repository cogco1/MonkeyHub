const STAGES = [
  { id: 1, label: "体块", detail: "Massing" },
  { id: 2, label: "粗略形态", detail: "Spatial character" },
  { id: 3, label: "系统占位", detail: "Envelope + systems" },
  { id: 4, label: "深化", detail: "Detail development" },
] as const;

interface StageRailProps {
  activeStage: 1 | 2 | 3 | 4 | null;
}

export function StageRail({ activeStage }: StageRailProps) {
  return (
    <section className="panel-section" aria-labelledby="stage-heading">
      <div className="section-heading-row">
        <h2 id="stage-heading">STAGES</h2>
        <span className="micro-label">只读</span>
      </div>
      <ol className="stage-list">
        {STAGES.map((stage) => (
          <li
            key={stage.id}
            className={activeStage === stage.id ? "is-active" : undefined}
          >
            <span className="stage-index">{String(stage.id).padStart(2, "0")}</span>
            <span>
              <strong>{stage.label}</strong>
              <small>{stage.detail}</small>
            </span>
            <span className="stage-state">
              {activeStage === stage.id ? "CURRENT" : "UNBOUND"}
            </span>
          </li>
        ))}
      </ol>
      <p className="panel-note">
        Stage 由 ArchFlow 状态决定。首版预览器不能推进或跳过阶段。
      </p>
    </section>
  );
}


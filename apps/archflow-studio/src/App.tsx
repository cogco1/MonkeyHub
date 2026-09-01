import { useEffect, useRef, useState } from "react";

import { CapabilityPanel } from "./components/CapabilityPanel";
import { StageRail } from "./components/StageRail";
import type { StudioGatewaySnapshot } from "./contracts/studio";
import { HttpStudioGateway } from "./gateway/HttpStudioGateway";
import {
  ThreeDmViewport,
  type ViewportController,
  type ViewportStatus,
} from "./viewer/ThreeDmViewport";
import type { SceneInspection } from "./viewer/sceneInspection";

const gateway = new HttpStudioGateway();

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && value >= 1024; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${unit}`;
}

function formatDimension(value: number): string {
  if (Math.abs(value) >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export default function App() {
  const viewportRef = useRef<ViewportController>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [gatewaySnapshot, setGatewaySnapshot] =
    useState<StudioGatewaySnapshot | null>(null);
  const [gatewayStatus, setGatewayStatus] = useState<
    "loading" | "online" | "offline"
  >("loading");
  const [inspection, setInspection] = useState<SceneInspection | null>(null);
  const [viewportStatus, setViewportStatus] = useState<ViewportStatus>("idle");
  const [viewportMessage, setViewportMessage] = useState(
    "拖入 .3dm，或从本机选择文件",
  );

  useEffect(() => {
    const controller = new AbortController();
    gateway
      .snapshot(controller.signal)
      .then((snapshot) => {
        setGatewaySnapshot(snapshot);
        setGatewayStatus(snapshot.health.status === "ok" ? "online" : "offline");
      })
      .catch(() => setGatewayStatus("offline"));
    return () => controller.abort();
  }, []);

  const requestFile = () => fileInputRef.current?.click();

  const openFile = (file: File) => {
    void viewportRef.current?.openFile(file);
  };

  return (
    <main className="studio-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true" />
          <div>
            <strong>ARCHFLOW STUDIO</strong>
            <span>PREVIEW SLICE 01</span>
          </div>
        </div>
        <div className="topbar-context">
          <span>LOCAL / READ ONLY</span>
          <span className={`gateway-status gateway-status--${gatewayStatus}`}>
            <i aria-hidden="true" />
            {gatewayStatus === "loading"
              ? "CONNECTING"
              : gatewayStatus === "online"
                ? "ARCHFLOW READY"
                : "GATEWAY OFFLINE"}
          </span>
        </div>
        <button className="button button--primary" type="button" onClick={requestFile}>
          打开 3DM
        </button>
        <input
          ref={fileInputRef}
          className="visually-hidden"
          type="file"
          accept=".3dm"
          aria-label="选择 Rhino 3DM 文件"
          onChange={(event) => {
            const file = event.currentTarget.files?.item(0);
            if (file) openFile(file);
            event.currentTarget.value = "";
          }}
        />
      </header>

      <aside className="launcher-panel" aria-label="项目与阶段">
        <section className="panel-section" aria-labelledby="source-heading">
          <div className="section-heading-row">
            <h2 id="source-heading">MODEL SOURCE</h2>
            <span className="micro-label">LOCAL</span>
          </div>
          <button className="source-card" type="button" onClick={requestFile}>
            <span className="source-type">3DM</span>
            <span>
              <strong>{inspection?.fileName ?? "选择本机模型"}</strong>
              <small>
                {inspection
                  ? `${formatBytes(inspection.fileSize)} · 未上传`
                  : "拖入或浏览文件，数据留在浏览器"}
              </small>
            </span>
          </button>
        </section>

        <StageRail activeStage={gatewaySnapshot?.session.stage ?? null} />

        <section className="panel-section panel-section--boundary">
          <h2>AUTHORITY</h2>
          <dl className="authority-list">
            <div>
              <dt>Viewer</dt>
              <dd>READ</dd>
            </div>
            <div>
              <dt>Validator override</dt>
              <dd>NONE</dd>
            </div>
            <div>
              <dt>Canonical write</dt>
              <dd>NONE</dd>
            </div>
          </dl>
        </section>
      </aside>

      <section className="workspace" aria-label="3DM 预览工作台">
        <div className="viewport-toolbar">
          <div>
            <span className={`viewport-dot viewport-dot--${viewportStatus}`} />
            <span aria-live="polite">{viewportMessage}</span>
          </div>
          <div className="toolbar-actions">
            <button
              className="button button--quiet"
              type="button"
              disabled={!inspection}
              aria-label="保持当前方位并切换到水平正视图"
              onClick={() => viewportRef.current?.frontView()}
            >
              正视
            </button>
            <button
              className="button button--quiet"
              type="button"
              disabled={!inspection}
              onClick={() => viewportRef.current?.fitView()}
            >
              适应视图
            </button>
            <button
              className="button button--quiet"
              type="button"
              disabled={!inspection}
              onClick={() => viewportRef.current?.clear()}
            >
              清除
            </button>
          </div>
        </div>
        <ThreeDmViewport
          ref={viewportRef}
          onInspection={setInspection}
          onStatus={(status, message) => {
            setViewportStatus(status);
            setViewportMessage(message);
          }}
          onRequestFile={requestFile}
        />
        <div className="viewport-footer">
          <span>左键旋转 · 右键平移 · 滚轮缩放</span>
          <span>Z-UP · THREE.JS / RHINO3DM</span>
        </div>
      </section>

      <aside className="inspection-panel" aria-label="模型检查">
        <section className="inspection-section" aria-labelledby="inspect-heading">
          <div className="section-heading-row">
            <h2 id="inspect-heading">MODEL INSPECTION</h2>
            <span className="micro-label">{inspection ? "LOADED" : "EMPTY"}</span>
          </div>
          {inspection ? (
            <>
              <dl className="stat-grid">
                <div>
                  <dt>Objects</dt>
                  <dd>{inspection.objectCount.toLocaleString()}</dd>
                </div>
                <div>
                  <dt>Meshes</dt>
                  <dd>{inspection.meshCount.toLocaleString()}</dd>
                </div>
                <div>
                  <dt>Triangles</dt>
                  <dd>{inspection.triangleCount.toLocaleString()}</dd>
                </div>
                <div>
                  <dt>Vertices</dt>
                  <dd>{inspection.vertexCount.toLocaleString()}</dd>
                </div>
                <div>
                  <dt>Curves</dt>
                  <dd>{inspection.curveCount.toLocaleString()}</dd>
                </div>
                <div>
                  <dt>Decode</dt>
                  <dd>{inspection.loadDurationMs.toLocaleString()} ms</dd>
                </div>
              </dl>
              {inspection.bounds && (
                <div className="bounds-readout">
                  <span>BOUNDING SIZE</span>
                  <strong>
                    {inspection.bounds.size.map(formatDimension).join(" × ")}
                  </strong>
                  <small>模型原单位</small>
                </div>
              )}
              {inspection.warnings.length > 0 && (
                <div className="warning-block">
                  <strong>Loader warnings</strong>
                  <ul>
                    {inspection.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          ) : (
            <p className="empty-copy">
              打开模型后，这里显示对象、网格、三角面、边界和解析警告。
            </p>
          )}
        </section>

        <section className="inspection-section" aria-labelledby="layers-heading">
          <div className="section-heading-row">
            <h2 id="layers-heading">LAYERS</h2>
            <span className="micro-label">{inspection?.layers.length ?? 0}</span>
          </div>
          {inspection && inspection.layers.length > 0 ? (
            <ul className="layer-list">
              {inspection.layers.map((layer) => (
                <li key={layer.index}>
                  <label>
                    <input
                      type="checkbox"
                      checked={layer.visible}
                      onChange={(event) => {
                        const visible = event.currentTarget.checked;
                        viewportRef.current?.setLayerVisibility(layer.index, visible);
                        setInspection((current) =>
                          current
                            ? {
                                ...current,
                                layers: current.layers.map((item) =>
                                  item.index === layer.index ? { ...item, visible } : item,
                                ),
                              }
                            : current,
                        );
                      }}
                    />
                    <span className="layer-swatch" aria-hidden="true" />
                    <span>
                      <strong>{layer.name}</strong>
                      <small>{layer.objectCount.toLocaleString()} objects</small>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          ) : (
            <p className="empty-copy">尚无可检查图层。</p>
          )}
        </section>

        <CapabilityPanel
          capabilities={gatewaySnapshot?.capabilities.capabilities ?? []}
          loading={gatewayStatus === "loading"}
        />
      </aside>
    </main>
  );
}

import { useEffect, useRef, useState } from "react";
import { ACESFilmicToneMapping, SRGBColorSpace, WebGLRenderer } from "three";
import { previewSize, type RenderView } from "../monkeyarch/viewer/renderView";

export default function ModelPreview({ active, readView, onModeling, onCapture, capturing, zh }: {
  active: boolean; readView?: () => RenderView | null; onModeling?: () => void; onCapture(): void; capturing: boolean; zh: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [available, setAvailable] = useState(false);
  const [failed, setFailed] = useState(false);
  const [sourceIssue, setSourceIssue] = useState<RenderView["sourceIssue"]>("unbound");
  useEffect(() => {
    if (!active || !host.current || !readView) return;
    const element = host.current;
    let renderer: WebGLRenderer | null = null, frame = 0, stopped = false;
    let lastWidth = 0, lastHeight = 0;
    setFailed(false);
    const draw = () => {
      if (stopped) return;
      try {
        const view = readView();
        setAvailable(Boolean(view));
        setSourceIssue(view?.sourceIssue ?? (view?.modelSource ? null : "unbound"));
        if (view) {
          if (!renderer) {
            renderer = new WebGLRenderer({ antialias: true });
            renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
            renderer.outputColorSpace = SRGBColorSpace;
            renderer.toneMapping = ACESFilmicToneMapping;
            renderer.domElement.setAttribute("aria-label", "Modeling camera preview");
            renderer.domElement.setAttribute("role", "img");
            element.append(renderer.domElement);
          }
          const [width, height] = previewSize(element.clientWidth, element.clientHeight, view.aspect);
          if (width !== lastWidth || height !== lastHeight) {
            renderer.setSize(width, height); lastWidth = width; lastHeight = height;
          }
          renderer.toneMappingExposure = view.exposure;
          renderer.domElement.hidden = false;
          renderer.render(view.scene, view.camera);
        } else if (renderer) renderer.domElement.hidden = true;
      } catch {
        setFailed(true);
        if (renderer) renderer.domElement.hidden = true;
        return;
      }
      frame = window.setTimeout(draw, 100);
    };
    draw();
    return () => {
      stopped = true; window.clearTimeout(frame);
      // Only this renderer is ours. Scene, geometries and materials belong to Modeling.
      renderer?.dispose(); renderer?.forceContextLoss(); renderer?.domElement.remove();
    };
  }, [active, readView]);
  return <section className="render-model-preview" aria-label={zh ? "建模视角" : "Modeling view"}>
    <header><strong>{zh ? "建模视角 · 实时预览" : "Modeling view · Live preview"}</strong>
      <div className="render-model-actions"><button type="button" onClick={onModeling}>{zh ? "前往建模调整视角" : "Adjust view in Modeling"}</button>
        <button type="button" disabled={!active || !available || failed || Boolean(sourceIssue) || capturing} onClick={onCapture}>
          {capturing ? (zh ? "正在保存底图…" : "Saving source…") : (zh ? "使用当前视角作为底图" : "Use current view as source")}</button></div></header>
    <div ref={host} className="render-model-canvas" hidden={!available || failed} />
    {(!available || failed) && <p role="status">{failed
      ? (zh ? "预览暂时不可用，请返回建模后重试。" : "Preview unavailable. Return to Modeling and try again.")
      : (zh ? "先在建模中打开模型并调整视角，再进入渲染。" : "Open a model and set its view in Modeling, then enter Render.")}</p>}
    {available && sourceIssue && <small role="status">{sourceIssue === "unsaved"
      ? (zh ? "先在建模中同步修改并打开候选，再使用这个视角。" : "Sync edits and open the saved candidate in Modeling before using this view.")
      : sourceIssue === "loading" ? (zh ? "模型正在加载。" : "The model is loading.")
        : (zh ? "请打开一个已保留的模型版本作为底图来源。" : "Open one retained model version to use this view as a source.")}</small>}
  </section>;
}

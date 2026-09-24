import { useEffect, useRef, useState } from "react";
import { ACESFilmicToneMapping, SRGBColorSpace, WebGLRenderer } from "three";
import { previewSize, type RenderView } from "../monkeyarch/viewer/renderView";

export default function ModelPreview({ active, readView, onModeling, zh }: {
  active: boolean; readView?: () => RenderView | null; onModeling?: () => void; zh: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [available, setAvailable] = useState(false);
  const [failed, setFailed] = useState(false);
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
      <button type="button" onClick={onModeling}>{zh ? "前往建模调整视角" : "Adjust view in Modeling"}</button></header>
    <div ref={host} className="render-model-canvas" hidden={!available || failed} />
    {(!available || failed) && <p role="status">{failed
      ? (zh ? "预览暂时不可用，请返回建模后重试。" : "Preview unavailable. Return to Modeling and try again.")
      : (zh ? "先在建模中打开模型并调整视角，再进入渲染。" : "Open a model and set its view in Modeling, then enter Render.")}</p>}
    <small>{zh ? "与建模共用场景；保留原画幅。此预览尚未作为 AI 渲染输入。" : "Shared Modeling scene; original framing preserved. This preview is not yet an AI image input."}</small>
  </section>;
}

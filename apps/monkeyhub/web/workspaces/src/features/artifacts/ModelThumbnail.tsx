import { useEffect, useRef, useState } from "react";
import { useStudio } from "../../api/ProjectRuntimeContext";
import type { ModelSourceDto } from "../../api/generated";
import { MODEL_PREVIEW_RETAINED, previewSourceKey } from "./useRetainedModelPreview";
import "./modelThumbnail.css";

/** A retained image is optional; no source or failed reads always show the model icon. */
export function ModelThumbnail({ source }: { source: ModelSourceDto | null | undefined }) {
  const studio = useStudio();
  const host = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false);
  const [revision, setRevision] = useState(0);
  const [image, setImage] = useState<{ key: string; url: string } | null>(null);
  const key = previewSourceKey(source);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    if (host.current) observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const retained = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (detail.studio === studio && detail.key === key) setRevision(value => value + 1);
    };
    window.addEventListener(MODEL_PREVIEW_RETAINED, retained);
    return () => window.removeEventListener(MODEL_PREVIEW_RETAINED, retained);
  }, [studio, key]);
  useEffect(() => {
    if (!visible || !source) return;
    let live = true, url: string | null = null;
    setImage(null);
    void studio.modelPreview(source).then(async (document) => {
      if (!live || !document || previewSourceKey(document.modelSource) !== key) return;
      const file = await studio.documentFile(document.runId, document.assetSha256, document.fileName, document.revisionRef);
      if (!live) return;
      url = URL.createObjectURL(file);
      const decoded = new Image(); decoded.src = url;
      await decoded.decode();
      if (live) setImage({ key, url });
    }).catch(() => { if (live) setImage(null); });
    return () => { live = false; if (url) URL.revokeObjectURL(url); };
  }, [studio, key, visible, revision]);
  const url = image?.key === key ? image.url : null;
  return <span ref={host} className="model-thumbnail" data-preview-source={key} aria-hidden="true">
    {url ? <img src={url} alt="" loading="lazy" onError={() => setImage(null)} />
      : <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.3"><path d="m12 3 9 5v8l-9 5-9-5V8Zm0 10 9-5M12 13 3 8m9 5v8M7 5.8l9 5" /></svg>}
  </span>;
}

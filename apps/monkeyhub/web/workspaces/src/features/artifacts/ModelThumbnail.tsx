import { useEffect, useRef, useState } from "react";
import { useProjectStore, useStudio } from "../../api/ProjectRuntimeContext";
import type { ModelSourceDto } from "../../api/generated";
import { MODEL_PREVIEW_RETAINED, previewSourceKey, readModelPreview } from "./useRetainedModelPreview";
import { askThumbnail, askedThumbnail, thumbnailBlobs, thumbnailImage, thumbnailsMoved } from "./modelThumbnails";
import "./modelThumbnail.css";

/** The model's retained viewport screenshot (#326) where one is; no source or failed reads always show the model icon. */
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
    void readModelPreview(studio, source).then(async (preview) => {
      if (!live || !preview) return;
      url = URL.createObjectURL(preview.file);
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

/**
 * The model's blob digest: the store's thumbnail, else a done answer this page was given, else null.
 * A model in view without either is asked for once; the store brings the thumbnail when it is drawn.
 */
export function useThumbnailBlob(source: ModelSourceDto | null | undefined, visible: boolean): string | null {
  const studio = useStudio();
  const held = useProjectStore((state) => source ? thumbnailBlobs(state).get(source.assetSha256) ?? null : null);
  const [answered, setAnswered] = useState<{ key: string; blob: string } | null>(null);
  const key = previewSourceKey(source);
  useEffect(() => {
    if (!visible || !source || held) return;
    let live = true;
    void (askedThumbnail(studio, source) ?? askThumbnail(studio, source)).then((blob) => {
      if (live && blob) setAnswered({ key, blob });
    });
    return () => { live = false; };
  }, [studio, key, visible, held]);
  return held ?? (answered?.key === key ? answered.blob : null);
}

/**
 * The model's server thumbnail (#367), as the Design Tree's canvas draws it: one download and one decode per blob,
 * shared with the canvas (#409). No source, a pending drawing or a failed read shows the model icon.
 */
export function ProjectionThumbnail({ source }: { source: ModelSourceDto | null | undefined }) {
  const studio = useStudio();
  const host = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false);
  const [image, setImage] = useState<{ blob: string; url: string } | null>(null);
  const key = previewSourceKey(source);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    if (host.current) observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  const blob = useThumbnailBlob(source, visible);
  // A read that failed (the server was drawing the blob again) is tried again when a projection lands.
  const moved = useProjectStore(thumbnailsMoved);
  useEffect(() => {
    if (!visible || !blob) return;
    let live = true;
    // One download and one decode per blob, shared with the Design Tree's canvas and every other thumbnail.
    void thumbnailImage(studio, blob).then((decoded) => { if (live && decoded) setImage({ blob, url: decoded.url }); });
    return () => { live = false; };
  }, [studio, blob, visible, moved]);
  const url = image && image.blob === blob ? image.url : null;
  return <span ref={host} className="model-thumbnail" data-preview-source={key} data-thumbnail={url ? blob ?? undefined : undefined} aria-hidden="true">
    {url ? <img src={url} alt="" onError={() => setImage(null)} />
      : <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.3"><path d="m12 3 9 5v8l-9 5-9-5V8Zm0 10 9-5M12 13 3 8m9 5v8M7 5.8l9 5" /></svg>}
  </span>;
}

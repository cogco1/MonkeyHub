import { useEffect, useRef, useState } from "react";
import { useProjectStore, useStudio } from "../../api/ProjectRuntimeContext";
import type { ModelSourceDto } from "../../api/generated";
import { askThumbnail, askedThumbnail, thumbnailBlobs, thumbnailImage } from "./modelThumbnails";
import { previewSourceKey } from "./useRetainedModelPreview";
import "./modelThumbnail.css";

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

/** The model's server thumbnail (#367); no source, a pending drawing or a failed read shows the model icon. */
export function ModelThumbnail({ source }: { source: ModelSourceDto | null | undefined }) {
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
  useEffect(() => {
    if (!visible || !blob) return;
    let live = true;
    // One download and one decode per blob, shared with the Design Tree's canvas and every other thumbnail.
    void thumbnailImage(studio, blob).then((decoded) => { if (live && decoded) setImage({ blob, url: decoded.url }); });
    return () => { live = false; };
  }, [studio, blob, visible]);
  const url = image && image.blob === blob ? image.url : null;
  return <span ref={host} className="model-thumbnail" data-preview-source={key} data-thumbnail={url ? blob ?? undefined : undefined} aria-hidden="true">
    {url ? <img src={url} alt="" onError={() => setImage(null)} />
      : <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.3"><path d="m12 3 9 5v8l-9 5-9-5V8Zm0 10 9-5M12 13 3 8m9 5v8M7 5.8l9 5" /></svg>}
  </span>;
}

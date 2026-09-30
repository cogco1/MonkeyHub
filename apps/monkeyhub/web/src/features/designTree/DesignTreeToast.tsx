/**
 * An act that changed the design confirms itself beside the chip (FN-5):
 * "Current is now “Entrance B” · Undo" after Continue, "Accepted as S3" after
 * Accept as next Stage. It stays about eight seconds, and as long as the
 * pointer or the keyboard is on it; a refusal is never a toast.
 */
import { useEffect, useRef, useState } from "react";
import { usePreferences } from "../settings/preferences";
import { useT } from "../../i18n/useT";
import { DESIGN_TREE_UNSYNCED } from "./continueUndo";
import type { DesignTreeData, DesignTreeToast as Toast } from "./useDesignTree";
import { actionWords, refusalWords, type TreeWords } from "./words";

/** How long a toast stays once nothing holds it. */
export const DESIGN_TREE_TOAST_MS = 8_000;
/** After the pointer or focus lets go of it, it stays this long more. */
const LINGER_MS = 2_000;
const CHECK_MS = 250;
const FADE_MS = 240;

export function DesignTreeToast({ data, toast, words }: { data: DesignTreeData; toast: Toast; words: TreeWords }) {
  const t = useT();
  const { language } = usePreferences();
  const copy = actionWords(language);
  const box = useRef<HTMLDivElement>(null);
  const [leaving, setLeaving] = useState(false);
  const undoing = data.busy?.kind === "undo";
  const held = useRef(undoing);
  held.current = undoing;
  const dismiss = useRef(data.dismissToast);
  dismiss.current = data.dismissToast;
  // One toast, one time on screen: hovering, focus or its running Undo holds it; it then fades.
  useEffect(() => {
    let release = Date.now() + DESIGN_TREE_TOAST_MS;
    let fade = 0;
    const timer = window.setInterval(() => {
      const element = box.current;
      if (held.current || (element && (element.matches(":hover") || element.contains(document.activeElement)))) {
        release = Math.max(release, Date.now() + LINGER_MS);
        if (fade) { window.clearTimeout(fade); fade = 0; setLeaving(false); }
        return;
      }
      if (fade || Date.now() < release) return;
      setLeaving(true);
      fade = window.setTimeout(() => dismiss.current(toast.id), FADE_MS);
    }, CHECK_MS);
    return () => { window.clearInterval(timer); window.clearTimeout(fade); };
  }, [toast.id]);

  // An Undo held back by edits made since the Continue finds Current changed, like one another window moved.
  const refusal = toast.kind === "continued" && toast.refusal ? toast.refusal.code === DESIGN_TREE_UNSYNCED ? copy("undoMoved")
    : refusalWords(t, language, toast.refusal) : null;
  const text = refusal ?? (toast.kind === "continued" ? copy("continued", { name: words.title(toast.node) })
    : toast.kind === "accepted" ? copy("accepted", { stage: toast.stage }) : copy("undone"));
  return <div ref={box} className="design-tree-toast" data-kind={toast.kind} data-refused={refusal ? "" : undefined}
    data-leaving={leaving ? "" : undefined}>
    <span className="design-tree-toast__text">{text}</span>
    {toast.kind === "continued" && toast.undo && <>
      <span aria-hidden="true">·</span>
      <button type="button" className="design-tree-toast__undo" data-action="undo" disabled={data.busy !== null}
        onClick={() => void data.undo()}>{undoing ? copy("undoing") : copy("undo")}</button>
    </>}
  </div>;
}

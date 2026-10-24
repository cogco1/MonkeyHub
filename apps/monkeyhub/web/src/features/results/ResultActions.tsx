import { usePreferences } from "../settings/preferences";
import "./results.css";

const copy = {
  en: { label: "This result", board: "Send to Board", layout: "Put in Layout" },
  "zh-CN": { label: "这张图", board: "送到画板", layout: "放进排版" },
} as const;

/** The page's own download: a file it has read (none yet while it reads), or a file it writes when pressed. */
export type ResultDownload = { command: string; label: string; title?: string; disabled?: boolean }
  & ({ href: string | null; fileName: string } | { onDownload(): void });

/**
 * ADR-014 B5: what a finished drawing or render offers where it shows, the same three actions in the same order:
 * download it, send it to the Board, put it in the layout. Each is one press beside the result, so none of them
 * waits in a menu; the Board and the layout open with the result already there.
 */
export function ResultActions({ download, onBoard, onLayout, disabled = false }: {
  /** Absent where the page has no file of its own to give for this result. */
  download?: ResultDownload;
  onBoard?: () => void;
  onLayout?: () => void;
  /** The result is not saved yet, or is being written: nothing leaves the page until it is. */
  disabled?: boolean;
}) {
  const { language } = usePreferences(), text = copy[language];
  const off = disabled || Boolean(download?.disabled) || (download !== undefined && "href" in download && !download.href);
  return <span className="result-actions" role="group" aria-label={text.label}>
    {!download ? null : "href" in download
      ? off ? <button data-command={download.command} type="button" disabled title={download.title}>{download.label}</button>
        : <a data-command={download.command} href={download.href ?? undefined} download={download.fileName} title={download.title}>{download.label}</a>
      : <button data-command={download.command} type="button" disabled={off} title={download.title} onClick={download.onDownload}>{download.label}</button>}
    {onBoard && <button data-command="board.place_image" type="button" disabled={disabled} onClick={onBoard}>{text.board}</button>}
    {onLayout && <button data-command="view.layout.place_result" type="button" disabled={disabled} onClick={onLayout}>{text.layout}</button>}
  </span>;
}

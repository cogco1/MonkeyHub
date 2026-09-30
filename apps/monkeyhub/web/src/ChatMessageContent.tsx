import { Fragment, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { marked, type Token, type Tokens } from "marked";
import type { ChatAttachment, ChatMessage } from "./api/generated";
import { ChatCard } from "./ChatCard";
import "./ChatMessageContent.css";

export type ChatDocument = NonNullable<ChatMessage["documents"]>[number];
type Labels = { attachments: string; previewImage: string; close: string; download: string;
  imageLoading: string; imageFailed: string; openDocument: string; filesTitle: string;
  fileCount: (count: number) => string; fileDetails: (count: number) => string;
  filePage: (page: number) => string; fileReference: (index: number) => string;
  renderRoleSource: string; renderRoleReference: string };

/** Model text never supplies executable markup, image URLs or application routes. */
function safeLink(value: string): string | undefined {
  if (!/^(https?:\/\/|mailto:)/i.test(value) || /[\u0000-\u0020\u007f]/.test(value)) return undefined;
  try { const url = new URL(value); return ["https:", "http:", "mailto:"].includes(url.protocol) ? url.href : undefined; }
  catch { return undefined; }
}

function renderTokens(tokens: Token[] = []): ReactNode {
  return tokens.map((token, index) => {
    let node: ReactNode;
    switch (token.type) {
      case "space": return null;
      case "paragraph": node = <p>{renderTokens((token as Tokens.Paragraph).tokens)}</p>; break;
      case "text": { const text = token as Tokens.Text; node = text.tokens ? renderTokens(text.tokens) : text.text; break; }
      case "escape": case "html": node = token.text; break;
      case "strong": node = <strong>{renderTokens(token.tokens)}</strong>; break;
      case "em": node = <em>{renderTokens(token.tokens)}</em>; break;
      case "del": node = <del>{renderTokens(token.tokens)}</del>; break;
      case "br": node = <br />; break;
      case "hr": node = <hr />; break;
      case "codespan": node = <code>{token.text}</code>; break;
      case "code": node = <pre className="chat-code"><code>{token.text}</code></pre>; break;
      case "heading": {
        const heading = token as Tokens.Heading;
        const Tag = `h${Math.min(heading.depth + 1, 6)}` as "h2" | "h3" | "h4" | "h5" | "h6";
        node = <Tag>{renderTokens(heading.tokens)}</Tag>; break;
      }
      case "blockquote": node = <blockquote>{renderTokens(token.tokens)}</blockquote>; break;
      case "link": case "image": {
        const link = token as Tokens.Link | Tokens.Image;
        const href = safeLink(link.href);
        const content = link.type === "image" ? link.text : renderTokens((link as Tokens.Link).tokens);
        node = href ? <a href={href} target="_blank" rel="noopener noreferrer" title={link.title ?? undefined}>{content}</a> : content;
        break;
      }
      case "list": {
        const list = token as Tokens.List;
        const items = list.items.map((item, itemIndex) => <li key={itemIndex}>
          {item.task && <input type="checkbox" checked={Boolean(item.checked)} disabled aria-label={item.text} />}
          {renderTokens(item.tokens)}</li>);
        node = list.ordered ? <ol start={Number(list.start) || 1}>{items}</ol> : <ul>{items}</ul>; break;
      }
      case "table": {
        const table = token as Tokens.Table;
        node = <div className="chat-table"><table><thead><tr>{table.header.map((cell, column) =>
          <th key={column} scope="col">{renderTokens(cell.tokens)}</th>)}</tr></thead>
          <tbody>{table.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, column) =>
            <td key={column}>{renderTokens(cell.tokens)}</td>)}</tr>)}</tbody></table></div>; break;
      }
      default: node = token.raw;
    }
    return <Fragment key={index}>{node}</Fragment>;
  });
}

export function ChatMarkdown({ text }: { text: string }) {
  const content = useMemo(() => renderTokens(marked.lexer(text, { gfm: true })), [text]);
  return <div className="chat-prose">{content}</div>;
}

const rasterTypes = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
const fileSize = (size: number) => size < 1024 ? `${size} B` : size < 1024 * 1024
  ? `${(size / 1024).toFixed(1)} KiB` : `${(size / (1024 * 1024)).toFixed(1)} MiB`;

function ImagePreview({ src, name, downloadUrl, labels }: { src: string; name: string; downloadUrl: string; labels: Labels }) {
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  return <div className="chat-image">
    {state !== "failed" && <button className="chat-image__preview" type="button" ref={opener}
      aria-label={`${labels.previewImage}: ${name}`} disabled={state !== "ready"} onClick={() => dialog.current?.showModal()}>
      <img src={src} alt={name} loading="lazy" decoding="async" onLoad={() => setState("ready")} onError={() => setState("failed")} />
    </button>}
    {state !== "ready" && <p className="chat-muted" role="status">{state === "failed" ? labels.imageFailed : labels.imageLoading}</p>}
    <dialog className="chat-image-dialog" ref={dialog} aria-labelledby={titleId} onClose={() => opener.current?.focus()}>
      <div className="chat-image-dialog__heading"><h2 id={titleId}>{name}</h2>
        <button type="button" onClick={() => dialog.current?.close()} autoFocus>{labels.close}</button></div>
      {state === "ready" && <img src={src} alt={name} />}
      <a href={downloadUrl} download={name}>{labels.download}</a>
    </dialog>
  </div>;
}

export function ChatMessageFiles({ sessionId, messageId, attachments = [], documents = [], onOpenDocument, documentBusy = false, labels }: {
  sessionId: string; messageId: string; attachments?: ChatAttachment[]; documents?: ChatDocument[];
  onOpenDocument?: (document: ChatDocument) => void; documentBusy?: boolean; labels: Labels;
}) {
  type FileRow = { key: string; name: string; mimeType: string; url: string; downloadUrl: string;
    size?: number; document?: ChatDocument };
  // A document's identity includes its page and revision. Keep its original route index;
  // filenames and list order never establish a latest version or matching content.
  const seen = new Set<string>();
  const rows: FileRow[] = documents.flatMap((document, index) => {
    const key = JSON.stringify([document.runId, document.assetSha256, document.revisionRef ?? null, document.pageIndex ?? 0]);
    if (seen.has(key)) return [];
    seen.add(key);
    const url = `/api/chat/sessions/${encodeURIComponent(sessionId)}/documents/${encodeURIComponent(messageId)}/${index}`;
    return [{ key, name: document.fileName, mimeType: document.mimeType, url, downloadUrl: `${url}?download=true`, document }];
  });
  const files = attachments.flatMap((file): FileRow[] => {
    const key = `attachment:${file.id}`;
    if (seen.has(key)) return [];
    seen.add(key);
    const url = `/api/chat/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(file.id)}`;
    return [{ key, name: file.name, mimeType: file.mimeType, url: `${url}?inline=true`, downloadUrl: url, size: file.size }];
  });
  rows.push(...files.filter((file) => rasterTypes.has(file.mimeType)), ...files.filter((file) => !rasterTypes.has(file.mimeType)));
  if (!rows.length) return null;
  const primary = rows.slice(0, 2), remaining = rows.slice(2);
  const format = (file: FileRow) => file.mimeType === "application/pdf" ? "PDF"
    : (file.name.includes(".") ? file.name.split(".").at(-1)! : file.mimeType.split("/").at(-1) ?? "").toUpperCase().slice(0, 12);
  // Same-named rows are different bindings; number them so a row and its folded source line up.
  const reference = (file: FileRow) => {
    const sameName = rows.filter((row) => row.name === file.name);
    return sameName.length > 1 ? labels.fileReference(sameName.indexOf(file) + 1) : null;
  };
  const list = (items: FileRow[]) => <ul className="chat-file-list" aria-label={labels.attachments}>
    {items.map((file) => {
      const itemReference = reference(file);
      return <li key={file.key} className="chat-saved-file">
        {rasterTypes.has(file.mimeType) && <ImagePreview src={file.url} name={file.name} downloadUrl={file.downloadUrl} labels={labels} />}
        <div className="chat-file-row">
          <div className="chat-file-row__label">
            <span className="chat-file-row__name" title={file.name}>{file.name}</span>
            <span className="chat-file-row__meta">{format(file)}
              {file.size != null && <> · {fileSize(file.size)}</>}
              {file.document && <> · {labels.filePage((file.document.pageIndex ?? 0) + 1)}</>}
              {/* #253: the role the architect gave this image in an image discussion. */}
              {file.document?.role && <> · {file.document.role === "source" ? labels.renderRoleSource : labels.renderRoleReference}</>}
              {itemReference && <> · {itemReference}</>}
            </span>
          </div>
          <div className="chat-file-row__actions">
            {file.document && onOpenDocument && <button type="button" className="chat-activity__open" disabled={documentBusy}
              onClick={() => onOpenDocument(file.document!)}>{labels.openDocument}</button>}
            <a href={file.downloadUrl} download={file.name}>{labels.download}</a>
          </div>
        </div>
      </li>;
    })}
  </ul>;
  const sources = rows.filter((row) => row.document);
  return <ChatCard className="chat-files" title={labels.filesTitle} status={labels.fileCount(rows.length)}
    details={remaining.length || sources.length ? { label: labels.fileDetails(remaining.length), children: <>
      {remaining.length > 0 && list(remaining)}
      {sources.length > 0 && <dl className="chat-file-sources">{sources.map((row) => <div key={row.key}>
        <dt>{row.name} · {labels.filePage((row.document!.pageIndex ?? 0) + 1)}{reference(row) && <> · {reference(row)}</>}</dt>
        <dd><code>{row.document!.runId}</code><br /><code>{row.document!.assetSha256}</code>
          {row.document!.revisionRef && <><br /><code>{row.document!.revisionRef}</code></>}</dd>
      </div>)}</dl>}
    </> } : undefined}>
    {list(primary)}
  </ChatCard>;
}

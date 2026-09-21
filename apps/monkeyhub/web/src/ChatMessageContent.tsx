import { Fragment, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { marked, type Token, type Tokens } from "marked";
import type { ChatAttachment, ChatMessage } from "./api/generated";
import "./ChatMessageContent.css";

export type ChatDocument = NonNullable<ChatMessage["documents"]>[number];
type Labels = { attachments: string; previewImage: string; close: string; download: string;
  imageLoading: string; imageFailed: string; openDocument: string };

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
  if (!attachments.length && !documents.length) return null;
  return <ul className="chat-attachments chat-attachments--saved" aria-label={labels.attachments}>
    {attachments.map((file) => {
      const url = `/api/chat/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(file.id)}`;
      return <li key={file.id} className="chat-saved-file">
        {rasterTypes.has(file.mimeType) && <ImagePreview src={`${url}?inline=true`} name={file.name} downloadUrl={url} labels={labels} />}
        <a href={url} download={file.name}><span className="chat-attachment__name" title={file.name}>{file.name}</span>
          <span className="chat-attachment__size">{fileSize(file.size)}</span><span>{labels.download}</span></a>
      </li>;
    })}
    {documents.map((file, index) => {
      const url = `/api/chat/sessions/${encodeURIComponent(sessionId)}/documents/${encodeURIComponent(messageId)}/${index}`;
      const downloadUrl = `${url}?download=true`;
      return <li key={`${file.runId}:${file.assetSha256}:${file.revisionRef}:${file.pageIndex}`} className="chat-saved-file">
        {rasterTypes.has(file.mimeType) && <ImagePreview src={url} name={file.fileName} downloadUrl={downloadUrl} labels={labels} />}
        <a href={downloadUrl} download={file.fileName}><span className="chat-attachment__name" title={file.fileName}>{file.fileName}</span><span>{labels.download}</span></a>
        {onOpenDocument && <button type="button" className="chat-activity__open" disabled={documentBusy} onClick={() => onOpenDocument(file)}>{labels.openDocument}</button>}
      </li>;
    })}
  </ul>;
}

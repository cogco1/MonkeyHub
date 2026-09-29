import { useId, type DetailsHTMLAttributes, type HTMLAttributes, type ReactNode } from "react";
import "./ChatCard.css";

export type ChatCardProps = Omit<HTMLAttributes<HTMLElement>, "title"> & {
  title: ReactNode;
  status?: ReactNode;
  summary?: ReactNode;
  actions?: ReactNode;
  details?: { label: ReactNode; children: ReactNode };
  footer?: ReactNode;
};

/** Shared presentation only. Its caller owns the facts, actions and their permissions. */
export function ChatCard({ title, status, summary, children, actions, details, footer, className = "", ...props }: ChatCardProps) {
  const titleId = useId();
  return <section {...props} className={`chat-card ${className}`} aria-labelledby={titleId}>
    <div className="chat-card__heading">
      <h3 className="chat-card__title" id={titleId}>{title}</h3>
      {status && <div className="chat-card__status">{status}</div>}
    </div>
    {summary && <div className="chat-card__summary">{summary}</div>}
    {children && <div className="chat-card__body">{children}</div>}
    {actions && <div className="chat-card__actions">{actions}</div>}
    {details && <ChatCardDetails summary={details.label}>{details.children}</ChatCardDetails>}
    {footer && <div className="chat-card__footer">{footer}</div>}
  </section>;
}

/** Native disclosure keeps keyboard support and starts closed without another saved state. */
export function ChatCardDetails({ summary, children, className = "", ...props }:
  DetailsHTMLAttributes<HTMLDetailsElement> & { summary: ReactNode }) {
  return <details {...props} className={`chat-card__details ${className}`}>
    <summary>{summary}</summary>
    <div className="chat-card__detail-body">{children}</div>
  </details>;
}

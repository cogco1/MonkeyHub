/**
 * The frame the panels sit in: a top bar, three columns, a bottom strip.
 *
 * This file decides where things go and nothing else. There is no stage rail
 * and no chat transcript: a rail would be a hardcoded picture of a workflow the
 * server never described, and a transcript would look like history in an app
 * whose history lives in the project's records.
 */

import type { ReactNode } from "react";

export function Shell({
  top,
  left,
  center,
  right,
  bottom,
}: {
  top: ReactNode;
  left: ReactNode;
  center: ReactNode;
  right: ReactNode;
  bottom: ReactNode;
}) {
  return (
    <div className="shell">
      <header className="shell__top">{top}</header>
      <div className="shell__body">
        <aside className="shell__left">{left}</aside>
        <main className="shell__center">{center}</main>
        <aside className="shell__right">{right}</aside>
      </div>
      <footer className="shell__bottom">{bottom}</footer>
    </div>
  );
}

export function Panel({
  title,
  aside,
  children,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <h2 className="panel__title">
        <span>{title}</span>
        {aside}
      </h2>
      <div className="panel__body">{children}</div>
    </section>
  );
}

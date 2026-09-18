/**
 * What the shell shows when its own code throws.
 *
 * A render that throws with no boundary above it unmounts the React root, and
 * the operator is left with a blank page: no top bar, no honesty lines, no code
 * to quote. That is the one failure mode this shell cannot report on, so it is
 * the one it must catch. The most likely cause is not a bug in a panel but a
 * machine without a GPU — `new WebGLRenderer(...)` throws when the canvas has no
 * WebGL context — which is why the viewport carries a boundary of its own: a
 * dead GPU should cost the viewport, not the shell around it.
 *
 * The code is `CLIENT_CRASH` because that is what happened, and it is issued
 * here rather than read off the wire — no server said it. It is not a server
 * code and does not pretend to be one: the status line stays blank (`status: 0`)
 * because there was no response, and the detail is the thrown error's own
 * `message`, verbatim. This boundary explains nothing and diagnoses nothing;
 * saying more than the exception said would be this browser inventing a cause.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

import { StudioApiError } from "../api/client";
import { useT } from "../i18n/useT";
import { ErrorPanel } from "./ErrorPanel";

/** A crash in this client's own code, as distinct from anything the API said. */
export const CLIENT_CRASH = "CLIENT_CRASH";

/** The thrown value's own words, and nothing added to them. */
function thrownMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "something was thrown that carried no message this client could read";
}

interface ErrorBoundaryProps {
  /** Which part of the shell this boundary stands in front of. */
  readonly label?: string;
  readonly children: ReactNode;
}

interface ErrorBoundaryImplProps extends ErrorBoundaryProps {
  readonly reloadLabel: string;
}

interface ErrorBoundaryState {
  readonly error: unknown;
  readonly caught: boolean;
}

class ErrorBoundaryImpl extends Component<
  ErrorBoundaryImplProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null, caught: false };

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { error, caught: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    // The panel says what the operator needs; the console keeps the stack the
    // panel deliberately does not print.
    console.error("MonkeyHub workspace caught a render error", error, info);
  }

  render(): ReactNode {
    if (!this.state.caught) return this.props.children;
    return (
      <div className="error-boundary">
        <ErrorPanel
          error={
            new StudioApiError({
              status: 0,
              code: CLIENT_CRASH,
              detail: thrownMessage(this.state.error),
            })
          }
          what={this.props.label}
        />
        <button
          type="button"
          className="button button--small"
          onClick={() => this.setState({ error: null, caught: false })}
        >
          {this.props.reloadLabel}
        </button>
      </div>
    );
  }
}

export function ErrorBoundary({ label, children }: ErrorBoundaryProps) {
  const t = useT();
  return (
    <ErrorBoundaryImpl
      label={label ?? t("shell.client")}
      reloadLabel={t("shell.reloadPage")}
    >
      {children}
    </ErrorBoundaryImpl>
  );
}

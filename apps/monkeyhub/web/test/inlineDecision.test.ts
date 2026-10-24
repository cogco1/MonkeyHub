import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("a page's in-place decision is drawn as the queue's decision card, answers counted as their commands", async (t) => {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { InlineDecision } = await vite.ssrLoadModule("/src/notifications/cards/InlineDecision.tsx");
  const render = (props: Record<string, unknown>) => renderToStaticMarkup(createElement(InlineDecision, props));
  const noop = () => {};

  await t.test("nothing to ask or say draws nothing", () => {
    assert.equal(render({ label: "Editing base", choices: [false, null, undefined] }), "");
  });

  await t.test("the row keeps the page's class and data, in the decide channel's card", () => {
    const html = render({
      label: "Editing base", className: "editing-base", compact: true, data: { "source-match": "different", unset: undefined },
      title: "Viewing D · read-only", live: true, children: createElement("strong", { className: "editing-base__name" }, "home.3dm"),
      choices: [
        { command: "design.go_to_step.continue", label: "Continue from here", title: "Edit from this model", onSelect: noop },
        false,
        { command: "draft.record.continue", action: "record", label: "Record and continue", primary: true, disabled: true, onSelect: noop },
        { command: "view.notice.retry", arg: "document_save", label: "Retry", autoFocus: true, onSelect: noop },
      ],
    });
    assert.match(html, /^<article class="attention-card attention-card--inline attention-card--compact editing-base" data-channel="decide" data-kind="choice" role="group" aria-label="Editing base" data-source-match="different">/);
    assert.doesNotMatch(html, /data-unset/, "an unset attribute is left out");
    assert.match(html, /<header class="attention-card__head" role="status" aria-live="polite"><p class="attention-card__title">Viewing D · read-only<\/p><\/header>/);
    assert.match(html, /<strong class="editing-base__name">home.3dm<\/strong><div class="attention-card__answers" role="group"/);
    const buttons = [...html.matchAll(/<button ([^>]*)>([^<]*)<\/button>/g)].map(([, attributes, text]) => ({ attributes, text }));
    assert.deepEqual(buttons.map((button) => button.text), ["Continue from here", "Record and continue", "Retry"], "a false answer does not apply now");
    assert.match(buttons[0]!.attributes, /data-command="design.go_to_step.continue" class="btn attention-card__answer" title="Edit from this model"/);
    assert.match(buttons[1]!.attributes, /data-command="draft.record.continue" data-action="record" class="btn btn--primary attention-card__answer" disabled=""/);
    assert.match(buttons[2]!.attributes, /data-command="view.notice.retry" data-command-arg="document_save"/);
    assert.doesNotMatch(html, /data-command-view|attention\.queue\./, "a page's question has no queue answers and its answers run as commands");
  });

  await t.test("a refusal is an alert and a quiet word a status", () => {
    const html = render({ label: "Record", failure: "Model edits are not recorded yet.", status: "Saving…", choices: [] });
    assert.match(html, /<p class="attention-card__status" role="status">Saving…<\/p><p class="attention-card__failure" role="alert">Model edits are not recorded yet.<\/p>/);
    assert.doesNotMatch(html, /attention-card__answers|attention-card__head/, "no answers and no question are not drawn");
  });
});

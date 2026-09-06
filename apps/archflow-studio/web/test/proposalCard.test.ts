import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("design cards distinguish component edits from scalar controls and fold compiler details", async (t) => {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)),
    configFile: false,
    logLevel: "silent",
    server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { ProposalCard } = await vite.ssrLoadModule("/src/features/conversation/cards/ProposalCard.tsx");
  const { QuestionCard } = await vite.ssrLoadModule("/src/features/conversation/cards/QuestionCard.tsx");
  const { UserPreferencesProvider } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");
  const { StudioApiError } = await vite.ssrLoadModule("/src/api/error.ts");
  const base = {
    proposalId: "proposal-wall-opening",
    status: "proposed",
    baseStateDigest: "a".repeat(64),
    target: { componentId: "passages", elementId: null, key: null, ref: "entity:passages" },
    protected: ["entity:existing-stair"],
    impact: {
      propagated: ["entity:new-support-wall"],
      conflicts: [],
      unknownCoverage: { count: 0 },
      honesty: [],
    },
    utterance: "Add a wall with an arched opening here.",
  };
  const cardProps = {
    agent: null,
    ghostShown: true,
    refinements: 0,
    refining: false,
    busy: false,
    inactive: false,
    onRun() {},
    onAdjust() {},
    onRefine() {},
    onEvidence() {},
  };
  const componentChange = {
    kind: "edit_components",
    summary: "Add a supporting wall with an open archway.",
    changes: [{ action: "add", entityId: "new-support-wall", label: "Support wall", description: "Wall with hosted opening" }],
    kept: ["Keep the stair width and landing level."],
    edits: { entities: [{ entity_id: "new-support-wall" }], parameters: [], relations: [], removeEntityIds: [], removeParameterKeys: [], removeRelationIds: [] },
  };
  const componentHtml = renderToStaticMarkup(createElement(UserPreferencesProvider, null,
    createElement(ProposalCard, { ...cardProps, proposal: { ...base, change: componentChange } }),
  ));
  const visibleSummary = componentHtml.split("<details")[0];
  assert.match(visibleSummary, /Add a supporting wall with an open archway/);
  assert.match(visibleSummary, /Keep the stair width and landing level/);
  assert.doesNotMatch(visibleSummary, /new-support-wall|entity:|removeEntityIds/);
  assert.doesNotMatch(componentHtml, /type="range"|ghost-mark|undefined|NaN/);
  assert.match(componentHtml, /<details class="card__row card__details">/);
  assert.match(componentHtml, /removeEntityIds/);

  const scalarHtml = renderToStaticMarkup(createElement(UserPreferencesProvider, null,
    createElement(ProposalCard, {
      ...cardProps,
      proposal: {
        ...base,
        status: "conflict",
        target: { ...base.target, elementId: "wall", key: "height" },
        change: { kind: "set_scalar", old: 3, new: 4, unit: "m" },
      },
    }),
  ));
  assert.match(scalarHtml, /height 3 → <b>4<\/b> m/);
  assert.match(scalarHtml, /<details class="card__row card__details"><summary>[^<]+<\/summary><div class="card__row refine"/);
  assert.match(scalarHtml, /type="range"/);
  assert.match(scalarHtml, /class="btn btn--primary" disabled=""/);

  const questionHtml = renderToStaticMarkup(createElement(UserPreferencesProvider, null,
    createElement(QuestionCard, {
      error: new StudioApiError({
        status: 422, code: "BLOCKED_NEEDS_HUMAN", question: "Which side should the opening face?",
        detail: "missing internal slot: target", acceptedForms: ["set height to <number>"],
      }),
      onReply() {}, onChoose() {},
    }),
  ));
  assert.match(questionHtml.split("<details")[0], /Which side should the opening face/);
  assert.doesNotMatch(questionHtml.split("<details")[0], /internal slot|set height/);
  assert.match(questionHtml, /<details class="card__row card__details">/);
  assert.match(questionHtml, /set height to/);
});

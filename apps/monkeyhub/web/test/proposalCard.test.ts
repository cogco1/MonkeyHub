import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import type { ValidationDto } from "../src/api/project-runtime/generated/types.gen.ts";

test("verdict cards distinguish no checkable relations from held and refused dependencies", async (t) => {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { VerdictCard } = await vite.ssrLoadModule("/src/features/conversation/cards/VerdictCard.tsx");
  const { UserPreferencesProvider, usePreferences } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");
  const { messagesEn } = await vite.ssrLoadModule("/src/i18n/messages.en.ts");
  const { messagesZhCN } = await vite.ssrLoadModule("/src/i18n/messages.zh-CN.ts");
  const base: ValidationDto = {
    candidateId: "candidate-empty-relations",
    relationChecks: { held: 0, violated: 0, unchecked: 0, heldFlag: true, fullyChecked: true },
    reviewReady: true, blockedBy: [], seatExecutionComplete: true,
    receipt: {
      receiptId: "receipt-empty-relations", submissionId: "submission-empty-relations",
      submissionDigest: "b".repeat(64), checkedState: { version: 1, stateSha256: "a".repeat(64) },
      passed: true, findings: [],
    },
    canonicalFacts: "Retained project facts", validators: ["receipt-gate"], effectiveChecks: ["receipt-gate"],
    validatorNote: "Only the retained receipt gate ran.", honesty: [],
  };
  const props = { candidateId: base.candidateId, protectedRefs: [], onRetry() {}, onEvidence() {} };
  for (const [language, words, emptyCopy] of [
    ["en", messagesEn, "No checkable relations"],
    ["zh-CN", messagesZhCN, "没有可检查的关系"],
  ] as const) {
    for (const developerMode of [false, true]) {
      await t.test(`${language}, developer mode ${developerMode}`, () => {
        function render(value: typeof base) {
          const before = JSON.stringify(value);
          function Card() {
            // SSR has no settings click. Set this fresh provider's diagnostic
            // view only; the card, translator and relation chips are real.
            Object.assign(usePreferences(), { developerMode });
            return createElement(VerdictCard, { ...props, validation: { status: "ready", value } });
          }
          const html = renderToStaticMarkup(createElement(UserPreferencesProvider,
            { appearance: { language, theme: "light", fontScale: 1 } }, createElement(Card)));
          assert.equal(JSON.stringify(value), before, "display never rewrites the server result");
          return html;
        }
        function row(html: string, label: string) {
          const start = html.indexOf(`<dt>${label}</dt><dd>`);
          assert.notEqual(start, -1, `missing review row: ${label}`);
          return html.slice(start, html.indexOf("</dd>", start));
        }
        function dependencies(html: string) { return row(html, words["verdict.review.dependencies"]); }
        const emptyHtml = render(base);
        const empty = dependencies(emptyHtml);
        assert.ok(empty.includes(emptyCopy), empty);
        assert.doesNotMatch(empty, /✓|mark--ok|mark--no|chip--held|chip--violated|chip--unchecked/);
        assert.ok(emptyHtml.includes(words["verdict.reviewReady"]), "readiness stays the server's answer");
        if (developerMode) {
          assert.match(empty, /relations\.held · relations\.fully_checked/);
          assert.match(empty, /heldFlag true/);
          assert.match(empty, /fullyChecked true/);
          assert.equal((empty.match(/chip--neutral/g) ?? []).length, 3, "all three original zero counts remain neutral");
          // React escapes the apostrophe in the English source-attribution copy.
          const sourceCopy = renderToStaticMarkup(createElement("span", null, words["verdict.serverSource"])).slice(6, -7);
          assert.ok(emptyHtml.includes(sourceCopy));
          assert.ok(emptyHtml.includes(words["verdict.readReceipt"]));
        } else {
          assert.doesNotMatch(empty, /relations\.held|chips|heldFlag/);
        }

        const heldHtml = render({ ...base, relationChecks: { ...base.relationChecks, held: 2 } });
        const held = dependencies(heldHtml);
        assert.match(held, /mark--ok[^>]*>✓/);
        assert.ok(held.includes(words["verdict.held"]));
        assert.ok(!held.includes(emptyCopy));
        if (developerMode) assert.match(held, /chip--held/);
        // Empty dependencies alter neither unrelated review lines nor receipt evidence.
        for (const label of [words["verdict.review.geometry"], words["verdict.review.receipt"], words["verdict.protected"]]) {
          assert.equal(row(emptyHtml, label), row(heldHtml, label));
        }

        for (const [checks, blockedBy] of [
          [{ ...base.relationChecks, violated: 1, heldFlag: false }, ["relations.held"]],
          [{ ...base.relationChecks, unchecked: 1, fullyChecked: false }, ["relations.fully_checked"]],
          [{ ...base.relationChecks, held: 2, unchecked: 1, fullyChecked: false }, ["relations.fully_checked"]],
          [base.relationChecks, ["relations.held", "relations.fully_checked"]],
        ] as const) {
          const html = render({ ...base, relationChecks: checks, reviewReady: false, blockedBy: [...blockedBy] });
          const detail = dependencies(html);
          assert.match(detail, /mark--no[^>]*>△/);
          assert.doesNotMatch(detail, /mark--ok|chip--held/);
          assert.ok(detail.includes(words["verdict.refused"]));
          assert.ok(!detail.includes(emptyCopy), "a server refusal wins even with zero relation counts");
          assert.ok(html.includes(words["verdict.blocked"]));
          if (developerMode) for (const clause of blockedBy) assert.ok(detail.includes(clause));
        }

        const receiptRefusal = render({ ...base, reviewReady: false,
          blockedBy: ["validation.receipt", "server.future_clause"],
          receipt: { ...base.receipt, passed: false,
            findings: [{ code: "RECEIPT_REFUSED", severity: "error", message: "Receipt refused this candidate." }] },
          honesty: ["Source remains unverified."],
        });
        assert.ok(dependencies(receiptRefusal).includes(emptyCopy));
        assert.ok(receiptRefusal.includes(words["verdict.blocked"]));
        const receipt = row(receiptRefusal, words["verdict.review.receipt"]);
        assert.match(receipt, /mark--no[^>]*>△/);
        assert.ok(receipt.includes(words["verdict.didNotPass"]));
        assert.match(receiptRefusal, /Receipt refused this candidate\./);
        if (developerMode) {
          assert.match(receiptRefusal, /server\.future_clause/);
          assert.match(receiptRefusal, /Source remains unverified\./);
          assert.match(receiptRefusal, /RECEIPT_REFUSED/);
        }
      });
    }
  }
});

test("design cards distinguish component edits from scalar controls; compiler details wait for developer mode", async (t) => {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)),
    configFile: false,
    logLevel: "silent",
    server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { ProposalCard } = await vite.ssrLoadModule("/src/features/conversation/cards/ProposalCard.tsx");
  const { QuestionCard } = await vite.ssrLoadModule("/src/features/conversation/cards/QuestionCard.tsx");
  const { MissingControlCard } = await vite.ssrLoadModule("/src/features/conversation/cards/MissingControlCard.tsx");
  const { Composer } = await vite.ssrLoadModule("/src/features/conversation/Composer.tsx");
  const { ErrorPanel } = await vite.ssrLoadModule("/src/app/ErrorPanel.tsx");
  const { UserPreferencesProvider, usePreferences } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");
  const { StudioApiError } = await vite.ssrLoadModule("/src/api/project-runtime/error.ts");
  /** One card in a fresh provider; SSR has no settings click, so the switch is set on the provider itself. */
  const render = (element: ReturnType<typeof createElement>, developerMode = false) => {
    function Card() {
      Object.assign(usePreferences(), { developerMode });
      return element;
    }
    return renderToStaticMarkup(createElement(UserPreferencesProvider, { appearance: { language: "en", theme: "light", fontScale: 1 } }, createElement(Card)));
  };
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
  const componentCard = createElement(ProposalCard, { ...cardProps, proposal: { ...base, change: componentChange } });
  const componentHtml = render(componentCard);
  assert.match(componentHtml, /Add a supporting wall with an open archway/);
  assert.match(componentHtml, /Keep the stair width and landing level/);
  assert.doesNotMatch(componentHtml, /new-support-wall|entity:|removeEntityIds/);
  assert.doesNotMatch(componentHtml, /type="range"|ghost-mark|undefined|NaN/);
  assert.doesNotMatch(componentHtml, /<details/, "outside developer mode the card has no technical details");
  const componentDeveloper = render(componentCard, true);
  assert.equal(componentDeveloper.split("<details")[0], componentHtml, "developer mode adds details below the same card");
  assert.match(componentDeveloper, /<details class="card__row card__details">/);
  assert.match(componentDeveloper, /removeEntityIds/);

  const scalarHtml = render(
    createElement(ProposalCard, {
      ...cardProps,
      proposal: {
        ...base,
        status: "conflict",
        target: { ...base.target, elementId: "wall", key: "height" },
        change: { kind: "set_scalar", old: 3, new: 4, unit: "m" },
      },
    }),
  );
  assert.match(scalarHtml, /height 3 → <b>4<\/b> m/);
  assert.match(scalarHtml, /<details class="card__row card__details"><summary[^>]*>[^<]+<\/summary><div class="card__row refine"/);
  assert.match(scalarHtml, /type="range"/);
  assert.match(scalarHtml, /class="btn btn--primary" disabled=""/);

  const questionCard = createElement(QuestionCard, {
    error: new StudioApiError({
      status: 422, code: "BLOCKED_NEEDS_HUMAN", question: "Which side should the opening face?",
      detail: "missing internal slot: target", acceptedForms: ["set height to <number>"],
    }),
    onReply() {}, onChoose() {},
  });
  const questionHtml = render(questionCard);
  assert.match(questionHtml, /Which side should the opening face/);
  assert.doesNotMatch(questionHtml, /internal slot|set height|<details/);
  const questionDeveloper = render(questionCard, true);
  assert.match(questionDeveloper, /<details class="card__row card__details">/);
  assert.match(questionDeveloper, /set height to/);

  const missingCard = createElement(MissingControlCard, {
    error: new StudioApiError({
      status: 422, code: "MISSING_EDITABLE_CONTROL", detail: "The stair has no control for its riser count.",
      pendingIntent: { targetComponentId: "stair-1", reasonCode: "NO_CONTROL", missingSlots: [], candidates: [], rejectedCandidates: [] },
    }),
  });
  const missingHtml = render(missingCard);
  assert.match(missingHtml, /The stair has no control for its riser count/);
  assert.doesNotMatch(missingHtml, /stair-1|NO_CONTROL|<details/, "outside developer mode the card says only what is missing");
  assert.match(render(missingCard, true), /<details class="card__row card__details">[^]*stair-1[^]*NO_CONTROL/);

  const errorPanel = createElement(ErrorPanel, {
    error: new StudioApiError({ status: 422, code: "SEMANTIC_EDIT_INVALID", detail: "entity wall basis_refs must be sorted and unique" }),
    what: "POST /api/intents",
  });
  const errorHtml = render(errorPanel);
  assert.match(errorHtml, /No model was generated|尚未生成模型/);
  assert.doesNotMatch(errorHtml, /basis_refs|SEMANTIC_EDIT_INVALID|POST|<details/);
  // FN-4, 2-7: the compiler's own words wait for developer mode, under Technical details.
  assert.match(render(errorPanel, true).split("<details")[1] ?? "", /basis_refs must be sorted and unique/);

  const clarificationHtml = render(
    createElement(ErrorPanel, {
      error: new StudioApiError({ status: 422, code: "BLOCKED_NEEDS_HUMAN", question: "Which side should the opening face?", detail: "missing internal slot: target", acceptedForms: ["set height to <number>"] }),
    }),
  );
  assert.match(clarificationHtml, /Which side should the opening face/);
  assert.match(clarificationHtml, /set height to/);
  assert.doesNotMatch(clarificationHtml.split("<details")[0], /missing internal slot/);

  const composerProps = {
    selection: { componentId: "passages", elementId: null }, projection: { catalog: null },
    disabledReason: null, busy: false, gestures: [], draft: "Add an arched opening.",
    onRemoveGesture() {}, onDraft() {}, onSubmit() {}, onSelect() {},
  };
  for (const provider of ["codex", "deterministic"]) {
    const composerHtml = renderToStaticMarkup(createElement(UserPreferencesProvider, { appearance: { language: "en", theme: "light", fontScale: 1 } },
      createElement(Composer, { ...composerProps, intentProvider: provider }),
    ));
    if (provider === "codex") {
      assert.match(composerHtml, /<details class="card__details"><summary[^>]*>(?:View editable parameters|查看可编辑参数)<\/summary>/);
    } else {
      assert.doesNotMatch(composerHtml, /<details/);
    }
  }
});

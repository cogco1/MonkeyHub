import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

type Refusal = { status: number; code: string; detail: string; question?: string; acceptedForms?: string[] };

test("a refusal says what happened and the next step, with the server's words folded beneath (FN-4)", async (t) => {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)),
    configFile: false,
    logLevel: "silent",
    server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { ErrorPanel } = await vite.ssrLoadModule("/src/app/ErrorPanel.tsx");
  const { UserPreferencesProvider } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");
  const { StudioApiError } = await vite.ssrLoadModule("/src/api/error.ts");
  const render = (refusal: Refusal, language = "en", what?: string) => renderToStaticMarkup(
    createElement(UserPreferencesProvider, { appearance: { language, theme: "light", fontScale: 1 } },
      createElement(ErrorPanel, { error: new StudioApiError(refusal), what })));
  const shown = (html: string) => html.split("<details")[0]!;
  const folded = (html: string) => html.split("<details")[1] ?? "";

  // Each refusal, the English reason and next step it must lead with, and the Chinese reason.
  const cases: Array<[Refusal, RegExp, RegExp, RegExp]> = [
    [{ status: 409, code: "STALE_BASE", detail: "The elevation action no longer matches its source." },
      /no longer current/, /latest version/, /已不是最新/],
    [{ status: 409, code: "WORKING_DRAFT_STALE", detail: "The working draft changed in another window." },
      /no longer current/, /latest version/, /已不是最新/],
    [{ status: 0, code: "NETWORK_ERROR", detail: "GET /api/state never reached the API: TypeError: Failed to fetch" },
      /could not be reached/, /MonkeyHub is still running/, /无法连接到项目服务/],
    [{ status: 0, code: "TRANSPORT_ERROR", detail: "Failed to fetch" },
      /could not be reached/, /MonkeyHub is still running/, /无法连接到项目服务/],
    [{ status: 403, code: "PROJECT_MISMATCH", detail: "The board names another project." },
      /belongs to another project/, /Open the project it belongs to/, /属于另一个项目/],
    [{ status: 409, code: "CANDIDATE_REJECTED", detail: "Candidate cand-7 was rejected (turned down by the architect)." },
      /turned down, so it cannot become a Stage/, /continue from here/, /已被否定/],
    [{ status: 422, code: "SECTION_PLANE_MISSES_MODEL", detail: "the section plane meets none of the drawn objects" },
      /does not cut through the model/, /Move the section line/, /剖切线没有切到模型/],
    [{ status: 422, code: "SECTION_EYE_ON_KEPT_SIDE", detail: "the eye stands on the kept side of the section" },
      /eye point is not on the side the section removes/, /Move the eye point/, /视点不在被剖去的一侧/],
    [{ status: 422, code: "SECTION_DEPTH_INVALID", detail: "depth must be a positive distance behind the section plane" },
      /section perspective settings/, /Adjust the section line/, /剖透视设置/],
    [{ status: 503, code: "DRAWING_FONT_UNAVAILABLE", detail: "Install Arial, DejaVu Sans or Liberation Sans regular and bold TTF fonts to render this sheet." },
      /missing a font/, /Install Arial/, /缺少出图所需的字体/],
    [{ status: 409, code: "DRAWING_SOURCE_MISMATCH", detail: "The selected Stage pins a different model receipt." },
      /cannot use the selected model/, /Choose another model version/, /无法使用所选的模型/],
    [{ status: 422, code: "DRAWING_EMPTY", detail: "Keep at least one physical object visible on the sheet." },
      /No object is left to draw/, /Show at least one object/, /没有可画的构件/],
    [{ status: 409, code: "DRAWING_GENERATION_FAILED", detail: "OCCT could not project the section." },
      /drawing could not be made/, /drawing settings/, /图纸没有生成/],
    [{ status: 404, code: "DOCUMENT_NOT_FOUND", detail: "No document sha-1234 is registered." },
      /no longer in the project/, /Choose again/, /已不在项目中/],
    [{ status: 502, code: "TRANSPORT_ERROR", detail: "GET /api/jobs/job-1 answered HTTP 502 with a body this client could not read." },
      /ran into a problem/, /restart MonkeyHub/, /项目服务出错/],
    [{ status: 0, code: "TRANSPORT_ERROR", detail: "Cannot read properties of undefined (reading x)" },
      /This step did not finish/, /open Technical details below/, /这一步没有完成/],
  ];
  for (const [refusal, reason, next, zhReason] of cases) {
    const html = render(refusal, "en", "POST /api/drawings/elevations");
    const lead = shown(html);
    assert.match(lead, reason, `${refusal.code}: reason`);
    assert.match(lead, next, `${refusal.code}: next step`);
    assert.match(lead, /class="error-panel__detail error-panel__next"/, `${refusal.code}: one next step`);
    assert.ok(!lead.includes(refusal.code) && !lead.includes(refusal.detail) && !lead.includes("POST"),
      `${refusal.code}: no code, raw detail or route before the details`);
    assert.ok(folded(html).includes(refusal.code) && folded(html).includes(refusal.detail) && folded(html).includes("POST /api/drawings/elevations"),
      `${refusal.code}: code, detail and route stay under Technical details`);
    assert.match(html, /<summary>Technical details<\/summary>/);
    assert.doesNotMatch(html, /<details[^>]*\sopen/, `${refusal.code}: details start folded outside developer mode`);
    const zh = render(refusal, "zh-CN");
    assert.match(shown(zh), zhReason, `${refusal.code}: Chinese reason`);
    assert.match(zh, /<summary>技术详情<\/summary>/);
    assert.ok(!shown(zh).includes(refusal.code), `${refusal.code}: no code in the Chinese lead`);
  }

  // A compiler refusal keeps the catalog's reason and gains a next step.
  const semantic = shown(render({ status: 422, code: "SEMANTIC_EDIT_INVALID", detail: "entity wall basis_refs must be sorted and unique" }));
  assert.match(semantic, /No model was generated/);
  assert.match(semantic, /Describe the change another way/);

  // A question is its own next step: its forms follow it, and no generic step is added.
  const question = render({ status: 422, code: "BLOCKED_NEEDS_HUMAN", question: "Which side should the opening face?",
    detail: "missing internal slot: target", acceptedForms: ["set height to 3"] });
  assert.match(shown(question), /Which side should the opening face/);
  assert.match(shown(question), /set height to 3/);
  assert.doesNotMatch(shown(question), /error-panel__next|missing internal slot/);

  // A Board note refused before it was sent keeps its own whole sentence.
  const board = render({ status: 0, code: "EDITING_PROJECT_CHANGED", detail: "The saved draft belongs to another project." }, "en", "MonkeyBoard");
  assert.match(shown(board), /<p class="error-panel__detail error-panel__reason">This drawing no longer matches its saved model version\. Your feedback was not sent; the original instruction is kept below\.<\/p>/);
  assert.doesNotMatch(shown(board), /error-panel__next/);
  // Outside Board the same code is an ordinary project mismatch.
  assert.match(shown(render({ status: 0, code: "EDITING_PROJECT_CHANGED", detail: "The saved draft belongs to another project." })),
    /belongs to another project/);
});

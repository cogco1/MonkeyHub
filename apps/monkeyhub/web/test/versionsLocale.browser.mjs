import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import { workspaceFixture } from "./workspaceFixture.mjs";

// Mount the real history panel with its real preference/runtime providers. Only
// caller-owned inputs and callbacks are fixtures; no production UI is replaced.
const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const cacheDir = await mkdtemp(path.join(tmpdir(), "versions-locale-"));
const source = (runId) => ({ runId, stateDigest: `${runId}-state`, assetSha256: `${runId}-asset` });
const stage = { stageRef: "stage-0", parentStageRef: null, branchId: "main", label: "S0 {stage}",
  candidateId: "stage-run", modelSource: source("stage-run"), recordDigest: "stage-record", acceptedBy: "person" };
const otherStage = { ...stage, stageRef: "stage-1", label: "S1", modelSource: source("stage-one") };
const candidates = [
  { label: "院落 {label}", modelSource: source("candidate-a"), sourceStageRef: stage.stageRef },
  { label: "Courtyard {label}", modelSource: source("candidate-b"), sourceStageRef: stage.stageRef },
  { label: "Other stage", modelSource: source("candidate-other"), sourceStageRef: otherStage.stageRef },
];
const row = (runId, label = null) => ({ runId, label, updatedAt: "2026-09-30T12:00:00Z", sourceStageRef: stage.stageRef, branchId: "main" });
const draft = { projectId: "locale-project", current: row("draft-current"),
  saved: [row("draft-saved", "保留 {name}"), row("draft-unnamed")], recovery: [row("draft-recovery")], managedRunIds: [] };
const artifact = { artifactId: "legacy-asset", runId: "legacy-run", sha256: "legacy-asset", fileName: "保留 {file}.3dm",
  available: true, format: "3dm", status: "succeeded", representation: "external", modelSource: null, kind: "model", mimeType: "model/3dm" };
const groups = [{ runId: artifact.runId, label: "Run", title: "Legacy {title}", detail: null,
  exports: [{ artifact, seat: "seat", sourceLabel: "原始 {source}" }] }];
const copies = [{ groupId: "exploration", label: "探索 {name}", selectedOptionId: "option-a",
  options: [{ id: "option-a", label: "Option {name}", modelSource: source("option-run") }] }];
const expected = {
  en: { aria: "Stage history", name: "Milestone name", placeholder: "Milestone name (optional)", save: "Save milestone",
    current: "Current working draft · autosaved", saved: "Saved version", recovery: "Automatic recovery points · 1", point: "Recovery point",
    guidance: "Ordinary edits update the working draft. Automatic recovery points do not expire; milestones and accepted Stages are listed separately.",
    legacy: "Existing models and past runs · not yet assigned to a Stage", history: "Design history", branch: "Branch", head: " · Current commit",
    fork: "New branch from here", forkName: "New branch name for S0 {stage}", create: "Create branch", cancel: "Cancel",
    exploration: "Exploration · 探索 {name}", candidates: "Candidates and existing history", combine: "Combine selected candidates and preview",
    combineHelp: "Select candidates from the same Stage. The combined result still needs to be accepted.", checkbox: "Combine 院落 {label}",
    uncommitted: "Candidate · uncommitted", historical: "This candidate comes from an earlier Stage. Create a branch from that Stage first." },
  "zh-CN": { aria: "Stage 历史", name: "重点版本名称", placeholder: "重点版本名称（可选）", save: "保存重点版本",
    current: "当前工作草稿 · 自动保存", saved: "已保存版本", recovery: "自动恢复点 · 1", point: "恢复点",
    guidance: "普通修改更新工作草稿。自动恢复点不会过期；重点版本与已确认 Stage 另行列出。",
    legacy: "已有模型与历史运行 · 尚未归入 Stage", history: "设计历史", branch: "分支", head: " · 当前提交",
    fork: "从这里新建分支", forkName: "S0 {stage} 的新分支名称", create: "创建分支", cancel: "取消",
    exploration: "探索 · 探索 {name}", candidates: "候选方案与已有历史", combine: "合并选中候选并预览",
    combineHelp: "选择同一 Stage 下的候选，合并后仍需接受。", checkbox: "合并 院落 {label}",
    uncommitted: "候选 · 未提交", historical: "此候选来自历史阶段，请先从该阶段新建分支。" },
};
const failures = [], observations = [], unexpected = [];
function equal(actual, desired, label) {
  observations.push({ label, actual, expected: desired });
  try { assert.deepEqual(actual, desired, label); } catch (error) { failures.push(error.message); console.error(JSON.stringify({ label, actual, expected: desired })); }
}
let vite, browser;
const http = createHttpServer();
try {
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] },
    logLevel: "error", cacheDir, publicDir: ".generated/public", plugins: [workspaceFixture(), {
      name: "real-versions-locale-fixture", enforce: "pre", transform(code, id) {
        if (id.split("?")[0].replaceAll("\\", "/") !== `${webRoot.replaceAll("\\", "/")}/test/workspace-fixture.tsx`) return;
        return { code: `
          import { useState } from "react";
          import { createRoot } from "react-dom/client";
          import { flushSync } from "react-dom";
          import { VersionsStrip } from "/src/features/stage/VersionsStrip";
          import { UserPreferencesProvider, usePreferences } from "/test/TestProviders.tsx";
          import "/src/app/styles.css";
          const stage=${JSON.stringify(stage)}, candidates=${JSON.stringify(candidates)}, groups=${JSON.stringify(groups)};
          const history={projectId:"locale-project",branchId:"main",branches:[{branchId:"main",headStageRef:stage.stageRef},{branchId:"alternate",headStageRef:stage.stageRef}],stages:[stage],candidates:[],studies:[],warnings:[]};
          window.__calls=[];
          const record=(name)=>(...args)=>window.__calls.push({name,args});
          function Fixture() {
            const {setLanguage}=usePreferences();
            const [overrides,setOverrides]=useState({});
            window.__setLanguage=setLanguage; window.__setFixture=(value)=>flushSync(()=>setOverrides(value));
            const tree=new URLSearchParams(location.search).get("mode")==="tree";
            const design={history,acceptedModelSources:[stage.modelSource],currentStageRef:stage.stageRef,currentModelSource:${JSON.stringify(source("draft-current"))},
              candidates,busy:false,error:null,workingDraft:${JSON.stringify(draft)},
              onInitialize:()=>record("initialize")(),onStage:record("stage"),onBranch:record("branch"),onCandidate:record("candidate"),
              onAccept:record("accept"),onFork:record("fork"),onCombine:record("combine"),onRestoreDraft:record("restore"),onSaveDraft:record("save"),...overrides};
            return <VersionsStrip groups={groups} loadingSha={null} loadedShas={[]} loadedRunId="draft-current"
              onOpen={record("open")} onOpenRun={record("openRun")} onCompare={record("compare")}
              workingCopies={${JSON.stringify(copies)}} design={design} onOpenTree={tree?()=>record("tree")():undefined}/>;
          }
          createRoot(document.getElementById("root")).render(<UserPreferencesProvider><Fixture/></UserPreferencesProvider>);
        `, map: null };
      },
    }, react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (request.url?.startsWith("/api/")) { unexpected.push(request.url); response.writeHead(405); response.end(); return; }
    vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  for (const mode of ["tree", "fallback"]) for (const initial of ["en", "zh-CN"]) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
    await context.route((url) => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, (route) => {
      unexpected.push(route.request().url()); return route.abort("blockedbyclient");
    });
    const page = await context.newPage(); page.setDefaultTimeout(15_000);
    page.on("pageerror", (error) => failures.push(`${mode}/${initial}: ${error.message}`));
    await page.goto(`${origin}/?mode=${mode}&lang=${initial}`);
    await page.waitForFunction(() => !!window.__setLanguage);
    await page.evaluate(language => window.__setLanguage(language), initial);
    const root = page.locator("#root > .versions");
    const current = root.locator('[data-working-draft="draft-current"]');
    const name = current.locator("input");
    await name.fill("  用户 {name}  ");
    const recovery = root.locator(":scope > details").first();
    const legacy = root.locator(":scope > details").last();
    await recovery.locator("summary").click(); await legacy.locator("summary").click();
    if (mode === "fallback") {
      await root.locator('[data-design-stage] button').nth(1).click();
      await root.locator(":scope > form input").fill("alternate-layout");
      await root.locator(":scope > details").nth(1).locator("summary").click();
      await root.locator('[data-preview-candidate="candidate-a"] input').check();
      await root.locator('[data-preview-candidate="candidate-b"] input').check();
    }
    await name.focus(); await name.evaluate(node => { node.setSelectionRange(2, 7); window.__nameNode=node; });
    const state = async () => page.evaluate(() => ({ value: window.__nameNode.value,
      same: document.querySelector('[data-working-draft="draft-current"] input')===window.__nameNode,
      focus: document.activeElement===window.__nameNode, selection:[window.__nameNode.selectionStart,window.__nameNode.selectionEnd],
      open:[...document.querySelectorAll("#root > .versions > details")].map(node=>node.open),
      checks:[...document.querySelectorAll('[data-preview-candidate] input')].map(node=>node.checked),
      branch:document.querySelector('#root > .versions > form input')?.value??null,
      pressed:[...document.querySelectorAll('[aria-pressed]')].map(node=>node.getAttribute("aria-pressed")) }));
    const before = await state();
    for (const language of [initial, initial === "en" ? "zh-CN" : "en", initial]) {
      await page.evaluate(language => window.__setLanguage(language), language);
      await page.waitForFunction(language => document.documentElement.lang===language, language);
      const text=expected[language], prefix=`${mode}/${initial} → ${language}`;
      equal(await root.getAttribute("aria-label"), text.aria, `${prefix} list accessible name`);
      equal(await name.getAttribute("aria-label"), text.name, `${prefix} input accessible name`);
      equal(await name.getAttribute("placeholder"), text.placeholder, `${prefix} input placeholder`);
      equal(await current.locator("form button").textContent(), text.save, `${prefix} save action`);
      equal(await current.locator("strong").textContent(), text.current, `${prefix} current draft`);
      equal(await root.locator('[data-working-draft="draft-saved"] strong').textContent(), "保留 {name}", `${prefix} saved user name`);
      equal(await root.locator('[data-working-draft="draft-unnamed"] strong').textContent(), text.saved, `${prefix} unnamed saved version`);
      equal(await recovery.locator("summary").textContent(), text.recovery, `${prefix} recovery count`);
      equal(await recovery.locator("p").textContent(), text.guidance, `${prefix} recovery explanation`);
      equal(await recovery.locator("strong").textContent(), text.point, `${prefix} recovery row`);
      equal(await legacy.locator("summary").textContent(), text.legacy, `${prefix} legacy history`);
      equal(await legacy.locator(".vcard__filename").textContent(), artifact.fileName, `${prefix} user filename`);
      equal(await state(), before, `${prefix} mounted input/focus/selection and open/selected state`);
      equal(await page.evaluate(() => window.__calls), [], `${prefix} locale never invokes actions`);
      if (mode === "tree") {
        equal(await root.locator('[data-design-stage], [data-preview-candidate], select').count(), 0, `${prefix} no fallback UI restored`);
      } else {
        equal(await root.locator(":scope > .vcard > .vcard__head > strong").last().textContent(), text.history, `${prefix} design history`);
        equal(await root.locator("select").getAttribute("aria-label"), text.branch, `${prefix} branch accessible name`);
        equal(await root.locator('[data-design-stage] button').allTextContents(), [stage.label+text.head,text.fork], `${prefix} stage actions`);
        equal((await root.locator(":scope > form label").textContent()).trim(), text.forkName, `${prefix} branch label interpolation`);
        equal(await root.locator(":scope > form button").allTextContents(), [text.create,text.cancel], `${prefix} branch actions`);
        equal(await root.locator('[data-working-copy] strong').textContent(), text.exploration, `${prefix} exploration interpolation`);
        const candidateDetails=root.locator(":scope > details").nth(1);
        equal(await candidateDetails.locator("summary").textContent(),text.candidates,`${prefix} candidates summary`);
        equal(await candidateDetails.locator(":scope > .vcard > button").textContent(),text.combine,`${prefix} combine action`);
        equal(await candidateDetails.locator(":scope > .vcard > .quiet").textContent(),text.combineHelp,`${prefix} combine guidance`);
        equal(await root.locator('[data-preview-candidate="candidate-a"] input').getAttribute("aria-label"),text.checkbox,`${prefix} candidate accessible name`);
        equal(await root.locator('[data-preview-candidate="candidate-a"] label').textContent(),text.uncommitted,`${prefix} candidate status`);
        equal(await root.locator('[data-preview-candidate="candidate-a"] strong').textContent(),candidates[0].label,`${prefix} candidate user name`);
        assert.equal(await root.locator('[data-preview-candidate="candidate-other"] input').isDisabled(),true);
      }
    }
    // Explicit actions still receive exactly the existing caller values.
    await current.locator("form button").click();
    // The save response can also label the current run; it must not replace the input.
    await page.evaluate(workingDraft => window.__setFixture({workingDraft}), {
      ...draft, current: {...draft.current, label: "用户 {name}"},
    });
    assert.equal(await name.evaluate(node => node === window.__nameNode), true, "saving a name keeps the current input mounted");
    assert.equal(await name.inputValue(), "  用户 {name}  ", "save response keeps the entered text");
    await current.locator(".vcard__exports button").first().click();
    await recovery.locator("button").click();
    await current.locator(".vcard__exports button").nth(1).click();
    const wanted=[{name:"save",args:["draft-current","用户 {name}"]},{name:"restore",args:["draft-current"]},
      {name:"restore",args:["draft-recovery"]},{name:"accept",args:["draft-current"]}];
    if(mode==="tree") { await root.locator('[data-design-tree-link] button').click(); wanted.push({name:"tree",args:[]}); }
    else {
      await root.locator("select").selectOption("alternate"); wanted.push({name:"branch",args:["alternate"]});
      await root.locator('[data-design-stage] button').first().click(); wanted.push({name:"stage",args:[stage]});
      await root.locator(":scope > form button").first().click(); wanted.push({name:"fork",args:[stage,"alternate-layout"]});
      await root.locator('[data-working-copy] button').click(); wanted.push({name:"candidate",args:[copies[0].options[0].modelSource]});
      await root.locator('[data-preview-candidate="candidate-a"] button').click(); wanted.push({name:"candidate",args:[candidates[0].modelSource]});
      const combine=root.locator(":scope > details").nth(1).locator(":scope > .vcard > button");
      await combine.click(); wanted.push({name:"combine",args:[["candidate-a","candidate-b"]]});
      await root.locator('[data-preview-candidate="candidate-b"] input').uncheck();
      assert.equal(await combine.isDisabled(),true,"one candidate cannot combine");
      await root.locator(":scope > form button").last().click();
      assert.equal(await root.locator(":scope > form").count(),0,"Cancel dismisses without forking again");
      await page.evaluate(source=>window.__setFixture({currentStageRef:"stage-1",currentModelSource:source}),candidates[0].modelSource);
      const selected=root.locator('[data-preview-candidate="candidate-a"]');
      equal(await selected.locator(".quiet").textContent(),expected[initial].historical,`${mode}/${initial} historical candidate guidance`);
      assert.equal(await selected.locator("button").nth(1).isDisabled(),true,"historical candidate cannot accept on current branch");
    }
    await legacy.locator(".vcard__export").click(); wanted.push({name:"open",args:[artifact,"原始 {source}"]});
    await legacy.locator(".vcard__compare").click(); wanted.push({name:"compare",args:[artifact]});
    assert.deepEqual(await page.evaluate(()=>window.__calls),wanted);
    await page.evaluate(()=>window.__setFixture({busy:true}));
    for(const button of await root.locator('[data-working-draft] button').all()) assert.equal(await button.isDisabled(),true);
    if(mode==="fallback") for(const control of await root.locator('[data-design-stage] button, [data-preview-candidate] input, [data-preview-candidate] button, select, [data-working-copy] button').all()) assert.equal(await control.isDisabled(),true);
    await page.evaluate(()=>window.__setFixture({history:{projectId:"locale-project",branchId:"main",branches:[],stages:[],candidates:[],studies:[],warnings:[]},currentModelSource:null}));
    const initialize=mode==="tree"?root.locator('[data-design-tree-link] button').nth(1):root.locator(":scope > .vcard > .vcard__head > button");
    assert.equal(await initialize.isDisabled(),true,"initial acceptance needs a current model");
    await page.evaluate(source=>window.__setFixture({history:{projectId:"locale-project",branchId:"main",branches:[],stages:[],candidates:[],studies:[],warnings:[]},currentModelSource:source}),source("draft-current"));
    await initialize.click(); wanted.push({name:"initialize",args:[]});
    assert.deepEqual(await page.evaluate(()=>window.__calls),wanted);
    await context.close();
  }
  assert.deepEqual(unexpected,[],"no escaped network request or persistence action");
  console.log(JSON.stringify({observations,failures},null,2));
  assert.deepEqual(failures,[],"history panel locale and live-state observations");
  console.log(`PASS ${observations.length} bilingual history observations; original callbacks, disabled conditions and live state retained`);
} finally {
  await browser?.close(); await vite?.close(); await new Promise(resolve=>http.close(resolve));
  await rm(cacheDir,{recursive:true,force:true});
}

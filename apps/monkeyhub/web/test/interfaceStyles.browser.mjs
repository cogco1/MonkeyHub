import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import { workspaceFixture } from "./workspaceFixture.mjs";

// The five interface styles over real components (card, view tabs, Board | Layout switch) and the
// shell's own buttons, composer, current conversation row and notice. The four older styles read
// no component hook, so they draw as before; Critique Table (board) turns the hooks; the instrument
// rules follow data-ui-family. Until the settings accept "board", the test sets that style on the root
// itself, as the Hub will once it is a saved choice. INTERFACE_STYLE_SHOTS=<dir> saves each style.
const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const cacheDir = await mkdtemp(path.join(tmpdir(), "interface-styles-"));
const shots = process.env.INTERFACE_STYLE_SHOTS ? path.resolve(process.env.INTERFACE_STYLE_SHOTS) : null;
const HOOKS = ["--control-radius", "--control-bg", "--control-border", "--primary-bg", "--primary-ink", "--card-bg", "--card-border",
  "--card-radius", "--card-pad", "--card-title-size", "--tab-on-bg", "--tab-on-ink", "--tab-on-mark", "--tab-radius", "--tab-pad", "--tab-h",
  "--nav-on-bg", "--nav-on-ink", "--shell-gap", "--pane-radius", "--composer-bg", "--composer-radius", "--toast-bg", "--toast-ink", "--toast-radius"];
const escaped = [];
let vite, browser;
const http = createHttpServer();
try {
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] },
    logLevel: "error", cacheDir, publicDir: ".generated/public",
    plugins: [workspaceFixture(), { name: "interface-styles-fixture", enforce: "pre", transform(source, id) {
      const filename = id.split("?")[0].replaceAll("\\", "/"), root = webRoot.replaceAll("\\", "/");
      if (filename !== `${root}/test/workspace-fixture.tsx`) return;
      return { code: `
        import { useState } from "react";
        import { createRoot } from "react-dom/client";
        import { UserPreferencesProvider } from "/test/TestProviders.tsx";
        import { ChatCard } from "/src/ChatCard";
        import { MenuTabs } from "/src/features/chrome/SurfaceChrome";
        import { BoardModeSwitch } from "/src/app/BoardModeSwitch";
        import "/src/styles.css";
        import "/src/app/styles.css";
        import "/src/ChatShell.css";
        import "/src/features/chrome/chrome.css";
        import "/src/notifications/attention.css";
        function Sheet() {
          const [view, setView] = useState("board");
          const [mode, setMode] = useState("board");
          return <main style={{ display: "grid", alignContent: "start", gap: 20, padding: 20, minHeight: "100vh", boxSizing: "border-box", background: "var(--ground)", color: "var(--ink)" }}>
            <div className="surface-bar">
              <MenuTabs label="Views" value={view} options={[{ value: "attention", label: "关注" }, { value: "board", label: "Board" }, { value: "model", label: "模型" }]} onChange={setView} />
              <BoardModeSwitch mode={mode} onChange={setMode} />
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              <button type="button" className="btn" data-probe="button">稍后决定</button>
              <button type="button" className="btn btn--primary" data-probe="primary">采用推荐</button>
              <button type="button" className="btn" disabled>暂时不可用</button>
            </div>
            <ChatCard data-probe="card" title="地脚 X 形斜撑：留，还是去掉" status="Thi Hemp"
              summary={<p>你在 V16 的建模草稿里删了 4 根斜撑，删完的这一版记成 B。</p>}
              actions={<><button type="button" className="btn btn--primary">比较并决定</button><button type="button" className="btn">稍后决定</button></>} />
            <div style={{ display: "grid", gap: 2, maxWidth: 260, padding: 8, borderRadius: 12, background: "var(--panel)" }}>
              <button type="button" className="chat-thread" aria-current="page" data-probe="current">Thi Hemp 墙身节点</button>
              <button type="button" className="chat-thread">Housing 13 户</button>
            </div>
            <form className="chat-composer" data-probe="composer" onSubmit={(event) => event.preventDefault()}>
              <textarea aria-label="Composer" rows={2} defaultValue="楼板再薄一点" style={{ width: "100%", border: 0, background: "transparent", color: "inherit", font: "inherit", resize: "none" }} />
            </form>
            <div className="attention-toast" data-kind="finished" data-probe="toast" style={{ maxWidth: 360 }}>
              <span className="attention-toast__mark" />
              <p className="attention-toast__text">已替你处理<span className="attention-toast__dot">·</span><button type="button" className="attention-toast__view">撤销</button></p>
              <button type="button" className="attention-toast__close" aria-label="关闭">×</button>
            </div>
          </main>;
        }
        createRoot(document.getElementById("root")).render(<UserPreferencesProvider><Sheet /></UserPreferencesProvider>);
      `, map: null };
    } }, react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (request.url?.startsWith("/api/")) { escaped.push(request.url); response.writeHead(405); response.end(); return; }
    vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  if (shots) await mkdir(shots, { recursive: true });

  for (const style of ["classic", "quiet", "titleblock", "night", "board"]) for (const theme of ["dark", "light"]) {
    const label = `${style} ${theme}`;
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.addInitScript(([uiStyle, theme]) => {
      localStorage.setItem("archflow-studio.user-preferences", JSON.stringify({ version: 1, language: "zh-CN", theme, uiStyle }));
    }, [style, theme]);
    const board = style === "board";
    await context.route((url) => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, (route) => {
      escaped.push(route.request().url()); return route.abort("blockedbyclient");
    });
    const page = await context.newPage(); page.setDefaultTimeout(15_000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    try {
      await page.goto(origin);
      await page.locator("[data-probe=toast]").waitFor();
      if (board) await page.evaluate(() => { document.documentElement.dataset.uiStyle = "board"; document.documentElement.dataset.uiFamily = "board"; });
      await page.waitForFunction((style) => document.documentElement.dataset.uiStyle === style, style);
      // Controls ease their colours in; read them once the switch to this style and theme has finished.
      await page.evaluate(() => Promise.all(document.getAnimations().map((animation) => animation.finished.catch(() => undefined))));
      const seen = await page.evaluate((hooks) => {
        const root = document.documentElement, rootStyle = getComputedStyle(root);
        // A token as the browser resolves it on a colour property, to compare with a component's.
        const colour = (token) => { const probe = document.createElement("i"); probe.style.color = `var(${token})`; document.body.append(probe);
          const value = getComputedStyle(probe).color; probe.remove(); return value; };
        const css = (selector) => getComputedStyle(document.querySelector(selector));
        const tab = css(".menu-tab[aria-pressed=true]"), radio = css(".board-mode-switch [aria-checked=true]");
        return {
          style: root.dataset.uiStyle, family: root.dataset.uiFamily,
          hooks: Object.fromEntries(hooks.map((hook) => [hook, rootStyle.getPropertyValue(hook).trim()])),
          tokens: { panel: colour("--panel"), panel2: colour("--panel-2"), ground: colour("--ground"), ink: colour("--ink"), accent: colour("--accent"),
            radiusSm: rootStyle.getPropertyValue("--radius-sm").trim(), radiusLg: rootStyle.getPropertyValue("--radius-lg").trim() },
          button: { radius: css("[data-probe=button]").borderTopLeftRadius, background: css("[data-probe=button]").backgroundColor },
          primary: { background: css("[data-probe=primary]").backgroundColor },
          card: { radius: css(".chat-card").borderTopLeftRadius, background: css(".chat-card").backgroundColor,
            titleSize: css(".chat-card__title").fontSize, titleWeight: css(".chat-card__title").fontWeight },
          tab: { background: tab.backgroundColor, colour: tab.color, mark: tab.boxShadow, radius: tab.borderTopLeftRadius },
          radio: { background: radio.backgroundColor, mark: radio.boxShadow },
          current: { background: css("[data-probe=current]").backgroundColor },
          composer: { radius: css("[data-probe=composer]").borderTopLeftRadius, background: css("[data-probe=composer]").backgroundColor },
          toast: { radius: css("[data-probe=toast]").borderTopLeftRadius, background: css("[data-probe=toast]").backgroundColor,
            edge: css("[data-probe=toast]").borderLeftWidth },
        };
      }, HOOKS);
      assert.equal(seen.style, style, label);
      assert.equal(seen.family, style === "board" ? "board" : style === "classic" ? "classic" : "instrument", `${label}: family`);
      const defined = Object.entries(seen.hooks).filter(([, value]) => value).map(([hook]) => hook);
      if (style !== "board") {
        // The component rules fall back to their own values exactly when no hook is set.
        assert.deepEqual(defined, [], `${label}: no component hook is set`);
        assert.equal(seen.button.radius, seen.tokens.radiusSm, `${label}: button corners`);
        assert.equal(seen.button.background, seen.tokens.panel, `${label}: button ground`);
        assert.equal(seen.primary.background, seen.tokens.accent, `${label}: primary button`);
        assert.equal(seen.tab.background, "rgba(0, 0, 0, 0)", `${label}: a chosen tab keeps no fill`);
        assert.ok(seen.tab.mark.includes("inset"), `${label}: a chosen tab is underlined`);
        assert.ok(seen.radio.mark.includes("inset"), `${label}: the chosen mode is underlined`);
        assert.equal(seen.current.background, seen.tokens.panel2, `${label}: current conversation`);
        assert.equal(seen.composer.background, seen.tokens.panel2, `${label}: composer`);
        assert.equal(seen.toast.background, seen.tokens.ground, `${label}: notice`);
        // The instrument rules reach their three styles through the family.
        assert.equal(seen.toast.edge, style === "classic" ? "1px" : "2px", `${label}: the notice's state edge`);
        assert.equal(seen.toast.radius, style === "classic" ? "12px" : seen.tokens.radiusLg, `${label}: notice corners`);
        assert.equal(seen.composer.radius, style === "classic" ? "20px" : seen.tokens.radiusLg, `${label}: composer corners`);
      } else {
        assert.deepEqual(defined, HOOKS, `${label}: every hook is set`);
        assert.equal(seen.button.radius, "999px", `${label}: pill buttons`);
        assert.equal(seen.button.background, "rgba(0, 0, 0, 0)", `${label}: outlined secondary button`);
        assert.equal(seen.primary.background, seen.tokens.ink, `${label}: ink primary button`);
        assert.deepEqual([seen.card.radius, seen.card.titleSize, seen.card.titleWeight], ["14px", "18px", "700"], `${label}: card`);
        assert.deepEqual([seen.tab.background, seen.tab.colour, seen.tab.mark, seen.tab.radius], [seen.tokens.ink, seen.tokens.ground, "none", "999px"], `${label}: chosen tab`);
        assert.deepEqual([seen.radio.background, seen.radio.mark], [seen.tokens.ink, "none"], `${label}: chosen mode`);
        assert.equal(seen.current.background, seen.tokens.ink, `${label}: current conversation reads reversed`);
        assert.deepEqual([seen.composer.radius, seen.composer.background], ["22px", seen.tokens.panel], `${label}: composer`);
        assert.deepEqual([seen.toast.radius, seen.toast.background, seen.toast.edge], ["999px", seen.tokens.ink, "1px"], `${label}: notice`);
        assert.notEqual(seen.tokens.ground, theme === "dark" ? "rgb(236, 238, 241)" : "rgb(16, 18, 21)", `${label}: the theme's own ground`);
      }
      if (shots) {
        await page.screenshot({ path: path.join(shots, `style-${style}-${theme}-1440.png`), fullPage: true });
        if (style === "board" || style === "classic") {
          await page.setViewportSize({ width: 390, height: 844 });
          await page.screenshot({ path: path.join(shots, `style-${style}-${theme}-390.png`), fullPage: true });
        }
      }
      assert.deepEqual(errors, [], `${label}: page errors`);
      console.log(`PASS ${label}`);
    } finally { await context.close(); }
  }
  assert.deepEqual(escaped, [], "No API or external requests");
  console.log("PASS interface styles: four styles unchanged, Critique Table through the hooks, instrument rules by family");
} finally {
  await browser?.close(); await vite?.close();
  await new Promise((resolve) => http.close(resolve));
  await rm(cacheDir, { recursive: true, force: true });
}

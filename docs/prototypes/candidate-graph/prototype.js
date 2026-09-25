/*
 * GH-284 Candidate Graph: DEV PROTOTYPE on fixture data, not production.
 *
 * Renders one dataset (data/<name>.js assigns window.CANDIDATE_GRAPH_DATA) as
 * the MonkeyHub shell with a project-level Design Tree. Every action changes
 * this page's memory only: there is no runtime, no project write, no network.
 *
 * Model, following #284 and #294:
 *   Stage -> Study -> admitted Candidates -> Continue -> next Stage.
 * Lineage is derived from `parent` links; the Working Head is `head.parent`.
 * Viewing never moves the Working Head. Continue moves it. Accepting a Stage
 * is a separate step from the Working Head. Items that are still "working" or
 * "queued" are Agent worktrees, not Candidates; runs, repairs, redo attempts
 * and results rejected before admission appear only in Advanced details.
 *
 * Hash parameters: #state=1..6, #theme=system|light|dark, #data=<name>.
 */
(function () {
  "use strict";

  const DEFAULT_DATA = "issue-fixture";
  const THEMES = ["system", "light", "dark"];
  const params = () => new URLSearchParams(location.hash.replace(/^#/, ""));
  const safeName = (value) => (value && /^[a-z0-9][a-z0-9-]{0,63}$/.test(value) ? value : DEFAULT_DATA);
  const dataName = safeName(params().get("data"));
  const app = document.getElementById("app");
  const compactQuery = window.matchMedia("(max-width: 1100px)");
  const narrowQuery = window.matchMedia("(max-width: 640px)");

  function applyTheme(value) {
    const theme = THEMES.includes(value) ? value : "system";
    document.documentElement.dataset.theme = theme;
    return theme;
  }
  applyTheme(params().get("theme"));

  /* ---------------------------------------------------------------- utils */

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const cssEsc = (value) => (window.CSS && CSS.escape ? CSS.escape(value) : String(value).replace(/["\\]/g, "\\$&"));

  const ICONS = {
    tree: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="12" r="2"/><path d="M6 7v10M6 9.5c0 2.5 2 2.5 4 2.5h6"/>',
    cube: '<path d="m12 3 9 5v9l-9 5-9-5V8Zm0 10 9-5M3 8l9 5v9M7.5 5.5l9 5"/>',
    board: '<rect x="3" y="4" width="18" height="15" rx="2"/><path d="M7 8h4v5H7Zm7 0h3M14 12h3M8 22l4-3 4 3"/>',
    drawing: '<path d="M5 3h10l4 4v14H5ZM15 3v5h4M8 11h8v5H8Z"/><path d="M8 19h8M8 18v2m8-2v2"/>',
    chart: '<path d="M4 4v16h17M8 16v-4M13 16V7M18 16v-7"/>',
    folder: '<path d="M3 6h7l2 2h9v11H3Z"/>',
    panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
    sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    settings: '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>',
    chat: '<path d="M4 4h16v13H9l-5 4Z"/>',
    send: '<path d="M12 19V5m-6 6 6-6 6 6"/>',
    eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    compare: '<rect x="3" y="4" width="7.5" height="16" rx="1.5"/><rect x="13.5" y="4" width="7.5" height="16" rx="1.5"/>',
    arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    flag: '<path d="M5 21V4h12l-2.5 4.5L17 13H5"/>',
    archive: '<path d="M4 8h16v13H4ZM3 3h18v5H3ZM9 12h6"/>',
    camera: '<path d="M4 8h3l2-3h6l2 3h3v11H4Z"/><circle cx="12" cy="13" r="3.5"/>',
    check: '<path d="m5 12 5 5 9-10"/>',
    orbit: '<circle cx="12" cy="12" r="3"/><path d="M3 12a9 4 0 0 0 18 0 9 4 0 0 0-18 0"/>',
    pan: '<path d="M12 3v18M3 12h18M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/>',
    section: '<path d="M4 20 20 4M4 4h7M13 20h7"/>',
  };
  const icon = (name, cls = "icon") => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.chat}</svg>`;

  /* ------------------------------------------------------------ the model */

  let base = null; // the dataset as loaded; presets start from a copy of it
  let D = null;    // the live copy this page mutates
  let S = null;    // UI state
  let idx = null;
  let toastTimer = null;
  let appliedState = null;

  function index() {
    idx = { stage: new Map(), study: new Map(), item: new Map(), line: new Map(), studyOf: new Map() };
    for (const stage of D.stages) idx.stage.set(stage.id, stage);
    for (const study of D.studies) {
      idx.study.set(study.id, study);
      for (const item of study.items) { idx.item.set(item.id, item); idx.studyOf.set(item.id, study); }
    }
    for (const line of D.lines) idx.line.set(line.id, line);
  }
  const kindOf = (id) => (idx.stage.has(id) ? "stage" : idx.item.has(id) ? "item" : idx.line.has(id) ? "line" : null);
  const nodeOf = (id) => idx.stage.get(id) || idx.item.get(id) || idx.line.get(id) || null;

  function parentOf(id) {
    switch (kindOf(id)) {
      case "stage": return idx.stage.get(id).parent || null;
      case "item": return idx.studyOf.get(id).stage;
      case "line": return idx.line.get(id).parent || null;
      default: return null;
    }
  }
  function lineageOf(id) {
    const out = [], seen = new Set();
    for (let cur = id; cur && !seen.has(cur); cur = parentOf(cur)) { seen.add(cur); out.unshift(cur); }
    return out;
  }
  const onLine = () => new Set(lineageOf(D.head.parent));
  function currentStage() {
    const line = lineageOf(D.head.parent);
    for (let i = line.length - 1; i >= 0; i -= 1) if (kindOf(line[i]) === "stage") return idx.stage.get(line[i]);
    return D.stages[0];
  }
  const lastStage = () => D.stages[D.stages.length - 1];
  const stageLabel = (stage) => `${stage.label} · ${stage.name}`;
  const studyShort = (study) => study.short || study.name.replace(/\s+Study$/i, "");
  const itemLabel = (item) => `${item.letter ? `${item.letter} · ` : ""}${item.name}`;
  function shortLabel(id) {
    switch (kindOf(id)) {
      case "stage": return stageLabel(idx.stage.get(id));
      case "item": {
        const item = idx.item.get(id);
        return item.letter ? `${studyShort(idx.studyOf.get(id))} ${item.letter}` : item.name;
      }
      case "line": return idx.line.get(id).name;
      default: return String(id);
    }
  }
  const stageTag = (id) => (kindOf(id) === "stage" ? idx.stage.get(id).label : shortLabel(id));
  function headLabel() {
    const head = D.head.parent;
    return kindOf(head) === "stage" ? stageLabel(idx.stage.get(head)) : `${shortLabel(head)} (from ${currentStage().label})`;
  }
  function previewOf(id) {
    const node = nodeOf(id);
    return node ? node.preview ?? null : null;
  }
  function continuedText(continued) {
    if (!continued) return "";
    const who = !continued.by || continued.by === "you" ? "you" : continued.by;
    return `Continued by ${who}${continued.onRequest ? " at your request" : ""}`;
  }
  function tally(study) {
    const t = { ready: 0, working: 0, queued: 0 };
    for (const item of study.items) t[item.status] = (t[item.status] || 0) + 1;
    return t;
  }
  function nextStageLabel() {
    let n = D.stages.length;
    while (idx.stage.has(`S${n}`)) n += 1;
    return `S${n}`;
  }
  /* Stages on the current line that Continue from `id` would leave behind. */
  function wouldLeave(id) {
    const target = new Set(lineageOf(id));
    return lineageOf(D.head.parent).filter((x) => kindOf(x) === "stage" && !target.has(x)).map((x) => idx.stage.get(x).label);
  }
  function badgeCounts() {
    const line = onLine(), stage = currentStage();
    const out = { ready: 0, readyStudy: null, running: 0, runStudy: null };
    for (const study of D.studies) {
      for (const item of study.items) {
        if (item.status === "working") { out.running += 1; out.runStudy = out.runStudy || study.id; }
        if (study.stage === stage.id && item.status === "ready" && !line.has(item.id)) { out.ready += 1; out.readyStudy = out.readyStudy || study.id; }
      }
    }
    return out;
  }

  /* -------------------------------------------------------------- actions */

  function preset(n) {
    D = clone(base);
    D.lines = D.lines || [];
    D.chat = D.chat || [];
    index();
    const states = base.states || {};
    const p = states[n] || (n === 1 ? { tree: false } : { tree: true });
    const expand = Array.isArray(p.expand) ? p.expand : p.focus ? [p.focus] : [];
    const focusStudy = p.focus ? idx.study.get(p.focus) : null;
    S = {
      n,
      tree: Boolean(p.tree),
      focus: p.focus || null,
      selected: p.select || null,
      view: p.view && nodeOf(p.view) ? p.view : null,
      compare: p.compare ? clone(p.compare) : null,
      checked: clone(p.checked || {}),
      open: new Set(p.open || []),
      collapsed: new Set(D.studies.filter((study) => !expand.includes(study.id)).map((study) => study.id)),
      sidebar: false,
      panel: !compactQuery.matches,
      surface: "arch",
      projection: "axon",
      accept: null,
      toast: null,
      scrollTo: p.scroll || (focusStudy ? `node:${focusStudy.stage}` : p.select ? `node:${p.select}` : null),
      chatTo: p.focus ? `res:${p.focus}` : "bottom",
    };
    if (p.continue && nodeOf(p.continue.id)) continueFrom(p.continue.id, p.continue, true);
    if (S.view || S.compare) revealTools();
  }

  function revealTools() {
    if (compactQuery.matches) S.panel = true;
    if (narrowQuery.matches) S.tree = false;
    S.surface = "arch";
  }

  function continueFrom(id, how = {}, silent = false) {
    const node = nodeOf(id);
    if (!node || D.head.parent === id) return;
    const left = wouldLeave(id);
    node.continued = { by: how.by || "you", onRequest: Boolean(how.onRequest), at: how.at || "Just now" };
    D.head = { parent: id };
    S.view = null;
    S.compare = null;
    S.accept = null;
    S.selected = id;
    const study = idx.studyOf.get(id);
    if (study) S.collapsed.delete(study.id);
    if (!silent) {
      toast(`Continued from ${shortLabel(id)}. The Working Head moved${left.length ? `; ${left.join(" and ")} stay in history` : ""}. Nothing was accepted as a Stage.`);
    }
  }

  function acceptHead() {
    const from = D.head.parent;
    if (kindOf(from) === "stage") return;
    const label = nextStageLabel();
    const study = idx.studyOf.get(from);
    const name = study ? studyShort(study) : nodeOf(from).name;
    const stage = {
      id: label, label, name,
      summary: `Accepted from ${shortLabel(from)}.`,
      acceptedAt: "Just now", acceptedBy: "you", preview: previewOf(from), parent: from,
      advanced: { record: `DesignStage ${label} · prototype memory only`, model: "not written: dev prototype", accepted: "you · prototype" },
    };
    D.stages.push(stage);
    index();
    D.head = { parent: stage.id };
    S.accept = null;
    S.selected = stage.id;
    S.scrollTo = `node:${stage.id}`;
    toast(`${label} · ${name} accepted as a new Stage checkpoint (prototype memory only). The Working Head now follows ${label}.`);
  }

  function toggleCheck(studyId, id, force) {
    const list = S.checked[studyId] || (S.checked[studyId] = []);
    const has = list.includes(id);
    const want = force === undefined ? !has : force;
    if (want && !has) {
      if (list.length >= 5) { toast("Compare takes 2 to 5 options from one Study."); return; }
      list.push(id);
    }
    if (!want && has) list.splice(list.indexOf(id), 1);
    if (S.compare && S.compare.study === studyId && list.length >= 2) {
      S.compare.ids = list.slice();
      if (!list.includes(S.compare.chosen)) S.compare.chosen = null;
    }
  }

  function startCompare(studyId) {
    const study = idx.study.get(studyId);
    if (!study) return;
    const ready = study.items.filter((item) => item.status === "ready").map((item) => item.id);
    const checked = (S.checked[studyId] || []).filter((id) => ready.includes(id));
    const ids = checked.length >= 2 ? checked.slice(0, 5) : ready.slice(0, 5);
    if (ids.length < 2) return;
    if (checked.length < 2) S.checked[studyId] = ids.slice();
    S.compare = { study: studyId, ids, chosen: null };
    S.view = null;
    S.focus = studyId;
    revealTools();
  }

  function showStudy(studyId) {
    const study = idx.study.get(studyId);
    if (!study) return;
    S.tree = true;
    S.focus = studyId;
    S.collapsed.delete(studyId);
    S.scrollTo = `node:${study.stage}`;
  }

  function simAdmit(id) {
    const item = idx.item.get(id);
    if (!item || !item.admitted) return;
    const study = idx.studyOf.get(id);
    item.status = "ready";
    item.preview = item.admitted.preview ?? null;
    item.at = item.admitted.at || "Just now";
    if (item.admitted.metrics) item.metrics = item.admitted.metrics;
    if (item.advanced) {
      item.advanced.admission = `Admitted ${item.at} · closed loop complete`;
      if (Array.isArray(item.advanced.runs)) {
        const runs = item.advanced.runs.map((run) => (run[2] === "running" ? [run[0], run[1], "ok"] : run));
        runs.push([`run ${String(runs.length + 1).padStart(2, "0")}`, "readback + checks", "ok"]);
        item.advanced.runs = runs;
      }
    }
    delete item.progress;
    delete item.admitted;
    const next = study.items.find((other) => other.status === "queued");
    if (next) {
      next.status = "working";
      next.at = "Started just now";
      next.progress = "Generating · run 1";
      if (next.advanced) {
        next.advanced.worktree = String(next.advanced.worktree || "").replace(/\s*\(not started\)/, "");
        next.advanced.runs = [["run 01", "first pass", "running"]];
        next.advanced.admission = "Not admitted yet: the worktree is still running";
      }
    }
    S.collapsed.delete(study.id);
    toast(`${shortLabel(id)} completed and passed Candidate Admission: it is now a Candidate in the ${study.name}.${next ? ` ${shortLabel(next.id)} started.` : ""}`);
  }

  function simReject(id) {
    const item = idx.item.get(id);
    if (!item) return;
    const study = idx.studyOf.get(id);
    const label = shortLabel(id);
    study.items = study.items.filter((other) => other.id !== id);
    (study.hidden || (study.hidden = [])).unshift({
      kind: "rejected", name: itemLabel(item),
      note: "Completed, then rejected by you before admission. It never entered the Candidate Pool.", at: "Just now",
    });
    for (const key of Object.keys(S.checked)) S.checked[key] = S.checked[key].filter((other) => other !== id);
    if (S.selected === id) S.selected = null;
    index();
    S.collapsed.delete(study.id);
    S.open.add(`adv:${study.id}`);
    toast(`${label} was rejected before admission, so it is not a Candidate. The rejection is kept in the ${study.name}'s Advanced details.`);
  }

  function toast(text) {
    S.toast = text;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { S.toast = null; render(); }, 6500);
  }

  function setHashParam(key, value) {
    const p = params();
    p.set(key, value);
    const next = `#${p.toString()}`;
    if (location.hash === next) return false;
    location.hash = next;
    return true;
  }

  /* ------------------------------------------------------------ previews */

  /* Same site, same camera for every preview: a south-east axonometric or a plan. */
  const ISO = 0.8660254;
  const AXON_BOX = "-38 -16 104 69";
  const PLAN_BOX = "-5 -11 70 54";
  const P = (x, y, z) => [((x - y) * ISO).toFixed(2), ((x + y) * 0.5 - z).toFixed(2)];
  const poly = (points, cls) => `<polygon class="${cls}" points="${points.map((p) => p.join(",")).join(" ")}"/>`;
  const seg = (a, b, cls) => `<line class="${cls}" x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}"/>`;

  /* Painter's order for touching, non-overlapping boxes: a box that lies
     wholly west, north or below another is drawn first. */
  function drawOrder(list) {
    const before = (a, b) => {
      const [ax, ay, az, aw, ad, ah] = a.box, [bx, by, bz] = b.box;
      return ax + aw <= bx + 1e-6 || ay + ad <= by + 1e-6 || az + ah <= bz + 1e-6;
    };
    const out = [], left = list.slice();
    while (left.length) {
      let i = left.findIndex((a) => !left.some((b) => b !== a && before(b, a) && !before(a, b)));
      if (i < 0) i = 0;
      out.push(left.splice(i, 1)[0]);
    }
    return out;
  }

  function faceLines([x, y, z, w, d, h], shape) {
    let s = "";
    const vertical = (step) => {
      for (let t = x + step; t < x + w - 0.01; t += step) s += seg(P(t, y + d, z), P(t, y + d, z + h), "sk-pat");
      for (let t = y + step; t < y + d - 0.01; t += step) s += seg(P(x + w, t, z), P(x + w, t, z + h), "sk-pat");
    };
    const horizontal = (step, cls) => {
      for (let t = z + step; t < z + h - 0.01; t += step) {
        s += seg(P(x, y + d, t), P(x + w, y + d, t), cls);
        s += seg(P(x + w, y, t), P(x + w, y + d, t), cls);
      }
    };
    if (shape.pattern === "piers") vertical(3);
    if (shape.pattern === "fins") vertical(1.5);
    if (shape.pattern === "screen") horizontal(1.2, "sk-pat");
    if (shape.floors) horizontal(shape.floors, "sk-floor");
    return s;
  }

  function axonSvg(shape) {
    const site = D.site;
    let out = "";
    if (site) {
      if (site.river) out += poly([P(-4, site.river[0], 0), P(site.w + 4, site.river[0], 0), P(site.w + 4, site.river[1], 0), P(-4, site.river[1], 0)], "sk-river");
      out += poly([P(0, 0, 0), P(site.w, 0, 0), P(site.w, site.d, 0), P(0, site.d, 0)], "sk-site");
      if (site.setback) out += seg(P(0, site.setback, 0), P(site.w, site.setback, 0), "sk-setback");
    }
    const list = (shape.boxes || []).map((box) => ({ box, accent: false }))
      .concat((shape.accent || []).map((box) => ({ box, accent: true })));
    for (const { box, accent } of drawOrder(list)) {
      const [x, y, z, w, d, h] = box;
      const top = [P(x, y, z + h), P(x + w, y, z + h), P(x + w, y + d, z + h), P(x, y + d, z + h)];
      const south = [P(x, y + d, z), P(x + w, y + d, z), P(x + w, y + d, z + h), P(x, y + d, z + h)];
      const east = [P(x + w, y, z), P(x + w, y + d, z), P(x + w, y + d, z + h), P(x + w, y, z + h)];
      out += `<g>${poly(south, accent ? "sk-acc sk-acc--l" : "sk-l")}${poly(east, accent ? "sk-acc sk-acc--r" : "sk-r")}${accent ? "" : faceLines(box, shape)}${poly(top, accent ? "sk-acc sk-acc--t" : "sk-t")}</g>`;
    }
    if (site && site.trees) {
      for (const [tx, ty] of site.trees) {
        const ground = P(tx, ty, 0), crown = P(tx, ty, 3.2);
        out += `${seg(ground, crown, "sk-trunk")}<circle class="sk-tree" cx="${crown[0]}" cy="${crown[1]}" r="2.3"/>`;
      }
    }
    return `<svg viewBox="${AXON_BOX}" preserveAspectRatio="xMidYMid meet" aria-hidden="true">${out}</svg>`;
  }

  function planSvg(shape) {
    const site = D.site;
    let out = "";
    if (site) {
      if (site.river) out += `<rect class="sk-river" x="-4" y="${site.river[0]}" width="${site.w + 8}" height="${site.river[1] - site.river[0]}"/>`;
      out += `<rect class="sk-site" x="0" y="0" width="${site.w}" height="${site.d}"/>`;
      if (site.setback) out += `<line class="sk-setback" x1="0" y1="${site.setback}" x2="${site.w}" y2="${site.setback}"/>`;
    }
    const list = (shape.boxes || []).map((box) => ({ box, accent: false }))
      .concat((shape.accent || []).map((box) => ({ box, accent: true })))
      .sort((a, b) => (a.box[2] + a.box[5]) - (b.box[2] + b.box[5]));
    for (const { box: [x, y, z, w, d, h], accent } of list) {
      const shade = accent ? 0.85 : 0.25 + 0.6 * Math.min(1, (z + h) / 18);
      out += `<rect class="${accent ? "sk-acc" : "sk-plan"}" x="${x}" y="${y}" width="${w}" height="${d}" style="fill-opacity:${shade.toFixed(2)}"/>`;
    }
    if (site && site.trees) for (const [tx, ty] of site.trees) out += `<circle class="sk-tree" cx="${tx}" cy="${ty}" r="2"/>`;
    return `<svg viewBox="${PLAN_BOX}" preserveAspectRatio="xMidYMid meet" aria-hidden="true">${out}</svg>`;
  }

  /* A preview is a shape key, an image {src}, or nothing. */
  function preview(value, projection = "axon") {
    if (value && typeof value === "object" && value.src) return `<img src="${esc(value.src)}" alt="">`;
    const shape = typeof value === "string" && D.shapes ? D.shapes[value] : null;
    if (!shape) return '<svg viewBox="0 0 64 44" aria-hidden="true"><rect class="sk-none" x="1" y="1" width="62" height="42" rx="4"/></svg>';
    return projection === "plan" ? planSvg(shape) : axonSvg(shape);
  }

  /* ---------------------------------------------------------- rendering */

  const LABELS = {
    base: "Base revision", task: "Task", worktree: "Worktree", scope: "Scope", admission: "Admission",
    record: "Stage record", model: "Model run", accepted: "Accepted by", request: "Requested by",
    worktrees: "Worktrees", edits: "Edits",
  };
  const HIDDEN = { rejected: "Rejected before admission", failed: "Failed", redo: "Redo attempt", superseded: "Superseded" };

  function dl(values, skip = []) {
    const rows = Object.entries(values || {}).filter(([key, value]) => !skip.includes(key) && (typeof value === "string" || typeof value === "number"));
    return rows.length ? `<dl>${rows.map(([key, value]) => `<dt>${esc(LABELS[key] || key)}</dt><dd>${esc(value)}</dd>`).join("")}</dl>` : "";
  }
  function adv(key, body) {
    return `<details class="adv" data-key="${esc(key)}"${S.open.has(key) ? " open" : ""}><summary>Advanced details</summary><div class="adv__body">${body || "<p>Nothing recorded.</p>"}</div></details>`;
  }
  function itemAdvanced(item) {
    const a = item.advanced || {};
    let out = dl(a, ["runs"]);
    if (Array.isArray(a.runs)) {
      const result = (r) => (r === "ok" ? "ok" : r === "running" ? "running" : "bad");
      out += `<h4>Runs in this worktree (${a.runs.length}) · not Candidates</h4>`;
      out += a.runs.length
        ? `<ul>${a.runs.map(([id, what, r]) => `<li data-result="${result(r)}"><span>${esc(id)}</span><span>${esc(what)}</span><span>${esc(r)}</span></li>`).join("")}</ul>`
        : "<p>No runs yet.</p>";
    }
    return out;
  }
  function studyAdvanced(study) {
    const hidden = study.hidden || [];
    return `${dl(study.advanced)}<h4>Not Candidates (${hidden.length}) · kept for provenance</h4>${hidden.length
      ? `<ul class="adv__hidden">${hidden.map((h) => `<li><span>${esc(HIDDEN[h.kind] || h.kind)}</span><span><em>${esc(h.name)}</em> · ${esc(h.note)}${h.at ? ` · ${esc(h.at)}` : ""}</span></li>`).join("")}</ul>`
      : "<p>None.</p>"}<p class="adv__rule">Only admitted Candidates appear in the tree (#294). Runs, repairs, redo attempts and results rejected before admission stay here.</p>`;
  }

  const viewingPill = () => `<span class="pill pill--accent pill--icon">${icon("eye")}Viewing</span>`;

  function itemPills(item, line) {
    const out = [];
    const isHead = D.head.parent === item.id;
    const became = D.stages.find((stage) => stage.parent === item.id);
    if (item.status === "working") out.push('<span class="pill pill--run">Agent working…</span>');
    else if (item.status === "queued") out.push('<span class="pill pill--queued">Queued</span>');
    if (isHead) out.push('<span class="pill pill--solid">Working Head</span>');
    if (item.status === "ready" && item.continued) {
      const current = line.has(item.id);
      out.push(`<span class="pill ${current ? "pill--accent" : "pill--quiet"}" title="${esc(item.continued.at || "")}">${esc(continuedText(item.continued))}${current ? "" : " · left"}</span>`);
    }
    if (became) out.push(`<span class="pill pill--plain pill--icon">${icon("check")}Accepted as ${esc(became.label)}</span>`);
    if (item.status === "ready" && !item.continued && !isHead) out.push('<span class="pill">Ready</span>');
    if (S.view === item.id) out.push(viewingPill());
    return out.join("");
  }

  function devbar() {
    const states = [1, 2, 3, 4, 5, 6].map((n) => {
      const title = (base.states && base.states[n] && base.states[n].title) || `State ${n}`;
      return `<button type="button" data-act="state" data-id="${n}" aria-pressed="${S.n === n}" title="${esc(`${n}. ${title}`)}">${n}</button>`;
    }).join("");
    const theme = document.documentElement.dataset.theme;
    const themes = THEMES.map((t) => `<button type="button" data-act="theme" data-id="${t}" aria-pressed="${theme === t}">${t[0].toUpperCase()}${t.slice(1)}</button>`).join("");
    const sets = Array.isArray(window.CANDIDATE_GRAPH_DATASETS) && window.CANDIDATE_GRAPH_DATASETS.length
      ? window.CANDIDATE_GRAPH_DATASETS : [{ name: DEFAULT_DATA, label: DEFAULT_DATA }];
    const options = sets.some((set) => set.name === dataName) ? sets : sets.concat([{ name: dataName, label: dataName }]);
    const pending = [...idx.item.values()];
    const admit = pending.find((item) => item.status === "working" && item.admitted);
    const reject = pending.find((item) => (item.status === "queued" || item.status === "working") && !item.admitted);
    const sim = admit || reject ? `<span class="devbar__group devbar__sim"><span class="devbar__label">Simulate</span>${admit
      ? `<button type="button" data-act="sim-admit" data-id="${esc(admit.id)}" title="The running worktree completes and passes Candidate Admission">${esc(admit.letter || admit.name)} admitted</button>` : ""}${reject
      ? `<button type="button" data-act="sim-reject" data-id="${esc(reject.id)}" title="The result completes but is rejected before admission: it never becomes a Candidate">${esc(reject.letter || reject.name)} rejected before admission</button>` : ""}</span>` : "";
    return `<div class="devbar" role="region" aria-label="Prototype controls">
      <span class="devbar__mark">DEV PROTOTYPE · fixture data · not production</span>
      <span class="devbar__group"><span class="devbar__label">State</span>${states}</span>
      <span class="devbar__group devbar__theme"><span class="devbar__label">Theme</span>${themes}</span>
      <label class="devbar__group devbar__data"><span class="devbar__label">Data</span><select data-act="data" aria-label="Dataset">${options.map((set) => `<option value="${esc(set.name)}"${set.name === dataName ? " selected" : ""}>${esc(set.label || set.name)}</option>`).join("")}</select></label>
      ${sim}<span class="devbar__spacer"></span>
    </div>`;
  }

  function sidebar() {
    const threads = S.sidebar ? `<p class="side__label side__text">Threads</p>${(D.project.threads || []).map((title, i) => `<button type="button" class="side__item side__thread" data-act="noop"${i === 0 ? ' aria-current="page"' : ""}><span>${esc(title)}</span></button>`).join("")}` : "";
    return `<aside class="side" aria-label="Projects">
      <div class="side__top"><strong class="wordmark">MonkeyHub</strong><button type="button" class="icon-btn" data-act="sidebar" aria-label="${S.sidebar ? "Collapse" : "Expand"} sidebar">${icon("sidebar")}</button></div>
      <div class="side__body">
        <button type="button" class="side__item" data-act="noop" title="New chat">${icon("plus")}<span class="side__text">New chat</span></button>
        <button type="button" class="side__item" data-act="sidebar" title="${esc(D.project.name)}" aria-current="page">${icon("folder")}<span class="side__text">${esc(D.project.name)}</span></button>
        ${threads}
      </div>
      <div class="side__foot"><button type="button" class="side__item" data-act="noop" title="Settings">${icon("settings")}<span class="side__text">Settings</span></button></div>
    </aside>`;
  }

  function topbar() {
    const stage = currentStage(), head = D.head.parent, b = badgeCounts();
    const beyond = kindOf(head) === "stage" && head === stage.id ? "" : `› ${stageTag(head)}`;
    return `<header class="topbar">
      <button type="button" class="icon-btn topbar__menu" data-act="sidebar" aria-label="Projects">${icon("sidebar")}</button>
      <span class="topbar__project">${icon("folder")}${esc(D.project.name)}</span><span class="topbar__sep">/</span>
      <button type="button" class="stage-chip" data-act="tree-toggle" aria-expanded="${S.tree}" aria-controls="design-tree" title="Design Tree: Stages, Studies and Candidates">${icon("tree")}<span class="stage-chip__stage">${esc(stageLabel(stage))}</span>${beyond ? `<span class="stage-chip__head">${esc(beyond)}</span>` : ""}<span class="stage-chip__current">· Current</span>${icon("chevron", "icon stage-chip__caret")}</button>
      <span class="topbar__badges">
        ${b.ready ? `<button type="button" class="badge" data-act="show-study" data-id="${esc(b.readyStudy)}"><span class="badge__dot"></span><span>${b.ready}<span class="badge__long"> ${b.ready === 1 ? "alternative" : "alternatives"}</span> ready</span></button>` : ""}
        ${b.running ? `<button type="button" class="badge" data-act="show-study" data-id="${esc(b.runStudy)}"><span class="badge__dot dot--run"></span><span>${b.running}<span class="badge__long"> ${b.running === 1 ? "task" : "tasks"}</span> running</span></button>` : ""}
      </span>
      ${S.view ? `<span class="topbar__viewing">${icon("eye")}Viewing ${esc(stageTag(S.view))} · read-only<button type="button" data-act="back-head" aria-label="Back to the Working Head">${icon("close")}</button></span>` : ""}
      <span class="topbar__spacer"></span>
      <span class="topbar__hint">Opens on the Working Head</span>
    </header>`;
  }

  function resultCard(message) {
    const study = idx.study.get(message.study);
    if (!study) return "";
    const t = tally(study), n = study.items.length, noun = study.noun || "options";
    const headline = message.headline || (t.ready === n
      ? `${n} ${noun} ready`
      : `${t.ready} of ${n} ready${t.working ? ` · ${t.working} Agent working…` : ""}${t.queued ? ` · ${t.queued} queued` : ""}`);
    const thumbs = study.items.map((item) => `<span class="result__thumb" data-status="${esc(item.status)}">${item.status === "ready" ? preview(item.preview) : ""}<b>${esc(item.letter || "")}</b></span>`).join("");
    const stage = idx.stage.get(study.stage);
    return `<article class="result" data-study="${esc(study.id)}" aria-label="Agent result: ${esc(study.name)}">
      <div class="result__head">${icon("tree")}<div><strong>${esc(study.name)}</strong><span>from ${esc(stage ? stageLabel(stage) : study.stage)} · ${esc(study.agent)}</span></div></div>
      <div class="result__thumbs">${thumbs}</div>
      <div class="result__foot"><span class="result__line">${esc(headline)}</span><span class="result__arrow" aria-hidden="true">→</span><button type="button" class="result__open" data-act="show-study" data-id="${esc(study.id)}">Show in Design Tree</button></div>
    </article>`;
  }

  function chat() {
    const messages = D.chat.map((m) => {
      switch (m.kind) {
        case "day": return `<p class="msg msg--day">${esc(m.text)}</p>`;
        case "user": return `<p class="msg msg--user">${esc(m.text)}</p>`;
        case "agent": return `<p class="msg msg--agent">${esc(m.text)}</p>`;
        case "event": return `<p class="msg msg--event">${icon("tree")}<span>${esc(m.text)}</span></p>`;
        case "result": return resultCard(m);
        default: return "";
      }
    }).join("");
    return `<section class="chat" aria-label="Conversation">
      <header class="chat__head"><h1>${esc(D.project.chatTitle || "Conversation")}</h1></header>
      <div class="chat__log" role="log">${messages}</div>
      <div class="composer" aria-hidden="true"><div class="composer__input">Ask the Agent about ${esc(D.project.name)}…</div><div class="composer__bottom">${icon("plus")}<span>Arch Agent · fixture</span><span class="composer__send">${icon("send")}</span></div></div>
    </section>`;
  }

  function acceptConfirm() {
    const label = nextStageLabel();
    return `<div class="confirm" role="group" aria-label="Accept as next Stage"><strong>Accept ${esc(shortLabel(D.head.parent))} as ${esc(label)}?</strong>This creates an immutable Stage checkpoint from the Working Head. Stage authority and acceptance rules still apply. In this prototype nothing is written.<div class="confirm__actions"><button type="button" class="btn btn--primary btn--small" data-act="accept-confirm">${icon("flag")}Accept as ${esc(label)}</button><button type="button" class="btn btn--small" data-act="accept-cancel">Cancel</button></div></div>`;
  }

  function compareButton(study) {
    const ready = study.items.filter((item) => item.status === "ready");
    if (ready.length < 2) return "";
    const checked = (S.checked[study.id] || []).filter((id) => ready.some((item) => item.id === id));
    const active = S.compare && S.compare.study === study.id;
    let label = `Compare ${Math.min(5, ready.length)}`, disabled = false, title = "Compare side by side with the same camera";
    if (checked.length >= 2) label = `Compare ${checked.length}`;
    else if (checked.length === 1) { label = "Compare"; disabled = true; title = "Pick 2 to 5 options from this Study"; }
    return `<button type="button" class="btn btn--small" data-act="compare" data-id="${esc(study.id)}"${disabled ? " disabled" : ""} title="${esc(title)}" aria-pressed="${Boolean(active)}">${icon("compare")}${label}</button>`;
  }

  function itemActions(item, study) {
    const key = `adv:${item.id}`;
    if (item.status !== "ready") {
      const note = item.status === "working"
        ? "An Agent worktree is still running. It becomes a Candidate only after it completes and is admitted; its runs stay in Advanced details."
        : "Waiting for a worker. There is nothing to view or continue yet.";
      return `<p class="acts__note">${note}</p>${adv(key, itemAdvanced(item))}`;
    }
    const isHead = D.head.parent === item.id;
    const became = D.stages.find((stage) => stage.parent === item.id);
    const checked = (S.checked[study.id] || []).includes(item.id);
    const acceptTitle = became ? `Already accepted as ${became.label}` : isHead
      ? "Create an immutable Stage checkpoint from the Working Head" : "Continue from this Candidate first: a Stage is accepted from the Working Head";
    const left = wouldLeave(item.id);
    return `<div class="acts" role="group" aria-label="Actions for ${esc(shortLabel(item.id))}">
      <button type="button" class="act" data-act="view" data-id="${esc(item.id)}" aria-pressed="${S.view === item.id}">${icon("eye")}View</button>
      <button type="button" class="act" data-act="check-toggle" data-id="${esc(item.id)}" data-study="${esc(study.id)}" aria-pressed="${checked}">${icon("compare")}${checked ? "In compare" : "Compare"}</button>
      <button type="button" class="act act--primary" data-act="continue" data-id="${esc(item.id)}"${isHead ? ' disabled title="Already the Working Head"' : ""}>${icon("arrow")}Continue from here</button>
      <button type="button" class="act act--stage" data-act="accept-ask" data-id="${esc(item.id)}"${isHead && !became ? "" : " disabled"} title="${esc(acceptTitle)}">${icon("flag")}Accept as next Stage</button>
      <button type="button" class="act" disabled title="Reject / Archive arrives with #289">Reject / Archive <small>needs #289</small></button>
    </div>
    <p class="acts__note">${isHead
      ? "Continued: this is the Working Head. Accepting it as a Stage is a separate step."
      : `View never moves the Working Head. Continue moves it${left.length ? ` (${left.join(" and ")} stay in history)` : ""}; accepting a Stage is a separate step.`}</p>
    ${S.accept === item.id ? acceptConfirm() : ""}
    ${adv(key, itemAdvanced(item))}`;
  }

  function lineRow(lineNode, line) {
    const selected = S.selected === lineNode.id, viewing = S.view === lineNode.id, current = line.has(lineNode.id), isHead = D.head.parent === lineNode.id;
    const actions = selected ? `<div class="acts" role="group" aria-label="Actions for ${esc(lineNode.name)}">
        <button type="button" class="act" data-act="view" data-id="${esc(lineNode.id)}" aria-pressed="${viewing}">${icon("eye")}View</button>
        <button type="button" class="act act--primary" data-act="continue" data-id="${esc(lineNode.id)}"${isHead ? " disabled" : ""}>${icon("arrow")}Continue from here</button>
        <button type="button" class="act act--stage" data-act="accept-ask" data-id="${esc(lineNode.id)}"${isHead ? "" : " disabled"} title="${isHead ? "Create a Stage from the Working Head" : "Continue from this line first"}">${icon("flag")}Accept as next Stage</button>
        <button type="button" class="act" disabled title="Reject / Archive arrives with #289">Reject / Archive <small>needs #289</small></button>
      </div>
      <p class="acts__note">A retained line is never deleted. Continuing from it starts the Working Head there again.</p>
      ${S.accept === lineNode.id ? acceptConfirm() : ""}
      ${adv(`adv:${lineNode.id}`, dl(lineNode.advanced))}` : "";
    return `<div class="oldline" data-anchor="node:${esc(lineNode.id)}" data-selected="${selected}" data-viewing="${viewing}" data-line="${current}">
      <button type="button" class="crow__main" data-act="select" data-id="${esc(lineNode.id)}" aria-expanded="${selected}">
        <span class="thumb">${preview(lineNode.preview)}</span>
        <span class="crow__text"><span class="crow__title"><span>${current ? "" : "Earlier line · "}${esc(lineNode.name)}</span></span>
          <span class="crow__sum">${esc(lineNode.summary || "")}</span>
          <span class="crow__meta"><span>${esc(lineNode.by || "")} · ${esc(lineNode.at || "")}</span>${isHead ? '<span class="pill pill--solid">Working Head</span>' : current ? "" : '<span class="pill pill--queued">Not current · kept</span>'}${viewing ? viewingPill() : ""}</span></span>
      </button>
      ${actions}
    </div>`;
  }

  function itemRow(item, study, line) {
    const selected = S.selected === item.id, viewing = S.view === item.id, current = line.has(item.id);
    const pending = item.status !== "ready";
    const checked = (S.checked[study.id] || []).includes(item.id);
    const check = pending
      ? '<span class="crow__check" aria-hidden="true"></span>'
      : `<label class="crow__check" title="Select to compare"><input type="checkbox" data-act="check" data-id="${esc(item.id)}" data-study="${esc(study.id)}"${checked ? " checked" : ""} aria-label="Compare ${esc(itemLabel(item))}"></label>`;
    const thumb = pending
      ? `<span class="slot-thumb">${item.status === "working" ? "working…" : "queued"}</span>`
      : `<span class="thumb">${preview(item.preview)}</span>`;
    const not = pending
      ? `<span class="crow__not">${item.status === "working" ? `${esc(item.progress || "Running")} · not a Candidate until admitted` : "Not started · not a Candidate until admitted"}</span>` : "";
    const lines = D.lines.filter((l) => l.parent === item.id);
    return `<li class="crow" data-anchor="node:${esc(item.id)}" data-status="${esc(item.status)}" data-line="${current}" data-selected="${selected}" data-viewing="${viewing}">
      <div class="crow__row">${check}<button type="button" class="crow__main" data-act="select" data-id="${esc(item.id)}" aria-expanded="${selected}">${thumb}<span class="crow__text">
        <span class="crow__title">${item.letter ? `<span class="crow__letter">${esc(item.letter)}</span>` : ""}<span>${esc(item.name)}</span></span>
        <span class="crow__sum">${esc(item.summary || "")}</span>
        <span class="crow__meta"><span>${esc(item.by || "")} · ${esc(item.at || "")}</span>${itemPills(item, line)}</span>${not}</span></button></div>
      ${selected ? itemActions(item, study) : ""}
      ${lines.map((l) => lineRow(l, line)).join("")}
    </li>`;
  }

  function studyBlock(study, line) {
    const collapsed = S.collapsed.has(study.id);
    const t = tally(study), n = study.items.length;
    const rejected = (study.hidden || []).filter((h) => h.kind === "rejected").length;
    const status = [
      t.ready ? `<span class="pill">${t.ready} ready</span>` : "",
      t.working ? `<span class="pill pill--run">${t.working} Agent working…</span>` : "",
      t.queued ? `<span class="pill pill--queued">${t.queued} queued</span>` : "",
      rejected ? `<span class="study__hint">+${rejected} rejected before admission</span>` : "",
    ].join("");
    const body = collapsed
      ? `<div class="study__mini">${study.items.map((item) => `<span class="thumb" data-status="${esc(item.status)}" title="${esc(itemLabel(item))}">${item.status === "ready" ? preview(item.preview) : ""}</span>`).join("")}</div>`
      : `<ol class="study__items">${study.items.map((item) => itemRow(item, study, line)).join("")}</ol>${adv(`adv:${study.id}`, studyAdvanced(study))}`;
    return `<div class="study" data-anchor="study:${esc(study.id)}" data-collapsed="${collapsed}" data-focus="${S.focus === study.id}">
      <div class="study__head">
        <button type="button" class="study__toggle" data-act="study-toggle" data-id="${esc(study.id)}" aria-expanded="${!collapsed}" aria-label="${collapsed ? "Expand" : "Collapse"} ${esc(study.name)}">${icon("chevron")}</button>
        <div class="study__title">${esc(study.name)} <span>· ${count(n, "option", "options")}</span></div>
        <div class="study__compare">${compareButton(study)}</div>
        <div class="study__meta">${esc(study.agent || "")} · asked by ${esc(study.askedBy || "")} · ${esc(study.createdAt || "")}</div>
        <div class="study__status">${status}</div>
      </div>
      ${body}
    </div>`;
  }

  function stageNode(stage, current, segment, line) {
    const selected = S.selected === stage.id, viewing = S.view === stage.id, isHead = D.head.parent === stage.id;
    const studies = D.studies.filter((study) => study.stage === stage.id);
    const lines = D.lines.filter((l) => l.parent === stage.id);
    const from = stage.parent ? stage.fromNote || `Accepted from ${shortLabel(stage.parent)}` : "";
    const tag = isHead ? '<span class="pill pill--solid">Working Head</span>'
      : current && stage.id === currentStage().id ? '<span class="pill pill--accent">Current Stage</span>' : "";
    const actions = selected ? `<div class="acts" role="group" aria-label="Actions for ${esc(stageLabel(stage))}">
        <button type="button" class="act" data-act="view" data-id="${esc(stage.id)}" aria-pressed="${viewing}">${icon("eye")}View read-only</button>
        <button type="button" class="act act--primary" data-act="continue" data-id="${esc(stage.id)}"${isHead ? ' disabled title="Already the Working Head"' : ""}>${icon("arrow")}Continue from here</button>
      </div>
      <p class="acts__note">${isHead ? "This Stage is the Working Head: new work and Agent tasks start here."
        : `Viewing never moves the Working Head. Continue starts a new line here${wouldLeave(stage.id).length ? `; ${wouldLeave(stage.id).join(" and ")} stay in history` : ""}.`}</p>
      ${adv(`adv:${stage.id}`, dl(stage.advanced))}` : "";
    return `<section class="snode" data-anchor="node:${esc(stage.id)}" data-line="${current}" data-seg="${segment}" data-selected="${selected}" data-viewing="${viewing}">
      <div class="snode__badge" aria-hidden="true">${esc(stage.label)}</div>
      <button type="button" class="scard" data-act="select" data-id="${esc(stage.id)}" aria-expanded="${selected}">
        <span class="scard__text">
          <span class="scard__title"><strong>${esc(stageLabel(stage))}</strong>${tag}${viewing ? viewingPill() : ""}</span>
          <span class="scard__meta">Accepted ${esc(stage.acceptedAt || "")} · ${esc(stage.acceptedBy || "")}</span>
          <span class="scard__sum">${esc(stage.summary || "")}</span>
          ${from ? `<span class="scard__from">${esc(from)}</span>` : ""}
          ${current ? "" : '<span class="scard__off">Not on the current line · kept in history</span>'}
        </span>
        <span class="thumb">${preview(stage.preview)}</span>
      </button>
      ${actions}
      ${studies.map((study) => studyBlock(study, line)).join("")}
      ${lines.map((l) => lineRow(l, line)).join("")}
    </section>`;
  }

  function headNode(line) {
    const from = D.head.parent, kind = kindOf(from), stage = currentStage();
    const fork = !line.has(lastStage().id);
    let text, actions = "";
    if (kind === "stage") {
      text = fork
        ? `New line from ${stageLabel(stage)}. The later Stages stay in history, de-emphasised.`
        : `At ${stageLabel(stage)}. New work and Agent tasks start here.`;
    } else {
      const node = nodeOf(from);
      text = `${continuedText(node.continued)} from ${shortLabel(from)}${node.continued && node.continued.at ? ` · ${node.continued.at}` : ""}. Based on ${stageLabel(stage)}; not a Stage yet.`;
      actions = `<div class="acts"><button type="button" class="act act--stage" data-act="accept-ask" data-id="head">${icon("flag")}Accept as next Stage (${esc(nextStageLabel())})…</button></div>${S.accept === "head" ? acceptConfirm() : ""}`;
    }
    return `<section class="snode snode--head" data-anchor="node:head" data-seg="none" data-line="true">
      <div class="snode__badge snode__badge--head${fork ? " snode__badge--fork" : ""}" aria-hidden="true"></div>
      <div class="hcard">
        <div class="hcard__title"><strong>Current</strong><span class="pill pill--solid">Working Head</span></div>
        <p>${esc(text)}</p>
        <p class="hcard__rule">Opening the project follows the Working Head. Viewing history never moves it; Continue does. Accepting a Stage is its own step.</p>
        ${actions}
      </div>
    </section>`;
  }

  function spineStrip(line) {
    const parts = [];
    D.stages.forEach((stage, i) => {
      if (i > 0) parts.push(`<span class="strip__link" data-line="${line.has(stage.id) && line.has(D.stages[i - 1].id)}" aria-hidden="true"></span>`);
      parts.push(`<button type="button" class="strip__stage" data-act="jump" data-id="${esc(stage.id)}" data-line="${line.has(stage.id)}" title="${esc(stageLabel(stage))}"><b>${esc(stage.label)}</b><span>${esc(stage.name)}</span></button>`);
    });
    parts.push(`<span class="strip__link" data-line="${line.has(lastStage().id)}" aria-hidden="true"></span>`);
    parts.push(`<button type="button" class="strip__head" data-act="jump" data-id="head" title="${esc(headLabel())}">Current</button>`);
    return `<nav class="strip" aria-label="Stage spine">${parts.join("")}</nav>`;
  }

  function drawer() {
    const line = onLine();
    const nodes = D.stages.map((stage, i) => {
      const next = D.stages[i + 1];
      const current = line.has(stage.id);
      const segment = next ? (current && line.has(next.id) ? "accent" : "plain") : current ? "accent" : "plain";
      return stageNode(stage, current, segment, line);
    }).join("");
    return `<aside class="drawer" id="design-tree" aria-label="Design Tree">
      <div class="drawer__head"><div class="drawer__title"><h2>Design Tree</h2><p>${esc(D.project.name)} · admitted Candidates only</p></div><button type="button" class="icon-btn" data-act="tree-close" aria-label="Close Design Tree">${icon("close")}</button></div>
      ${spineStrip(line)}
      <div class="drawer__body">${nodes}${headNode(line)}
        <div class="drawer__legend" aria-label="Legend"><span class="legend"><i class="lg-line"></i>Current line</span><span class="legend"><i class="lg-view"></i>Viewing</span><span class="legend"><i class="lg-run"></i>Agent worktree, not a Candidate yet</span><span class="legend"><i class="lg-old"></i>Earlier line, kept</span></div>
      </div>
    </aside>`;
  }

  function banner() {
    const id = S.view, node = nodeOf(id), isHead = D.head.parent === id;
    const detail = kindOf(id) === "stage"
      ? `${stageLabel(node)}, accepted ${node.acceptedAt} by ${node.acceptedBy}.`
      : `${kindOf(id) === "item" ? itemLabel(node) : node.name}.`;
    return `<div class="ro-banner" role="status">${icon("eye")}
      <span class="ro-banner__text"><strong>Viewing ${esc(stageTag(id))}</strong> · read-only <span class="ro-banner__sep">·</span></span>
      <button type="button" class="btn btn--primary" data-act="continue" data-id="${esc(id)}"${isHead ? " disabled" : ""}>Continue from here</button>
      <button type="button" class="btn" data-act="back-head">Back to Working Head</button>
      <span class="ro-banner__sub">${esc(detail)} Working Head unchanged: ${esc(headLabel())}.</span>
    </div>`;
  }

  function compareView() {
    const study = idx.study.get(S.compare.study);
    const line = onLine();
    const ids = S.compare.ids.filter((id) => idx.item.has(id));
    const chosen = ids.includes(S.compare.chosen) ? S.compare.chosen : null;
    const readyCount = study.items.filter((item) => item.status === "ready").length;
    const stage = idx.stage.get(study.stage);
    const tiles = ids.map((id) => {
      const item = idx.item.get(id), isChosen = chosen === id;
      return `<article class="ctile" data-chosen="${isChosen}" aria-label="${esc(itemLabel(item))}">
        <div class="ctile__head">${item.letter ? `<span class="crow__letter">${esc(item.letter)}</span>` : ""}<span>${esc(item.name)}</span></div>
        <div class="ctile__view">${preview(item.preview, S.projection)}</div>
        <div class="ctile__metrics">${(item.metrics || []).map(([k, v]) => `<span>${esc(k)} <b>${esc(v)}</b></span>`).join("")}</div>
        <div class="ctile__foot"><button type="button" class="btn btn--small${isChosen ? " btn--primary" : ""}" data-act="compare-choose" data-id="${esc(id)}" aria-pressed="${isChosen}">${isChosen ? `${icon("check")}Chosen` : "Choose"}</button>${itemPills(item, line)}</div>
      </article>`;
    }).join("");
    let choice = '<div class="compare__choice"><p>Choose one to continue from. Choosing changes nothing until you press Continue.</p></div>';
    if (chosen) {
      const left = wouldLeave(chosen);
      choice = `<div class="compare__choice"><strong>Chosen: ${esc(itemLabel(idx.item.get(chosen)))}</strong>
        <button type="button" class="btn btn--primary btn--small" data-act="continue" data-id="${esc(chosen)}"${D.head.parent === chosen ? " disabled" : ""}>${icon("arrow")}Continue from here</button>
        <button type="button" class="btn btn--small" data-act="view" data-id="${esc(chosen)}">${icon("eye")}View alone</button>
        <p>Choosing does not move the Working Head or accept anything. Continue moves the Working Head to ${esc(shortLabel(chosen))}${left.length ? `; ${esc(left.join(" and "))} stay in history` : ""}. Accepting a Stage stays a separate step.</p></div>`;
    }
    return `<div class="compare" role="region" aria-label="Compare ${esc(study.name)}">
      <div class="compare__head"><div><h2>Compare · ${esc(study.name)}</h2><p>${ids.length} of ${readyCount} · from ${esc(stage ? stageLabel(stage) : study.stage)} · selection kept in the tree</p></div>
        ${S.tree ? "" : `<button type="button" class="btn btn--small" data-act="show-study" data-id="${esc(study.id)}">${icon("tree")}Back to tree</button>`}
        <button type="button" class="btn btn--small" data-act="compare-exit">Exit compare</button></div>
      <p class="compare__frame">${icon("camera")}Same camera for every option · ${S.projection === "plan" ? "plan at a site-fixed scale" : "south-east axonometric at a site-fixed scale"}</p>
      <div class="compare__grid" data-count="${ids.length}">${tiles}</div>
      ${choice}
    </div>`;
  }

  function workspace() {
    if (S.surface !== "arch") {
      const surfaces = {
        diagram: ["Diagram", "board", "Surface", "Board pages for the same project and the same Working Head."],
        drawing: ["Drawing", "drawing", "Tool", "A Tool over the project: drawings project the Working Head; they are not another authority (#295)."],
        monitor: ["Monitor", "chart", "Tool", "Usage and cost for this machine; not project state."],
      };
      const [name, ic, level, text] = surfaces[S.surface];
      return `<section class="ws" aria-label="${esc(name)}"><div class="ws__bar"><strong>${esc(name)}</strong><span class="ws__crumb">${level}</span><span class="ws__base" data-mode="head">Follows Working Head · ${esc(headLabel())}</span></div>
        <div class="viewport"><div class="ws-placeholder">${icon(ic)}<h2>${esc(name)} placeholder</h2><p>${esc(text)}</p><p>The Design Tree stays in the project bar above, whichever surface or tool is open.</p></div></div></section>`;
    }
    const target = S.view || D.head.parent;
    const bar = `<div class="ws__bar"><strong>Arch</strong><span class="ws__crumb">Modeling</span>
      <span class="ws__base" data-mode="${S.view ? "view" : "head"}">${S.view ? `Viewing ${esc(stageTag(S.view))} · editing base unchanged` : `Editing base · Working Head · ${esc(headLabel())}`}</span>
      <div class="seg" role="group" aria-label="Projection"><button type="button" data-act="proj" data-id="axon" aria-pressed="${S.projection === "axon"}">Axon</button><button type="button" data-act="proj" data-id="plan" aria-pressed="${S.projection === "plan"}">Plan</button></div></div>`;
    const body = S.compare ? compareView() : `${S.view ? banner() : ""}
      <div class="viewport__tools" aria-hidden="true"><span>${icon("orbit")}</span><span>${icon("pan")}</span><span>${icon("section")}</span></div>
      <div class="viewport__model">${preview(previewOf(target), S.projection)}</div>
      <div class="viewport__foot"><span>Viewport placeholder · fixture sketch</span><span>${S.view ? "Read-only" : "Working Head"} · ${esc(S.view ? shortLabel(S.view) : headLabel())}</span></div>`;
    return `<section class="ws" aria-label="Arch · Modeling">${bar}<div class="viewport" data-readonly="${Boolean(S.view) && !S.compare}">${body}</div></section>`;
  }

  function rail() {
    const tool = (id, label, ic, title) => `<button type="button" class="rail__tool" data-act="surface" data-id="${id}" aria-pressed="${S.panel && S.surface === id}" title="${esc(title)}">${icon(ic)}<span>${label}</span></button>`;
    return `<nav class="rail" aria-label="Project tools">
      <div class="rail__group" role="group" aria-label="Surfaces"><span class="rail__caption">Surfaces</span>${tool("arch", "Arch", "cube", "Arch · 3D modeling")}${tool("diagram", "Diagram", "board", "Diagram · Board pages")}</div>
      <div class="rail__group" role="group" aria-label="Tools"><span class="rail__caption">Tools</span>${tool("drawing", "Drawing", "drawing", "Drawing · a Tool over the Working Head")}${tool("monitor", "Monitor", "chart", "Monitor · usage and cost")}</div>
      <div class="rail__spacer"></div>
      <div class="rail__group" role="group" aria-label="This project"><span class="rail__caption">Project</span><button type="button" class="rail__tool" data-act="noop">${icon("folder")}<span>Info</span></button></div>
      <button type="button" class="rail__tool rail__tool--view" data-act="panel" aria-pressed="${S.panel}">${icon("panel")}<span>${S.panel ? "Hide tools" : "Show tools"}</span></button>
    </nav>`;
  }

  function render() {
    const oldBody = app.querySelector(".drawer__body");
    const keepTree = oldBody ? oldBody.scrollTop : null;
    const oldLog = app.querySelector(".chat__log");
    const keepLog = oldLog ? oldLog.scrollTop : null;
    const active = document.activeElement && app.contains(document.activeElement) && document.activeElement.dataset
      ? { act: document.activeElement.dataset.act, id: document.activeElement.dataset.id } : null;

    app.innerHTML = `${devbar()}<div class="hub" data-sidebar="${S.sidebar ? "open" : "closed"}" data-tree="${S.tree ? "open" : "closed"}" data-panel="${S.panel ? "open" : "closed"}">
      ${sidebar()}
      <div class="main">${topbar()}<div class="content">${chat()}${S.tree ? drawer() : ""}<div class="resizer" aria-hidden="true"></div>${workspace()}</div></div>
      ${rail()}
    </div>${S.toast ? `<div class="toast" role="status">${esc(S.toast)}</div>` : ""}`;

    const body = app.querySelector(".drawer__body");
    if (body) {
      if (keepTree !== null) body.scrollTop = keepTree;
      if (S.scrollTo) {
        const target = body.querySelector(`[data-anchor="${cssEsc(S.scrollTo)}"]`);
        if (target) body.scrollTop += target.getBoundingClientRect().top - body.getBoundingClientRect().top - 6;
        S.scrollTo = null;
      }
    }
    const log = app.querySelector(".chat__log");
    if (log) {
      if (S.chatTo) {
        const card = S.chatTo.startsWith("res:") ? log.querySelector(`.result[data-study="${cssEsc(S.chatTo.slice(4))}"]`) : null;
        log.scrollTop = card ? log.scrollTop + card.getBoundingClientRect().top - log.getBoundingClientRect().top - 90 : log.scrollHeight;
        S.chatTo = null;
      } else if (keepLog !== null) log.scrollTop = keepLog;
    }
    if (active && active.act) {
      const selector = active.id ? `[data-act="${cssEsc(active.act)}"][data-id="${cssEsc(active.id)}"]` : `[data-act="${cssEsc(active.act)}"]:not([data-id])`;
      const el = app.querySelector(selector);
      if (el) el.focus({ preventScroll: true });
    }
    document.documentElement.dataset.state = String(S.n);
    document.documentElement.dataset.ready = "1";
  }

  /* -------------------------------------------------------------- events */

  function onClick(event) {
    const el = event.target.closest("[data-act]");
    if (!el || el.disabled || !app.contains(el)) return;
    if (el.tagName === "INPUT" || el.tagName === "SELECT") return;
    const act = el.dataset.act, id = el.dataset.id;
    switch (act) {
      case "state": {
        const n = Number(id);
        if (!setHashParam("state", String(n))) { preset(n); appliedState = n; break; }
        return;
      }
      case "theme": setHashParam("theme", id); return;
      case "tree-toggle":
        S.tree = !S.tree;
        if (S.tree) { S.scrollTo = `node:${currentStage().id}`; if (narrowQuery.matches) S.panel = false; }
        break;
      case "tree-close": S.tree = false; break;
      case "show-study": showStudy(id); if (narrowQuery.matches) S.panel = false; break;
      case "jump": S.scrollTo = `node:${id}`; break;
      case "select": S.selected = S.selected === id ? null : id; S.accept = null; break;
      case "view": S.view = id; S.compare = null; S.selected = id; revealTools(); break;
      case "back-head": S.view = null; break;
      case "check-toggle": toggleCheck(el.dataset.study, id); break;
      case "compare": startCompare(id); break;
      case "compare-choose": S.compare.chosen = S.compare.chosen === id ? null : id; break;
      case "compare-exit": S.compare = null; break;
      case "continue":
        continueFrom(id, { by: "you" });
        if (narrowQuery.matches) { S.panel = false; S.tree = true; S.scrollTo = `node:${id}`; }
        break;
      case "accept-ask": S.accept = id === "head" ? "head" : id; break;
      case "accept-confirm": acceptHead(); break;
      case "accept-cancel": S.accept = null; break;
      case "study-toggle": if (S.collapsed.has(id)) S.collapsed.delete(id); else S.collapsed.add(id); break;
      case "sidebar": S.sidebar = !S.sidebar; break;
      case "panel": S.panel = !S.panel; break;
      case "surface":
        if (S.panel && S.surface === id && compactQuery.matches) S.panel = false;
        else { S.surface = id; S.panel = true; }
        break;
      case "proj": S.projection = id === "plan" ? "plan" : "axon"; break;
      case "sim-admit": simAdmit(id); break;
      case "sim-reject": simReject(id); break;
      case "noop": toast("Outside this prototype: only the Design Tree and its entry points are mocked."); break;
      default: return;
    }
    render();
  }

  function onChange(event) {
    const el = event.target;
    if (el.matches('input[type="checkbox"][data-act="check"]')) {
      toggleCheck(el.dataset.study, el.dataset.id, el.checked);
      render();
    } else if (el.matches('select[data-act="data"]')) {
      setHashParam("data", safeName(el.value));
    }
  }

  function onToggle(event) {
    const el = event.target;
    if (!(el instanceof HTMLDetailsElement) || !el.dataset.key || !S) return;
    if (el.open) S.open.add(el.dataset.key); else S.open.delete(el.dataset.key);
  }

  function onKey(event) {
    if (event.key !== "Escape" || !S) return;
    if (S.accept) S.accept = null;
    else if (S.compare) S.compare = null;
    else if (S.view) S.view = null;
    else if (S.tree) S.tree = false;
    else return;
    render();
  }

  function applyHash() {
    const p = params();
    if (safeName(p.get("data")) !== dataName) { location.reload(); return; }
    applyTheme(p.get("theme"));
    const n = Math.min(6, Math.max(1, parseInt(p.get("state") || "1", 10) || 1));
    if (n !== appliedState || !S) { preset(n); appliedState = n; }
    render();
  }

  /* ---------------------------------------------------------------- boot */

  function fail(message) {
    document.documentElement.dataset.ready = "error";
    app.innerHTML = `<div class="load-error"><strong>DEV PROTOTYPE · fixture data · not production</strong><p>${esc(message)}</p></div>`;
  }

  function validate(data) {
    if (!data || typeof data !== "object") return "The dataset file did not assign window.CANDIDATE_GRAPH_DATA.";
    if (!Array.isArray(data.stages) || !data.stages.length) return "The dataset has no Stages.";
    if (!Array.isArray(data.studies)) return "The dataset has no studies list.";
    if (!data.head || !data.head.parent) return "The dataset has no Working Head (head.parent).";
    if (!data.project || !data.project.name) return "The dataset has no project name.";
    return null;
  }

  function boot() {
    const problem = validate(window.CANDIDATE_GRAPH_DATA);
    if (problem) { fail(`Dataset “${dataName}”: ${problem}`); return; }
    base = window.CANDIDATE_GRAPH_DATA;
    document.addEventListener("click", onClick);
    document.addEventListener("change", onChange);
    document.addEventListener("toggle", onToggle, true);
    document.addEventListener("keydown", onKey);
    window.addEventListener("hashchange", applyHash);
    compactQuery.addEventListener("change", () => { if (!S) return; S.panel = !compactQuery.matches || Boolean(S.view || S.compare); render(); });
    applyHash();
  }

  const script = document.createElement("script");
  script.src = `data/${dataName}.js`;
  script.onload = boot;
  script.onerror = () => fail(`Could not load data/${dataName}.js. Datasets live next to this page in data/.`);
  document.head.appendChild(script);
})();

/*
 * Candidate Graph prototype dataset: the #284 issue fixture.
 *
 * Invented data for a dev-only prototype. Nothing here is read from, or
 * written to, a MonkeyHub project. A dataset file assigns exactly one object
 * to window.CANDIDATE_GRAPH_DATA; prototype.js renders whatever it finds.
 *
 * Semantics follow #294: only admitted Candidates are items with
 * status "ready". "working" and "queued" items are Agent worktrees that have
 * not reached Candidate Admission; runs, repairs, redo attempts and results
 * rejected before admission live only in `advanced` / `hidden`.
 *
 * Names are arbitrary UTF-8 strings. A continuation records who made it:
 * { by: "you" } or { by: "Arch Agent", onRequest: true }.
 */
window.CANDIDATE_GRAPH_DATA = {
  meta: {
    name: "issue-fixture",
    label: "Issue #284 fixture",
    note: "Invented Riverside Library project; geometry is a sketch, not project data.",
  },

  project: {
    name: "Riverside Library",
    chatTitle: "Massing and entrance options",
    threads: ["Massing and entrance options", "Site constraints"],
  },

  /* Preview geometry, in metres: x runs east, y runs south, the river lies
     north of y = 0. Every preview uses the same site and the same camera. */
  site: {
    w: 60, d: 40, setback: 6, river: [-9, -1.5],
    trees: [[12, 38.6], [24, 39.2], [36, 38.6], [48, 39.2], [57.6, 30], [57.6, 19]],
  },
  shapes: {
    site: { boxes: [] },
    massingA: { boxes: [[4, 6, 0, 52, 12, 15]] },
    massingB: { boxes: [[8, 10, 0, 44, 24, 5], [12, 14, 5, 12, 12, 13], [36, 18, 5, 12, 12, 13]] },
    massingC: { boxes: [[6, 6, 0, 48, 9, 15], [6, 15, 0, 9, 21, 11], [45, 15, 0, 9, 21, 11], [15, 30, 0, 30, 6, 5]] },
    massingD: { boxes: [[6, 6, 0, 48, 30, 5], [6, 6, 5, 48, 20, 5], [6, 6, 10, 48, 10, 6]] },
    massingE: { boxes: [[6, 8, 0, 12, 10, 8], [22, 6, 0, 14, 12, 12], [42, 8, 0, 12, 12, 9], [10, 24, 0, 14, 10, 6], [32, 24, 0, 16, 10, 10]] },
    facadeA: { boxes: [[6, 6, 0, 48, 9, 15], [6, 15, 0, 9, 21, 11], [45, 15, 0, 9, 21, 11], [15, 30, 0, 30, 6, 5]], pattern: "piers" },
    facadeB: { boxes: [[6, 6, 0, 48, 9, 15], [6, 15, 0, 9, 21, 11], [45, 15, 0, 9, 21, 11], [15, 30, 0, 30, 6, 5]], pattern: "fins" },
    facadeC: { boxes: [[6, 6, 0, 48, 9, 15], [6, 15, 0, 9, 21, 11], [45, 15, 0, 9, 21, 11], [15, 30, 0, 30, 6, 5]], pattern: "screen" },
    facadeAEdits: { boxes: [[6, 6, 0, 48, 9, 15], [6, 15, 0, 9, 21, 11], [45, 15, 0, 9, 21, 11], [15, 30, 0, 30, 6, 5]], accent: [[22, 7, 15, 16, 6, 3]], pattern: "piers" },
    layout: { boxes: [[6, 6, 0, 48, 9, 15], [6, 15, 0, 9, 21, 11], [45, 15, 0, 9, 21, 11], [15, 30, 0, 30, 6, 5]], pattern: "fins", floors: 4 },
    entranceA: { boxes: [[6, 6, 0, 48, 9, 15], [6, 15, 0, 9, 21, 11], [45, 15, 0, 9, 21, 11], [15, 30, 0, 30, 6, 5]], accent: [[25, 36, 3.4, 10, 3.2, 0.7]], pattern: "fins", floors: 4 },
    entranceB: { boxes: [[6, 6, 0, 48, 9, 15], [6, 15, 0, 9, 21, 11], [45, 15, 0, 9, 21, 11], [15, 30, 0, 30, 6, 5]], accent: [[45, 36, 0, 9, 2.6, 4.5]], pattern: "fins", floors: 4 },
  },

  /* Accepted, immutable Stages. `parent` is the node the Stage was accepted
     from (a Candidate or a retained line); lineage is derived from parents. */
  stages: [
    {
      id: "S0", label: "S0", name: "Site",
      summary: "Site boundary, 6 m river setback, 18 m height limit, plane trees on the south edge.",
      acceptedAt: "Sep 12 · 10:05", acceptedBy: "Kaiwen", preview: "site", parent: null,
      advanced: { record: "DesignStage S0 · branch main", model: "run 0c41e2 · state 7d1a", accepted: "Kaiwen · Hub · authenticated" },
    },
    {
      id: "S1", label: "S1", name: "Massing",
      summary: "Stepped courtyard block; reading room on the river wing.",
      acceptedAt: "Sep 16 · 17:40", acceptedBy: "Kaiwen", preview: "massingC", parent: "massing-C",
      advanced: { record: "DesignStage S1 · branch main", model: "run 5be9a0 · state 1f63", accepted: "Kaiwen · Hub · authenticated" },
    },
    {
      id: "S2", label: "S2", name: "Layout",
      summary: "Timber-fin facade kept; stacks east, café on the courtyard, four floors on the river wing.",
      acceptedAt: "Sep 22 · 18:12", acceptedBy: "Kaiwen", preview: "layout", parent: "facade-B",
      fromNote: "Continued from Facade B, then layout edits by Kaiwen",
      advanced: { record: "DesignStage S2 · branch main", model: "run 9e07d3 · state c2b8", accepted: "Kaiwen · Hub · authenticated" },
    },
  ],

  /* A Study groups the results of one request from one exact base. */
  studies: [
    {
      id: "massing", stage: "S0", name: "Massing Study", noun: "schemes",
      ask: "Five massing schemes from S0; keep the 18 m limit and the river setback.",
      askedBy: "Kaiwen", agent: "Arch Agent", createdAt: "Sep 13 · 14:02",
      items: [
        {
          id: "massing-A", letter: "A", name: "Slab bar along the river",
          summary: "One long bar on the setback line; the south half stays a garden.",
          status: "ready", by: "Arch Agent", at: "Sep 13 · 14:26", preview: "massingA",
          metrics: [["GFA", "≈ 2,500 m²"], ["Height", "15 m"], ["Site cover", "26 %"]],
          advanced: {
            base: "S0 · Site · rev 3f9a1c", task: "Massing Study · option 1 of 5", worktree: "wt-massing-a",
            scope: "writes massing volumes · reads site, zoning", admission: "Admitted Sep 13 · 14:26 · closed loop complete",
            runs: [["run 01", "generate volumes", "ok"], ["run 02", "readback + setback check", "ok"]],
          },
        },
        {
          id: "massing-B", letter: "B", name: "Twin towers on a podium",
          summary: "Low podium with two reading towers; the strongest skyline.",
          status: "ready", by: "Arch Agent", at: "Sep 13 · 14:29", preview: "massingB",
          metrics: [["GFA", "≈ 2,900 m²"], ["Height", "18 m"], ["Site cover", "44 %"]],
          advanced: {
            base: "S0 · Site · rev 3f9a1c", task: "Massing Study · option 2 of 5", worktree: "wt-massing-b",
            scope: "writes massing volumes · reads site, zoning", admission: "Admitted Sep 13 · 14:29 · closed loop complete",
            runs: [["run 01", "generate volumes", "height limit exceeded"], ["run 02", "repair tower height", "ok"], ["run 03", "readback + checks", "ok"]],
          },
        },
        {
          id: "massing-C", letter: "C", name: "Stepped courtyard block",
          summary: "Courtyard ring stepping down to the south; best daylight for the reading room.",
          status: "ready", by: "Arch Agent", at: "Sep 13 · 14:31", preview: "massingC",
          metrics: [["GFA", "≈ 3,040 m²"], ["Height", "15 m"], ["Site cover", "41 %"]],
          continued: { by: "you", at: "Sep 16 · 17:20" },
          advanced: {
            base: "S0 · Site · rev 3f9a1c", task: "Massing Study · option 3 of 5", worktree: "wt-massing-c",
            scope: "writes massing volumes · reads site, zoning", admission: "Admitted Sep 13 · 14:31 · closed loop complete",
            runs: [["run 01", "generate volumes", "ok"], ["run 02", "readback + daylight check", "ok"]],
          },
        },
        {
          id: "massing-D", letter: "D", name: "Terraced wedge",
          summary: "Three terraces rising to the river; the most floor area.",
          status: "ready", by: "Arch Agent", at: "Sep 13 · 14:33", preview: "massingD",
          metrics: [["GFA", "≈ 3,120 m²"], ["Height", "16 m"], ["Site cover", "60 %"]],
          advanced: {
            base: "S0 · Site · rev 3f9a1c", task: "Massing Study · option 4 of 5", worktree: "wt-massing-d",
            scope: "writes massing volumes · reads site, zoning", admission: "Admitted Sep 13 · 14:33 · closed loop complete",
            runs: [["run 01", "generate volumes", "ok"], ["run 02", "redo terraces at 5 m steps", "ok"], ["run 03", "readback + checks", "ok"]],
          },
        },
        {
          id: "massing-E", letter: "E", name: "Pavilion cluster",
          summary: "Five linked pavilions around a public garden; the lowest scale.",
          status: "ready", by: "Arch Agent", at: "Sep 13 · 14:36", preview: "massingE",
          metrics: [["GFA", "≈ 1,500 m²"], ["Height", "12 m"], ["Site cover", "31 %"]],
          advanced: {
            base: "S0 · Site · rev 3f9a1c", task: "Massing Study · option 5 of 5", worktree: "wt-massing-e",
            scope: "writes massing volumes · reads site, zoning", admission: "Admitted Sep 13 · 14:36 · closed loop complete",
            runs: [["run 01", "generate volumes", "ok"], ["run 02", "readback + checks", "ok"]],
          },
        },
      ],
      /* Results that are not Candidates: kept for provenance, never tree nodes. */
      hidden: [
        { kind: "rejected", name: "Cantilever over the river", note: "Rejected by Kaiwen before admission: it breaks the river setback.", at: "Sep 13 · 14:20" },
        { kind: "failed", name: "3 failed runs", note: "Two volume generations and one readback timed out; their worktrees closed without a result.", at: "Sep 13" },
      ],
      advanced: { base: "S0 · Site · rev 3f9a1c", request: "Kaiwen · chat “Massing and entrance options”", worktrees: "6 worktrees · 5 admitted · 1 rejected" },
    },
    {
      id: "facade", stage: "S1", name: "Facade Study", noun: "options",
      ask: "Three facade treatments for the courtyard block.",
      askedBy: "Kaiwen", agent: "Arch Agent", createdAt: "Sep 17 · 09:15",
      items: [
        {
          id: "facade-A", letter: "A", name: "Brick pier rhythm",
          summary: "Brick piers at 3 m with deep reveals; heavy and quiet.",
          status: "ready", by: "Arch Agent", at: "Sep 17 · 09:48", preview: "facadeA",
          metrics: [["Glazing", "38 %"], ["Depth", "450 mm"], ["Cost band", "B"]],
          continued: { by: "you", at: "Sep 17 · 16:05" },
          advanced: {
            base: "S1 · Massing · rev 81c0de", task: "Facade Study · option 1 of 3", worktree: "wt-facade-a",
            scope: "writes envelope · reads massing", admission: "Admitted Sep 17 · 09:48 · closed loop complete",
            runs: [["run 01", "apply envelope", "ok"], ["run 02", "readback + glazing check", "ok"]],
          },
        },
        {
          id: "facade-B", letter: "B", name: "Deep timber fins",
          summary: "Timber fins at 1.5 m shade the reading room; light and warm.",
          status: "ready", by: "Arch Agent", at: "Sep 17 · 09:52", preview: "facadeB",
          metrics: [["Glazing", "52 %"], ["Depth", "600 mm"], ["Cost band", "B+"]],
          continued: { by: "Arch Agent", onRequest: true, at: "Sep 18 · 10:02" },
          advanced: {
            base: "S1 · Massing · rev 81c0de", task: "Facade Study · option 2 of 3", worktree: "wt-facade-b",
            scope: "writes envelope · reads massing", admission: "Admitted Sep 17 · 09:52 · closed loop complete",
            runs: [["run 01", "apply envelope", "fin clash at corner"], ["run 02", "repair corner fins", "ok"], ["run 03", "readback + glazing check", "ok"]],
          },
        },
        {
          id: "facade-C", letter: "C", name: "Perforated terracotta screen",
          summary: "A continuous terracotta screen; the most uniform elevation.",
          status: "ready", by: "Arch Agent", at: "Sep 17 · 09:57", preview: "facadeC",
          metrics: [["Glazing", "45 %"], ["Depth", "300 mm"], ["Cost band", "C"]],
          advanced: {
            base: "S1 · Massing · rev 81c0de", task: "Facade Study · option 3 of 3", worktree: "wt-facade-c",
            scope: "writes envelope · reads massing", admission: "Admitted Sep 17 · 09:57 · closed loop complete",
            runs: [["run 01", "apply envelope", "ok"], ["run 02", "readback + glazing check", "ok"]],
          },
        },
      ],
      hidden: [
        { kind: "redo", name: "Redo of B at 1.2 m spacing", note: "Superseded by the 1.5 m version inside the same worktree.", at: "Sep 17 · 09:50" },
      ],
      advanced: { base: "S1 · Massing · rev 81c0de", request: "Kaiwen · chat “Massing and entrance options”", worktrees: "3 worktrees · 3 admitted" },
    },
    {
      id: "entrance", stage: "S2", name: "Entrance Study", noun: "options",
      ask: "Three entrance options from S2; keep the plane trees on the south edge.",
      askedBy: "Kaiwen", agent: "Arch Agent", createdAt: "Today · 21:30",
      items: [
        {
          id: "entrance-A", letter: "A", name: "Courtyard gate on the south bar",
          summary: "A canopy opens the low south bar; you enter through the courtyard.",
          status: "ready", by: "Arch Agent", at: "Today · 21:44", preview: "entranceA",
          metrics: [["Entrance area", "96 m²"], ["Trees kept", "6 of 6"], ["Walk to desk", "24 m"]],
          advanced: {
            base: "S2 · Layout · rev c2b8f0", task: "Entrance Study · option 1 of 3", worktree: "wt-entrance-a",
            scope: "writes south bar, canopy · reads layout, trees", admission: "Admitted today · 21:44 · closed loop complete",
            runs: [["run 01", "cut opening + canopy", "ok"], ["run 02", "readback + egress check", "ok"]],
          },
        },
        {
          id: "entrance-B", letter: "B", name: "Corner entrance at the south-east",
          summary: "A glazed corner volume at the end of the stacks wing.",
          status: "working", by: "Arch Agent", at: "Started 21:40", preview: null, progress: "Repairing a stair clash · run 3",
          admitted: { preview: "entranceB", at: "Today · 21:58", metrics: [["Entrance area", "74 m²"], ["Trees kept", "6 of 6"], ["Walk to desk", "31 m"]] },
          advanced: {
            base: "S2 · Layout · rev c2b8f0", task: "Entrance Study · option 2 of 3", worktree: "wt-entrance-b",
            scope: "writes east wing, corner · reads layout, trees", admission: "Not admitted yet: the worktree is still running",
            runs: [["run 01", "add corner volume", "ok"], ["run 02", "readback + stair check", "stair clash"], ["run 03", "repair stair landing", "running"]],
          },
        },
        {
          id: "entrance-C", letter: "C", name: "River promenade entrance",
          summary: "Enter from the river walk under the reading-room wing.",
          status: "queued", by: "Arch Agent", at: "Queued 21:30", preview: null,
          advanced: {
            base: "S2 · Layout · rev c2b8f0", task: "Entrance Study · option 3 of 3", worktree: "wt-entrance-c (not started)",
            scope: "writes north wing ground floor · reads layout, river edge", admission: "Not admitted yet: waiting for a worker",
            runs: [],
          },
        },
      ],
      hidden: [],
      advanced: { base: "S2 · Layout · rev c2b8f0", request: "Kaiwen · chat “Massing and entrance options”", worktrees: "3 worktrees · 1 admitted · 1 running · 1 queued" },
    },
  ],

  /* Retained lines that are no longer current. They stay visible, de-emphasised. */
  lines: [
    {
      id: "line-facade-a", parent: "facade-A", name: "Facade A + 2 edits",
      summary: "Brick piers with a rooftop reading room; left when B was continued.",
      by: "Kaiwen", at: "Sep 17 · 18:40", preview: "facadeAEdits",
      advanced: { base: "Facade A · rev 2d04aa", worktree: "wt-main-facade-a", edits: "raise reading room · deepen reveals" },
    },
  ],

  /* The Working Head: default editing base, derived from retained project facts. */
  head: { parent: "S2" },

  chat: [
    { kind: "day", text: "Sep 13" },
    { kind: "user", text: "From S0, give me five massing schemes for the riverside site. Keep the 18 m height limit and the river setback." },
    { kind: "agent", text: "Five schemes are ready, all inside the height limit and the setback. D has the most floor area; C gives the reading room the best daylight." },
    { kind: "result", study: "massing" },
    { kind: "event", text: "Sep 16 · You continued C and accepted it as S1 · Massing." },
    { kind: "event", text: "Sep 18 · Arch Agent continued Facade B at your request; S2 · Layout accepted Sep 22." },
    { kind: "day", text: "Today" },
    { kind: "user", text: "From S2, try three entrance options. Keep the plane trees on the south edge." },
    { kind: "agent", text: "A is ready. I'm repairing a stair clash in B; C starts when a worker is free." },
    { kind: "result", study: "entrance" },
  ],

  /* The six review states (#284 E). Each preset starts from this dataset. */
  states: {
    1: { title: "Tree closed", tree: false },
    2: { title: "Tree open · five S0 Candidates", tree: true, focus: "massing", select: "massing-D" },
    3: {
      title: "Compare", tree: true, focus: "massing",
      checked: { massing: ["massing-A", "massing-C", "massing-D", "massing-E"] },
      compare: { study: "massing", ids: ["massing-A", "massing-C", "massing-D", "massing-E"], chosen: "massing-D" },
    },
    4: { title: "Old Stage read-only", tree: true, expand: [], select: "S0", view: "S0" },
    5: { title: "Continued into the current line", tree: true, focus: "entrance", select: "entrance-A", continue: { id: "entrance-A", by: "you", at: "Just now" } },
    6: { title: "Agent worktrees running", tree: true, focus: "entrance", select: "entrance-B", open: ["adv:entrance-B"], scroll: "study:entrance" },
  },
};

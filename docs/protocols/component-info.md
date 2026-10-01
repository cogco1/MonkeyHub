# Component information datasets

What is known about the components of one model version, one topic at a time — basic facts,
procurement, thermal, structure, carbon — written as `MonkeyHubComponentInfo@1` datasets, kept on
the project's Board and shown in Modeling for the component an architect clicks (#549).

The interface knows only a generic vocabulary: a field is a label, a value and how sure it is.
It never knows what a price, a supplier or a conductivity is; each dataset says that in its own
words. A new topic therefore needs a new dataset, not new code.

## Where it lives and how it is shown

- **One dataset, one Board card.** Each dataset is carried by one visible rectangle on the project's
  Board: `customData.componentInfo` holds the whole document, and its bound label reads
  `构件资料 · <title>`, the version label, the component count and the date. The card's element id is
  `component-info:<dataset id>` and its label `component-info:<dataset id>:label`. Cards are found by
  the `customData.componentInfo` marker, never by position or text. They are ordinary Board
  elements: saved with the scene through its revision chain, undone with Ctrl+Z, and removed by
  deleting the card. *Clear annotations* leaves them alone.
- **Importing.** The Board's *导入构件资料 / Import component info* command takes one JSON file holding
  one dataset or an array of datasets. Every dataset is checked first (below); nothing changes if
  any problem is found, and the notice names the problems by path. A dataset id already on the
  Board replaces its card in place (position, frame, group and z-order kept); a new id adds a card.
  A hand-made copy of a card with the same id is retired. A dataset whose `appliesTo.projectId` is
  not this project is refused. The scene is saved through the Board's own queue at its base
  revision and may not exceed the Board's 8 MiB limit.
- **Reading.** Modeling reads `GET /api/board` while it shows a model, again only when the project
  moves, and parses a Board revision once. A click reads nothing: it looks the picked component up
  in what was read. No click makes a network request, calls a model or writes anything.
- **Guard.** Data is shown only when all of these hold: the Board has been read; the model on screen
  is exactly one retained version (not a local file, several exports or unrecorded edits); the
  click was resolved against that version (the loaded run's own catalog, or the runtime answering
  `current`); and the dataset's `appliesTo` names the same project, run and state digest. A dataset
  written for another version is listed under *技术信息与来源* as not matching and is never shown as
  data; when no dataset matches, the card says `构件资料与当前显示的版本不符（资料对应：<versionLabel>）` and
  shows only the model's own label and ids. `appliesTo` is compared with what Modeling shows; it
  never claims or declares a model source.
- **The card.** Once the Board carries any component information, a click on a component opens the
  card in the inspector slot Versions uses, docked beside the canvas. Without any, a click behaves
  as before and the bar's *构件信息* command opens the card on request (it then says
  `构件资料未导入`). Another click updates the card; a click on nothing, Close, another model or
  version, or opening Versions closes it. The card shows the name, the summary datasets' fields,
  then each section dataset in `order`, then an expandable *技术信息与来源* with the component and
  element ids, the version shown, the Board revision, the datasets used, every source with its date
  and the datasets written for another version. Each field shows its key information: label, value
  and status. What explains it (its `note`, its `url` and its source) folds under a small arrow
  after the value. A section's `note` and the entry's `notes` fold under an arrow beside the section
  heading, and a group's allocation basis and `note` under one beside the group title. Every arrow
  starts shut, also after another click. *Copy* copies the whole card as plain text, folded notes
  included. Addresses are selectable text with their own copy button: the desktop shell opens no
  address outside the Hub.

## Format: `MonkeyHubComponentInfo@1`

A dataset is one JSON object. Unknown keys are refused, so a misspelt key is an error rather than
silently ignored data.

| Key | Required | Meaning |
| --- | --- | --- |
| `schema` | yes | `"MonkeyHubComponentInfo@1"` |
| `id` | yes | 1–100 of `A-Z a-z 0-9 . _ -`, starting with a letter or digit. Importing the same id again replaces that dataset. |
| `title` | yes | The topic as the card heads it, e.g. `采购方案`, `热工`. |
| `role` | yes | `summary` (the card's first screen: what the component is) or `section` (a topic shown below it). |
| `order` | no | Number; summaries and sections each sort by it, then by id. |
| `preparedAt` | yes | `YYYY-MM-DD`, when the dataset was written. |
| `appliesTo` | yes | `{ projectId, runId, stateDigest, versionLabel? }`: the exact version the data describes. `stateDigest` is the 64-hex design state digest of that run (the `stateDigest` of the model source Modeling shows). `versionLabel` is the human name used in the mismatch sentence. |
| `statusLabels` | no | The dataset's own words for statuses, e.g. `{ "verified": "已查价格", "estimate": "预算估计", "to-ask": "待询价", "to-confirm": "待确认" }`. Unworded statuses use the interface's (已核实 / 估计 / 待询 / 待确认 / 未知). |
| `missing` | no | What the card says for a component this dataset has no entry for, e.g. `采购信息未录入`. |
| `note` | no | One paragraph for every component (scope, caveats, totals), folded under the section heading's arrow. |
| `sources` | no | `[{ id, label, date?, kind? }]`; ids are unique. `kind` is free text (`budget-estimate`, `quote`, `catalog`, `measurement`, `model-data`, …). |
| `groups` | no | `{ <groupId>: Group }`: something several components share, described once. |
| `components` | yes | `{ <componentId>: Entry }`, keyed by the model's own component ids. |

**Entry** `{ name?, fields?, group?, notes? }` — `name` is the component's readable name (the first
summary dataset that names a component gives the card its title; otherwise the model's own label is
used); `group` names a group of this dataset.

**Group** `{ title, fields, shared?, sharedBy?, allocation?, note? }` — shown once, inside the
section of each component that names it. `shared: true` with `sharedBy: N` prints
`共享：N 个构件共用`. `allocation` is `{ basis, fields? }` when the group is divided among its
components, or `null` when it is not; a shared group is never divided by the interface. Say what an
undivided group means in `note`, e.g. `共享原料预算，未分配单件成本；整组费用只计一次。`

**Field** `{ label, value, unit?, status?, source?, date?, url?, note? }`

- `value` is a non-empty string or a finite number; `unit` is printed after it.
- `status` is one of `verified`, `estimate`, `to-ask`, `to-confirm`, `unknown`. A value that already
  reads as its status (`待确认` marked `to-confirm`) is not followed by the same word again.
- `source` names an id in `sources`; the card prints the source's label and the field's `date`, or
  else the source's date.
- `url` is an `http` or `https` address.

Paths in problem messages point into the document: `$.components["hemp-03-1-0"].fields[2].status`,
`$[1].id` for the second dataset of an array.

The Board refuses the keys `files`, `dataURL`, `contentBase64`, `appState`, `modelSource` and
`sourceStageRef` anywhere inside an element, so a dataset may not contain them either.

## Authoring a dataset for a new topic

This is the procedure an agent follows to add, say, thermal data to a project.

1. **Find the exact version.** Read the model source Modeling shows for the version the data
   describes: `runId` and `stateDigest` (the Versions panel, the candidate's record, or the
   runtime's `GET /api/artifacts` `modelSource`), and the project id. Read them from the project's
   files or APIs; never copy them from another dataset without checking. Name the version in
   `versionLabel`.
2. **Key by the model's own component ids.** Take them from that run's retained state (its State
   Record's `Component@1` entities, or a candidate record's `objects[].componentId`). Check that every
   id you write exists in that version and that the mapping is one to one. Never match by mesh
   order, object order, colour or appearance.
3. **One topic per dataset.** Use `role: "section"` with an `order` for a topic; `summary` only for
   what a component is (name, material, use, size). Pick a stable `id` such as
   `thermal-20261002`; import the same id to correct it and a new id to add a topic.
4. **Write what the sources say, and how sure it is.** Give every value its unit and say what the
   number means (model inches or real size; for a sloped or tapered member, the cut length and how
   it was computed, since a bounding box is not a cut length). Mark anything not checked with a
   `status`; write `待确认` (`to-confirm`) or `unknown` rather than a guess. Put every document you
   used in `sources` with its date, and point prices, measurements and checked links at it.
5. **Describe shared things once.** Put what several components share — a purchased stock, a wall
   type, a test report — in a group, and let each component carry only what is its own. Give
   `sharedBy` when the count is known. Leave `allocation: null` unless the sources give a basis for
   dividing it, and say in the group's `note` what that means for the topic.
6. **Say what a gap means.** Set `missing` to the sentence the card shows for a component the topic
   does not cover (`热工数据未录入`).
7. **Check before handing over.** Import the file on a copy of the project's Board, or run it
   through `parseDatasetImport` in `apps/monkeyhub/web/src/features/componentInfo/componentInfo.ts`;
   the problems name exact paths. Click a few components in Modeling and read the card and its
   technical section, including one the topic does not cover.

A minimal thermal section (see `apps/monkeyhub/web/test/fixtures/component-info/` for complete
synthetic examples):

```json
{
  "schema": "MonkeyHubComponentInfo@1",
  "id": "thermal-20261002",
  "title": "热工",
  "role": "section",
  "order": 30,
  "preparedAt": "2026-10-02",
  "appliesTo": { "projectId": "<project>", "runId": "<run>", "stateDigest": "<64 hex>", "versionLabel": "design_v1" },
  "missing": "热工数据未录入",
  "sources": [{ "id": "lab", "label": "导热系数测试报告", "date": "2026-09-12", "kind": "measurement" }],
  "groups": {
    "wall-type-1": {
      "title": "墙体类型 1",
      "fields": [{ "label": "U 值", "value": 0.35, "unit": "W/m²K", "status": "verified", "source": "lab" }],
      "shared": true, "sharedBy": 57, "allocation": null,
      "note": "整面墙的 U 值，不分摊到单个砌块。"
    }
  },
  "components": {
    "hemp-03-1-0": {
      "fields": [{ "label": "导热系数", "value": 0.13, "unit": "W/mK", "status": "estimate", "source": "lab" }],
      "group": "wall-type-1"
    }
  }
}
```

## Ownership

The card, the dataset format, its Board cards and the import are `hub.shell` front-end code in
`apps/monkeyhub/web/src/features/componentInfo/`, mounted by Modeling (`features/stage/Stage.tsx`) and
the Board (`workspaces/monkeyboard/Board.tsx`). The datasets themselves are project data on the
Board (`project_runtime.board` stores the scene unchanged); class or project datasets are never
committed to this repository.

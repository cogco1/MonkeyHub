"""The words the Agent is given about its tools.

studio_request's modeling guide, which the CLI loads before every turn, the
full guide of each other domain, answered once by action discovery, and how to
present results and suggestion cards in the conversation.
"""

from __future__ import annotations


_SUGGESTION_INSTRUCTIONS = (
    "Before recommending work, inspect the actually connected capabilities and their exact schemas; consult relevant evidence when needed. "
    "Do a simple, clear, already requested task directly. When there is a real choice, an inferred but unrequested next step, "
    "substantial time or cost, or a missing capability, publish one structured suggestion through chat_present instead of executing it. "
    "State its goal/outcome, deliverables, actual tools, capability available or needs-development, and why it fits. "
    "Leave timeEstimate and costEstimate unknown unless you can state a concrete basis; never invent estimates. "
    "For missing capabilities offer an assessment, not a promise that implementation exists. "
    "The suggestion prompt continues this same agent conversation when the user selects it; it is not a script or URL executor "
    "and does not authorize plugin installation or bypass existing permissions or exact-source checks. "
)


# studio_request's description is what the CLI loads before every turn, so it
# holds modeling, the common case, with each ordinary body complete: a schema
# read is for an unusual field, not for the request the recipe already states.
# Modeling is one construction script (#419): its vocabulary and one example
# are stated here; meaning, capabilities and domains follow in their own
# stages, and no runtime realisation is named.
# Every other domain is one line here; its full text is the guide that
# studio_schema answers with that domain's pathPrefix, read when the work
# actually turns to it.
_MODELLING = chr(10).join([
    "Use the bound project's Studio API in metres; plan points are (x, z) and 3D points (x, y, z), with Y up.",
    "Design tools prepare the project's runtime themselves; nobody needs to open a page first.",
    "The bodies below are complete for the ordinary case: send them as written. studio_schema is for a field not shown here",
    "or for correcting a refusal. The chat fills projectId; omit it.",
    "",
    "START: POST /api/project/modeling with body {} prepares an empty project once (an existing project keeps its model inputs)",
    "and answers the default base: {stateDigest, sourceStageRef, levels: [{levelId, elevation}], components: [{componentId,",
    "parentComponentId}], elementCount}. Write the first script against it directly; no read first.",
    "CURRENT MODEL: GET /api/construction/model gives stateDigest, levels, parameters and one entry per geometry id:",
    "form, bounds, cuts, cutBy, facets and the capabilities they unlock. Without ?run=<candidateId> it reads the default",
    "source; with it, send sourceRunId on writes. Keep sourceStageRef when provided. With elementCount > 0, read it before changing geometry.",
    "",
    "MAKE AND CHANGE GEOMETRY: POST /api/proposals/construction {stateDigest, script, summary?, sourceRunId?, sourceProposalId?, keep?}.",
    "The script is a small Python-like program; one script makes and changes many shapes:",
    "  base = rect(20, 0, 12, 8)                       # plan rectangle: corner (x, z), 12 along x, 8 along z",
    "  mass = extrude(base, 3.2)                       # push-pull up from the ground level",
    "  upper = extrude(rect(20, 0, 10, 8), 3, at=top(mass))   # stands on mass and follows its height",
    "  for i in range(4):",
    "      w = extrude(rect(21.5 + 3 * i, 0, 1.2, 0.3), 1.5, at=0.9)",
    "      cut(mass, w)                                # removes w from mass; w stays, hidden, under its own id",
    "Verbs: rect polygon circle offset | plane front side | extrude face path loft section | move rotate scale mirror copy array |",
    "pushpull set_height set_base | cut uncut | level top param bounds | name get delete | print.",
    "Plain Python works: variables, arithmetic, for/if/def, lists, range, pi/sin/cos/sqrt.",
    "A variable is the shape's id (w in a loop gives w-1..w-4); name(obj, \"id\") sets one; get(\"id\") edits",
    "existing geometry; running a script again with the same names updates the same shapes, it never duplicates them.",
    "A refused script saves nothing and names its line. GET /api/construction lists every verb and argument.",
    "Never state what a part is while making it; geometry needs no classification.",
    "",
    "MEANING: only when the user says what a part is, POST /api/proposals/facets",
    "{stateDigest, targets: [{id, set: {\"architectural.role\": \"wall\"}}]}. Facets never change geometry or ids.",
    "Keys: architectural.role, architectural.enclosure, structural.role, material.name, material.color, fabrication.method.",
    "MATERIALS: declare only what the user or the project's sources state, never from an id, name or shape: set \"material.name\"",
    "in the facets proposal, plus \"material.color\": \"#RRGGBB\" only when the design intent states it (one per material; never derive",
    "a colour from a name). A part of a mixed id: {id, part: <one of its parts>, set}; its own material wins.",
    "The model has one material per name, in that colour or one telling materials apart; what states none wears none,",
    "marked material_status undeclared; a succeeded candidate checked both on readback. GET /api/construction/model?run=<candidateId>",
    "shows the facets and partFacets it was built from. Leave a missing material unknown, or offer it as a reversible proposal.",
    "CAPABILITIES appear per entity in the model view once its facets allow them, e.g. hosted-opening",
    "(a door or window with its frame and leaf) on a wall: POST /api/proposals/hosted-opening.",
    "DOMAINS: GET /api/domains/{structure|envelope}/readiness says what a domain reads or which facets to add; it never guesses.",
    "PARAMETERS: POST /api/proposals {stateDigest, semanticEdit: {summary, parameters: [...]}} defines linked dimensions;",
    "a script binds one with param(\"key\") as a height or an at offset. semanticEdit never carries geometry.",
    "Formulas go in parameters[].expr with inputs, and value must match it; revise upstream controls for linked edits.",
    "STAGES: explore with scripts and candidates; admission/Continue promotes; meaning, capabilities and domains come after.",
    "",
    "CANDIDATE: when a proposal answers status 'proposed', POST /api/proposals/{id}/candidate with no body and awaitSeconds: 60 beside method/path.",
    "That one call builds a reversible, unaccepted candidate, waits for it and answers candidateId, candidate.stateDigest, artifacts",
    "(with modelSource), objects and, when the proposal had a sourceRunId, compare. These are completed readbacks: no GET /api/jobs,",
    "/api/candidates or /api/construction/model is needed after it. On timeout, follow the returned job/candidate reads; never send the request again merely to wait.",
    "A status 'conflict' reaches something kept: inspect impact.conflicts before executing. Multiple observation and revision cycles can occur within the same Stage.",
    "NEXT EDIT, in this turn or a later one: write against the candidate just made with {stateDigest: <its candidate.stateDigest>,",
    "sourceRunId: <candidateId>, ...}; that stateDigest is the one GET /api/construction/model?run=<candidateId> answers, so no read is needed.",
    "sourceProposalId continues an unexecuted chain, not a newly selected candidate base.",
    "keep is a list of protected refs, e.g. ['entity:mass']; a kept geometry id keeps its parts. Use the actual target and source, not a guessed field.",
    "For an existing numeric control, GET /api/capabilities/candidate.modify_existing?target=<id>&run=<candidateId> (elementId is optional)",
    "returns its current values, units and a ready request; edit that body and POST /api/capabilities/{capabilityId}/run.",
    "The numeric capability run also accepts awaitSeconds: 60 with sourceRunId in its body. Waiting posts the change once.",
    "GET /api/capabilities?goal=<user request> helps discover operations; a miss there does not exclude the APIs listed here.",
    "Chain known edits in memory by passing the last proposalId as sourceProposalId; keep stateDigest at the chain's original baseStateDigest.",
    "sourceRunId/sourceStageRef are inherited. GET /api/proposals/{id} reads accumulated changes; inspect conflicts before executing.",
    "",
    "OBSERVE: GET /api/candidates/{id} lists retained objects: Z-up bbox [x, z, y], lengthUnit, upAxis or objectReadbackError.",
    "GET /api/candidates/{id}/compare?against=<runId> compares with the required source run. The first candidate has no prior run to compare.",
    "Use an available artifact's non-null modelSource unchanged for visual_review and model-view. A missing source cannot be reconstructed from hashes.",
    "SEE A VIEW: GET /api/drawings/model-view?runId=<id>&stateDigest=<digest>&assetSha256=<3dm sha256>&view=front returns an MCP image;",
    "views front/back/left/right/top/axon. Read modelSource from the awaited result's artifacts.",
    "CHECK COLOURS: &display=material draws each object in its 3dm material's colour, none grey-hatched, with a legend; visual_review: axon-material.",
    "Bounds and passing checks are not visual inspection or proof of spatial intent; for a spatial or formal task, check with visual_review and report gaps.",
    "Correct invalid input from its named schema; on stale state or conflicts, refresh the exact source and reconcile within keep conditions.",
    "A refused request made no model: tell unsupported operations from correctable inputs; report open limits; never invent success.",
    "",
    "ADMIT: when a model revision loop is complete, POST /api/admissions once with",
    "{results: [{runId: <candidateId>, outcome: 'admitted', supersedes: [attempt runIds it replaced], label}]}; supersedes may be [].",
    "Beside method/path/body put the loop's taskClass: deterministic_edit when readback checks it; spatial_formal or polish",
    "when the result needs a look, and then only after visual_review looked at that result or an attempt it supersedes.",
    "Add study: {id, label, baseRunId} (id an ASCII slug) for several alternatives built from one run.",
    "outcome 'rejected' only where the user's words reject that result; add feedbackQuote with their exact passage.",
    "outcome 'withdrawn', alone in its request: your own result from this chat, no longer proposed.",
    "The chat fills projectId, task {kind: 'hub-chat'}, messageSource and rawLanguage; never supply them. A refusal names each failing",
    "clause per run; an identical retry returns the same record. GET /api/admissions?include=rejected lists what is already admitted or tried.",
    "CONTINUE: a change the user asked for lands on their working design: once its result is admitted, in the same turn,",
    "PUT /api/working-draft {runId, baseRevisionSha256} with revisionSha256 from GET /api/working-source. Their request is",
    "the Continue. Options or alternatives they asked for, and results whose checks failed, stay candidates. Continue from",
    "any other result only on their words (feedbackQuote selects them). Generating a result never moves the Working Head.",
    "",
    "OTHER READS: GET /api/project, /api/state/volumes, /api/program, /api/options, /api/artifacts, /api/jobs/{id}.",
    "OTHER ACTIONS: POST /api/state/closure, /api/program, /api/options, /api/options/{id}/select, /api/candidates/combine.",
    "",
    "OTHER DOMAINS: before working in one, call studio_schema with its pathPrefix (and no path) once; the answer carries that",
    "domain's full guide beside its action list.",
    "- Drawings (elevations, 剖透视 section perspectives, cut plans and entourage, sheets, drawing pages): pathPrefix /api/drawings.",
    "- Retained feedback, decisions, context reads (POST /api/intents/context) and study precedents: pathPrefix /api/decisions.",
    "- Project memory (where things are, where to look first): pathPrefix /api/memory.",
    "- Board, documents and page annotations: pathPrefix /api/board.",
    "- Model export or conversion (3DM, SKP, GLB, DWG): pathPrefix /api/exports.",
    "- AI image renders from a registered source image (providers, requests, results): pathPrefix /api/render.",
    "Stage acceptance, formal issue and printer upload are separate from this tool's reversible design actions.",
])

# The full text of each domain the upfront guide names in one line. Answered
# with studio_schema action discovery for that pathPrefix, never repeated in
# the schema answers themselves.
_GUIDES = {
    "/api/drawings": chr(10).join([
        "DRAWINGS: POST /api/drawings/elevations automatically registers results in MonkeyDiagram's documents list; drawing-only work admits nothing.",
        "Before drawing, read POST /api/intents/context for the task's scoped decisions (pathPrefix /api/decisions explains it).",
        "SECTION PERSPECTIVE (剖透视): POST /api/drawings/section-perspectives cuts the exact model with a section plane, removes the side the eye is on,",
        "and draws the kept side in true perspective: the cut filled (poché) and true to scale at 1:scaleDenominator, farther geometry smaller,",
        "lines perpendicular to the cut converging at the eye's point on it. Minimal body: {projectId, sourceStageRef or modelSource,",
        "section: {line: [[x1, y1], [x2, y2]], keep: 'left'|'right'}}. The line is a plan line with the same plan numbers as the script's plan points",
        "(the exact STEP's X/Y in its unit, Z up); keep is the side kept walking from the first point to the second; the eye stands on the other side.",
        "The default camera looks straight through the cut from 1.6 m above the lowest cut point, fitting the cut's width in 55 degrees.",
        "Optional: camera {eyeHeight, fovDeg} or {eye, target, up?, fovDeg?} (the target centres the frame; to move only the vanishing point,",
        "move the eye and keep the target at the cut's centre); section {origin, normal} for any plane (normal points toward the eye);",
        "depth, hiddenObjectIds, scaleDenominator (e.g. 50 for a room) and drawingId. Like elevations it registers the drawing in the documents list",
        "and returns that document; see it with POST /api/board/export using its runId, assetSha256, revisionRef and pageIndex 0.",
        "Refusals are named, e.g. SECTION_PLANE_MISSES_MODEL or SECTION_EYE_ON_KEPT_SIDE; correct the plane or camera rather than retrying.",
        "CUT PLAN: POST /api/drawings/plans makes or rebuilds a retained cut plan from modelSource or sourceStageRef (read its schema). To place entourage",
        "on a retained plan, send its previousRevisionRef with dressingOperations, one batch applied whole: {op: 'insert', id, object: {id, assetId: 'person-plan'|'tree-plan',",
        "positionUv: [u, v], size, flipped?, anchorObjectId?}}, {op: 'move', id, positionUv}, {op: 'scale', id, size}, {op: 'flip', id, flipped} or {op: 'delete', id}.",
        "Positions and sizes are in the source model's length unit; with anchorObjectId, positionUv is an offset from that object's projected centre.",
        "Each object keeps its id and stays editable on its own; a refused batch writes nothing. Add reason with the user's correction when one asked for it.",
        "GET /api/drawings/plans/vector?runId=&assetSha256=&revisionRef= reads the plan's SVG, symbols and anchor choices; POST /api/drawings/plans/status",
        "{runId, assetSha256, revisionRef} says whether it is current and which objects are missing or outside the view; GET /api/drawings/plans/dimensions lists",
        "the dimensions a plan can place. Drawing revisions never move the design. GET /api/drawings/corrections?projectId=[&drawingId=] reads how",
        "revisions changed and which repeated corrections the architect may save as a project recipe; only the architect can save one.",
        "VERTICAL SECTION: the same POST /api/drawings/plans with section instead of cutHeight/bottom: {line: [[x1, y1], [x2, y2]], keep: 'left'|'right'}",
        "along X or Y (give exact axis-aligned numbers, e.g. [[x0, y], [x1, y]]; a slanted plane is refused SECTION_PLANE_NOT_MODEL_AXIS, so draw it as a",
        "section perspective) or {origin, normal} with a horizontal +/-X or +/-Y normal pointing to the removed side. depth is how far beyond the plane it",
        "draws (default: the model's far side); lengthUnit, when given, must be the source's own unit (DRAWING_UNIT_MISMATCH); cropUv is its window in u",
        "(along the paper) and v (Z). It takes no dimensions or entourage. A rebuild with previousRevisionRef keeps its plane, depth and window unless",
        "stated, and a drawingId stays a plan or a section (DRAWING_ORIENTATION_CHANGED): give a new section its own drawingId.",
        "Every drawingId keeps the kind it first named - plan, section, elevation or axonometric, section perspective, sheet - so",
        "reusing one for another kind is refused (DRAWING_KIND_CHANGED); a sheet and its views each take their own ids.",
        "AXONOMETRIC: POST /api/drawings/elevations {view: 'axon', direction: [dx, dy, dz]}, the direction from the model toward the viewer ([1, -1, 1] is",
        "the isometric from +X, -Y, +Z; default [-1, -1, 1]; never vertical). It is foreshortened: its scaleDenominator is a display size, not a measurable one.",
        "SEE A VIEW: GET /api/drawings/model-view?runId=<id>&stateDigest=<digest>&assetSha256=<3dm sha256>&view=front returns an MCP image",
        "with exact source metadata. Read modelSource from the awaited result's artifacts or the candidate's 3dm artifact. Views: front/back/left/right/top/axon",
        "(axon is isometric). This is a read-only line projection from complete retained STEP; unsupported sources refuse rather than show a proxy.",
        "MATERIAL COLOURS: &display=material draws the same exact model with each object filled flat in the diffuse colour of the material its 3dm's",
        "own table binds to it, an object wearing none grey under a hatch, and answers a legend: each material {name, color, source declared|file,",
        "objects, visible} and the undeclared count. A visual_review takes it as view <view>-material, e.g. axon-material. Per object,",
        "GET /api/model-assets/<assetSha256>/index?runId=&stateDigest= gives objects[].material {name, color, source declared|file|undeclared}.",
        "SHEETS: GET /api/drawings/styles lists styleId. POST /api/drawings/sheets {projectId, one source (sourceStageRef, modelSource, or sourceAsset",
        "{runId, assetSha256} of an imported model from GET /api/artifacts), styleId, views, paperSizeMm?, title?, subtitle?, sheetNumber?, drawingId?,",
        "notes?, lengthUnit?} draws every view from that one source and places it. A view is {id, placeMm: [x, y] (its top-left in paper mm from the",
        "sheet's top-left), exactly one of plan (a cut plan or vertical section), elevation or sectionPerspective with that route's fields and its own",
        "scaleDenominator, title?, subtitle?, markOn: <a plan view's id> with markLabel: 'A' to draw a section's cut line on that plan}. Each view is",
        "registered as its own drawing under its id and named in the sheet's viewRecipe.views; the sheet is registered as one PDF page (revisionRef null).",
        "A drawing that leaves the paper or overlaps is refused by name (DRAWING_SHEET_LAYOUT_INVALID), never moved: change placeMm or the paper.",
        "Without views it is the front/right/top review sheet at one scaleDenominator.",
        "FILES: GET /api/drawings/{assetSha256}/files/{pdf|dxf|svg|png}?runId=&revisionRef= serves a view revision's svg/png (its revisionRef required)",
        "and a sheet's pdf/dxf/svg/png (no revisionRef) to the architect's apps; this chat does not read it. Look at any page with POST /api/board/export.",
        "Top is an orthographic projection, not a cut plan. GET /api/documents?runId=<runId> reads that run's drawings.",
        'DRAWING PAGE: POST /api/board/export is a read-only native MCP image: body {projectId, pages:[{runId, assetSha256, revisionRef, pageIndex}], format:"png", zip:false, maxEdge:2048}.',
        "Copy exact source fields from GET /api/documents or the generated drawing result; revisionRef must be explicit (null for sources without a revision),",
        "pageIndex is zero-based. One clean source page, no annotations, at most 2048 pixels per edge and 4 MiB; use smaller maxEdge if too large. No operationId or awaitSeconds.",
        "SHOW THE PAGES: when a turn made or revised drawing pages, present them before you finish: chat_present kind=assistant with",
        "documents:[{runId, assetSha256, revisionRef, pageIndex}] per page, the exact fields of the result. The chat shows each page as the PNG",
        "this export gives; ids and page numbers alone do not show the user the drawing.",
    ]),
    "/api/decisions": chr(10).join([
        "CONTEXT READ: POST /api/intents/context compiles current task facts from projectId, stateDigest, utterance and exact sourceRunId/sourceStageRef; focus is optional.",
        "Repeat the same source/task/focus with contextRefs for omitted facts or contextOffset for the next reference index page. This reads only and grants no edits; use studio_schema for its full contract.",
        "For an explicitly selected precedent, add studyEvidence:[{studyId,ledgerRef}] (up to 3 exact revisions) to that context read. It returns the retained prior with conditions and counterevidence, not accepted design truth. Never infer that an older revision is current. If completeness is false or numerical details are needed, GET /api/studies/{studyId}?ledgerRef=<exact-ref> reopens that source; external citation summaries are not verified source text.",
        "RETAINED FEEDBACK: When the user gives an avoid/keep direction for later work, POST /api/decisions using its studio_schema, exact observed source and narrow stated scope. Save that feedback before continuing; do not turn an ordinary change request or your own judgment into a retained preference.",
        "Where project content is, and where to look first for research, is project memory, not a decision: pathPrefix /api/memory.",
        "The chat fills rawLanguage/messageSource from this actual user turn and sourceKind=agent for your interpretation. Never supply those fields, invent user approval or strengthen a soft preference into a hard rule. The user need not confirm an internal grant; the existing Runtime authorization still applies.",
        "GET /api/decisions reads retained feedback; GET /api/decisions/{id} reads its history. On the user's revocation request, POST /api/decisions/{id}/revisions with action=revoke and the revisionRef you read as expectedRevisionRef; the chat binds the reason and revisionMessageSource. This tool cannot supersede rules, save lock decisions, accept a Stage or unlock a parameter.",
        "Before drawing or writing artifact copy, read /api/intents/context with the actual Stage/targets/source; its default reads design and drawing decisions, and copy work selects decisionContext.domain=copy. Consume only scopedDecisions returned for that task, not every record in the decision list. Refresh after saving/revoking feedback or changing scope/source.",
        "Copy feedback targets copy:style; drawing feedback targets drawing:hatch, drawing:lineweight, drawing:beyond, drawing:entourage or drawing:poche. Only design uses targetRefs. For copy/drawing context omit targetRefs; omit decisionContext.source when no exact document/Board evidence is needed, rather than putting the outer Design source there. Copy evidence is document; drawing evidence is document or Board. The outer ContextPack still binds the current Design source.",
        "Carry applicable supported design keep refs into the existing edit's keep field and check the execution result. Preserve the actual relation or parameter asked for, not an entire unrelated object. Keep existing parameter locks; unsupported relation protection or hatch controls require explicit defer, not invented enforcement.",
        "Use each decision once for its relevant effect: preserve/filter for supported hard constraints, a generation preference for soft wording, or defer for unsupported effects. Inspect the next artifact and name any remaining gap; a context entry alone proves no behavior changed.",
    ]),
    "/api/memory": chr(10).join([
        "PROJECT MEMORY is how this project works, not what it settled: where retained content is (a locator), where to look first for a topic (a source policy) and which library skill a task follows (a recipe). POST /api/memory saves one from the user's words; use its studio_schema.",
        "The chat fills rawLanguage/messageSource from this actual user turn and sourceKind=agent. Never supply them, and never save an item the user did not say. Scope is project and authority explicit; omit both.",
        "LOCATOR, when the user says where content is (e.g. 项目图框在这份文件里): {kind: 'locator', value: {label: their name for it, target}}. target is {kind: 'document', runId, assetSha256, revisionRef, pageIndex} from GET /api/documents, {kind: 'artifact', sha256} or {kind: 'board', revisionSha256, elementId}. A file path or URL is never a target: the file must be registered first.",
        "A where-is question: GET /api/memory/locate?q=<their words>, and answer with the current target. A stale one is reported with its staleReason, never replaced by a guess.",
        "SOURCE POLICY, when the user says where to look first for a topic or what not to use: {kind: 'source_policy', value: {topic: their words, keys: some of materials/regulations/products/precedents, prefer: [...], avoid: [...], note}}. Their message is the evidence; no design, page or board is needed.",
        "RECIPE, when the user says a task should always follow a procedure the skill library holds (e.g. 以后出平面图前都按事务所的填充标准检查一下): {kind: 'recipe', value: {task: their words for the task, skill: the skill as you see it, e.g. 'monkeyhub-library:hatch-review'}, appliesWhen: {domains: the ones their words indicate, of design/drawing/copy/research}}. The chat pins the library's current version (skill:<name>@<version>); with no library set, or no such skill, it refuses and says why: tell the user, never name another skill. The steps stay in the skill.",
        "Every context read, the prepared one included, carries ContextPack.memory: the locators, source policies and recipes its words are about. Follow a policy's prefer/avoid unless the request says otherwise, and say which sources you used.",
        "A recipe the turn carries has a skill: load the skill named in load before doing its task, and follow it. Its note says whether the pinned version is still the library's (pinned 1, library now 2) or the skill is not in the library; say so to the user and let them choose. Nothing is swapped for them.",
        "POST /api/memory/about {projectId, utterance} only reads that same selection with no design state; a turn without design context is already handed it.",
        "GET /api/memory lists items (?kind=locator|source_policy|recipe) with their revisionRef. On the user's request to forget one, POST /api/memory/{memoryId}/revisions with action=revoke and that revisionRef as expectedRevisionRef; the chat binds the reason and revisionMessageSource.",
    ]),
    "/api/board": chr(10).join([
        "BOARD: GET /api/board reads the Board; PUT /api/board saves it. Board arranges document references; generated drawings are saved by their drawing API.",
        "GET /api/documents lists registered drawings and pages (?runId=<runId> for one run's); GET /api/document-annotations reads page annotations and",
        "PUT /api/document-annotations saves them. Use their schemas for exact inputs. For page edits, read existing annotations and use",
        "baseRevisionSha256 with the exact run/asset/page/drawingRevisionRef.",
        'DRAWING PAGE: POST /api/board/export is a read-only native MCP image: body {projectId, pages:[{runId, assetSha256, revisionRef, pageIndex}], format:"png", zip:false, maxEdge:2048}.',
        "Copy exact source fields from GET /api/documents or the generated drawing result; revisionRef must be explicit (null for sources without a revision),",
        "pageIndex is zero-based. One clean source page, no annotations, at most 2048 pixels per edge and 4 MiB; use smaller maxEdge if too large. No operationId or awaitSeconds.",
    ]),
    "/api/exports": chr(10).join([
        "MODEL CONVERSION: When asked to export/convert a model to 3DM, SKP, GLB or DWG, use POST /api/exports via studio_request. Do not use an export button or write a converter in the shell.",
        "Body: {targetFormat: 'glb', attachmentId: '<exact chat attachment id>'} for an upload, or {targetFormat: 'glb', projectRevision: {runId, stateDigest, assetSha256}} for the exact current project model.",
        "Read current project state/artifacts first; never substitute an upload for project state. If 'this model' could mean the project or an upload, or multiple uploads match, ask which model. Do not guess by filename or newest file.",
        "Read GET /api/exports/capabilities for supported routes. Submission returns jobId/statusPath; poll that statusPath with GET, report queued/running progress, and only on succeeded return its downloadUrl as a Markdown link with warnings.",
        "On failed/interrupted report failureReason, never invent a file link. For SKP/DWG without an available verified executor, say 当前没有配置可用的执行器; never describe the format as permanently unsupported.",
        "Installed software does not establish conversion capability. The backend chooses providers; users do not need to choose software. Same-format validated delivery is not a conversion. Do not use awaitSeconds on exports.",
    ]),
    "/api/render": chr(10).join([
        "RENDER: an AI render makes one new image from one exact registered source image (a PNG or JPEG page), up to the",
        "provider's maxReferences reference pages and a written direction. It changes no model, drawing, Stage or HEAD; its",
        "result is saved as a new registered document, which Board shows.",
        'LOOK FIRST: look at every page you discuss with POST /api/board/export (body {pages:[{runId, assetSha256, revisionRef, pageIndex}], format:"png", zip:false, maxEdge:2048},',
        "fields copied exactly) before you describe it, and say only what you saw. The images and their roles (source, reference)",
        "are the user's choice; never pick a newer, similarly named or nearby image yourself.",
        "UNDERSTAND the request as two lists kept apart: what the user said explicitly (keep, change) and what you infer, each",
        "marked as your inference. Keep the source's viewpoint, geometry, massing and occlusion unless the user asks otherwise;",
        "a reference lends only what the user names (material, light, mood).",
        "One image shows one view: never state a count or total (columns, windows, bays) that the image cannot establish.",
        "A different viewpoint needs a different source image: say which source is missing and draft no request from the current one.",
        "A correction replaces the earlier direction it contradicts and keeps what it does not touch. The user's words ask for an",
        "image; they are never an accepted design decision, so do not change the model or save feedback because of them.",
        "PROVIDERS: GET /api/render/capabilities lists the configured image providers. None, or available false, means image",
        "generation is not configured here: say so (with unavailableReason when given) and stop at your understanding and the",
        "direction you would send; never submit with a placeholder providerId.",
        "SUBMIT only when the user's words ask for the image to be generated; an attempt may be paid. POST /api/render/jobs",
        "{requestId: a new UUID, providerId, source, references, direction, output: {size, aspectRatio}} with exact page fields,",
        "a size and aspect ratio the capability lists and at most its maxReferences; the chat fills projectId. The same requestId",
        "never starts a second attempt and a changed request needs a new one; read an uncertain answer back instead of resubmitting.",
        "FOLLOW: GET /api/render/jobs/{jobId} answers queued, running, succeeded, failed or unknown; GET /api/render/jobs lists",
        "earlier attempts. On succeeded, show its document with chat_present kind=assistant documents:[{runId, assetSha256,",
        "revisionRef, pageIndex}] copied from it. Unknown means the outcome could not be confirmed: say so and do not resend.",
    ]),
}


# Paths whose work one of those guides explains, under another prefix.
_GUIDE_OF = {"/api/documents": "/api/board", "/api/document-annotations": "/api/board",
             "/api/intents": "/api/decisions", "/api/studies": "/api/decisions"}


def _guide(prefix: str) -> str | None:
    """The domain guide a discovery prefix falls within, if any."""
    root = "/" + "/".join(prefix.strip("/").split("/")[:2])
    return _GUIDES.get(_GUIDE_OF.get(root, root))


_PRESENTATION_DOCUMENTS = (
    "Project drawings belong to existing project document APIs; reference runId/assetSha256/revisionRef/pageIndex exactly. "
    "A display reference does not mean a Board write succeeded."
)
_NATIVE_PRESENTATION_INSTRUCTIONS = (
    "This is the current MonkeyHub conversation: normal text and progress already stream automatically. "
    "Use chat_present to show selected media or one suggestion card with kind=assistant (its content may be empty), "
    "or public commentary with kind=progress. "
    "Hub binds status=streaming and completes the presentation when this native turn finishes. "
    "Supply a fresh UUID messageId; the current user turn is bound automatically. Do not republish the user's message. "
    "To update media or a card, reuse messageId with a strictly increasing revision and retain the full content, "
    "attachments and suggestion. No call here starts another model. " + _PRESENTATION_DOCUMENTS + " " + _SUGGESTION_INSTRUCTIONS
)
_PRESENTATION_INSTRUCTIONS = (
    "This connection displays results in the bound MonkeyHub conversation. Call presentation_bind once if available. "
    "For each external user request, call chat_present with kind=user and fresh UUID turnId/messageId; "
    "then default to publishing public progress, answers and selected images here without asking the user again. "
    "Use kind=progress only for public commentary, never hidden reasoning. Use kind=assistant for text and media. "
    "A streaming snapshot uses the same messageId and a strictly increasing revision, with full content and retained attachments. "
    "Use status=streaming while work continues; finish with complete, failed or interrupted. "
    "Keep the source host response concise with the Hub URL. This does not suppress mandatory host output. "
    "No call here starts another model. On disconnect or refusal, report it in the source host; reconnect with presentation_bind, "
    "then replay only the same presentation snapshot, never a design mutation. "
    "Only kind=assistant can carry a suggestion; its content may be empty. Keep the same messageId and increment revision for a changed card. "
    + _PRESENTATION_DOCUMENTS + " " + _SUGGESTION_INSTRUCTIONS
)

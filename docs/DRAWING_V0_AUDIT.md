# #244：Drawing 当前仓库审计与最小 V0 提案

审计日期：2026-09-23。基线：`27e7f2fddb8bf969abd4db8e1c575b8e1ba14b0d`，核查时与远端 `main` 一致。工作位置为本任务分配的 `dfdf/ARCHFLOW_V4`；未新建 worktree。前十节记录实施前审计与方案，末节记录获准实施的结果；均不代表真实项目接受或 Drawing 产品验收。

**建议 EXTEND：以一张真实水平剖切平面，闭合“准确设计来源 → 可修改表示 → 设计修改 → 派生过时 → 重建与断锚”链路。** 当前的 exact STEP、断面算法、文档身份、图纸 receipt、批注版本及设计 proposal 可以复用；缺少把它们接成模型关联 Drawing 的产品路径。不要新增 DrawingState、通用 RepresentationState、项目数据库或几何引擎。

依据为 [#244](https://github.com/cogco1/MonkeyHub/issues/244) 最新正文（读取时无评论）、[#223 审计结论](https://github.com/cogco1/MonkeyHub/issues/223#issuecomment-5783447948)、[#66](https://github.com/cogco1/MonkeyHub/issues/66)，以及下列源码。`docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md` 第 0 节和 P115 功能节点图用于定位；其中旧 MonkeyDiagram 产品称谓不覆盖当前 Hub 代码。

## 1. 哪些现有类型覆盖 V0？

| 事实及 owner | 实际类型、public API 与实现位置 | 可复用程度 |
|---|---|---|
| 设计真源：`state.record` | `StateRecord`、`Entity`、`Parameter`、`StateRecordOperator`；[state_record.py](../archflow/state/state_record.py)，137、175、479、965 行 | 设计实体、参数、关系、依赖与锁；Drawing 不写入这些事实 |
| 来源与登记：`studio.artifacts` | `ModelSource`、`SourceDocument`、`DocumentPage`；[application/artifacts.py](../apps/archflow-studio/api/archflow_studio_api/application/artifacts.py)，120、217、247 行 | 已保留精确来源、逻辑 drawingId、immutable revision、recipe；可直接延伸 |
| 投影与保留：`runtime.drawing_elevation` | `ElevationSource`、`ElevationView`、`ElevationProjection`、`ElevationDrawing`、`VerifiedElevationSource`；[drawing_elevation.py](../monkeydiagram/drawing_elevation.py)，144、187、267、326、339 行 | 已能校验 exact STEP、生成 SVG/PNG、P036 落盘及冷读回；目前只接 elevation/top |
| 几何：`adapters.cad_execution` | `StepEntry`、`OcctDrawingPolyline(object_id, kind, points)`、`OcctDrawingRegion(object_id, loops)`；[occt_backend.py](../archflow/adapters/occt_backend.py)，896、1124、1133 行 | 已有真实剖切、断面区域、深度裁切、整体 HLR；无持久 edge/vertex naming |
| 页批注：`studio.intent` | `DocumentGesture`、`DocumentAnnotationRef`、`DocumentAnnotationPage`；`read_document_annotations` / `save_document_annotations`；[gestures.py](../apps/archflow-studio/api/archflow_studio_api/application/gestures.py)，97、149、187、313、327 行 | exact page/revision、撤销、CAS、冷重开；不是模型尺寸 |
| 图纸表达：`documentation.drawings` | `DrawingPlan`、图纸验证、PaperCanvas 与 PDF/DXF renderer；[drawings.py](../monkeydiagram/documentation/drawings.py)、[drawing_output.py](../monkeydiagram/drawing_output.py) | 可复用纸面单位、尺寸一致性和绘制。其 `DrawingState` JSON schema 是调用方提供数据的验证格式，不是已存在的持久 Drawing 产品状态；不得将它升级为第二真源 |
| 存储：`project.repository` | `FilesystemProjectRepository.put_json / put_workspace_file / load_json / list_json`；[repository.py](../archflow/project/repository.py) | 沿现有 `STUDIO_SOURCE_DOCUMENT`、`DRAWING_PROJECTION_RECEIPT`、`STUDIO_DOCUMENT_ANNOTATIONS` record kinds；无需新数据库 |

实际已有 HTTP：`GET/POST /api/documents`、`GET /api/documents/{sha}/bytes`、`POST /api/drawings/elevations`、`GET /api/drawings/styles`、`POST /api/drawings/sheets`、`GET /api/drawings/model-view`、`GET/PUT /api/document-annotations`、`POST /api/proposals`。前两类 drawing 生成由 [routes/drawings.py](../apps/archflow-studio/api/archflow_studio_api/routes/drawings.py) → `application/drawings.py` → 当前投影 owner 调用；`model-view` 是不保留的观察 PNG。

## 2. 哪些 UI 可以复用？

- [ChatShell.tsx](../apps/monkeyhub/web/src/ChatShell.tsx)：37–40 行只有 Arch、Board、Fab、Monitor；当前没有独立 Drawing 导航。641、697、962 行等工具路由也只有 arch/board 分支。V0 应在同一 Hub/ProjectRuntimeProvider 下增加 Drawing 页面选择，不增加应用进程、launcher 或 retired Diagram 服务。
- [DocumentCanvas.tsx](../apps/monkeyhub/web/workspaces/src/workspaces/monkeydiagram/DocumentCanvas.tsx)：449 行比较 `ModelSource` 三元组；532 行恢复 style/scale；712 行生成新图；保留页面缩放、文档选择、准确 revision、来源展示、异步 scope 检查与批注保存。由 [Stage.tsx](../apps/monkeyhub/web/workspaces/src/features/stage/Stage.tsx) 2064 行挂载。复用这些交互；Drawing 专用控件只显示 view/scale/样式/尺寸/重建，Board 继续承担 review tools。
- 当前 `generateSheet()` 传 `modelSource`，但未传已有 `sourceStageRef` prop；首片须完整传递已有 Stage 字段，不能从“最新”或文件名反推。
- [client.ts](../apps/monkeyhub/web/workspaces/src/api/client.ts)：210 行已有 `elevation/drawingStyles/drawingSheet`，423 行已有页面批注读写；沿同一客户端扩展。
- [boardScene.ts](../apps/monkeyhub/web/workspaces/src/workspaces/monkeyboard/boardScene.ts)：`PageSource = runId + assetSha256 + revisionRef + pageIndex`，`pageKey()` 使用全部四项。[boardNavigation.ts](../apps/monkeyhub/web/workspaces/src/workspaces/monkeyboard/boardNavigation.ts) 保留 Board 视口和选中项；继续用这一准确页面交接。
- [boardFeedback.ts](../apps/monkeyhub/web/workspaces/src/workspaces/monkeyboard/boardFeedback.ts) `prepareBoardDesignRequest()` → [App.tsx](../apps/monkeyhub/web/workspaces/src/app/App.tsx) 1582 行 `propose()` / 1673 行 `compileIntent()`：已有批注转设计请求、来源核验和候选路径。复用，不另造 Drawing 建模执行器。

现有 UI 的“文档模型与当前编辑模型不同”仅是 source mismatch，不是 dependency-based outdated。浏览历史不能自动切换设计基底。当前页面工具也没有可编辑的模型尺规；不能以现有 ruler renderer 当作尺寸产品已完成。

## 3. `StudioSourceDocument@1` / `viewRecipe` 的准确字段与缺口

`SourceDocument` 内部字段为：

```text
project_id, run_id, asset_sha256, file_name, mime_type, size_bytes, pages
model_source, model_source_binding_ref, drawing_id, revision_ref
source_stage_ref, view_recipe, generated_at, replaces_pages
```

持久登记的基本字段是 snake_case；新增历史字段使用 `modelSource / drawingId / revisionRef / sourceStageRef / viewRecipe / generatedAt`。`model_source_binding_ref` 是读回登记 record 的 URI，不是额外持久文档身份。HTTP 的 [SourceDocumentDto](../apps/archflow-studio/api/archflow_studio_api/transport/artifacts.py)（330 行）映射为 camelCase。

`ModelSource` 恰为 `runId / stateDigest / assetSha256`；相同像素或相同文件名不能替换其中任一项。`revisionRef` 当前在单视投影中指向 `DrawingProjectionReceipt@1`。`drawingId` 用于同一逻辑视图的多次生成。

当前存在两种 recipe，不能混为已有统一强类型：

| 单视 elevation/top：`ElevationView.to_dict()`，249 行 | 三视 review-sheet：`generate_sheet()`，315 行 |
|---|---|
| `name, kind, projection, origin, look, right, up` | `kind: review-sheet, style, scaleDenominator` |
| `uv_definition, depth_definition, crop_uv` | `hiddenObjectIds, outlineObjectIds, notes` |
| `near_depth, far_depth, hidden_lines, linear_deflection` | `views: {front,right,top}`，各自为左列 frame |
| `scale: "1:N"` | `source: {modelSource,sourceStageRef,stepSha256,cadReceiptRef}, fonts, title` |

三视 PDF 经 `save_document()` 登记，**目前没有单视 projection receipt 的 `revisionRef`**；它依靠原始资产 SHA 和登记身份。不可假定每个生成图纸都已有相同 revision 协议。[artifacts.py](../apps/archflow-studio/api/archflow_studio_api/application/artifacts.py) 386 行对非空 `revisionRef` 特别调用 `read_model_axis_elevation()` 并验证 PNG、来源 run 与 recipe。

首片需要新增的是现有单视 recipe 的局部内容和调用行为：

1. cut-plan discriminator/切面定义与向下深度，复用现有 frame/crop/scale/units。
2. 最小 `graphics`：纸面 mm 的 section/visible 笔宽及一种剖切 hatch；样式值由旧 recipe 恢复。
3. 模型关联 `dimensions`（本页内 id、语义端点、测量方式、纸面放置）；见第 5 节。它们是 `viewRecipe` 内容，不是另一个 DrawingState。
4. 同一 receipt 中可选 `previousRevisionRef`，使表示修改/重建有准确前驱；保持 `drawingId`，登记新 `revisionRef`。旧数据未提供字段时按旧语义读取，不能补值后重新计算历史 digest。
5. 针对明确目标来源的只读 status/断锚计算，以及相应 UI；不保存 `stale` 权威字段。

表示修改继续生成新的 immutable drawing receipt。表示内容与旧图相同且来源相同的重试，沿既有 cache/readback 返回原图。recipe 是确定性输入，几何和像素是其派生物；允许修改的是前者。

## 4. 当前究竟能生成什么几何？

已有立面/顶投影生产链：`_selected_source()` → `_complete_source()` → `read_elevation_source()` → `project_model_axis_elevation()` → `project_occt_lines()` → SVG → 同源 PNG → `freeze_model_axis_elevation()` → 文档登记。它验证完整模型、STEP receipt/hash、program/source/run/base、单位和对象集合。

`ElevationRequestDto` 只允许 front/back/left/right/top；[transport/drawings.py](../apps/archflow-studio/api/archflow_studio_api/transport/drawings.py) 28 行明确 top 不是剖切平面。没有已接通的 section/plan HTTP 产品路径。

但几何原语已具备，位于现有 CAD owner：

- [occt_backend.py](../archflow/adapters/occt_backend.py) 1299 行：`project_occt_lines(..., depth_range=None)`；深度裁切实际对 B-rep 与 slab 作 Boolean common，再对所有参与对象统一执行 HLR。
- 1375 行：`section_occt_lines(...)`，由 `BRepAlgoAPI_Section` 生成真实相交线，保留 `object_id`。
- 1457 行：`section_occt_regions(...)`，按 solid 与拓扑连通性取得闭环及内孔；开壳/开线不会自动变成填黑材料。
- [drawing_svg.py](../monkeydiagram/drawing_svg.py) 159–181 行当前只输出 visible/hidden；传入 section 会被漏掉。278 行的 PNG renderer 只接受受限 SVG 元素，尚不支持填充。必须同时扩展两者及孔洞校验，不能只接算法就声称平面完成。
- 尺寸还需要引线/端部符号/文字的同源绘制，现有 SVG→PNG 通道也没有文字支持。[documentation/styles.py](../monkeydiagram/documentation/styles.py) 134 行 `_dimension()` 和 [drawing_output.py](../monkeydiagram/drawing_output.py) 176 行 `PaperCanvas`、311 行文字 primitive 已有纸面绘制实现。应在原 owner 延伸这些绘制原语及字体度量，避免用网页 overlay 显示新尺寸却把旧 PNG 当作完整 retained drawing；SVG/PNG 必须来自同一 recipe 和已解析尺寸。
- [documentation/styles.py](../monkeydiagram/documentation/styles.py) 199 行的 `compose_review_sheet()` 固定 front/right/top；不是现成的单视剖切排版。`drawing_output.py` 532 行虽然有 model-view DXF/section hatch 支持，当前 sheet caller 未使用，而且该接口要求模型米→输出 mm，不能直接传任意 STEP 单位。

## 5. 当前 anchors 是什么？首个尺寸最少需要什么？

当前页引用：`DocumentAnnotationRef(run_id, asset_sha256, page_index, revision_sha256, drawing_revision_ref)`。当前 `DocumentGesture` 字段：`id, kind, points, color, line_width, label, font_size, closed`。`points` 为页面左上原点的 [0,1] 坐标；text anchor 是文字框左上角。DTO 明确拒绝 model hits/world/camera。`ruler.label` 是手工文字，不是测量值。[gestures.py](../apps/archflow-studio/api/archflow_studio_api/application/gestures.py) 97–210 行、[transport/intent.py](../apps/archflow-studio/api/archflow_studio_api/transport/intent.py) 145 行。

CAD 线条已有 `object_id`，SVG 有 `data-object`；没有可跨重建信任的 edge ordinal。`DrawingPlan` 中 start/end/value/datum/source 可以做尺寸一致性校核，却不证明来源模型正确。不能用像素端点、最近线或 `EDGE5` 作持久语义锚点。

**建议首个 dimension 限定为一个直墙上的单个矩形空门洞宽度**，其 width 已有 `@parameter_key` 绑定；不处理成排实例、拱洞、任意 imported B-rep 子边或任意约束反解。最小新增内容放在已有 `viewRecipe.dimensions`，不改变纯 page ink 的定义：

```text
id                          本图内尺寸 id，不是另一文档 id
anchors[2]                  entityRef + openingId + feature(start-jamb/end-jamb)
measurement                 opening-width
placement                   纸面偏移/文字位置；从模型端点投影后应用
designTarget                可选 parameter:<key>；仅真实存在的绑定可启用驱动
```

来源无需在每个 anchor 再复制一遍：它由包含它的 document/receipt 的准确 `ModelSource + sourceStageRef` 固定。接收时服务器解析 `entityRef`、opening id、真实参数映射及 producing object refs，验证它们确实属于该来源；不能信任客户端自行宣称 `designTarget`。

重用 [reference_resolver.py](../monkeyarch/capabilities/reference_resolver.py) 的 `HostLine / parse_reference / resolve_plan` 以及 [wall_solver.py](../monkeyarch/capabilities/wall_solver.py) 的 `OpeningRequest.along0/along1`、`HostedVoid`、`WallElement.plan_point()`。`HostedVoid` 已携带 wall、host/cut/tool/aperture object ids、along0/along1、sill/head；[element_producers.py](../monkeyarch/capabilities/element_producers.py) 719 行 `produce_wall()` 是真实调用方。若需要暴露对应 semantic feature，就延伸这个 owner 的纯输出，不在 Drawing 重写墙/门算法。

应用层组合语义 feature 与 exact cut geometry，核验切面穿过该门洞且端点确实落在当前断面几何上，随后投影尺寸。没有准确匹配时返回未解析，不能拿名义参数值冒充已测量模型。图纸算法仅消费解析后的值；不允许 `monkeydiagram` 直接导入 `monkeyarch` 工作流。

重建后分辨 `resolved / missing subject / changed feature / outside view / ambiguous`；删除墙或洞就保留原尺寸定义并列出断锚，不重绑邻墙，不继续显示一个似乎有效的旧测量值。修复必须是明确选择新绑定。旧图仍可按原来源查看。

## 6. revision 如何参与 dependency / invalidation？

**当前 main 没有可直接登记 Drawing 的项目级 dependency index。** [StateRecord.dependency_edges()/closure()/closures()](../archflow/state/state_record.py)（479–536 行）覆盖设计实体、参数、关系。`DependencyEdge` 的现有字段为 `upstream_ref, downstream_ref, relation, source_ref, effect`；effect 为 `invalidates / requires_revalidation / blocks / supports_only`（[operational_state.py](../archflow/state/operational_state.py)，80、449 行）。#223 的 project-global index 是结论/方向，不能报告成已交付 API。

首片在 `studio.artifacts` 的 drawing 应用逻辑中按现有 records 派生一份临时 dependency/status 结果：

```text
exact design Stage / model receipt / compiled program
    → 本 drawing recipe 实际读取的对象、参数、材料和选择范围
    → DrawingProjectionReceipt / StudioSourceDocument revision
    → exact page review references
```

不往 StateRecord 塞 drawing entity，不新增全局 graph writer。将来项目级 index 可以读取这些现有 owner 的事实，无需搬迁内容；本任务不抢改 #32/#185 ContextPack 或 #122 Study 基础结构。

比较规则必须明确：

1. **先选比较目标。** 通常从源 Stage 所属设计分支的现有 branch head 读取目标 accepted Stage；也可明确选择目标 exact ModelSource。不可取项目任意“最新文件”。历史图正常可读，outdated 是相对目标来源的判断，不是损坏。
2. 比较实际 read set：解析 recipe 的对象选择规则、crop、cut/depth，再读取参与可见性/断面的对象及 anchor 的 entity/parameter closure。几何输入复用 [cad_patch.select_patch_operations](../archflow/adapters/cad_patch.py) 的 structural/analytic comparison 与 CAD receipt/program refs；编译器的 `object_digest` 包含整个 semantic binding 的对象清单和 evidence，会把无关对象新增误报为变化，不能直接作为 Drawing 几何比较键。材料仅在本图表达实际读取时参与。
3. **不能只追踪输出线的对象。** HLR 遮挡物同样是输入；还要重算新来源中进入/离开视域的对象集合，否则新增墙不会触发更新。V0 可保守地重算整张受影响视图的 HLR；不建立逐线增量引擎。源单位、坐标变换、算法变化也必须进入判断。
4. anchor 所依赖设计对象变化 → 测量/标签需重解；只是 lineweight、scale、placement 改变 → 新 Drawing revision，设计不变。无关 render override 不在 read set 中，不使图纸 outdated。
5. 只有 run/Stage binding 变化而实际读取内容相同，应显示“来源版本不同、相关内容未变”；不能仅比较 `stateDigest` 就宣称图形失效，也不能静默改写旧绑定。用户要求绑定新来源时仍产生新 receipt。
6. 结果是派生的 current/outdated/partially-broken 与原因；无充分 source 证据时显示无法验证，不能返回 current。旧 records 不写入 `stale: true`。

相关闭包只传播已声明依赖。V0 对一张视图做保守整视图重建，不承诺发现未声明建筑关系，也不把 compiled object digest 等价于最细粒度视觉等价。

## 7. 最小第一视图与完整操作链

选择 **单层房间、直墙、一个参数化空门洞的水平剖切平面**。它与竖向剖面使用同一 OCCT API，复杂度无实质差别，而且能直接检验门洞、剖切材料与平面尺寸。

1. 打开一个已接受 Stage 的完整 exact STEP；不接受缺少完整 STEP 的 composed/imported 模型，也不以其局部 native 模型代替。
2. 保存 cut height / look-down depth / crop / 1:N / 两种笔宽 / 一种 hatch。设计 Y-up 坐标由现有 `cad_point()` 一次转为 CAD Z-up；CAD frame 为 `origin=(0,0,h), right=X, up=Y, look=-Z`。
3. `section_occt_lines/regions()` 取切线和材料；同源 `project_occt_lines(..., depth_range=(0,h-bottom))` 取切面以下背景；按背景→hatch→section→尺寸绘制。h/bottom 按 STEP 的长度单位解释。
4. 首个尺寸绑定上述门洞的两个语义 jamb、实际断面和已有 width 参数。拖动尺寸只改 recipe placement。修改 scale/lineweight/hatch 生成新表示 revision；不调用设计 proposal。
5. 关闭重开，恢复同一 `drawingId`、选中 `revisionRef`、准确来源、recipe 与 dimension 定义。
6. 在 Arch 移动该墙，通过原 proposal/candidate/acceptance 路径形成新的 accepted source。Drawing 相对该分支来源派生 outdated；不会自动替用户接受候选。
7. 显式重建：新来源 + 旧有效 recipe + 重新解析的 anchors → 新 receipt；旧 SVG/PNG/recipe/批注 revision 均保留。有效 visibility/样式/纸面偏移保留；不存在的对象选择与尺寸锚点列出问题，不静默丢掉。
8. Board review marks 留在它们原来的 exact page revision 上。不能把旧 normalized points 整体复制到新图并声称评阅或几何关联已继承。Drawing 中带语义锚点的尺寸才可经解算迁移；无锚的 Drawing 标题等纸面意图按其明确 frame 规则保留。

建议只在现有 drawings router 增加一个 plan 请求入口，并共用当前生成/保留/读回实现；不改旧 `top` 的含义。新增入口、DTO 与函数名称在实现评审后定稿，不能将此提案中的字段误认为当前可调用 API。

## 8. 哪些交互只改表示，哪些走设计 proposal？

| 用户操作 | 唯一路径 |
|---|---|
| 改比例、切高、裁切、显隐、线宽、hatch、尺寸/标签位置、图名 | 更新现有 viewRecipe，生成 Drawing revision；保留 Design State、branch head 与 canonical HEAD |
| Review 圈注、意见、批准/驳回 | Board/现有 page annotation/comment owner；不得变成模型几何或自动设计接受 |
| “把这个门洞改为 1200 mm”且有有效直接参数绑定 | Drawing 解析准确 entity/parameter、单位与当前来源，调用现有 `POST /api/proposals`；以参数单位换算后的值生成 proposal，再进入 candidate 和明确 acceptance |
| 当前尺寸只有几何测量，找不到唯一直接设计控制，或参数派生/锁定/断锚 | 禁止改可见文字伪造结果；沿现有 derived/locked/missing-control 反馈，保留可读测量与明确限制 |

准确现有请求是 [ProposalRequestDto](../apps/archflow-studio/api/archflow_studio_api/transport/proposal.py) 483 行：`projectId, stateDigest, sourceRunId, sourceStageRef, keep`，以及 `utterance + targetComponentId + elementId` 或 `semanticEdit` 二选一。对于已验证 `@door_width` 且单位为米的首例，可提交 `semanticEdit: {summary: "…", parameters: [{key: "door_width", value: 1.2}]}`。不加入第二模型解释，不修改原 dimension label，不拉伸分离二维线。

调用方必须携带所显示图纸的准确来源，不用当前编辑窗口的另一模型代替。参数锁、keep、派生参数和 stale base 校验继续由现有 intent/StateRecord owner 执行。生成候选、接受 Stage、正式 issue 保持三个不同动作。

## 9. 哪些明确留给 Board / #66？

Board：自由草图、参考图、箭头/圈注、评阅意见、storyboard、查看和返回准确图页。Drawing 只提供准确图页及可重建表示，不搬入 Board 的自由构思工具。

Publish #66：跨 Drawing/Render/Board/文字/图片的页面组合、PPTX/PDF/report package、模板与叙事、sheet-set 排版与交付。Drawing 的 1:N 视域、图名、可引用 revision 是消费输入；首片不做一般出版编译器。现有三视 review-sheet 路径保持原边界，不借本任务扩张为 Publish。

另延后：任意 CAD 子拓扑持久命名、全套尺寸约束求解、DWG round-trip、详图建模、整套施工图标准、多用户审签、全局 production state、逐对象增量投影。按实际后续使用再扩展。

## 10. 精确实施范围、测试与验收

| 最小改动 | owner 与文件 | 复用/扩展检查 |
|---|---|---|
| 水平切面 recipe、cut geometry、immutable revision/前驱与老数据读回 | `runtime.drawing_elevation`：`monkeydiagram/drawing_elevation.py`；必要契约登记 | `tests/test_drawing_elevation.py`：旧 top 语义、冷读、same base、篡改/源缺失先拒绝；新增 plan/recipe/前驱冷读 |
| section 笔宽、even-odd hatch、尺寸线/文字、SVG→PNG 一致 | `adapters.drawing_svg`：`monkeydiagram/drawing_svg.py`；必要时延伸 `documentation/styles.py` 与 `drawing_output.py` 的已有纸面绘制原语 | `tests/test_drawing_svg.py`、`tests/test_drawing_output.py`；`tests/test_occt_execution.py::OcctDrawingTests`：洞不填、开壳、独立切块、深度遮挡、单位 |
| 单视 plan 接口、来源/对象核验、read set/status、重建 | `studio.artifacts`：`application/drawings.py`、`routes/drawings.py`、`transport/drawings.py`；`application/artifacts.py` 仅扩充已注册 revision 读回 | `apps/archflow-studio/api/tests/test_drawings.py`：保留旧图、精确 Stage、same-pixel revision、缺少完整 STEP；新增 outdated/rebuild/断锚/非相关输入 |
| 一个矩形洞尺寸的 semantic feature | 当前 producer/reference/墙洞 owner：`monkeyarch/capabilities/element_producers.py`、`reference_resolver.py`、`wall_solver.py`，仅在无法直接消费既有纯结果处延伸；应用层组装，Drawing 不跨 workflow import | `tests/test_element_producers.py`、`tests/test_derivations_and_references.py`、`tests/test_grid_free_wall.py`、`tests/test_wall_solver_contact.py`；新增准确洞 id/参数/feature 对应及删除后拒绝 |
| 独立 Drawing 页面与准确来源交接 | `hub.shell` / 既有工作区：`apps/monkeyhub/web/src/ChatShell.tsx`；workspaces `app/ProjectWorkspace.tsx`、`app/App.tsx`、`features/stage/Stage.tsx`、`workspaces/monkeydiagram/DocumentCanvas.tsx`、`api/client.ts` 及受影响现有 types/routes | `workspaces/test/drawingStyles.browser.mjs`、`documentModelSource.browser.mjs`、`documentAnnotations.test.ts`、`boardDocumentOpen.browser.mjs`；新增 Drawing 打开/关闭、迟到响应、表示操作无 design 请求、独立导航 |
| 改设计尺寸 | 复用现有 `POST /api/proposals`、candidate 和 Stage acceptance；除实际缺口外不改通用 proposal 引擎 | `apps/archflow-studio/api/tests/test_proposals.py`、`test_intents.py`：exact base、单位、参数更新、derived/locked/keep；新增从 Drawing 参数尺寸进入同一路径 |

代码实施时只因 public API、owner 契约或列出的 tests 发生实际变化才更新 `governance/module_registry.json`；沿现有 P115 live card 记录相关剩余接受项，不新造治理机制。需要公开 API 时同步现有 DTO/client/OpenAPI/MCP 对应项；不改并行任务的 ContextPack/Study 公共基础。

首片新增回归应覆盖以下可观察行为：

1. 墙/空门洞真实断面及纸面比例正确，SVG/PNG 的孔洞保持一致。
2. 修改 scale/线宽/尺寸放置后 Design state、branch head、canonical HEAD 不变，Drawing revision 改变。
3. 冷重开恢复准确来源与所有表示意图，旧 revision 仍可独立读取。
4. 移动墙导致相关尺寸/视图过时；进入视域的新遮挡物也被捕获；无关 render override 不触发。
5. 重建保留有效表示意图并重新测量；删除洞/墙、feature 不再唯一时明确断锚，绝不就近重绑。
6. 修改绑定设计尺寸产生现有 proposal/candidate；无参数绑定的测量不可伪驱动。旧图基底、keep 与锁得到相同保护。
7. Board 评阅仍绑定旧准确页；新 revision 不继承旧的审核结论。

实施完成需运行受影响测试、前端相应类型检查/浏览器回归、`python tools/archcheck.py`，并用真实房间平面操作核验。测试通过不代替产品视觉/空间验收。

## 本次只读验证与外部核验

已实际通过 **36 项现有测试**，无产品代码修改：

- 根目录：`python -m unittest tests.test_occt_execution.OcctDrawingTests tests.test_drawing_elevation tests.test_drawing_svg -v`，29 项，1.973 s。
- API 目录，`PYTHONPATH` 指向本 checkout 与 API：5 项 `DrawingTests`——`test_real_elevation_revisions_reopen_with_their_own_stage_and_bytes`、`test_same_png_drawing_revisions_have_independent_annotation_heads`、`test_stage_uses_pinned_runner_and_view_recipe_does_not_overwrite`、`test_top_projection_keeps_plan_dimensions_stage_source_and_cold_cache`、`test_sheet_hidden_objects_are_removed_before_visibility_and_recipe_changes_keep_old_pdf`；13.480 s。
- 同环境：2 项 `DocumentAnnotationTests`——`test_stale_revision_cannot_overwrite_new_ink_and_invalid_page_cannot_be_saved`、`test_page_text_edit_move_undo_redo_and_reopen_preserve_old_strokes_and_revision_bytes`；1.403 s。

第一次 API 调用因未正确设置本 checkout 的 import path 报 `No module named monkeydiagram`；更正路径后上述测试通过，不是代码修复。未执行前端浏览器测试或用户应用操作；本次不是 UI 验收，也未运行全应用套件。

另外用当前 `cadquery-ocp 7.9.3.1.1 / OCP 7.9.3.1` 做无文件输出的内存原型：4×0.2×3 m 墙减 1×0.4×2.1 m 门洞，在 CAD z=1.2 得到 8 条 section 线、1 个 region 内的 2 个闭环；全 top 投影存在横跨门洞的边，切面以下 depth-clipped HLR 不再横跨门洞，所有结果保持原 wall object id。这只证明现有算法可承担切片，不证明已完成尺寸关联或产品重建。

- [OCCT HLRBRep_Algo 官方参考](https://occt3d.com/dev/doc/refman/html/class_h_l_r_b_rep___algo.html) 与 [BRepAlgoAPI_Section 官方参考](https://occt3d.com/dev/doc/refman/html/class_b_rep_algo_a_p_i___section.html)：可见线投影与相交剖切是不同操作；支持本方案分别生成背景与切面。Section 输出不自动带来建筑 hatch、尺寸语义或持久锚点。这是对现有调用的核验，不是以最新版文档替代本机版本验证。
- [FreeCAD 官方仓库 #8878](https://github.com/FreeCAD/FreeCAD/issues/8878)：TechDraw 的二维 edge 索引在模型/HLR 变化后可能指向错误边，说明最近点/序号重绑存在真实风险。本方案据此把首例收窄到已有语义洞/参数，并显式报告无法解析；不能由此推定 MonkeyHub 已有通用 topology naming。

**审计后的实施决定：** 已获准按“同一 Hub 下的单张水平剖切平面 + recipe 内模型关联门洞尺寸 + 现有 revision/P036 + 派生 outdated/显式断锚”实施至可审阅 PR；Issue 保持打开。


## 首片实施结果

Drawing 已接入同一 Hub 与 Project Runtime。新 cut-plan recipe 沿用 SourceDocument/P036；水平剖切、below-cut HLR、单种 even-odd hatch 和门洞尺寸共用 SVG/PNG，源模型声明隐藏的 inspection witness 不参与可见线或填充。现有 top 保持未剖切投影语义。

`application/drawing_plans.py` 组合出图、准确来源比较、显式重建和现有 proposal；`application/drawing_dimensions.py` 复用 wall producer 的 HostedVoid，再核对原始 STEP 上两条唯一 jamb。尺寸纸面偏移与设计门宽分开，整个标注越界会显示 outside-view；初始自动图框为标注留出纸面空白。

真实房间 fixture 验证了 2000 mm 门宽改为 1200 mm 的候选生成、墙体移动、删除断锚、视域外对象进入、旧版本重开与旧图缓存。浏览器 fixture 验证 Drawing 进入/返回、窄屏、迟到响应、默认比较目标跟随准确来源分支而不重绑历史图纸；只有显式重建产生新版本。认证回归保留只读状态访问，设计提案仍要求 propose。代码测试和合成房间/页面检查不代表真实项目接受或正式出图。

本片尺寸仅支持已核验的直墙单个矩形空门洞和唯一直接宽度参数；派生、锁定或不唯一参数可读但不可驱动。既有斜墙布尔结果的 analytic bounds 限制仍可能使候选在出图前被拒绝；斜墙、填充门窗、多洞、全局约束尺寸与通用子拓扑命名不在此次验证承诺内。Publish 保留在 #66。

合并复核还覆盖了同一模型归属多份已接受 Stage 的情况：省略 Stage 时必须拒绝歧义，不能把历史模型当作未接受候选。已有无 Stage 引用的图纸在其模型后来进入多份接受历史后也不能驱动修改；历史页和模型来源不被重绑。显式当前 Stage 和真正未接受的候选保留原有行为。

本切片不等于当前 #244 全部验收完成。SVG 人物/树木等配景的插入、移动、缩放、翻转、删除，Agent 对独立矢量对象的放置，以及配景在重开和模型重建后的保留与断锚提示，仍需在同一表示所有者内实现。完成的源码范围与临时工作卡在集成时退役；这些产品剩余项继续由 GitHub #244 跟踪，Issue 保持开放。

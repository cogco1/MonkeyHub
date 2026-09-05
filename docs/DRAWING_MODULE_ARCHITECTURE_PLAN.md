# ArchFlow 通用出图模块架构方案

**状态：** 研究与实施建议，不是当前已交付能力。  
**审计基线：** 2026-09-04，`D:\ARCHFLOW_V4` `main`（`1e4d9cae`）及当时未提交的 Studio 工作树。  
**目标：** 从同一份语义模型稳定派生平面、立面、剖面、正交轴测和施工图纸；自动处理可追溯的
线宽、可见性、标注与更新，同时不复制模型、不把截图冒充图纸、不绕过 P036。

本文只规划一条新能力链。现有所有权仍以
[`governance/module_registry.json`](../governance/module_registry.json) 为准；进入实现前才为这条能力
登记唯一 owner，不能先并排建立多个 renderer 或 sheet system。

## 1. 核心判断

推荐组合是：

> **Revit 的 View 语义 + Snaptrude 的低摩擦视图/排版交互 + ArchFlow 的 exact identity、依赖闭包和验证。**

```text
StateRecord / Geometry Program / exact 3DM readback
                         │ exact project/run/base/state/program/model identity
                         ▼
                  Drawing View Spec
                         │ resolve datum, range, crop, visibility and style
                         ▼
                 Resolved View Plan
                         │ exact cut / projection / hidden-line backend
                         ▼
                 Projected Drawing
                         │ semantic annotations and dimensions
                         ▼
              Sheet + Placed View layout
                         │ SVG / PDF / PNG export
                         ▼
           P036 workspace files + boundary receipts
```

这里最重要的分离有四个：

1. **View 不是相机截图。** 它包含方向、投影类型、切面、范围、比例、语义可见性和样式规则。
2. **Placed View 不是另一份 View。** 它只负责把一个已解析视图放到纸面上的位置、比例、旋转和标题；
   crop 属于 View revision，需要不同裁切时生成明确的 view variant。
3. **图纸不是复制出来的模型。** 它引用 exact source；源变化后成为 `OUTDATED`，更新产生新 revision。
4. **施工图不是“更细的线稿”。** 缺少构造层、节点、材料、连接、容差或说明时必须保留缺口，
   不能让 Agent 或投影器猜补。

## 2. 外部参照中真正值得迁移的机制

### 2.1 Revit：视图语义和图形控制链

以下为 Autodesk 官方文档可确认的机制：

- `View` 是多种视图的共同对象，携带视图类型、比例、裁切、方向、Detail Level、Discipline、
  View Template 和 Filters 等属性；平面、立面、剖面、3D、详图、图纸等是不同 `ViewType`。
  [View API](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/fb92a4e7-f3a7-ef14-e631-342179b18de9.htm)、
  [ViewType API](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/76bee86d-3c34-7ee1-4349-cd7abcbf3d78.htm)
- 平面 View Range 由 Top、Cut Plane、Bottom、View Depth 共同决定；元素依据位置和可切割性显示为
  cut、projection 或 beyond，而不是简单从上往下截图。
  [View Range](https://help.autodesk.com/cloudhelp/2021/ENU/Revit-DocumentPresent/files/GUID-58711292-AB78-4C8F-BAA1-0855DDB518BF.htm)
- Object Styles 给项目级类别默认表达，Visibility/Graphics、Filters、element override 和 Linework
  在视图内形成有优先级的覆盖链。
  [Visibility and Graphic Display](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-DocumentPresent/files/GUID-A2FC119B-51D7-4C2E-84ED-CD51983EC532.htm)、
  [Override Hierarchy](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-DocumentPresent/files/GUID-67D3D6DB-E78D-4711-B9C3-4D30F1C22205.htm)
- View Template 把比例、细节级别、可见性等规则成组复用；有序 Filter 可按类别和参数改变显示。
  [View Templates](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-HaveYouTried/files/GUID-DD1AF833-EAA0-4C91-8CF3-EA8BB8B5C3D5.htm)、
  [View Filters](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-DocumentPresent/files/GUID-87A63C25-99A6-428D-A0FF-112E4FC9C3D7.htm)
- 模型线宽可使用按比例变化的笔宽表；透视线与注释线使用不同规则。线型由线宽、颜色和图案组成。
  [Custom Line Styles](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-HaveYouTried/files/GUID-1F4FD579-7C30-4BC1-8610-8FB0F5AF9D6A.htm)
- Sheet 与 Viewport 分离：Sheet 负责纸张和标题栏，Viewport 负责把 View 放到纸面坐标。
  [ViewSheet.Create](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/bc9e8be3-f3fd-97c2-2709-1d6eea3db775.htm)、
  [Add Views to a Sheet](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-DocumentPresent/files/GUID-D3E7FDC3-57AB-4497-AB87-D195476254C2.htm)

ArchFlow 要迁移的是这些数据分层，而不是 Autodesk 的 category id、16 号笔表或 UI 命令。

### 2.2 Snaptrude：Saved View、Placed View 和显式刷新

以下为 Snaptrude 官方帮助、发布说明和插件 API 可确认的机制：

- Saved View 保存 camera、visibility、storey 和 display settings；官方 API 明确把它称为 camera
  bookmark，而不是几何副本。
  [Edit and Save Views](https://help.snaptrude.com/en/articles/9091644-edit-and-save-2d-3d-views)、
  [Views API](https://docs.snaptrude.com/plugin-api/presentation/views)
- Present Mode 把 Saved View 放进 Sheet；Placed View 另存 sheet、位置、尺寸、比例、裁切和
  `isUnlinked`。独立 2D view 不能旋转，3D view 才能直接旋转；2D 只有成组后才随组旋转。内容刷新
  与纸面排版是两个动作。
  [Creating and Editing Sheets](https://help.snaptrude.com/en/articles/11087173-creating-editing-sheets)、
  [Placed Views API](https://docs.snaptrude.com/plugin-api/presentation/placed-views.html)
- 当前可靠更新语义是 `Update Views` / `Update All Views`，不是所有图纸永远自动同步；官方明确的
  更新矩阵覆盖 2D plan、site plan 和 3D view，更新保留 layout、crop、font 和 scale，部分相机/
  可见性变化需先重存 Saved View。现有资料没有证明 elevation/section 使用完全相同的刷新保证。
  [Update Views](https://www.snaptrude.com/product-releases/3-19-2)
- SVG-based Design plan 支持 cut/projection、线色、线宽、线型、填色、标签和文字；官方同时明确
  这套细化样式不普遍覆盖 raster、3D、elevation 和 BIM-mode objects。
  [View Settings in Present Mode](https://help.snaptrude.com/en/articles/12780498-view-settings-in-present-mode)
- Section Plane 可生成关联的垂直正投影视图，但现有公开资料不足以证明完整施工图、横剖/折线剖和
  详图索引。
  [Section Planes](https://www.snaptrude.com/product-releases/v2-7-0)
- Snaptrude 有 layer/bucket-based Proposals；新 proposal 可共享或复制 base proposal 的 bucket，
  删除时也可将 bucket 转移后执行不可撤销的 `merge and remove`。这不是 exact-base branch、commit
  graph、冲突合并或可恢复版本历史；History API 只证明命令栈 undo/redo。
  [Proposals API](https://docs.snaptrude.com/plugin-api/core/proposals.html)、
  [History API](https://docs.snaptrude.com/plugin-api/core/history.html)

ArchFlow 应采用它的低摩擦更新和排版，但不能复制 SVG-plan/raster-BIM 的二等公民结构，也不能把
`Update All` 变成静默覆盖。

## 3. 当前 ArchFlow 能力和真实缺口

### 3.1 可复用或必须明确受限的现有 owner

| 已有 owner | 可复用能力 | 在出图链中的职责边界 |
|---|---|---|
| `state.record` | Level、GridAxis、Element、Assembly、Space、Component、dependency closure | 提供模型语义和失效传播，不拥有图形样式 |
| `capabilities.reference_resolver` | GridRef、LevelRef、OffsetFrom、HostAlong 等已有符号引用 | 提供 grid/level/host datum 解析；尚不能解析 section frame、crop 或 view-local annotation anchor |
| `compilers.geometry` | operation order、frame/component/semantic-binding digest | 提供 exact program identity，不生成图纸 |
| `adapters.cad_program` | Rhino layer、对象 `archflow:*` 语义、分析 bounds | 提供对象语义映射，不把视口颜色当线宽标准 |
| `adapters.cad_execution` | Geometry Program → Rhino 3DM 的 host 监督、路径约束、保存和独立读回 | 现有 API 不是通用出图执行器且没有 repository 写权；A0 前只扩展一个共享 supervised invocation 入口，继续由它唯一拥有 host 生命周期 |
| `adapters.three_dm_inspector` | 3DM SHA、单位、对象、层、bounds、材质和 geometry digest | 验证 exact source，不以 bbox 代替线稿 |
| `runtime.project_runner` | 当前 discipline-seat、geometry export、Stage closure 的运行循环 | 是否可扩展为 drawing job 必须先用真实接口证明，不能先假定它已拥有通用出图编排 |
| `project.repository` | `put_json`、`put_workspace_file` 与 object ingest 的受限 P036 写入 | 第一版只用已注册 kind 写 record、用 `put_workspace_file` 写 run workspace；当前没有通用 `exports/` binary writer |
| `capabilities.relation_checks` | support 等关系的 held/violated/unchecked | 可生成诊断叠层，不负责制图 |

本机现有的 `render-archflow-stage-views` skill 可作为外部 inspection 消费者，验证固定 Stage evidence
sheet 的来源强度与视图标签。仓库不能依赖其本机路径或私有契约；未来只能消费仓库公开的 projection
artifact/receipt，不能反过来成为通用 View/Sheet owner。

### 3.2 当前没有的能力

定向检索当前生产路径后，没有发现通用 `Make2D`、View Range、View Template、自动线宽、
正交投影、Title Block、Drawing Sheet 或 Viewport Placement 实现。

特别需要纠正两个容易误判的现状：

- Studio `ThreeDmViewport` 当前使用 `PerspectiveCamera`；`frontView()` 只是改变现有透视相机的方向，
  不是由项目方位定义的正投影立面。
- `POST /api/captures` 保存的是 run-bound、non-canonical inspection PNG。它不具备矢量线稿、
  View recipe 或 renderer receipt，不能升级描述为正式图纸。

## 4. 建议的单一模块边界

模块名为实施建议，登记 registry 前不视为现有 owner。

```text
archflow/documentation/views.py
  ViewSpec + StyleProfile + resolve_view(...)

archflow/ports/drawing_projection.py
  DrawingProjectionPort

archflow/adapters/rhino_drawing_projection.py
  exact 3DM / Rhino orthographic / Make2D adapter

archflow/documentation/sheets.py
  sheet-native notes + placed view + sheet composition
```

`documentation.views` 是唯一的制图语义 owner；projection adapter 只执行已解析 recipe；
`documentation.sheets` 只拥有纸面组织。模型/视图空间 annotation 的 owner 到阶段 C 再决定，不能与
sheet-native notes 混在一起。Studio 通过 API 编辑和请求这些值，不重新计算 kernel 事实。

第一轮由同步的 documentation application use case 协调 `resolve → projection port → repository`，
不复用 candidate job，也不把制图语义塞入 `project_runner`。后续确需后台任务时，先决定是否将现有
candidate-specific job 抽象为 typed subject；不能伪装成 candidate job，也不能建立第二个项目 runner。

Rhino host 生命周期继续由 `adapters.cad_execution` 唯一拥有。A0 开工前先将它已有的启动、超时、
PID witness、清理和独立读回行为开放为一个最窄的 supervised invocation API；新 projection adapter
只拥有投影脚本和结果解析。不得在 adapter 内复制一套进程监督、PowerShell 或 cleanup 逻辑。

## 5. 数据值与分阶段登记

第一轮只登记一个持久化 `DrawingViewSpec@1` 和一个边界 `ProjectionReceipt@1`。下列其他名称先是
内部不可变值或后续阶段建议；在对应行为被真实纵切证明前，不登记为 record kind 或 wire contract。

### 5.1 A0 持久化契约：`DrawingViewSpec@1`

A0 只允许一种窄形状，不能预装尚未实现的联合分支：

- `view_id`；
- `kind = ELEVATION`；
- 一个 CAD execution receipt ref；该 receipt 必须唯一指向 source run 和 exact 3DM artifact，服务端据此
  验证并构造 `DrawingSourceBinding`，浏览器不得独立拼 project/run/base/state/program/model 身份；
- source model axis-aligned orthographic direction、crop、near/far clip；项目 North 尚无 canonical frame
  owner，不在首版猜测；
- 一个内置、固定且带 digest 的最小 display profile；
- visibility 固定为全部受支持模型对象；不含 query language、annotation、dimension 或 sheet policy。

后续按实际实现逐一扩展判别联合：PLAN 才引入 `LevelRef` 和 view range；SECTION 才引入 section
plane/segment；AXONOMETRIC 才引入 named orthographic orientation 和 section box。独立 storey、项目
North 或 facade direction 只能在有 canonical owner 后加入，不能先用 Studio 投影或可空字段代替。

无模型绑定的 manual/detail drawing 不进入这个 exact-model View 联合。未来若需要，应作为另一种 sheet
asset，显式保存来源并保持 `UNBOUND_DETAIL`，不得共享 `EXACT_MODEL_VIEW` 权威标签。

### 5.2 阶段 B 值：`DrawingStyleProfile`

- 不可变内容和 digest；
- compatible view kinds、scale buckets、controlled fields；
- semantic defaults、有序 filters、允许的局部 override；
- cut/projection/hidden/beyond 的 fill 与 pen mapping；
- 阶段 B 只处理模型线/面显示；annotation、material hatch、建筑 lifecycle phase 和 discipline display
  等到各自数据 owner 存在后再加入。

模板更新生成新 digest，不偷偷改变已经发布的图。

### 5.3 第一轮内部值：`ResolvedDrawingView`

- exact project/run/base/state/program/model digest；
- 已解析的 frame/range/crop/template；
- 候选 included/excluded 数量、原因分组和对象集合 digest；需要逐项审计时另存 manifest artifact；
- 已解析的固定 display rules；
- unsupported geometry、unresolved anchor、缺失语义。

存在会影响真实性的 unresolved 项时拒绝 `EXACT_MODEL_VIEW`，不能降级后仍沿用同一权威标签。

### 5.4 第一轮内部值：`ProjectedDrawing`

权威主格式是矢量 primitive。A0 只允许 model-derived path/region；每个 primitive 必须带 source
object GUID、实际 geometry class、固定 profile style 和 clipping/occlusion provenance。cut、projection、
hidden、beyond、silhouette 等实际分类只能由 projection backend 在真实几何运算后给出，不能由
`ResolvedDrawingView` 预判。

datum-derived、annotation-derived 与 sheet-native provenance 到对应阶段再加入；不得为了 A0 的统一
形状给非模型图元伪造 object/element/component identity。

PNG 只是从同一矢量结果按 DPI 生成的预览。没有实际 face/edge 时不能用 bbox 填面冒充模型投影。

### 5.5 阶段 C 值：`DrawingSheet`

- paper、orientation、title-block artifact ref；
- `PlacedView`：view revision ref、paper x/y、scale、rotation、title；
- sheet-level notes、revision 和 issue metadata。

同一不可变 View 可以被多个 Placed View 引用，不复制 Revit 的“一视图一图纸”产品限制。

## 6. 自动线宽算法

线宽不是按 Rhino layer 名猜，也不是把屏幕像素固定下来。算法先判定几何关系，再解析样式：

```text
exact geometry
  → cut / silhouette / projection / secondary / hidden / beyond / annotation
  → semantic importance（结构、围护、开口、家具、datum、诊断状态）
  → depth band + drawing scale bucket
  → ordered template/filter/element override cascade
  → PenClass
  → paper width in millimetres
  → SVG/PDF physical stroke；PNG 按 DPI 换算
```

建议最小 `PenClass` 词汇：

`CUT_PRIMARY`、`PROFILE`、`PROJECTION`、`SECONDARY`、`HIDDEN`、`BEYOND`、`ANNOTATION`。

纸面毫米值属于 `DrawingStyleProfile`，不是代码常量。比例变化可以选择不同映射表，但输出始终记录
最终纸面宽度和获胜规则。覆盖级联由低到高固定为：

```text
semantic default
→ temporal/discipline display profile
→ category/component rule
→ ordered filter
→ element override
→ primitive/linework override
```

任何最终线都应可回答“为什么是这条线宽”。未知 semantic role 不能悄悄落为普通黑线：模板必须
明确 fallback，或生成 issue。

## 7. 四类基础视图的推导

### 平面

1. 通过 Level/Datum + offset 解析 Top、Cut、Bottom、Depth。
2. 对实际几何求切割和投影，不以对象 bbox 代替。
3. 依据范围、可切割性、遮挡和 view depth 分类 cut/projection/beyond。
4. 生成墙体 cut region、真实 opening void、门窗/楼梯投影及模型关联尺寸锚点。

### 立面

1. 首版由 source model 的显式坐标轴 frame 定义视线，不依赖用户当前转到哪个角度；只有项目方向有
   canonical owner 后，才允许用 North/facade direction 命名和解析。
2. 使用 orthographic projection、crop 和 near/far clip。
3. 做 visible-edge/occlusion 解算，按轮廓、前后层次和语义分配 pen class。

### 剖面

1. Section 是有方向和范围的语义切面，不是透明 clipping plane。
2. 分开生成切到的实体 region、后方投影线和超出范围内容。
3. Section marker、detail callout 和尺寸引用同一 view revision 与 datum anchor。

### 轴测

1. 使用正交轴测 frame，而不是 perspective camera 截图。
2. 通过 section box 控制范围；需要剖切时沿用相同 cut classification。
3. 透视图可以作为 presentation view，但不能冒充带比例的 construction view。

## 8. 更新、依赖和 Agent 介入

Snaptrude 的“显式刷新”适合保留为交互启发，但以下 plan/elevation/section/axon 统一 revision 机制是
ArchFlow 自己的提案，不是 Snaptrude 已验证能力：

```text
source/template/annotation dependency changed
                  ↓
view status = OUTDATED
                  ↓
展示 changed refs → affected views/sheets → invalid annotations
                  ↓
Update Selected / Update All（先预览影响）
                  ↓
生成新的 View revision + projection receipt
                  ↓
旧 revision 仍可读；已发布图纸不被静默覆盖
```

`OUTDATED` 不是被写回旧 record 的可变状态，而是比较当前 dependency digests 与该 View revision
保存的 source/profile digests 后得到的派生结果。Stage A0 不实现 freshness/update，只验证一个 immutable
revision；至少两个 revision 的失效测试通过后才加入本节机制。

Agent 可以：

- 把“二层平面、北立面、穿中庭剖面，1:100”编译为 typed `DrawingViewSpec`；
- 根据 component tree、relations、levels、grids 和用户意图建议剖切位置；
- 解释哪些图因某次设计变更而过期，并生成更新候选。

Agent 不能：

- 在缺少 LevelRef、canonical project direction、section position 或图纸用途时自行猜关键参数；
- 直接吐一批 disconnected lines；
- 把 AI render、浏览器截图、bbox witness 或手绘 detail 伪装成 exact model view；
- 绕过 candidate/validation/issue 改 canonical state。

## 9. 持久化和 API 边界

第一版不新增项目级可变数据库或默认模板目录：

- drawing run 必须显式使用 `base = source_run.base`，并保留输入 CAD execution receipt ref；receipt、
  source artifact、source run 或 base 任一不一致即拒绝，不能默认绑定生成时的当前 `HEAD`；
- 只将 `DrawingViewSpec@1` 与 `ProjectionReceipt@1` 登记为 record kind；Style/Sheet 到对应阶段再登记；
- 第一版 SVG/PNG 落在 `runs/<drawing-run>/workspaces/documentation/`，只通过
  `put_workspace_file()` 写入；
- `ProjectionReceipt@1` 通过 `put_json()` 落在同一 run 的 `records/`；
- PDF、Sheet 和正式 `exports/` 发布延后到 repository/port 有明确 writer 与权限后；不得直接
  `Path.write_bytes()` 绕过 P036；
- Studio screenshot 继续留在来源 run 的 `workspaces/studio-captures/`，不混入 drawing artifacts；
- `HEAD` 只有既有 `issue_run` 路径可以改变。

Stage A0 不增加 API、后台 job 或 Studio UI，先在 Python application boundary 闭合真实行为。进入 Studio
接入前，再决定是将现有 `studio.artifacts` 扩展为多 artifact kind，还是由新 drawing owner 提供读取；
registry 边界未定前不新增平行 digest-serving endpoint。届时 DTO 仍由 FastAPI OpenAPI 生成
TypeScript client；浏览器不传服务器路径，也不手写 DTO 镜像。

第一轮只在真实投影边界保留一种回执：

`ProjectionReceipt@1`：source/model SHA、view/profile digest、projection/crop/backend、included-object
digest、filter counts、unsupported 数和矢量输出 SHA。

纯函数 `resolve_view` 不自造回执。阶段 C 真正存在 Sheet 外化边界时再决定是否需要
`SheetExportReceipt`；外部 Stage evidence sheet 只能消费仓库公开的 projection result，不能让仓库
反向依赖个人 skill。

## 10. 实施路线和停止门

### A. 精确视图骨架

只做一个经过回执认证的 exact 3DM → **一张按模型坐标轴定义的正交立面** → 确定性 SVG；
PNG 仅从同一 SVG 派生预览。先完成 spec → resolve → project → persist → readback → render 的真实纵切，
再按相同契约逐一加入平面、剖面和正交轴测。

通过条件：

- 同一 source/view/profile 生成相同 `ProjectedDrawing` digest 与 SVG bytes；规范化序列化必须固定 primitive
  排序、浮点精度、单位和 metadata，并排除时间戳；
- 正交 frame、crop、near/far clip 有机器测试；
- model primitive 的 source object GUID coverage = 100%，否则拒绝 exact；
- 缺 face/edge 或 unsupported geometry 时拒绝 exact；
- SVG 可解析，viewBox/单位/坐标均有效，unsupported/unresolved 为零，输出 SHA 与 repository 读回一致，
  receipt 绑定 source SHA，项目 `HEAD` 不变。

### B. 模板、过滤和自动线宽

加入 semantic defaults、有序 filters、cut/projection/hidden/beyond、比例桶和局部 override。

通过条件：

- 重叠 filter 的优先级确定；
- 同一 wall 的 cut 与 projection 取得不同 pen class；
- 至少在 1:50、1:100 验证 SVG/PDF 物理毫米线宽；
- 模板新 digest 只改变 `controlled_fields`；
- 未知语义进入明确 fallback 或 issue。

### C. 模型关联注释与单张 Sheet

加入 grid/level、dimension、tag、text、callout、title block 和 Placed View。

通过条件：

- datum/element 改动使尺寸重新解析；anchor 丢失时不产生外化 artifact；
- viewport 不越纸面，比例/标题可核验；crop 只随所引用的 View revision 变化；
- Sheet receipt 引用每个 projection receipt；
- 只更新选定 View 时不改变其他 Placed View 的排版。

### D. 施工图语义

在模型已有数据的前提下加入复合构造层、assembly、opening type、detail component、material hatch、
keynote、schedule 和 revision。

通过条件：

- 每项施工表达有模型或显式 `UNBOUND_DETAIL` 来源；
- 缺连接、材料层、容差或规范说明时报告 unresolved，不自动补画；
- detail 与上游模型变化通过 dependency closure 标记过期；
- `UNBOUND_DETAIL` 永远不被描述为模型精确证明。

### E. Studio 接入和批量图纸

Studio 只负责选择 source/view/style、显示 dirty/impact、请求后台任务、预览 SVG/PDF、排版和审查。

通过条件：

- 模型截图和正式 drawing artifacts 在 UI 与 API 中明确分开；
- 后台失败、unsupported geometry、broken annotation 不被吞掉；
- review comment 绑定 exact run/state/view revision/component/element/crop；
- 未来 Stage evidence composer 可消费公开 ProjectionReceipt，不产生第二套投影几何；在真实 adapter 和
  集成测试存在前，这仍是接入目标，不是当前事实。

## 11. 不照搬的部分

- Revit 的 Phase 是建筑生命周期显示，不等于 ArchFlow Stage。
- Revit Discipline 是图形规则，不等于 discipline seat 或写入权限。
- Revit Coarse/Medium/Fine 是视图表达，不等于模型成熟度。
- Autodesk category/family/parameter id 应映射到 ArchFlow semantic registry，而非进入核心契约。
- Revit 的隐式可变 View Template 和手工 Linework 必须改为有 digest、有来源的规则 revision。
- Snaptrude layer/bucket Proposals 不能代替 component tree、dependency graph 或 exact-base branch；
  其 `merge and remove` 是不可撤销的 bucket 转移，不是 commit merge。
- Snaptrude `Update All` 不应覆盖旧图；ArchFlow 必须生成可比较的新 revision。
- Undo、Save As、change log 和宣传中的“version history”都不能代替 P036 content identity 与 CAS HEAD。
- 未知构件不能静默降成 generic mass；需要 mapping report，并保持 PARKED/REFUSED。

## 12. 第一轮应做什么

第一轮只实现 **A：一张 exact 正交立面纵切**，并在一个小型、多层、含墙/门窗/楼梯/柱/屋顶的
真实 P036 fixture 上闭环。不要同时做另外三类 View、标题栏编辑器、无限画布、AI render、协作评论、
施工详图或办公室模板库。

第一轮成功的可见结果应该是：测试用 application call 接收一个 exact source ref 和 typed elevation
spec，生成一张可追溯 SVG、PNG 预览及一个 ProjectionReceipt；同一输入重跑内容身份相同，项目
`HEAD` 不变。首轮不含自然语言、Studio、job、annotation、sheet 或 update。随后再按平面 → 剖面 →
正交轴测的顺序扩展；四类 View 均闭环后才进入自动线宽阶段。

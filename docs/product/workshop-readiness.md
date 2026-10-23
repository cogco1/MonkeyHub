---
created: 2026-10-20
issue: 668
---

# 设计工作坊准备计划

一次 5 天的山地幼儿园设计工作坊，20–30 名学生、4–6 组，每人一台 Windows 电脑连续使用 MonkeyHub：

1. 用 Grasshopper 工具包算坡度、汇水、可达、挖填四张图，整理带出处的规则清单；
2. 用 Wallacei（Grasshopper 的多目标进化插件）优化台地和楼的摆布；
3. 把候选方案批量放进 MonkeyHub，比较、选择、写理由，并登记已定条件；
4. 深化时每次修改，由 MonkeyHub 检查这些条件，查不了的标出来。

日期未定，准备时间按工作坊第一天（T）往前倒排。本文按工作坊流程列出 MonkeyHub 要补的功能和稳定性改动，先 P0（工作坊离不开）后 P1（缺了明显影响，但有替代做法）。每一项以后各开一个 Issue；这里写清楚涉及的模块和文件、验收标准和依赖。

## 1. 现状（2026-10-20 核对）

| 项 | 核对结果 |
|---|---|
| main | `cc8f54e2`（10-20 02:47），含设计循环 D（#660、#662）和 10-14 的两批集成（#653、#654）。 |
| 本机安装版 | `D:\MonkeyHub\versions\4070fc6f5bb7-desktop`；`build-info.json` 的 `sourceCommit` 是 `4070fc6f`，即本地分支 `claude/skill-run` 的最新提交。 |
| `claude/skill-run` | 以 10-02 的 main（`389b0548`，#611）为底，比 main 多 62 个提交、少 103 个；没有推送到 GitHub。`claude/live-rhino-link`（`a32855aa`）是它的祖先，也没有推送。 |
| skill-run 的组成 | 两层：底下一层是本地联合候选 `0f5ce85f`，合了还没进 main 的 #627（GH-613）、#629（#618 M1 团队）和 #626（GH-619）；上面一层 `0f5ce85f..4070fc6f` 才是 Live Rhino、skill 运行、STL 导出、修剪环修复和相机分层图。 |
| 10-20 的两次重启 | 桌面日志写的是“确认空闲后重启到 `b2ad52af`／`4070fc6f`”，也就是有人调用了 `/api/updates/apply` 装本地补丁，不是 GitHub 上的自动更新。两次都是旧版先退出、新版后启动；端口依次是 61727、63330、51732。日志里没有两版同时运行的记录。 |
| 单实例 | 已有（#373）：同一个运行目录只开一个桌面窗口，第二次启动会把已开的窗口带到前面。 |
| 外网依赖 | 前端不从外网取字体；画板（Excalidraw）的素材随包带着。更新源是 GitHub；AI 渲染只接 Gemini。 |

## 2. 依赖关系

```
P0-1 版本线 ──┬─ P0-2 工作坊模式 ──── P0-10 冻结与离线安装包
              ├─ P0-3 对话后端（独立，可并行）
              ├─ P0-6 3dm 对象识别约定 ─┬─ P0-5 坡度、坡道、可达检查
              │                          └─ P0-7 候选批量导入 ── P0-8 比较视图
              ├─ P0-4 新增条件 ─────────── P0-5
              ├─ P0-9 默认打开条件检查（依赖 P0-4、P0-5）
              └─ S1–S5 稳定性（S1 依赖 P0-1 带进来的 undrawable 报告）
P1-1 中文化：在 P0 的新界面定稿后做
P1-2 教师视图：先定 #629 去留
P1-3 匿名导出、P1-4 总平面出图：彼此独立
```

## 3. P0

### P0-1 合成工作坊版本线

- **做什么**：把 `claude/skill-run` 上 `0f5ce85f..4070fc6f` 这一段合到 main 上，与设计循环 D 并存；底下的联合候选不带（见第 6 节第 1 条）。
- **模块和文件**：`hub.shell`（`chat/studio_tool.py`、`chat/tool_calls.py`、`ChatShell.tsx`、`features/skills/`）、新模块 `project_runtime.live_rhino`、`project_runtime.skill_runs`、`project_runtime.skill_library`、`project_runtime.camera_passes`，以及 `monkeycad.formats`（STL）、`monkeycad.occt`（修剪环）、`monkeydiagram`。
- **验收**：Live Rhino、skill 运行、STL、修剪环、相机分层图的测试在 main 上通过；设计循环 D 的测试照旧通过；PR 写明解决的冲突和改变的行为。
- **状态**：PR #669（分支 `claude/668-workshop-line`），等 owner 确认后合并。

### P0-2 工作坊模式

- **做什么**：一个开关（设置 → 软件更新 → 工作坊模式），打开后：不检查、不下载、不在退出时切换更新；同一个账户只开一个 MonkeyHub（不管运行目录）；桌面每次先要 8790 端口，重启后地址不变，已打开的页面自己连回来。
- **模块和文件**：`hub.shell`——`settings/models.py`（`workshopMode`）、`updates/desktop_updates.py`、`updates/models.py`、`updates/routes.py`、`SoftwareUpdateSettings.tsx`；桌面外壳 `apps/monkeyhub/desktop/src/{lib,main,single_instance}.rs`。
- **验收**：开着时更新检查不发网络请求、“立即检查”和打开自动更新返回 409 `UPDATE_WORKSHOP_MODE`；第二次启动（换运行目录也一样）把已开窗口带到前面；同端口重启后页面事件流自己重连。
- **状态**：与 P0-1 同在 PR #669。

### P0-3 国内网络下的对话后端

- **做什么**：在学校网络下实测并预设一种模型服务。main 已有 `coding-plan` 连接：Claude CLI 指向一个 Anthropic 兼容地址（智谱、Kimi、DeepSeek、百炼都有这种地址），令牌存在系统凭据里。要补的是：
  1. 实测清单：Claude CLI 在国内网络下的安装（npm 镜像）、登录不依赖境外账户、四家服务各跑一遍工作坊用到的对话（建模、出图、条件检查、写理由）；
  2. 设置页给 `coding-plan` 加几个预设地址和模型名，学生只填令牌；
  3. 确认不用 AI 时，导入、条件检查、比较、出图都能用（渲染不进工作坊流程）。
- **模块和文件**：`hub.shell`——`chat/providers.py`（`_coding_plan_env`）、`settings/models.py`（`codingPlanBaseUrl`）、`main.tsx`（连接设置）；文档 `docs/development/work-environment.md` 加一节。
- **验收**：实测记录（服务、地址、模型、延迟、失败率）；在一台没有境外网络的电脑上，从装好到完成一次对话改模型不超过 15 分钟。
- **依赖**：无，可以和 P0-1 并行，T−6 周完成。

### P0-4 新增条件的界面和接口

- **现状**：条件（`DesignObligation`，`packages/archflow/src/archflow/state/operational_state.py`）有说明 `statement`、出处 `source_ref`、检查方法 `validator_ref`，存在设计记录 `input/runner/state-record.json` 里；没有任何新增或修改条件的接口，`StateRecordEditKind` 也没有条件这一类修改；前端只显示条件数量（`features/evidence/HonestyTab.tsx`）。
- **做什么**：
  1. `archflow.state.state_record` 加条件的新增、修改、撤销三种记录修改；
  2. Project Runtime 加 `GET/POST /api/obligations`（经现有写入口落盘）；
  3. 出处写成“规范名称 + 条文号”（如 `norm:GB 50763-2012 §3.4.3`），在语义表里登记 `norm:` 这种出处；
  4. Hub 加“条件”面板：写数值和单位、填出处、从已有检查里选一种，或标“人工判断”。
- **模块和文件**：`archflow.state.state_record`、`project_runtime.validation`、新路由 `services/project-runtime/src/project_runtime/api/routes/obligations.py`、前端 `apps/monkeyhub/web/src/features/conditions/`。
- **验收**：在界面里新增一条条件后，下一次修改的检查结果里出现它（held、violated 或 unchecked）；撤销后不再检查；记录里留下谁在什么时候加的。
- **依赖**：P0-1。

### P0-5 坡度、坡道、标高差、可达检查

- **现状**：检查种类在 `state_record.py` 的 `OBLIGATION_CHECKS`（10 种，主要是楼梯、墙、洞口）；`centred-at`（±1 mm）可以表达“标高不变”；检查只读引用里的数字、记录里的参数和建好的模型的包围盒，不读外部算出的数值。
- **做什么**：新加四种检查——
  - `slope-at-most`：一个面或一条路线的最大坡度；
  - `ramp`：轮椅坡道的坡度、每段高差、平台长度；
  - `level-difference-at-most`：两点标高差；
  - `accessible-route`：沿路线逐段查纵坡，任一段超限即 violated。

  另外让外部算出的数值（日照时数等）作为记录参数写入，带出处，用现有的 `parameter-at-least` 检查。包围盒不够用，坡道和路线的检查要读折线和面：在 `project_runtime.validation` 的读回里加这两类几何。
- **模块和文件**：`archflow.state.state_record`（`OBLIGATION_CHECKS`、`_OBLIGATION_CHECKERS`、`_MEASURED_KINDS`）、`project_runtime.validation`（`application/validation.py` 的读回）、测试 `packages/monkeyarch/tests/test_state_record.py`。
- **验收**：每种检查都有通过、违反、查不了三种结果的测试；用样例场地的坡道和无障碍路线跑出与手算一致的结果（误差 ±0.1%）。
- **依赖**：P0-4（条件能写进去）、P0-6（知道哪个对象是坡道、路线）。

### P0-6 3dm 对象识别成构件的约定

- **现状**：3dm 导入读得到图层和对象用户文字（`monkeycad.formats.three_dm_inspector`），但明确不从名字推断构件；条件不知道哪个对象是入口、坡道、台地、主楼。
- **做什么**：项目里存一张显式的对照表：图层名前缀或用户文字键 → 构件角色（`role.*`），导入时按表给对象标角色，条件的对象可以写成“这个模型里角色为坡道的对象”。给学生一份建模命名约定（图层 `MH-入口`、`MH-坡道`、`MH-台地-1`……），Grasshopper 工具包按约定烘焙。
- **模块和文件**：`project_runtime.documents`（`application/artifacts.py` 的模型索引）、`monkeycad.formats.three_dm_inspector`、`archflow.semantics`（角色表，缺的角色按 ADR-006 登记）；约定文档放 `docs/protocols/`。
- **验收**：按约定建的样例 3dm 导入后，索引里每个对象都有角色；不按约定的对象列为“未识别”，不报错。
- **依赖**：P0-1。

### P0-7 候选批量导入（基因、适应度、几何）

- **现状**：候选是 MonkeyHub 自己建出来的；外部模型只能一个个登记成模型；`POST /api/admissions` 一次最多 64 个已有的运行；候选没有参数和适应度字段。
- **做什么**：`POST /api/candidates/import` 一次导入一批：每个候选带基因（参数名和值）、适应度（目标名、方向、数值）和一个 3dm。直接读 Wallacei 导出的表（代、个体、各基因、各目标）和按“代-个体”命名的表现型 3dm。候选 DTO 加 `parameters` 和 `objectives`。
- **模块和文件**：`project_runtime.candidate`（`application/candidate.py`）、`project_runtime.intent`（`design_history.py` 的候选列表与 DTO）、`project_runtime.documents`（模型登记）；新解析器放 `services/project-runtime/src/project_runtime/application/candidate_import.py`；样例数据只用合成的。
- **验收**：100 个候选一次导入在 2 分钟内完成；重复导入同一批不产生重复；缺 3dm 或表格对不上的个体逐个报出，其余照常导入。
- **依赖**：P0-6（候选 3dm 里的对象要能识别）。

### P0-8 候选比较视图

- **现状**：设计树只有“显示已处理”一个开关，不能排序、筛选；`/options` 能算面积等指标，但界面已删。
- **做什么**：一张候选表：按任一目标排序、按范围筛选、标出帕累托前沿（按各目标的方向算不被支配的候选）；勾选 2–4 个并排看模型和指标；选中后在这里直接写选择理由、登记为候选或否决（沿用现有的认可、否决、归档）。
- **模块和文件**：`hub.shell` 前端 `apps/monkeyhub/web/src/features/designTree/`（或新 `features/candidates/`），复用 `GET /api/candidates/{id}/compare` 和 `POST /api/candidate-reviews`。
- **验收**：100 个候选时排序和筛选在 200 ms 内响应；帕累托标记与手算一致；写下的理由出现在决策记录里。
- **依赖**：P0-7。

### P0-9 工作坊默认打开条件检查

- **现状**：`designLoop` 默认 `legacy`，完全不检查。
- **做什么**：工作坊安装包把 `designLoop` 设为 `advisory`（检查并报告，不拦）或 `enforced`（智能体的修改只能走 `POST /api/revisions`）。两者的区别见第 6 节第 3 条。
- **模块和文件**：`hub.shell`（`settings/models.py`、`runtime/applications.py`），安装脚本 `apps/monkeyhub/installer/install.ps1`。
- **验收**：新装的工作坊版本第一次打开项目，修改后就能看到条件检查结果。
- **依赖**：P0-4、P0-5。

### P0-10 冻结与离线安装包

- **做什么**：T−4 周从 main 切出 `workshop/<日期>` 分支，此后只修错；由它出离线安装包：`install.ps1` 加 `-WorkshopMode`（写 `workshopMode: true` 和 P0-9 的检查档位），不依赖 GitHub；附杀毒软件白名单说明（安装包未签名）。
- **模块和文件**：`tools/release/package_monkeyapps.py`、`apps/monkeyhub/installer/install.ps1`、`.github/workflows/desktop.yml`（从冻结分支出包）。
- **验收**：在断网的 Windows 电脑上从 U 盘装好并打开；打开后设置页显示工作坊模式“开”。
- **依赖**：P0-2，以及冻结前要进来的全部 P0。

### 稳定性（与 P0 同期）

| 编号 | 现象 | 做什么 | 模块和文件 | 验收 |
|---|---|---|---|---|
| S1 | 一个 3dm 对象读不了，整份模型的所有出图都失败（`read_three_dm` 遇到不支持的可见对象就整份报错；登记时没有几何的对象让整份文件报 `MODEL_ASSET_INVALID`） | 逐个对象容错：跳过并列出跳过了哪些、为什么，沿用 P0-1 带进来的 `undrawable` 报告格式 | `monkeycad.occt`（`backends/occt/native_models.py`）、`monkeycad.formats.three_dm_inspector`、`project_runtime.documents`（`application/drawings.py`） | 含一个坏对象的样例 3dm 能出图，跳过清单里正好是它 |
| S2 | 项目工作进程退出只记退出码和日志路径；恢复要手动点 | 退出原因带上日志最后 20 行；界面的“恢复”在学生电脑上演练；决定要不要空闲时自动重启一次 | `hub.shell`（`runtime/workers.py` 的 `_observe_exit`、`runtime/manager.py`） | 杀掉工作进程后，界面一步恢复，未保存的草稿还在 |
| S3 | 崩溃和断连后要去本机日志目录找 | 设置里加“导出诊断包”：桌面日志、Hub 日志、工作进程日志、版本信息打成一个 zip，不含项目数据和令牌 | `hub.shell`（新路由 `GET /api/diagnostics/bundle`）、`SoftwareUpdateSettings.tsx` 旁边 | 演练中每次问题都附一个诊断包 |
| S4 | 性能只测到 30 个运行的项目（打开 1,548 ms，热启动 891 ms） | 用 50–100 个候选的合成项目压测打开、候选表、比较 | 基准 #547（`benchmark-data` 分支）加一个场景 | 100 个候选时打开不超过 3 s |
| S5 | Wallacei 长时间运行时 Rhino 可能崩溃 | 工具包默认打开 Wallacei 的自动保存；候选分批导出、分批导入（P0-7 支持重复导入不重复） | 工具包（仓库外）、P0-7 | 演练中中断后从上一批继续 |

## 4. P1

### P1-1 中文化

- **现状**：72 个界面文件里约 21 个还有英文字面量（约 109 行，最多的是 `App.tsx`、`Board.tsx`、`DocumentStudyPanel.tsx`）；Hub 和 Project Runtime 的报错详情全是英文，`ErrorPanel.tsx` 按错误码给出中文原因和下一步。
- **做什么**：工作坊会走到的界面全部进翻译表；工作坊路径上的错误码（导入、条件、候选、出图、更新）都在 `ErrorPanel.tsx` 的 `FAILURES` 里有中文原因和下一步；不逐条翻译服务端的 1,000 处报错。
- **验收**：用中文界面走完演练流程，屏幕上没有未翻译的英文句子（专有名词除外）。
- **依赖**：P0 各项界面定稿之后。

### P1-2 教师视图

- **现状**：main 上没有加入、成员、教师角色，也没有跨项目查看；#629 有分享和加入，还没合。
- **做什么**：一屏看各组的规则清单、候选、决策和检查状态，评图时直接调出。两条路：
  1. 等 #629 合入，教师作为成员加入各组项目；
  2. 不依赖 #629：学生用现有的“导出项目档案”提交，教师的 Hub 读多份档案，只读汇总。
- **验收**：教师在一台电脑上 1 分钟内调出任一组的选择理由和检查状态。
- **依赖**：第 6 节第 1 条的决定。

### P1-3 匿名导出

- **现状**：项目档案导出只去掉凭据、缓存和日志；记录里保留操作人和原话。
- **做什么**：`write_project_archive` 加匿名选项：操作人换成组内编号，路径、账号去掉，原话保留；档案里附同意记录。
- **模块和文件**：`archflow.project.archive`（`archive.py` 的 `write_project_archive`）、`hub.shell`（`project_archive.py`）。
- **验收**：匿名档案里搜不到姓名、账号和本机路径；同意记录随档案走。研究使用前必须完成。

### P1-4 总平面出图

- **现状**：剖切面只能垂直于坐标轴；立面只有线稿；图版最多 12 个视图、不能放图片；没有等高线、标高、区域填色、文字标签、线型。
- **做什么**：总平面先做：等高线（地形面按等高距切）、标高点、挖填区填色、无障碍流线、文字标签；图版允许放图片。
- **模块和文件**：`monkeydiagram.rendering`（`rendering/svg.py` 的 `drawing_svg`、`rendering/paper.py`）、`monkeydiagram.documentation`（`sheet_layout`）、`project_runtime.documents`（`application/drawings.py`）。
- **验收**：样例场地出一张 A1 总平面，含以上五类内容。工作坊可先用 Rhino 出图。

## 5. 时间表

| 时间 | 完成 |
|---|---|
| T−6 周 | P0-1、P0-2 合入 main；P0-3 实测；S1 |
| T−5 周 | P0-4、P0-6；S2、S3 |
| T−4 周 | P0-5、P0-7、P0-8、P0-9；S4；切 `workshop/<日期>` 分支，冻结功能 |
| T−3 周 | P1-1、P1-3；P1-2（来不及就用人工汇总代替） |
| T−2 周 | 至少 5 台学校电脑全流程演练：安装、导入地形、写规则、跑 Wallacei、批量导入候选、选择、深化检查、出图、提交 |
| T−1 周 | 修完演练问题；P0-10 离线安装包 |

## 6. 需要拍板的事

1. **#627、#629、#626 进不进工作坊版本线。** 现在的安装版带着它们（分享、加入、成员角色），版本线没带。带上就要先完成这三个 PR 的审查；P1-2 的第一条路依赖 #629。
2. **GH-185 的写入范围。** 设计循环 D 的登记还开着（调校到 10-25 冻结），和版本线要改的 12 个文件重叠，架构检查因此不通过。要么 GH-185 缩小范围或释放，要么版本线等它。
3. **工作坊用 `advisory` 还是 `enforced`。** `enforced` 下智能体的修改只能走 `POST /api/revisions`，且智能体不能运行声明了访问权限的 skill；`advisory` 只检查和报告。
4. **工作坊模式下是否还允许手动装补丁。** 现在允许：人主动选补丁、再点“重启并更新”才生效，用于工作坊期间紧急修错。

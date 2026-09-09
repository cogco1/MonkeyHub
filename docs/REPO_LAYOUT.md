# ArchFlow、MonkeyArch、MonkeyDiagram：职责与文件归属

本文定义三个实际源码包及两个 Web 工作区的职责。当前 owner、路径和公开接口以
[module registry](../governance/module_registry.json) 为准；目录分离不表示所有规划能力已经实现。
协作规则见 [AGENTS](../AGENTS.md) 与 [CONTRIBUTING](../CONTRIBUTING.md)。

## 1. 三个明确的能力范围

| 名称 | 负责 | 不承担 |
| --- | --- | --- |
| **ArchFlow** | 共享项目身份、文件与记录保存、版本引用、建筑事实与语义契约、真实共用的计算和外部工具接口、正式 issue | 具体建模方法、图纸布局、某一工作流的界面与交互 |
| **MonkeyArch** | 3D 建模与空间修改：任务解释、构件与空间构造、模型候选、几何编译、关系检查、模型检查与续改 | 图纸字形、笔迹、二维图形、版面及图纸集组织 |
| **MonkeyDiagram** | 图纸与图解：平立剖、家具与节点表达、PDF／图片批注、二维内容编辑、文字尺寸、视图与图形表达、排版及导出 | 隐式改变模型空间或构件；建立第二套项目保存与发布权威 |

MonkeyArch 和 MonkeyDiagram 是平行工作流。ArchFlow 提供它们共同依赖的底座。
二维图纸可以表达新的设计想法；将该想法应用到三维模型是明确的跨工作流动作。
模型派生的轴测图、透视图和截图放到图纸中时，表达工作属于 MonkeyDiagram。

建模与出图执行已迁入 `monkeyarch/` 和 `monkeydiagram/`。`archflow/` 保留共同的建筑事实、
项目契约和技术适配。仅因代码可复用，不把某个工作流的业务算法放进公共核心。

## 2. 当前源码目录

下列三个 Python 包随同一发行包安装，两个 Web 工作区由同一 Studio 宿主装配。

```text
<source-root>/
├─ archflow/                     共享项目核心、事实契约、技术接口
│  ├─ project/                   P036、refs、layout、repository、issue
│  ├─ contracts/                 共同值契约与规范化
│  ├─ state/                     建筑事实、变更契约及共享几何值
│  ├─ semantics/                 建筑实体、角色与条件词汇
│  ├─ ports/                     已有外部调用接口
│  └─ adapters/                  两条工作流实际共用的技术适配
├─ monkeyarch/                   3D producer、solver、编译及运行编排
├─ monkeydiagram/                图纸投影编排、SVG 与 PNG 表达
├─ apps/archflow-studio/         共享启动与应用装配，不承载两套领域算法
│  ├─ api/                       HTTP、鉴权、DTO、路由及工作流装配
│  └─ web/src/
│     ├─ app/                   项目连接、同级入口、通用布局
│     ├─ api/                   一份生成客户端与连接层
│     ├─ workspaces/
│     │  ├─ monkeyarch/         3D 工作区、专用交互与组件
│     │  └─ monkeydiagram/      图纸工作区、专用交互与组件
│     └─ features/              两边实际复用的设置、会话等组件
├─ tools/                       对应既有能力的 CLI 与治理命令
├─ tests/                       行为和边界测试；随真实迁移同步 imports
├─ probes/                      明确晋升的项目输入与回归证据
├─ governance/                  现有模块、工作、策略三类来源
└─ docs/                        架构、目录、协议与现行工作说明
```

一个源码仓、同一发行版本可以包含三块代码。独立工作流首先要求职责、目录和依赖清楚；
是否拆成独立部署或安装包，由真实使用需要决定，不与本次目录划分捆绑。
`apps/archflow-studio/` 保留为共同宿主名称；它不是 MonkeyArch 的业务代码总目录。

## 3. 文件归属与迁移范围

| 当前实现 | 职责与边界 |
| --- | --- |
| `archflow/project/`、`contracts/`、共享 `state/` 与 `semantics/` | 留在 ArchFlow。图纸可引用建筑事实；图纸排版、字形和笔迹不进入建筑 StateRecord。状态中的建模专用表示需按实际消费者单独划分，不能整目录搬走。 |
| `monkeyarch/capabilities/`、`monkeyarch/compilers/geometry.py`、`monkeyarch/runtime/project_runner.py` | 3D 生成、求解、重建语义、关系检查、编译和运行。原 `archflow` 中的对应生产文件已退役，调用方直接导入新位置。 |
| `monkeydiagram/drawing_elevation.py`、`monkeydiagram/drawing_svg.py` | 图纸来源核验、模型轴立面投影编排、SVG／PNG 表达。两位既有 owner 保持原 API 和记录语义，不复制 renderer。 |
| `archflow/state/geometry_program.py` 的 `CompiledGeometryProgram` 等值 | 三维编译器与共享 CAD 执行器共用的结果契约。数据值留在 ArchFlow，生成这些值的编译算法归 MonkeyArch。 |
| `adapters/cad_execution.py`、`three_dm_inspector.py`、`ports/model.py` | 已被两条链使用的技术部分留在 ArchFlow。模型生成与二维投影的领域规则分别归各工作流；按函数职责处理混合文件，不整份复制。 |
| Web 的 `ThreeDmViewport`、Program／Options、模型 `Annotate`／`useModelAnnotations` | 归 `workspaces/monkeyarch/`；通用三维显示器若有实际共享消费者，可以继续共用。 |
| Web 的 `DocumentCanvas`、`DocumentTextLayer`、`documentInk`、`documentVisualInput`、`useDocumentAnnotations` | 已在 `workspaces/monkeydiagram/`。模型修改提交仍是显式交给 MonkeyArch 的动作，不能误称为重新出图。 |
| App／AppShell、生成 SDK、连接、通用设置、会话显示 | 留在共同宿主。会话按当前工作流调用不同能力；不能把整个 Conversation 都归 3D，也不复制一套消息系统。 |
| API 的 `application/artifacts.py`、`gestures.py`、`jobs.py` 等混合文件 | 公共文件访问、实际共用的排队／事件机制留在宿主或已有底座；模型用例归 MonkeyArch，图纸用例归 MonkeyDiagram。当前 job 合同仍偏向 candidate，不能先当成已完成的通用绘图任务接口。拆现有函数和调用，不复制保存、锁或来源校验。 |

API 中的装配用例仍保留一位 owner；拆出混合文件中的具体方法，应随下一项真实用例进行，
不能为目录对称复制 DTO、来源校验或保存流程。本轮保持 HTTP 接口及客户端契约不变。
模块 ID 不因产品名而改名，`owner_path`、调用方和公开类型的归属已同步到现有注册表。

## 4. 依赖方向与交接

```text
MonkeyArch 工作区    → monkeyarch    → archflow
MonkeyDiagram 工作区 → monkeydiagram → archflow
                     共同宿主负责装配
```

- ArchFlow 不导入两个工作流的内部代码；底座所需领域行为通过已有或实际需要的明确接口传入。
- 两个工作流不直接导入对方内部模块。模型到图纸传递明确的模型来源与视图输入；图纸要求改模型时，
  通过 MonkeyArch 的公开动作提交。必要的新接口与首个真实消费者一起形成。
- MonkeyDiagram 保存自己的图纸修订；查看某个模型不会悄悄替换图纸的来源。更新产生新图，旧图与批注仍可追溯。
- 共享是由真实消费者证明的职责。只有一个工作流使用的业务逻辑留在该工作流，不为了“以后能共用”提前抽进核心。

## 5. 项目数据、测试与发布文件

| 内容 | 放置方式 |
| --- | --- |
| 活跃模型、图纸、批注、候选、保留证据 | 显式外部项目根，沿同一 P036 项目格式。按所属 run 保存，图纸成果用已有 `workspaces/documentation/` 与 records；不建三个品牌数据仓。 |
| 项目专用建模／出图消费者 | 项目工作区中已明确的源码位置；通用算法进入相应 owner。SML 的特定拼装规则不作为公共模块默认值。 |
| 论文、私人研究资料 | 作者指定的学术工作区；不固定到某个历史 `paper/` 目录，也不成为应用启动依赖。 |
| 测试 | Python、API、Web 现有测试目录；合成 fixture 可以随测试提交。真实项目输入只有明确晋升后进入 `probes/`。 |
| 应用图标、字体、模板资源 | 实际运行需要的资源随相应应用或工作区提交；项目生成的图纸不是应用资源。 |
| 构建、日志、缓存、临时检查输出 | 配置的外部 runtime/cache/temp 或现有忽略目录；没有完成交接的源码和唯一回归不能作为可丢缓存删除。 |
| 软件 release | 准确 Git 提交对应的源码／构建包及发行说明；软件版本独立于协议版本、记录 schema 和项目 HEAD。 |

工作卡跟踪活跃任务，架构方案解释边界与取舍；完成工作从 live registry 退出，结果保留在 Git。
退役前核对真实调用、公开契约和保留数据。普通旧代码可由 Git 找回；私人原件和唯一临时材料先确认交接，
不根据“零 import”自动删除，不要求每次修复另建归档台账。

## 6. 当前迁移与后续工作

1. **已固定 `v0.1.0` 源码候选。** 本轮从当前开发代码的独立基线迁移，未改动该冻结包。
2. **现有二维与三维链迁入各自包和工作区。** 调用方直接使用新路径，旧生产模块退役；
   API、记录 schema、摘要计算与项目保存位置保持一致。
3. **后续功能沿新归属继续。** 显式更新图纸、独立二维编辑、通用平立剖与排版等仍按实际用例推进；
   本次迁移本身不宣称这些能力已经交付，也不将它们作为完成目录分离的前置条件。

包安装配置、registry 路径与受影响测试同步新位置；已有 archcheck 同时检查三个 Python 包，
拒绝底座反向导入工作流，以及两个工作流互导。
完成标准是新位置能独立测试、宿主经明确入口调用、原使用流程仍可运行，不是三个目录已经出现。

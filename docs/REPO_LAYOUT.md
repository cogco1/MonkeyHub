# ArchFlow、MonkeyArch、MonkeyDiagram：职责与文件归属

本文定义长期目录目标和当前代码的迁移归属。当前实际 owner、路径和公开接口仍以
[module registry](../governance/module_registry.json) 为准；目标目录不表示已经迁移。
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

`archflow/` **目前是统一 Python 实现包，不等于其中每个模块都属于共享底座**。
其中的建模和出图领域代码按下表迁入自己的命名空间；仅因代码可复用，不把它放进公共核心。

## 2. 长期源码目录目标

以下是目标目录；`monkeyarch/`、`monkeydiagram/` 与 Web 的两个工作区尚未创建。
它们随真实调用链迁移形成，不先建立空目录。

```text
<source-root>/
├─ archflow/                     共享项目核心、事实契约、技术接口
│  ├─ project/                   P036、refs、layout、repository、issue
│  ├─ contracts/                 共同值契约与规范化
│  ├─ state/                     共同读取的建筑事实；只保留共享部分
│  ├─ semantics/                 建筑实体、角色与条件词汇
│  ├─ ports/                     已有外部调用接口
│  └─ adapters/                  两条工作流实际共用的技术适配
├─ monkeyarch/                   3D 领域算法与应用编排
├─ monkeydiagram/                图纸／图解领域算法与应用编排
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

## 3. 现有文件分别归哪里

| 当前实现 | 长期归属与移动边界 |
| --- | --- |
| `archflow/project/`、`contracts/`、共享 `state/` 与 `semantics/` | 留在 ArchFlow。图纸可引用建筑事实；图纸排版、字形和笔迹不进入建筑 StateRecord。状态中的建模专用表示需按实际消费者单独划分，不能整目录搬走。 |
| `capabilities/element_producers.py`、wall/opening/reference solvers、`compilers/geometry.py`、`runtime/project_runner.py` | 归 MonkeyArch。按真实建模调用链迁移；能力名称、记录身份及已有检查保持稳定。 |
| `runtime/drawing_elevation.py`、`adapters/drawing_svg.py` | 归 MonkeyDiagram。来源核验与出图编排、SVG 表达分别保留自己的 owner；迁移时同时改调用方，不复制第二份 renderer。 |
| `adapters/cad_execution.py`、`three_dm_inspector.py`、`ports/model.py` | 已被两条链使用的技术部分留在 ArchFlow。模型生成与二维投影的领域规则分别归各工作流；按函数职责处理混合文件，不整份复制。 |
| Web 的 `ThreeDmViewport`、Program／Options、模型 `Annotate`／`useModelAnnotations` | 归 `workspaces/monkeyarch/`；通用三维显示器若有实际共享消费者，可以继续共用。 |
| Web 的 `DocumentCanvas`、`DocumentTextLayer`、`documentInk`、`documentVisualInput`、`useDocumentAnnotations` | 归 `workspaces/monkeydiagram/`。当前仍在 `features/stage/`；搬动时一起更新真实导入和交互检查。模型修改提交仍是显式交给 MonkeyArch 的动作。 |
| App／AppShell、生成 SDK、连接、通用设置、会话显示 | 留在共同宿主。会话按当前工作流调用不同能力；不能把整个 Conversation 都归 3D，也不复制一套消息系统。 |
| API 的 `application/artifacts.py`、`gestures.py`、`jobs.py` 等混合文件 | 公共文件访问、实际共用的排队／事件机制留在宿主或已有底座；模型用例归 MonkeyArch，图纸用例归 MonkeyDiagram。当前 job 合同仍偏向 candidate，不能先当成已完成的通用绘图任务接口。拆现有函数和调用，不复制保存、锁或来源校验。 |

当前目录和目标归属可以暂时不同。一个 owner 在迁移后仍负责原来的明确能力；仅改变
`owner_path`、相关文件、调用方和测试，不因产品名新增平行 owner 或记录 schema。

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

## 6. 迁移顺序与完成条件

1. **固定现有 `v0.1.0` 候选。** 后续目录和工作区调整不混入已经检查的源码包。
2. **先完成 MonkeyDiagram 的一个实际调用链。** 将现有文档组件组织成独立工作区，并接入已选出图／更新动作；
   随该调用链迁移出图领域文件。保留源模型、旧图和批注，完成一次可继续编辑的图纸交付。
3. **再迁移 MonkeyArch 的建模业务。** 从真实用例逐组拆出编排、producer 与编译代码；保留身份计算和历史读取。
   同一改动移除被替代入口，不能留下新旧两条生产链。

每一批同步真实 imports、包安装配置、registry 的路径与受影响测试；沿已有 archcheck 更新实际检查范围。
完成标准是新位置能独立测试、宿主经明确入口调用、原使用流程仍可运行，不是三个目录已经出现。

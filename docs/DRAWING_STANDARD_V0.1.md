# Drawing Standard v0.1

状态：2026-09-06 已实施并在当前柜体五页图中验证。标准及框架代码在本工作区；模型和图纸成果仍由项目仓库保存，当前成果为设计协调稿。

## 责任分工

| 内容 | 唯一负责处 | 图纸如何使用 |
|---|---|---|
| 构件身份、板厚、槽口、位置、装配关系及参数依赖 | 源设计 StateRecord 与现有构件生成、几何编译链 | 选择相关对象，引用真实尺寸和投影 |
| 遮挡、真实剖切与材料闭合边界 | 现有 CAD execution/OCCT 接口 | 设置视向、剖切位置、显示深度及图形样式 |
| 尺寸来源、确认程度、选材依据、现场资料、责任与确认人 | 现有项目资料；与相关对象或参数绑定 | 数值与状态一起传入图纸，不能由排版升级为确认 |
| 视图、比例、尺寸编排、编号引用、图签、说明及图例 | 制图标准与图纸编译 | 从选定设计版本生成可检查的图纸表达 |
| 持久模型、候选与成果归档 | 现有项目仓库 | 调用现有端口；不创建第二份设计状态或发布指针 |

二维节点若改变外形、洞口、净空、装配位置或跨视图尺寸，回源设计同步；不参与空间协调的材料符号、连接详注可留在二维，但必须对应实际构造。

## 最小实现

选择 CREATE 一个 `documentation.drawings` owner：当前 `adapters.cad_execution` 明确排除制图样式、尺寸和排版，`project.layout` 只管文件路径，`studio.artifacts` 只服务已有成果；没有现有 owner 能承担本次已授权的通用制图规则。当前柜体是首个真实消费者。

- 一份 JSON 标准配置及其结构约束，八个 System 是逻辑分区，不是八个模块。
- 一份普通内存 DrawingState：来源引用、sheets、views、dimensions、references、notes、tags/schedules、interfaces、revisions。它是设计与项目资料的出图投影，无独立写入权、digest 或 receipt。
- `validate_drawing_state` 返回普通 finding 列表；`compile_drawing_state` 校验后返回可渲染 DrawingPlan。
- PDF/DXF 消费同一纸面图元与标注数据，不再通过 PDF 反解得到另一套纸面内容。
- 项目负责具体对象选择、视图目的、剖切位置与材料来源；框架不含柜体尺寸、项目名或对象 ID。

## SheetSystem

纸面单位 mm；每张纸明确幅面与 printable rectangle。图号唯一；图号、图名突出，项目、用途、日期、人员和修订次之。本次按用户指定，直接消费 ARCH364 图签模板的图元与字段位置，在 A3 右栏适配宽高，保留原机构/顾问区、修订表、项目/用途/图名、紧凑字段行和居中图号，不再沿用旧右栏重排。字段字体按打印可读性处理；缺少的机构、客户、地址或审核人员留空。模板是项目选择，不硬编码进通用 grammar。

## ViewSystem

每个视图有稳定 id、sheet、number、name、purpose、type、scale、源几何对象与图面边界。统一标题为编号、名称、比例；剖面与节点引用指向 `(sheet_number, view_number)`，不得只绘制一个无目标字符串。

preferred scale denominators：100、50、20、10、5、2、1。其他比例须提供项目 exception 理由。比例由阅读任务指定；空间不足调整图格、换页或换幅面，不由装箱计算改变倍率。同组比较视图保持相同倍率。

## GraphicSystem

至少区分 cut、silhouette、visible、secondary、existing/reference、hidden/overhead/clearance。线宽、线型、灰度、填充密度按纸面单位定义；剖切填充只采用真实材料边界。existing/new/demolish 属于施工范围，confirmed/assumed 属于确认程度，不能靠同一个枚举代替。

## AnnotationSystem

尺寸分 overall、setting_out、component，按纸面间距形成层次；数值来自几何或明确设计基准，文字不可覆盖出另一个数值。记录端点/基准、视图、角色、来源、确认状态和可用范围。总体尺寸与分段链使用同一组端点，可检查链长、重复和冲突。ASSUMED/UNRESOLVED 不得以已确认的施工控制尺寸发布。

图纸代号映射到稳定构件 ID；材料/构造代号映射到 schedule，不能用构件名称猜材料。新增构件不重排既有编号。

## InformationSystem

每种图类定义 purpose、required/optional/forbidden 信息、preferred scales、图形与标注层级、引用规则：

| 图类 | 本次任务 | 可选或不应重复的信息 |
|---|---|---|
| GENERAL_DESIGN_INTENT | 外观、总体位置与主要控制关系 | 不重复全部内部构件 |
| ASSEMBLY_ACCESS | 去门构成、水平剖切及检修路径 | 隐藏内容仅保留当前决策所需 |
| SECTION_INTERFACE | 真实截面、净空与安装界面 | 不把设备包络当实材 |
| FABRICATION_PROJECTION | 按相同比例识别构件并对应明细 | 可按交付对象关闭；不是未经深化的 CNC 包 |
| SCHEDULE_RESPONSIBILITY | 材料/构件表与接口职责 | 通用说明集中，关键局部提醒可明确重复 |

项目通用说明包括单位、不得量图施工、现场复核、加工前核对、差异反馈、厂家接口；同一 note id 不机械地逐页重复。缩写与图例使用标准配置中的定义。

## StatusSystem

确认程度为 CONFIRMED/ASSUMED/UNRESOLVED；表达用途为 ACTUAL/REFERENCE_ONLY/ENVELOPE_ONLY；待办可包括 FIELD_VERIFY 与 BY_OTHERS。责任方单独引用项目角色。数值派生保留输入来源与确认限制：从估算设备高度计算的净空不能自动成为 CONFIRMED。DrawingState 引用这些状态，不创建第二个确认事实来源。

## CoordinationSystem

接口项包括 interface、design_provides、field_verify、vendor_provides、status 及相关对象/视图。图中说明和责任表从同一接口项生成。准确几何不能替代选型、现场测量或承载判断。

## RevisionSystem

修订包含 id、date、description、issued_by（未指定可空但标明未正式签发）、changed_views/objects 与 issue history。日期为明确输入，不取运行当天冒充内容修订。修订云线/三角标记针对明确变更范围；模型变更与文档修订分开，均引用实际采用的模型版本。

## 校验与验收

- preferred scale 或有理由的 exception、唯一图号、有效视图引用、图表代号一致。
- 尺寸端点与几何值一致、链闭合、重复/冲突、未确认数值的用途限制。
- 状态、通用说明引用、接口项和修订元数据完整。
- 实际文字/线条边界不超出 printable area；目标幅面下检查图名、引线、填充和表格。
- 当前五页保留专业信息，构件投影采用同组比例，PDF/DXF 同源。
- 几何变更后真实 STEP 与尺寸/明细同步；保留已存候选与源图。运行受影响行为测试及 `python tools/archcheck.py`。

## 当前柜体修正与实施结果

本次另行修正低柜右端板误用高柜高度的问题：低柜端板回到低柜台面下；高柜独立侧板由项目数据明确生成。具体尺寸、对象、来源与候选成果保存在该项目，本文不作为其几何输入。

当前源模型为 `living-candidate-012`：B05 矮柜右端板高 182 mm、顶部 282 mm；新增 B22 高柜左侧板高 482 mm、顶部 582 mm。两块台面完成高仍为 300/600 mm。冷读 STEP 检查端板与相邻板件接触且无实体穿插。源模型的 388 个参数未改动；只修正低柜端板及其说明，并添加高柜侧板。

临时变体将矮柜升至 350 mm，经现有参数解析、构件生成、几何编译与 STEP 重读，低柜端板顶部为 332 mm、缸顶为 950 mm，高柜端板仍为 582 mm、高柜台面仍为 600 mm。该变体仅用于复画检查。

框架的 29 条聚焦测试与 `python tools/archcheck.py` 已通过。当前五页包含 22 块板件、168 项尺寸/数值注释、完整尺寸链及六项接口责任；比例、引用、来源状态、说明、代号、修订和实际纸面边界检查均无 finding。五页 PDF 已按 A3 逐页检查；DXF 的五个纸空间和 30 个模型视图块已读回，audit 无错误和修复项。

PDF/DXF 实测主图为 1:20，三个 460 mm 深剖面均为纸面 46 mm（1:10）；板件台面 3200 mm 为纸面 160 mm，低高门板两轴均保持 1:20。关闭板件页后可生成四页，仍通过校验且不保留指向 CD-04 的纸面引用。350 mm 矮柜变体已重新生成五页，图中文字和尺寸同步为 350/430 mm，未残留旧 300 mm 控制注释或固定 300/600 说明。

成果位于该项目 `living-candidate-012/workspaces/cabinet-detail/revision-03/`：五页 PDF、可编辑 DXF、DrawingState JSON、项目消费者脚本和 ARCH364 模板图元。旧候选与旧图保持可对照；本轮没有正式签发项目版本。

## 当前五页的检查与保留范围

| 图页 | 已有信息与保留结构 | 原实现中的标准缺口 | 本轮处理 |
|---|---|---|---|
| CD-01 总图定位 | 柜墙外观、定位平面、台面高差、既有层架及设备位置 | 主图固定 1:15；估算缸高与派生净空没有独立尺寸状态；图签字段权重接近 | 主视图组采用 1:20；保留来源及使用范围；图号、图名与项目信息分层 |
| CD-02 内部构成与检修 | 拆去门板的内部立面、真实水平剖面、地盖检修范围、构件编号 | 图形与责任说明依赖项目脚本自由文本；代号随列表顺序生成 | 保留真实几何，显式传入固定代号、接口项及状态；同组 1:20 |
| CD-03 剖面与接口 | 真实剖切、材料闭合边界及空洞、设备与检修净空 | 三个同类剖面采用 1:10、1:5、1:5；引用字符串固定；估算值的限制未传播 | 同类剖面采用 1:10；引用稳定 view id；包络不作为实体材料填充 |
| CD-04 板件投影 | 单件真实投影及对应编号，可供木工阅读 | 根据图格自动选择 1:5 至 1:100；同类门板比较困难；默认总是生成 | 当前保留 22 板、统一 1:20，通过跨格容纳长板；后续按下游需要选用 |
| CD-05 明细与责任 | 板件明细、复测、选型与厂家深化说明 | 材料有按构件 id 推断的分支；责任项分散；固定 Rev 02 与生成当天日期 | 项目显式 material tag；说明与责任表引用同一接口项；使用明确修订日期与变更对象 |

以上是输出与代码的已见差异。旧图未发现实际断裂的尺寸链或悬空剖面引用；本轮新增校验，不能把缺少校验表述为已发生的图纸错误。

## 使用与扩展接口

标准配置为 `monkeydiagram/documentation/drawing_standard_v0_1.json`，结构为 `drawing_state.schema.json`。Python 入口为 `monkeydiagram.documentation` 的 `validate_drawing_state(state, standard)` 与 `compile_drawing_state(state, standard)`。JSON Schema 说明结构；Python 校验器还检查跨记录关系、尺寸端点及图面边界。

调用方先准备项目来源、对象、材料、视图目的与尺寸状态，运行校验与编译，再用 `monkeydiagram.drawing_output.PaperCanvas` 记录纸面内容。PDF 与 DXF 导出相同场景；图元边界回填 DrawingState 后，在写出成果前再检查实际可打印范围。成果字节交回现有项目仓库端口。输出依赖可用 `pip install -e ".[drawings]"` 安装；核心校验器不需要 CAD 或 PDF 库。

```python
import json
from importlib.resources import files
from monkeydiagram.documentation import compile_drawing_state, validate_drawing_state

standard = json.loads(files("monkeydiagram.documentation").joinpath(
    "drawing_standard_v0_1.json").read_text(encoding="utf-8"))
findings = validate_drawing_state(state, standard)
plan = compile_drawing_state(state, standard)  # invalid data raises ValueError
```

编译器解析统一的 `view.title`、`view.scale_label` 和 `reference.label`，消费者直接使用。`view.information` 声明图页实际包含的信息，校验器对照类型的 required/forbidden 集合；它不通过声明替代图面检查。纸面边界来自字体字形与线宽，不等同于自动解决所有文字碰撞，最终幅面仍需视觉复核。

室内或建筑项目替换对象、基准、视图组合与接口项，继续使用同一标准。新视图类型只有在实际阅读任务需要时加入配置。板件页由项目下游选择控制；选择 exact geometry 与 schedule 时仍需提供板材、封边、孔位、加工坐标和公差，不能把当前 STEP 自动称为 CNC 生产文件。

当前保留旧候选及旧图以便对照。新消费者取代本轮项目的自动缩放、固定图签元数据、列表枚举代号和 PDF 反解 DXF 纸面的路径；旧文件不再作为新图的上游。DXF 尺寸保留为可编辑线条与文字，其关联关系在 DrawingState 中，不声称是 CAD 原生关联尺寸。

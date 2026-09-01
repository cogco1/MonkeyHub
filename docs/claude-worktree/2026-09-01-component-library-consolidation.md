# 构件库整理方案 — 架构已在,生产绕行(2026-09-01)

问题:圆厅别墅收尾审查发现大量可架构化的复用构件(墙/窗/楼梯/柱/柱廊/拱道),
整个 ArchFlow 该怎么整理,让它们在未来项目里复用?

结论先行:**不需要发明新架构,需要回填与收口。** 逐项排查后发现,
构件化所需的每一层机制在仓库里都已存在且大多已验收;圆厅别墅的生产
实践(runs 005–015 的单体 authoring 脚本)整体绕开了它们,复用知识
因此沉积在错误的层(run 工作区)、错误的形态(命令式一次性代码)里。

## 一、现状盘点:五项已建成,一条被绕行

### 1. 裁决层——复用模板的归属早已定案

P047/P049/P050(均 Done)已裁决三元结构:

- 函数属于架构:项目盲的确定性数学(P049 求值器,12 种
  `GeometryOperationKind` 全覆盖);
- 类型学答案属于项目记录:"The framework owns no massing, opening
  count, dimension-bound, or typology constant"(P050 验收);
- **复用模板 = "a provenance-bound project input record the Architect
  selects and parameterizes, with its selection recorded and
  receipted"**(P050 验收原文)。

即"构件库"的架构形态已有答案:不是框架里的 stair.py 全家桶,而是
**架构侧能力族 + 记录侧模板库**。

### 2. 构件能力范式——楼梯已是完整样板

`capabilities/stair_solver.py` + `capabilities/stair_geometry.py`
是完整的构件能力范式:

- **语义求解器**:项目供给端点基准、单位、平面包络、尺寸带、2R+T
  采纳值;solver 枚举整数踢面数与梯段分布,输出几何中立的语义装配。
  无任何权限(七个 authority 标志全 False)、不读写项目态;
- **接口词汇已内置**:`StairInterface` / `StairInterfaceRole` /
  `StairObligationKind`(HOST_OPENING / SITE_SUPPORT / LOAD_PATH /
  HEADROOM)——构件自带接口义务的概念在这里已经存在;
- **几何桥**:局部 (run, side, up) 基 → 世界 Y-up 中立几何程序,
  authority-free、persistence-free,CAD 适配器无需楼梯特例。

`portico_geometry.py`、`terrain.py` 部分同构。**缺的只是覆盖面**:
墙-带-洞口、窗/门阵列、柱式(含 entasis)、拱道/拱顶、屋面族未建。

### 3. 模板记录机制——P050 生产者已实现

`capabilities/geometry_proposal.py` 已有 `template_refs:
tuple[ProjectRecordRef]` → `available_template_records` →
`selected_template_refs`,且"model selected a template outside the
supplied project records"是类型化失败(L1601–1605)。模板作为记录
供给、选择留痕、越界即拒——机制在,**没人往里放过真正的构件模板**。

### 4. 关系本体——比生产实践用的丰富一个量级

`relations/contracts.py` 的 `ArchitecturalRelationKind` 有 **25 种
谓词**,包括审查报告(run-014 audit)以为需要新增的那两个:
`INTERSECTS`(L69)与 `CLEARANCE`(L78),另有 HOST / HOSTS_VOID /
FILLS_VOID / INTERFACE / ALIGNMENT / DEPENDENCY / LOAD_TRANSFER。
relations 模块 25 万字节(authoring / contracts / coverage /
realization / traversal),是仓库最重的子系统之一。

### 5. 跨项目搬运纪律——有先例可仿

basis-import(`tools/run_basis_import.py`)已确立"证据跨项目搬运
必须带出处、进项目分片"的纪律;全局 reference_library 只放未采纳
原始文献。构件库可完全类比。

### 6. 被绕行的现场(审查实证)

- run-004 `build_stair_successor.py` 走了正道:
  `import archflow.capabilities.stair_solver`——证明该路线可用;
- run-012 `build_controlled_villa_rotonda.py`(3,572 行)**零 archflow
  导入**:墙/窗/柱/柱廊/屋面/关系清单全部内联重写;其手写关系清单
  `ProjectAuthoredFunctionalRelationManifest@2` 只有 SUPPORTS/MEETS/
  ENCLOSES ~25 条边、bbox 级核查——是仓库 relations 本体的降级体外
  复制品(无 INTERSECTS/CLEARANCE → 审查发现的 36 族穿模由此不可见);
- run-014 修复脚本以 SHA 钉死方式 `_load_module()` 加载前代脚本当
  "继承",`INTERFACE_OVERLAP = 0.015` 等嵌入常数将"正重叠"写成验收
  目标(见 run-014 audit 附录)。

**根因一句话**:架构层超前,生产层脱轨;复用知识以命令式单体脚本
形态沉积在 15 个 run 的工作区里,跨项目复用率为零。

## 二、整理方案(六步,按此排序)

### 第 1 步:关系接回(最小改动,立即止血)

run 内联关系清单废止,接回 `archflow/relations`;构件邻接边由空间
索引自动枚举候选、人工裁决,`INTERSECTS`/`CLEARANCE` 进入验收;
核查见证从 bbox 升级 Brep 级(若 relations/realization 现为 bbox
级)。

**接触语义(2026-09-01 晚,用户裁决定案):坐标同源替代测量证明。**
宿主发布命名接口基准(几何程序一等命名值),依赖方边界引用同一基准
节点——身份相等,非数值比较;发布-消费方向沿 SUPPORT/HOST 关系边,
推导图无环。接触从"事后测量"(需正重叠才可证伪 → 15mm 嵌入的根源)
变为"构造时保证"(推导图检查,记录级精确,零浮点问题)。此即铁律
"派生不落地"从决策层下沉到坐标层。缝型分治:承托/host=共享基准;
净空=CLEARANCE 区间;刻意咬合=显式深度参数;共面渲染闪烁在显示层
解决,不得反向决定几何语义。Brep 测量降为回归备份(公差 1e-3,仅防
编译器缺陷);`[0, δ_max]` 区间仅作遗留物过渡。嵌入常数族
(`INTERFACE_OVERLAP`/`SITE_EMBED`)整体废除。楼梯即样板:riser 数列
单一记录,踏步/cheek/平台共同消费——改一个值全梯段重排,顺带成为
dependency-scoped repair 的天然演示件。

### 第 2 步:构件模板记录规范(schema 一张卡)

在 GENERATION_RECORD_SPEC 九维度框架内定义构件模板记录的内容规范
(kind 名建议 `component-template`):

- 参数 schema(带单位、公差、维度带引用);
- 依据链(每个默认值的出处——villa 模板指向该项目采纳事实);
- **接口义务**(泛化 `StairObligationKind` → `ComponentInterface`:
  MEETS/SUPPORT 义务 + INTERSECTS 排斥 + CLEARANCE 区间);
- 适用性声明与开放边界(仿 run-014 assembly contract 的
  `open_boundaries` 字段);
- 版本/前驱链。

存放:项目内先用;验收后经 `component-import`(仿 basis-import)
提升到全局 `component_library/`(只放已验收模板)。

### 第 3 步:能力族按楼梯范式补齐(滚动进行)

每构件族一对 solver + bridge:墙-带-洞口、窗/门阵列(**接口义务里
声明与屋面带的 INTERSECTS 排斥**——直接封死 attic 窗穿柱廊屋面这类
缺陷)、柱式(entasis 曲线为数学、比例为记录)、柱廊装配、拱道/
拱顶(分圈砌块共享灰缝)、屋面族。villa 脚本里的数学提炼进架构,
类型学数值剥离成模板记录。

### 第 4 步:从圆厅别墅反提取(迁移即验收,验收即实验)

逐构件从 run-012/014 脚本提取为模板记录 + 能力调用,每构件一张
P 卡。**总验收 = 用新路线重放圆厅别墅**(reconstruction-016),与
run-014 模型做逐对象等价对比——差异只允许出现在已知缺陷修复处
(楼梯实心化、窗阵遮罩、修复件切割)。这份重放等价性本身就是
CAADRIA 论文的复用性实验证据。

### 第 5 步:生产纪律收口(需与 codex 对齐)

- run 工作区 authoring 脚本降格为胶水:组装模板记录 + 调 capabilities,
  禁止内联构件几何;
- archcheck 增规则:run 脚本内几何字面量密度超限报警、禁 SHA 钉死
  脚本加载替代记录引用;
- 修复纪律:前驱字节保全不变,但允许"后继对象 + 推导记录"合法化
  切割,废止叠置修复。

### 第 6 步:库治理

`component_library/` 进项目落点决策表(spec §八);模板升级走版本
链;跨项目引入必须在目标项目重新绑定证据(依据不迁移,引用迁移)。

## 二·补:收割循环(2026-09-01 追加,修正第 4 步的定位)

用户裁决:首次内联造轮子不是绕行,是**搜索**——不可能在不知道墙体程序
长什么样时提取墙体程序。真缺陷是搜索收敛后无人收割。故第 4 步从
"一次性迁移"改为**常设循环**,四个零件:

1. **收割义务**:stage ACCEPTED 时对本 run 内联生产的每个构件族自动
   生成收割义务记录——提取,或显式豁免+理由(豁免可审计)。义务机制
   从几何层抬到生产层,与 obligation-bearing 协议同构。
2. **二段式落点**:冲刺时收割 = 写 component-template 记录(参数表+
   接口义务+依据引用+指向收敛实现,~20 分钟);闲时提升 = 做成
   solver+bridge 能力走正式卡。收割留痕,提升还债。
3. **编目对质**:authoring 入口硬门——每构件族必须显式"调用哪个模板"
   或"声明弃用+理由";弃用即新族信号,接回收割义务。机制复用 P050
   的 available_template_records + 选择留痕 + 越界类型化失败。
4. **适用域与参数协商**:收割件声明什么能变/不能变;"差一点合适"走
   参数协商而非掉头内联。万神殿(圆墙)×别墅(方墙)为抽象试金石。

学习三层落位红线:内核学数学、库学带引注的类型学、项目学选择。

**可测验收(可入论文)**:第三案例 stage-3 的 runs-to-acceptance、
每 run 内联几何代码量、目录命中率。58 分钟六 run 的拷贝搜索若重现,
循环失败;查目录→选模板→绑证据→调参数一两 run 收工,循环成立。

最小落地三张卡:模板记录 schema(先行)→ 收割义务(挂 stage 验收)
→ 编目对质(挂 authoring 入口)。

## 二·补 2:臃肿审计(2026-09-01 量化)

| 病灶 | 量级 | 刀法 |
|---|---|---|
| 建筑专属 runner ×3(tools/run_pantheon 5,255 行、run_parthenon_stage4 5,570 + run_parthenon 3,259、villa 工作区单体 3,572)| ~17K 行,pantheon/parthenon 函数签名重合为零(54 vs 65 个 def)| 提取"重建执行协议"(stage 循环/记录写入/门调用/Rhino 执行/回读)为一台记录驱动通用 runner;建筑差异全进记录。tools 预计 -60% |
| 版本化 schema 蔓延 | 812 个 distinct schema@N,多族 2–4 版并存,消费者手抄键集(viewer 手抄 _THREE_DM_KEYS 致 @1/@3/@4 三代漂移)| schema 契约单一来源:键集/验证只在定义处,消费者导入;版本升级强制附消费者迁移清单 |
| 权限样板 | canonical_write_authority 508 处,七旗字典手写复制 | authority helper + 校验器,一处定义 |
| probes/ 运行数据在代码仓 | 17.4MB(p026 8.2 + p058 6.3 + test_pantheon 2.7)| 迁工作区留锚(p066 先例)|
| 单文件巨物 | state_tree_viewer 3,772 行(服务器+网页+手抄 schema);validation/vertical_circulation.py 3,453 行单验证器 | viewer 拆 schema 导入后减半;vertical_circulation 审查是否内嵌类型学数值,该拆记录的拆走 |
| **不砍** | runtime/design_controller 5,246 行(决策脊柱)、tests 513 文件、记录/证据纪律本身 | 三案例冻结前不动脊柱 |

诊断一句话:所有臃肿同源——**该只写一次的东西被写了 N 次**(runner×3、
schema 键集×消费者数、权限旗×508、构件×每项目)。刀只有一把:single
source + 记录驱动。通用 runner 与构件库分别是编排层和构件层的同一味药。

**偏执审计补充(2026-09-01)**:villa authoring 脚本手贴 SHA 常数 116 个
+按 SHA 加载 9 处(绕行症状,runner 落地后应归零);同一逻辑对象双摘要
(file_sha + program_digest)→ 引用收敛语义摘要单轨;exact-key 校验 297 处
→ **写严读宽**(写入端 exact,读取端未知键降为 warning),键集单源;
字节级前驱保全 → 后继对象+推导记录合法化切割。保守不对称是真病:键级
偏执 × 路线零门 × bbox 见证——偏执预算从读取端 exact-key 挪往路线 lint
与 Brep 见证。量化目标:审计保证一条不减,每 run 仪式性击键数降一个
数量级。纪律是基础设施,不是美德测验。

## 二·补 3:人类先例对 schema 的设计指令(2026-09-01)

人类构件语义化的六个动作:反复建造(搜索)→ 测绘佳例(提取)→ 命名
重复者(语义化)→ 模数化为比例(参数化)→ 出版论著/则例(记录化)→
学徒制与版次(采纳与演化)。对应关系逐项落在已有机制上(测绘=research
run 采纳事实;命名=收割;论著=模板记录;则例=编目对质;学徒纠错=修复
循环;版次=schema 版本链)。由此新增三条 schema 设计指令:

1. **参数以模数+比例带表达,不用绝对毫米**(柱式以柱径为模数;《营造
   法式》材份八等)。模板携带关系,绝对值在实例化时由模数绑定解出;
   一条引注可证成一整族比例。stair_solver 已内置 2R+T 采纳值,与此
   自洽。
2. **模板记录按论著页结构组织**:图版(witness)+ 数字(参数)+ 注文
   (适用规则/得体)+ 出处(provenance)+ 版次(前驱链)。Quattro
   Libri 的一页就是 component-template 渲染器的 UI 规范。
3. **两个历史失败模式写进治理**:清式则例化(目录成法而搜索死亡,
   斗拱退化为装饰)→ 弃用声明是防僵化的泄压阀,必须保留;行会消亡
   (未文本化的默会知识随人死亡)→ 未收割的 run 脚本即默会层,收割
   义务是防失传。BIM/IFC 的教训(有语义无理由、接口行为藏在软件里
   不在构件声明里)是本方案的反面教材。

学术接线:Stiny & Mitchell 1978 帕拉第奥语法形式化了规则;本工作
在其谱系上补齐出处与义务并使之可执行——且实验对象恰是 Palladio
本人的别墅、方法恰是他本人的方法(测绘→定则→出版→复用)。

## 四、目标架构(2026-09-01 收束)

五层两流。L1–L2 是代码,L3–L5 是记录;实例化流向下,收割流向上。

```
L5 项目层   选择+绑定+证据(每栋楼一套记录)
L4 编排层   通用 runner(记录驱动 stage 循环;三套建筑专属 runner 废止)
L3 构件库   模板记录(模数+比例带+接口义务+出处+版次;收割落点)
L2 能力层   族 solver+bridge(墙/窗阵/柱式/楼梯/柱廊/拱/屋面 —— 参数化模块的数学)
L1 内核     几何函数求值器(P049)+ 关系本体 + 记录/门/证据(已建成,做减法)
```

**参数化模块 = 三片**:数学切进 L2(项目盲:枚举、拓扑、烘焙),
类型学切进 L3(带引注的比例与义务),选择切进 L5(哪个模板、什么模数、
什么证据)。任何一片放错层,中立性或可追责性即破。

**组合总线 = 关系,不再靠坐标巧合。** 模块之间只交换接口记录:
窗模块向墙模块发 VoidRequest(洞口带、窗台带、reveal 带),墙以
HOSTS_VOID 应答,窗框以 FILLS_VOID 落位;墙对楼板声明 MEETS
(contact ∈ [0, δmax]),对柱廊屋面带接受 INTERSECTS 禁令(窗阵遮罩
由此自动成立)。装配编译器把协商结果解为单一几何程序;关系门按声明
接口做 Brep 级核查。——这是"把 IFC 做对":宿主行为是构件声明的数据,
不是软件里的硬编码。

**墙的样例切法**(自 run-012 反提取):
- L2 `wall_solver`:输入轴线段、层高基准、厚度(模数倍数)、
  VoidRequest[];输出 WallAssembly(实体段+受host洞口+接口义务)。
  零常数。桥烘焙为 EXTRUSION + BOOLEAN_DIFFERENCE(P049 已支持)。
- L3 `palladian-masonry-wall@1`:厚度模数比、三层窗台比例带(引
  Quattro Libri + 实测采纳事实)、reveal_embed 带 [0.06,0.10]
  (run-012 的 0.08 即收割来源)、适用域(承重砌体 1–3 层)、
  open_boundaries(砌法、防潮)。
- L5 绑定:villa 选模板、绑模数、轴线来自空间选项、证据引用。

**减法清单**(与臃肿审计对应):建筑专属 runner ×3 废止(-~17K 行);
schema 键集单源化;authority helper;probes 迁出;viewer 拆半。

**抽象两票规则**:一个比例/义务要进 L3 模板,至少要在两个非同构案例
中幸存(万神殿圆墙 × 别墅方墙);单案例数值留在 L5 项目记录,等第三
案例投票。防止 n=1 过度抽象建出错误的库。

**迁移次序**:①schema 单源+authority helper(小,先行)→ ②通用
runner 提取(最大减法,第三案例前提)→ ③component-template schema
卡 + 墙/窗 solver 对(自 run-012 反提取,模数比例制)→
④reconstruction-016 重放(验收+论文实验)→ ⑤编目对质+收割义务两门
→ ⑥viewer schema 修复(已有任务卡)。

## 五、复用生命周期(2026-09-01,架构力学)

**存在性证明**:run-014 修复即一次完整跨 run 构件复用——回收 011 拱道
方案时绑定 representation / applicability / load-path 三份记录,恰为
模板三片(数学/适用域/接口义务)的原型。三个痛点即三条提炼规则:
SOFT 近几何需人肉重映射 → **复用单位是语义求解不是几何**(几何永远
按项目重烘焙,stair_geometry 局部基已示范);6 个手贴 SHA → record-ref
自动校验(runner);逐项手工适配 → 模数比例带。
**模板 schema 卡(移交第 10 项)以此三元组为解剖原型,勿从白纸设计。**

生命周期六步与机制落位:形成(run 记录,现状)→ 提炼(模板 schema,
待立)→ 提升(仿 basis-import,库为 P036 式存储)→ 再发现(P050
available_template_records,**已建成**)→ 再绑定(SemanticBinding,
geometry_program.py:355,**已建成**)→ 再验证(relations 门,**已建成**)。
六步中四步管道已通——**复用今天不发生只因库空:管道齐全,缺的是水。**

三条定律:①复用=再推导非拷贝(证据链不可迁,迁移的是推导模板+证据类
要求;接收项目自供前提——《四书》模式);②模板身份=语义摘要,实例以
REALIZES/REFINES 边回指,版次 append-only;③目录必须在写入路径上
(P050 强制选择即执法点,复用靠机制不靠自觉)。

距离表:楼梯(近,首件收割)→ 拱道三元组(中)→ run-012 全家
(远,受抽象两票约束,价值最大)。

## 三、边界与风险

- **中立性红线**:能力族补齐时随时对照 P049 stop condition——任何
  求值器若需要类型学默认值才能工作,立即停下拆记录;
- **registry 耦合**:上述各卡走 devctl 流程注册,分批提交,勿与
  在途 P069 批次混淆;
- **codex 工作方式变更**(第 5 步)是唯一的组织性风险:绕行是在
  交付压力下发生的,收口若无 lint 硬门,会再次漂移;
- 本文档为分析稿,未立卡、未动代码;卡的拆分与编号待裁决后由
  devctl 流程正式注册。

## 四、依据索引

- 审查报告:V4_RUNTIME villa-rotonda run-014
  `cad-stage-5-material/audit-2026-09-01/model-audit-clash-and-stairs.md`
  (含根因溯源附录);
- 仓库锚点:P047/P049/P050(archive)、
  `capabilities/stair_solver.py`(接口/义务词汇)、
  `capabilities/geometry_proposal.py` L1433–1672(模板机制)、
  `relations/contracts.py` L61–86(25 谓词)、
  `docs/claude-worktree/2026-07-30-geometry-function-analysis.md`
  (函数/记录归属裁决)。

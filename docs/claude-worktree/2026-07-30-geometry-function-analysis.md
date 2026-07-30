# 几何函数体系分析 — 模板为何不在项目记录里（2026-07-30）

问题：P026 的几何模板为什么硬编码在 `sandbox_gold.py` 而不是项目记录？
架构里有没有"几何函数"（圆柱/圆锥/贝塞尔这类参数化生成函数——函数属于
架构，实例化跟随项目 + 语义）？

## 一、几何函数的三层现状：名字在，身体大半不在

### 1. 类型层（archflow/state/geometry_program.py）——词汇表完整

`GeometryOperationKind` 已声明 **12 种操作**：

```text
CURVE  SOLID  TRANSFORM  EXTRUSION  REVOLVE  LOFT  SWEEP  ARRAY
BOOLEAN_UNION  BOOLEAN_DIFFERENCE  BOOLEAN_INTERSECTION  ASSET_INSTANCE
```

REVOLVE 就是圆柱/圆锥的生成函数（轮廓旋转成形），LOFT/SWEEP 是曲线
驱动曲面，EXTRUSION/ARRAY 是拉伸与阵列。参数类型
（NUMBER/INTEGER/BOOLEAN/VECTOR3/POINTS3 + 单位 + 公差）足以参数化
这些函数。**合同层面，"几何函数属于架构"的设计已经存在。**

### 2. 编译层（runtime/geometry_compiler.py）——kind 无关

编译器除 ASSET_INSTANCE 有专门处理外不区分操作种类，只做通用的
参数/坐标系/摘要/权限校验——**12 种全部可编译**。

### 3. 实现层（realization/sandbox.py）——只有 6 个求值器

```python
_SUPPORTED = { CURVE, SOLID, BOOLEAN_UNION,
               BOOLEAN_DIFFERENCE, BOOLEAN_INTERSECTION, ASSET_INSTANCE }
```

且已实现的两个"原语"极窄：
- **SOLID = 轴对齐盒**（origin + size，实现为 `{"kind": "aabb"}`）——
  没有圆柱、圆锥、球等解析原语；
- **CURVE = 折线 polyline**（3D 点列）——没有贝塞尔/样条基。

EXTRUSION/REVOLVE/LOFT/SWEEP/TRANSFORM/ARRAY **编译通过、实现时被
类型化拒绝**（`sandbox.operation_unsupported`，[sandbox.py:1088-1100]）。
要一个圆柱，目前唯一的路是把它做成 mesh 资产近似。

## 二、模板为何不在项目记录里：四个事实

1. **正确的记录容器早已存在**。P023 的
   `SpatialOptionProposal`（state/spatial.py:532）——docstring 自述
   *"One Architect-authored schematic answer, never a framework
   default"*——携带 `footprint_cells`、`levels`、`volumes`（体量）、
   `zones`、`connections`、**`typology_hypothesis`**（类型学假设）、
   `palette_refs`。类型学在架构设计里本来就是 **Architect 作者的
   项目记录**，不是框架常量。`GeometryProgramProposal` 本身也全量
   可序列化（已经进入候选记录持久化）。

2. **缺的是中间的生产者**。从 SpatialOptionProposal（拓扑/体量语义）
   到 GeometryProgramProposal（操作序列）之间没有任何生产组件——
   全仓库除测试 fixture 外，只有 `sandbox_gold._geometry_proposal`
   构造过几何提案。P047 只建了编译器，"Architect-supplied" 的供给侧
   从未立卡。

3. **sandbox_gold 绕开了本应供给语义的链路**。它不导入
   spatial/design_controller，模型输出 schema 只允许约 15 个有界
   整数——语义→几何的映射除了写死在编排器里无处可去。

4. **求值器覆盖面是模板简化的硬约束**。即便让模型作者更丰富的
   几何，REVOLVE/LOFT 也会在实现层被拒。盒 + 门窗布尔 + 一个 mesh
   细部恰好就是 `_SUPPORTED` 能表达的全部——模板长那样不是偶然，
   是被已实现子集截断的结果。

## 三、设计方向（与现有架构自洽）

原则确认：**函数属于架构（项目盲的确定性数学），实例化属于项目记录
（哪些函数、什么参数、绑定什么语义——Architect 决定）**。语义绑定的
seam 已存在：`SemanticBinding`（geometry_program.py:355）把
object_ids 绑到 candidate_value_ids / commitment_refs / evidence_refs。

建议拆两张卡：

**卡 A — 补实现求值器（纯内核，无实例答案）**
- REVOLVE：解析旋转面（圆柱/圆锥/圆台的点包含测试进 `_contains`，
  与 aabb 同级的解析表示）；
- EXTRUSION：轮廓 × 方向 的解析棱柱；
- LOFT/SWEEP：realize 时确定性细分为 mesh + 损失码（复用本轮修好的
  射线奇偶采样与 MAX_FACES 上界）；
- TRANSFORM/ARRAY：纯矩阵/复制操作；
- CURVE 增加 basis 参数（polyline | bezier），贝塞尔在公差内确定性
  采样。
全部是确定性数学，不含建筑类型答案，完全符合 P047 "kernel knows
operations, units, tolerances… but no building-type answers"。

**卡 B — 语义→几何提案的生产者（P026 返工的核心）**
- 消费 P023 的 SpatialOptionProposal 记录（拓扑、体量、类型学假设）
  与模型轮次，产出 GeometryProgramProposal 作为**项目记录**；
- 模型的输出通道从"往固定模板填 15 个整数"扩展为"在架构函数
  词汇表上作者操作序列"（编译器 + 修复循环兜类型化失败）；
- `sandbox_gold._geometry_proposal` 的盒模板随之降格删除，P026 的
  中立性违规（审查发现 #3/#12）在此关闭。

依赖关系：卡 B 依赖卡 A 的最小子集（至少 REVOLVE/EXTRUSION，否则
模型的表达空间仍只有盒）；两张卡都不触碰单写者/硬门边界。

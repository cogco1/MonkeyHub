# labs/ — 兴趣驱动的探索

`labs/<名字>/` 用于验证算法、试用库和尚未确定产品入口的原型。本地探索不必先注册为生产模块；要提交到共享仓库时，复用覆盖这些路径的工作卡，没有合适归属时才新建。实验测试留在自己的 lab，不自动加入产品测试套件。

lab 可以 `import archflow.*`；`archflow/`、`tools/`、`apps/`、`tests/` 不反向 `import labs`。
`tools/archcheck.py` 用和 archive 同一套 `forbidden_layer_imports` 机制拦这条,越界报 `LAYER_AUTHORITY_VIOLATION`。
policy 的 `import_only_source_roots` 使 lab 只接受 import 静态检查，跳过生产代码的写入点、重复权威等检查；项目持久数据仍使用已有项目存储接口，外部系统操作仍按实际授权执行。

**接入实际功能时，在同一改动中完成：**

1. 确定真实调用方、输入输出与验收动作，复用工作卡并明确提交范围。
2. 选择生产位置：可以是独立领域目录或外部包，不要求所有算法进入 core。需要新增软件归口或公开契约时更新 module registry；一个 owner 可以包含多个实现文件。
3. 经已有函数、CLI、API 或 adapter 接线，由现有应用流程保存和展示结果；补上与实际行为相称的测试。只有新增状态语义、编译操作、持久接口或校核边界时，才扩展对应 core owner。
4. 将被产品使用的原型迁出 `labs/`，同时删除被替代的副本或旧生产入口，不保留两条生产路径。

活跃项目的数据、模型和导出物留在外部项目根；明确晋升的回归输入与证据按 [`AGENTS.md`](../AGENTS.md) 的 probe 约定处理。扩展分工和接入示例见[开发指南第 7 节](../docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md#7-新功能的最小搭建流程)。

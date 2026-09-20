# 精确 CAD 空间观察：原始研究与实现核查

核查日期：2026-09-20。问题范围为 [#195](https://github.com/cogco1/MonkeyHub/issues/195) 的观察表示与精确查询，以及 [#170](https://github.com/cogco1/MonkeyHub/issues/170) 的已知事实选择。本文查阅原论文方法部分、官方实现和许可证；引用代码固定到提交或发布版本。外部论文的结果属于其自身数据与任务，本文不将其视为 MonkeyHub 效果。A/B0/D0 与上下文选择的实际结果由本目录实验报告承担。

## 1. 需要作出的选择

第一轮应先验证：同一模型、同一正交线稿和同一任务下，提供可按需读取的对象身份、显隐事实与实体查询，是否降低错误，以及这种降低是否值得额外查询成本。这个问题可以由现有几何来源回答，不需要先构建学习式空间记忆。其反例也应保留：完整结构化输入可能已经足够；查询可能选错对象、遗漏依赖或消耗更多轮次；视觉也可能在某些任务中更便宜。

调查发现，七类工作解决的是不同环节。3D-LLM 与 PaLM-E 建立感知表示到语言模型的学习对齐；ConceptFusion 建立可检索的感知地图；Dreamer 学习动作后的状态与奖励；ConceptGraphs 从感知对象推断图；渲染器生成观测；模拟器保持观测与场景、相机的一致性；DLPack 处理本地数组互操作。这些机制均不能单独保证完整建筑依赖或设计意图已被表达。

对当前研究的取舍是：复用精确对象绑定与几何查询作为已定义事实的依据；用图像检查视点下的形态及尚未形式化的判断；把学习表示放在真正需要处理未建模视觉语义、且有可用模型入口的独立实验中。这是由接口与任务适配推导的实验优先级，不是 A/B0/D0 已证明的性能排序。

## 2. 固定版本与许可范围

“代码可读取”不等于权重、训练数据或示例资产具有相同许可。本轮没有下载研究模型权重或数据集，也没有上传私人建筑文件。下表的 unknown 表示没有在已核查发布材料中完成该具体资产的许可核实，不能由根目录代码许可证代替。

| 对象与固定实现 | 代码许可 | 权重许可 / 训练要求 | 数据与部署边界 |
| --- | --- | --- | --- |
| [3D-LLM `4000290`](https://github.com/UMass-Embodied-AGI/3D-LLM/tree/40002901129b73e79a41ba755c2a045121e19711)，2024-02-06 | 根目录 [MIT](https://github.com/UMass-Embodied-AGI/3D-LLM/blob/40002901129b73e79a41ba755c2a045121e19711/LICENSE)；所依赖的 LAVIS、模型组件与素材须分别核对 | README 提供 v2 预训练及任务微调 checkpoint；下载链接本身不充分说明独立权重条款，故此处记 unknown。对齐模块需要训练；采用发布权重推理不等于每个场景重新训练 | Objaverse、ScanNet、HM3D 等来源各自约束；不由项目 MIT 覆盖。官方训练命令用 8 进程，但不构成硬件最低要求；推理实现有 CUDA 假设 |
| [PaLM-E v1](https://arxiv.org/html/2303.03378v1)，2023-03-06 | [作者项目页](https://palm-e.github.io/)提供论文与示例，未提供可核查官方运行源码发布 | 官方页未提供可下载权重或模型许可，不能列为本机可部署模型。需训练感知编码到语言嵌入的映射 | 机器人演示与混合视觉语言训练数据不是一个已核实可重分发的数据包；复刻需另备合法数据与模型 |
| [ConceptFusion `4457c1f`](https://github.com/concept-fusion/concept-fusion/tree/4457c1f718a5612c2efc57b93a2dc152fed8a03d)，2023-05-24 | [MIT](https://github.com/concept-fusion/concept-fusion/blob/4457c1f718a5612c2efc57b93a2dc152fed8a03d/LICENSE) | 组合已训练 SAM 与 OpenCLIP，地图融合本身不额外训练；依赖 checkpoint 的独立条款未在本轮逐资产核实 | RGB-D、标定和位姿必须可用；数据集授权独立。官方脚本包含 CUDA 调用与外部 GradSLAM 依赖 |
| [DreamerV3 `e3f0224`](https://github.com/danijar/dreamerv3/tree/e3f02248693a79dc8b0ebd62c93683888ddaccfe)，2026-05-25 | [MIT](https://github.com/danijar/dreamerv3/blob/e3f02248693a79dc8b0ebd62c93683888ddaccfe/LICENSE) | 当前 README 称其为 reimplementation；从环境经历训练 world model、actor、critic。没有核实可用于 CAD 观察的通用 checkpoint | 环境、奖励与动作定义不可省略；JAX 及环境依赖各自安装。游戏/模拟器数据不随 MIT 自动授权 |
| [ConceptGraphs `93277a0`](https://github.com/concept-graphs/concept-graphs/tree/93277a02bd89171f8121e84203121cf7af9ebb5d)，2025-01-15 | [MIT](https://github.com/concept-graphs/concept-graphs/blob/93277a02bd89171f8121e84203121cf7af9ebb5d/LICENSE) | 不需要重新训练整套地图系统，但依赖 Grounded-SAM、CLIP、LLaVA；关系构建脚本调用 GPT-4。LLaVA-7B-v0 等具体权重条款记 unknown，不从根 MIT 推定 | README 测试环境为 Python 3.10.12 / PyTorch 2.0.1，并固定旧版 Grounded-SAM、LLaVA 代码；外部 API 另有费用与数据传输边界 |
| [nvdiffrast v0.4.0 / `253ac4f`](https://github.com/NVlabs/nvdiffrast/tree/253ac4fcea7de5f396371124af597e6cc957bfae)，2025-12-05 | [Nvidia Source Code License, 1-Way Commercial](https://github.com/NVlabs/nvdiffrast/blob/253ac4fcea7de5f396371124af597e6cc957bfae/LICENSE.txt#L30-L59)。§3.3 对 NVIDIA 以外的使用限定为研究/评估且无直接或间接金钱收益；不能按 MIT 依赖引入商业生产 | 渲染本身无权重、无需训练；可作为别的优化/训练计算图的一部分 | NVIDIA GPU / PyTorch 路径；示例 Earth 等资产另有 [素材条款](https://github.com/NVlabs/nvdiffrast/blob/253ac4fcea7de5f396371124af597e6cc957bfae/README.md#L19-L36) |
| [Habitat-Sim `57ee494`](https://github.com/facebookresearch/habitat-sim/tree/57ee4941dc4765240f0f91f70b2c97a919bf9038)，2026-05-07 | [MIT](https://github.com/facebookresearch/habitat-sim/blob/57ee4941dc4765240f0f91f70b2c97a919bf9038/LICENSE) | 传感器渲染无需学习权重；机器人策略另论 | 场景资产许可独立；headless/EGL、物理碰撞与 CUDA 需相应构建，不能据 Linux 安装说明假定本机 Windows 就绪 |
| [Isaac Lab v2.2.1 / `0f00ca2`](https://github.com/isaac-sim/IsaacLab/tree/0f00ca2b4b2d54d5f90006a92abb1b00a72b2f20)；另核查默认 `develop` 的 [`1fb0997`](https://github.com/isaac-sim/IsaacLab/tree/1fb0997504cdde462016c195fdcda8195d32dd30) | 核心 [BSD-3-Clause](https://github.com/isaac-sim/IsaacLab/blob/1fb0997504cdde462016c195fdcda8195d32dd30/LICENSE)；mimic 扩展 Apache-2.0；Isaac Sim 与资产有[单独条款](https://github.com/isaac-sim/IsaacLab/blob/1fb0997504cdde462016c195fdcda8195d32dd30/README.md#L129-L140) | 相机/射线观测无需策略权重；训练机器人策略是另一任务 | v2.2.x 对应 Isaac Sim 4.5/5.0；所核查 develop 面向 6.1。不能混用不同分支的安装或相机协议 |
| [DLPack `94485e2`](https://github.com/dmlc/dlpack/tree/94485e23519bc30e22a18397a9c8ff1ea6b2b81e) 与 [PyTorch `2217391`](https://github.com/pytorch/pytorch/tree/22173918c157cf5c3b647d41e5383286fc45d128)，另对照 PyTorch 2.14 文档 | DLPack [Apache-2.0](https://github.com/dmlc/dlpack/blob/94485e23519bc30e22a18397a9c8ff1ea6b2b81e/LICENSE)；PyTorch [BSD 风格及保留贡献者条款](https://github.com/pytorch/pytorch/blob/22173918c157cf5c3b647d41e5383286fc45d128/LICENSE) | 无权重、无需训练；它们没有提供空间理解模型 | 同一进程可访问的设备内存、流同步、生命周期和可变性约束；不授权任何被交换的数据 |

## 3. 七类机制的实际输入、实现与取舍

### 3.1 3D encoder / tokens：3D-LLM

[论文 §4.2–4.3 与附录 B.1](https://arxiv.org/html/2307.12981v1#S4) 的起点是多视图图像的预训练特征。RGB-D 与相机参数将特征提升到点云；Q-Former/Perceiver 等连接器把它们交给语言模型，并增加位置编码和定位 token。它不是直接读取 B-rep 的通用 LLM，也不是完全绕过图像。论文训练问题包括连接器与位置/语言对齐，不能用一个未经训练的线性层替代后仍声称实现了该方法。

官方 [DEMO 的输入](https://github.com/UMass-Embodied-AGI/3D-LLM/blob/40002901129b73e79a41ba755c2a045121e19711/3DLLM_BLIP2-base/DEMO.md#L41-L71) 是 `pc_feat: (B,N,1408)`、`pc: (B,N,3)` 与 `text_input`。`pc` 转为 long，并非带单位的任意世界坐标。在 [`blip2_t5.py`](https://github.com/UMass-Embodied-AGI/3D-LLM/blob/40002901129b73e79a41ba755c2a045121e19711/3DLLM_BLIP2-base/lavis/models/blip2_models/blip2_t5.py#L33-L95) 中，默认 32 个 query token，输入特征宽度固定为 1408，位置查表长度 256；T5 主体冻结但输入/输出 embedding 可训练。`predict_answers()` 把位置特征加到点特征，经 Q-Former 和 `t5_proj` 进入 T5。任意 CAD 坐标必须按该 checkpoint 的预处理约定映射，不能直接四舍五入为整数输入。

实际部署有两个容易被摘要遗漏的条件。其一，[dataset](https://github.com/UMass-Embodied-AGI/3D-LLM/blob/40002901129b73e79a41ba755c2a045121e19711/3DLLM_BLIP2-base/lavis/datasets/datasets/threedvqa_datasets.py#L64-L79) 注释写 10,000 点，执行代码却随机抽样或补零至 5,000 点，评估分支也如此；重复实验需固定实际采样。其二，DEMO 虽选择 CUDA/CPU，构造器与 [`predict_answers()`](https://github.com/UMass-Embodied-AGI/3D-LLM/blob/40002901129b73e79a41ba755c2a045121e19711/3DLLM_BLIP2-base/lavis/models/blip2_models/blip2_t5.py#L278-L304) 有显式 `.cuda()`。未跑通前不能承诺 CPU fallback。README 还要求特征/预训练 checkpoint 使用 v2，并区分 BLIP 与 CLIP 特征。

可借鉴的是“真实编码器输出 + 明确对齐模块 + 模型内入口”的完整条件，以及点与来源关联。暂不把它接入 A/B0/D0：它会同时改变底模、训练与表示，且丢弃精确拓扑、单位、持久对象身份的风险需要单独测量。若将来对外部无标签模型做开放语义识别，可把已发布 3D-LLM 作为独立系统基线；其失败或胜出不能归因于 token 数这一项。

### 3.2 Embodied VLM：PaLM-E

[论文 §3–5](https://arxiv.org/html/2303.03378v1#S3) 将图像、连续状态或对象表示编码为与语言 embedding 同宽的向量，并与文本交错输入；编码器通过语言输出目标训练。不同实验包含 ViT、状态估计、OSRT 表示和冻结/解冻语言模型的设置。PaLM-E 输出文本答案或高层技能序列，真正的动作由已有低层策略执行。它不提供任意 CAD 命令的执行正确性，也不把语言计划直接变成精确几何约束。

[官方项目页的 Approach 与机器人示例](https://palm-e.github.io/)说明了连续向量注入和低层策略接口，但该页没有官方模型源码、checkpoint 或本地推理 API 的发布入口。本轮因此不能完成“PaLM-E 真实部署”对照，也不能用同名第三方复刻代替。可借鉴其闭环：得到新观察后重新计划；不能借鉴一个当前 consumer 不具备的连续输入能力并称已经接通。

对当前 CAD 任务，已知状态转 JSON 是普通结构化输入，感知编码器产生 embedding 并由已训练模型消费才是 learned representation。两者必须分组。只有取得明确权重、编码协议与可运行实现，并声明新的模型对照后，PaLM-E 类方案才成为可测的 E 候选。

### 3.3 持久地图与 world model：ConceptFusion / DreamerV3

[ConceptFusion 论文 §IV](https://arxiv.org/html/2302.07241v3#S4) 将全局图像特征、局部区域特征融合为像素特征，再随 RGB-D 和位姿融合入 3D 地图；查询时比较开放词汇特征。它不需要为新地图重新训练特征模型，但依赖已经训练的基础模型。论文图 5 的 `howfar` 使用检索点集的质心距离；这不是实体最短距离，更不是通道净空。

官方实现的输入链可以逐步检查：[特征脚本](https://github.com/concept-fusion/concept-fusion/blob/4457c1f718a5612c2efc57b93a2dc152fed8a03d/examples/extract_conceptfusion_features.py#L109-L204) 调 SAM masks 与 OpenCLIP `ViT-H-14/laion2b_s32b_b79k`，构造 `(H,W,1024)` 特征；[融合脚本](https://github.com/concept-fusion/concept-fusion/blob/4457c1f718a5612c2efc57b93a2dc152fed8a03d/examples/run_feature_fusion_and_save_map.py#L141-L195) 读颜色、深度、intrinsics、pose 与特征，使用 `PointFusion(odom="gt", use_embeddings=True)`；[文本查询](https://github.com/concept-fusion/concept-fusion/blob/4457c1f718a5612c2efc57b93a2dc152fed8a03d/examples/demo_text_query.py#L79-L113) 是文本编码与地图向量的 cosine/top-k/阈值操作，不是几何真值服务。

论文与该脚本还有细节差异：论文 Eq.5 同时使用全局相似度与局部特征间的唯一性项；所固定脚本 L186–193 仅对 global/ROI cosine 做 softmax 再融合。这意味着直接运行该脚本不能未经核对就标成复现论文全部配方。对 CAD，可借鉴的部分是将不确定的视觉语义附着到已有对象，并可回到原视图检查；从 CAD 再渲染、重建点云、重新推断身份，不是获取已知距离的必要步骤。未知外部照片/扫描的跨视图语义检索才是继续它的合理反例任务。

[DreamerV3 原始论文的 Learning algorithm](https://arxiv.org/html/2301.04104v2) 描述的是动作条件 RSSM：由观察、历史动作、奖励和 episode 标记学习状态转移，并在想象轨迹上训练 actor/critic。它与静态可查询地图的目标不同。Nature 2025 [期刊版](https://www.nature.com/articles/s41586-025-08744-2) 在本次网页抓取中不可读，因此这里的方法细节依据作者 arXiv v2 与官方源码，不把两版逐项等同。

当前官方 [Agent](https://github.com/danijar/dreamerv3/blob/e3f02248693a79dc8b0ebd62c93683888ddaccfe/dreamerv3/agent.py#L115-L221) 的 `policy()` 调 `dyn.observe()`，`loss()` 训练重建、reward/continuation 并调用 `dyn.imagine()`；[RSSM](https://github.com/danijar/dreamerv3/blob/e3f02248693a79dc8b0ebd62c93683888ddaccfe/dreamerv3/rssm.py#L16-L103) 维护 deterministic 与 stochastic state，输出属于该训练任务的隐状态及动作。它没有 LLM token 注入或精确 B-rep 查询协议。仅为了读 CAD 状态引入 Dreamer 会多出奖励、动作环境、训练与误差累积问题。本轮不采用；只有明确需要预测未知动态后果且有可验证训练环境时再重开该问题。其潜在价值不应由静态尺寸问答实验否定。

### 3.4 Scene graph：ConceptGraphs

[原论文 §III](https://arxiv.org/html/2309.16650v1#S3) 从 posed RGB-D 感知对象，融合几何与开放词汇特征，生成供语言规划使用的对象图。这个图描述感知到的物体及推断关系；CAD 的声明依赖图则描述已有设计数据中的传播关系，两者既不等价也不互相保证完整。

[`mapping.py`](https://github.com/concept-graphs/concept-graphs/blob/93277a02bd89171f8121e84203121cf7af9ebb5d/conceptgraph/slam/mapping.py#L17-L100) 明确展示了关联逻辑：空间相似度可用 bbox IoU/GIoU 或点集 overlap，视觉相似度是 CLIP cosine，两者加权后把检测合并到最高分已有对象。这是带阈值和歧义的感知关联，并不是从 STEP 固定对象 ID 读值。“accurate IoU”函数的输入仍为 bbox，不能因此称其为精确 CAD 实体净空。

[`build_scenegraph_cfslam.py`](https://github.com/concept-graphs/concept-graphs/blob/93277a02bd89171f8121e84203121cf7af9ebb5d/conceptgraph/scenegraph/build_scenegraph_cfslam.py#L621-L782) 先以 overlap 选候选边、取 minimum spanning tree，再把一位小数的 bbox center/extent 和描述交给 `gpt-4` 推断关系。可选关系限于 on/in 的两个方向和 none。因此非邻近的支撑、共享参照、编辑传播关系可能根本不在候选边中；MST 的稀疏不是依赖完备性的证据。`refine_node_captions()` 与关系构建还会发送描述到外部 API，不能直接对私人模型运行其默认流程。

值得借鉴的是对象级检索后再利用图补回关联对象，以及对每个关系保留可复查的来源。本轮 #170 固定观察表示后，可独立比较 full / exact graph-rule / lexical / embedding+graph；将感知式 graph 的近邻/MST 机制替换成既有声明边是一个不同算法，必须如实命名。至少应设计一条检索怀疑：语义相似对象得到高分，但一个名称不相关的关键参照/依赖被遗漏，再用精确查询验证或反驳。不能把相似度阈值调整到全部通过后仍称 held-out。

### 3.5 GPU / differentiable rendering：nvdiffrast

[原论文 §3](https://arxiv.org/html/2011.03277v1#S3) 将 rasterization、interpolation、texture、antialias 分为可组合计算原语。梯度解决的是对图像损失反向优化几何或外观；它不训练语言模型理解这些输出。只需观察深度和对象 ID 时，反向传播不是必要条件。

v0.4.0 的 [`rasterize()`](https://github.com/NVlabs/nvdiffrast/blob/253ac4fcea7de5f396371124af597e6cc957bfae/nvdiffrast/torch/ops.py#L93-L135) 输入 contiguous GPU `float32` clip-space 顶点与 `int32` 三角形；输出 `(B,H,W,4)` 的 barycentric `(u,v)`、`z/w`、triangle ID，以及导数 buffer。`z/w` 是投影深度而非米制距离；triangle ID 需要外部保留 triangle→CAD object/face 映射。法线需计算或插值属性，并注明 face/smooth/world/camera 约定。[文档](https://nvlabs.github.io/nvdiffrast/#rasterization)还说明 ID 为三角形索引加一、背景为零；不能把抗锯齿后的颜色当离散身份编码。

[`DepthPeeler`](https://github.com/NVlabs/nvdiffrast/blob/253ac4fcea7de5f396371124af597e6cc957bfae/nvdiffrast/torch/ops.py#L139-L204) 可取后续深度层，适合专门测遮挡，而不是从单张首层 depth 猜被遮物体。对于 CAD，所有这些输出仍建立在三角化近似上，不能替代 B-rep 距离和交叠计算。本轮采用“显式相机、原始离散 ID、深度语义、三角化容差”的设计原则；暂不引入 nvdiffrast 生产依赖，其使用条款和 GPU 部署也需先满足。未来只有逆渲染任务确实需要梯度，或同等观测下实测成本有收益，才有采用该实现的理由。

### 3.6 Simulator-native observations：Habitat / Isaac Lab

[Habitat 原论文 §3、附录 A](https://arxiv.org/html/1904.01201v2#S3) 的可借鉴点是同一场景、同一传感器参数下生成 RGB/depth/semantic observations，并把任务评估与模拟器实现区分。当前 [`Simulator.get_sensor_observations()`](https://github.com/facebookresearch/habitat-sim/blob/57ee4941dc4765240f0f91f70b2c97a919bf9038/src_python/habitat_sim/simulator.py#L537-L573) 先更新 agent 下的传感器，再以传感器 uuid 返回结果。与单独保存一张无相机信息的图片相比，这种绑定适合保证 C 的通道可比较。

实际 GPU 通路在 [`sensor_wrapper.py`](https://github.com/facebookresearch/habitat-sim/blob/57ee4941dc4765240f0f91f70b2c97a919bf9038/src_python/habitat_sim/sensors/sensor_wrapper.py#L97-L156) 依 sensor type 分配 semantic int32、depth float32、RGBA uint8；[`get_observation()`](https://github.com/facebookresearch/habitat-sim/blob/57ee4941dc4765240f0f91f70b2c97a919bf9038/src_python/habitat_sim/sensors/sensor_wrapper.py#L189-L219) 从渲染目标读入 buffer，随后翻转竖直方向并应用 noise model。GPU 路径存在并不等于不重排。另 [`cast_ray`](https://github.com/facebookresearch/habitat-sim/blob/57ee4941dc4765240f0f91f70b2c97a919bf9038/src/esp/bindings/SimBindings.cpp#L369-L372) 查询的是 collidable scene，要求启用 physics，距离参数以 ray length 为单位。若碰撞网格省略细部，它不能充当 CAD 实体查询的独立精确真值。

[Isaac Lab 原论文 §3.3](https://arxiv.org/html/2511.04831v1#S3.SS3) 和 v2.2.1 的 [`Camera`](https://github.com/isaac-sim/IsaacLab/blob/0f00ca2b4b2d54d5f90006a92abb1b00a72b2f20/source/isaaclab/isaaclab/sensors/camera/camera.py#L40-L88)明确区分 `distance_to_camera`（光心距离）、`distance_to_image_plane`（沿相机 z 的深度）与 `depth` 别名，同时提供 normals、语义与 instance-ID 的 fast 通道。[`CameraData`](https://github.com/isaac-sim/IsaacLab/blob/0f00ca2b4b2d54d5f90006a92abb1b00a72b2f20/source/isaaclab/isaaclab/sensors/camera/camera_data.py#L17-L91) 保有相机位置、四元数、intrinsics、输出和 ID/prim-path 元信息，并在 world、ROS（+Z 前/-Y 上）、OpenGL（-Z 前/+Y 上）间显式转换。可借鉴这种约定，不应仅凭变量名 depth 假定含义相同。

版本需特别注意：2026-09-20 查询到 Isaac Lab 默认分支为 develop，`1fb0997` 中 [`TiledCamera`](https://github.com/isaac-sim/IsaacLab/blob/1fb0997504cdde462016c195fdcda8195d32dd30/source/isaaclab/isaaclab/sensors/camera/tiled_camera.py#L7-L41) 已是弃用的 `Camera` 别名；当日 main 为 `b0542fe2d45bf91c4e1d9ef6952b9c709c80b4e8`。本文下一节逐像素重排分析固定使用 v2.2.1，不把 develop 与旧文档混成一个接口。

这两套系统可证明多通道观测在工程上有成熟实现，不能证明当前 Hub 已开放这些通道，也不能证明值得为本轮实验引入整个模拟器。适合本研究的最小借鉴是：从同一验证来源导出观测，绑定相机、单位、对象映射、源版本与完整场景遮挡物；不复制它们的项目状态库或训练环境。

### 3.7 Tensor / zero-copy interop：DLPack 与 tiled camera

[DLPack Python 协议](https://github.com/dmlc/dlpack/blob/94485e23519bc30e22a18397a9c8ff1ea6b2b81e/docs/source/python_spec.rst#L16-L104) 定义数组内存、设备、stream、deleter 和 capsule 消费规则。consumer 必须提供其使用的流，producer 必要时同步；capsule 只消费一次。共享数据仍有所有者和生命周期；协议允许明确要求或在必要时发生复制。它不是 IPC、远程推理或语义协议。

[PyTorch 2.14 的 `from_dlpack`](https://docs.pytorch.org/docs/2.14/generated/torch.from_dlpack.html) 与所核查的 [PyTorch 实现](https://github.com/pytorch/pytorch/blob/22173918c157cf5c3b647d41e5383286fc45d128/torch/utils/dlpack.py#L124-L295)提供可核对的边界：默认可共享内存，原地变更可能相互可见；跨设备请求且 `copy=False` 会被拒绝，实际跨设备路径调用 `.to(device)`；CUDA/ROCm 使用当前 stream。不能由某一转换返回别名，推导出后续 normalize、transpose/contiguous、打包与传输都没有复制。

Isaac Lab v2.2.1 是现成反例。相机 annotator 用 `do_array_copy=False`，但 [`_update_buffers_impl()`](https://github.com/isaac-sim/IsaacLab/blob/0f00ca2b4b2d54d5f90006a92abb1b00a72b2f20/source/isaaclab/isaaclab/sensors/camera/tiled_camera.py#L231-L282) 可能执行设备转换，对 motion vectors 调 `.contiguous()`，然后用 `wp.from_torch(output)` 创建输出别名并启动 `reshape_tiled_image`。该 [kernel L101–116](https://github.com/isaac-sim/IsaacLab/blob/0f00ca2b4b2d54d5f90006a92abb1b00a72b2f20/source/isaaclab/isaaclab/utils/warp/kernels.py#L101-L116) 按 tile 索引逐像素写入另一个 batched buffer。zero-copy 的是 Torch→Warp 包装，不是 tile→batch 重排，也不包括云端模型传输。

本轮仅做一个独立 CPU 边界 smoke：在当前 `python` 的 Python 3.12.10 / NumPy 2.5.2 下，`x=np.arange(6,dtype=np.float32).reshape(2,3)`、`y=np.from_dlpack(x)` 后，`np.shares_memory(x,y)` 为 true；令 `x[0,1]=37`，读取 `y[0,1]` 为 37；本次 `y.flags.writeable` 为 true。该结果只证明本次 NumPy→NumPy 共享，未测 GPU、同步耗时、渲染或推理。相同解释器的 `find_spec` 对 torch、nvdiffrast、habitat_sim、isaaclab 均为 unavailable；这不等于机器上不存在其他 Python 环境。

本轮采用分段记账原则，暂不为文本/PNG consumer 新建 tensor 服务。只有真实本地 tensor consumer 已接通、拷贝在实际总延迟中占有显著比例时，优化这条链才可能改善任务成本；不能把零拷贝的局部成功作为空间推理提升。

## 4. C / E 的工程可行性与停止条件

当前生产事实固定在 MonkeyHub `3a92b41460b52c04963278a3300a29c34744c43e`。[`ModelViewDto`](https://github.com/cogco1/MonkeyHub/blob/3a92b41460b52c04963278a3300a29c34744c43e/apps/archflow-studio/api/archflow_studio_api/transport/drawings.py#L14-L26) 限定 front/back/left/right/top、最长边 1024、base64 PNG 和 `orthographic-line-projection`。[`chat.py`](https://github.com/cogco1/MonkeyHub/blob/3a92b41460b52c04963278a3300a29c34744c43e/apps/monkeyhub/api/monkeyhub_api/chat.py#L1472-L1479) 给 Claude 的附件明确构造成 text / image-base64 content blocks；Codex 分支使用 [`--image`](https://github.com/cogco1/MonkeyHub/blob/3a92b41460b52c04963278a3300a29c34744c43e/apps/monkeyhub/api/monkeyhub_api/chat.py#L1155-L1188)。所核查路径没有任意 tensor、DLPack 或 `inputs_embeds` 输入分支。这是当前接线的限制，不是所有模型永远不可能支持该能力。

| 条件 | 当前可核实能力 | 本轮处理 | 继续的最小条件与可反驳判断 |
| --- | --- | --- | --- |
| C：同 RGB + 原始 depth / ID / normal 数值或 tensor | 外部模拟器有对应生产者；当前 model-view 是线 PNG，当前聊天附件没有 raw tensor 入口 | 本轮原始多通道 C 记 unavailable，不以文本数组或彩色图伪装为 tensor 接入 | 从同一来源、同一相机得到 RGB 基线与对齐通道；独立验证单位/ID/裁剪；选定真实支持该输入的 consumer，再分别统计是否改善遮挡/身份任务 |
| C-vis：把深度或法线可视化为 PNG | image consumer 可以接收 PNG，但消费的是颜色编码后的图片 | 若以后执行，应单列为视觉化通道条件，不能并入原始数值 C | 保留固定编码范围、缺失值、图例；与同 RGB 基线比较，并计额外图片输入成本。编码损失或额外图像成本使收益消失是有效负结果 |
| D0：同线稿 + 精确查询 | 既有 OCCT HLR 身份/显隐与实体测量可由实验端只读组合；这不是现成通用 Agent 生产接口 | 先验证已存在算法所能回答的事实，保留未知/陈旧拒绝 | 同源版本、完整参与场景、明确单位与坐标；若成本增加而错误无改善，不主张 D0 优于 A/B0 |
| E：compact learned tokens / tensor | 3D-LLM 有真实点特征消费实现，但模型/特征/对齐均不同；PaLM-E 无已核实可下载入口；当前 Hub 无此链路 | 本轮 E 记 unavailable，不强行训练或把 JSON 命名为 tensor | 固定 encoder、checkpoint、dtype/shape、坐标归一化、consumer 和许可；跑过端到端真实推理后才能比较。异构模型结果单独报告，不能归因为一种表示变化 |

新增通道的大小不能只计算最后的 learned token 数。以 **1024×1024 的未压缩数组作算术示例**，RGB uint8 为 3 MiB、depth float32 为 4 MiB、ID uint32 为 4 MiB、normal float32×3 为 12 MiB，合计 23 MiB/视图；不包括相机和对象映射，也不代表实际 PNG 大小或模型计费。3D-LLM 的 5,000×1,408 float32 特征仅该数组就是 28,160,000 bytes，32 个 query token 位于其后的模型内部压缩阶段。预处理、特征缓存与更新成本仍需计入总成本。

## 5. 对受控实验的具体约束

1. **表示与选择分开。** #195 固定模型/任务/预算比较 A、B0、D0；#170 固定同一观察事实，改变 full、graph-rule、lexical、embedding+graph 的选择方法。保持任务与判定规则不变，才可分别讨论表示损失和关键事实遗漏。
2. **几何量有严格语义。** Euclidean 质心距离、bbox 分离、实体最短距离、射线 hit depth、碰撞网格距离与通道净空分别命名。任意两个实体的最短距离也不自动等于某个完整通道的净空；几何真值应由可手算合成对象或独立审定输入核验。
3. **坐标必须同源。** Hub 的 Y-up/平面 `[x,z]` 与 CAD/STEP 的 Z-up 显式转换；相机 forward/up、世界到相机方向、长度单位、正交投影范围、像素原点、裁剪及三角化/线离散容差都应固定。相机变换错误不得记为模型空间能力失败。
4. **身份与依赖可追问。** 相似物体、未知角色与没有词汇相似性的关键参照进入 held-out queries/edits。检索结果可以提出“这个对象可能相关”，但必须能用同版本 exact query 验证或反驳至少一条具体怀疑。unknown role 不能因相似度高就自动变为已知。
5. **陈旧与遗漏保留在分母。** 每次读、改后重索引、查询与纠错都绑定源版本。陈旧 embedding 或图可以被拒绝，但不能悄悄换成最新完整输入后算作旧索引成功。遗漏未声明依赖应标明知识边界，不能把未检索到误写为不存在。
6. **成本沿实际边界记录。** 渲染、特征生成、索引创建/更新、重排/复制、序列化、传输、推理、查询与纠错分别记账；没有遥测写 unknown。总费用区分订阅消耗、实际账单与 API 等价估算。先给首次有效答案与最终正确答案时间，再判断少 token 是否省总成本。

当前可以直接查询的，是对象已有且定义清楚的身份、参数、实体间距离/交叠、指定视图参与物体的显隐与声明依赖。整体构图、开合感、视觉连续性、材料表现与未建模参考图仍需要视觉和可归属的判断。值得继续的学习路线是未知视觉语义到已有对象的可验证映射，或在确有动作/奖励任务时预测未知动态后果；这些方向的价值仍需独立实际实验，不能由本轮静态合成场景提前宣布。

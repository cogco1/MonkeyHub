# 团队与 Agent 接入清单

先完成一件事：**在自己的电脑、源码目录和运行目录里跑通一个现有任务，让另一人能按同一版本复现。** 不需要先理解整个 ArchFlow，也不用先新增模块。

ArchFlow 源码接入默认从 GitHub `main` 开始，记录实际提交和对应 Actions 结果；已有任务则使用负责人约定的提交。本页随 `main` 更新，可直接把页面链接交给 Agent。

## 先看这一屏

**应用统一从 MonkeyHub 进入。** 启动、项目选择和 Agent 接入沿同一流程；代码开发使用现有查询命令。
首次会话按 [Hub 入口与定向检索顺序](WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md#0-monkeyhub-统一入口)执行，
不需要先读完整工具箱或模块地图。MonkeyArch 是 Hub 的建模工作区，不是另一个总入口。

| 你要做什么 | 从哪里进入 | 第一个结果 |
| --- | --- | --- |
| 直接使用 MonkeyHub 整合包 | [Windows 候选包安装说明](../apps/monkeyhub/installer/README.md) | 完整解压后运行 `INSTALL_MONKEYHUB.cmd`，打开 Hub；无需自行安装 Python 或 Node.js |
| 使用或改进研究工具、Skills、实验记录、图表与报告 | [共享工具箱](https://github.com/cogco1/huaguoshan-digital-infrastructure)，先读其 `AGENTS.md`、`README.md`、`docs/CLI.md` | 在自己的 Runtime 生成一次合成演示，找到报告、图表和来源 run |
| 使用或改进 MonkeyArch 界面、模型修改与候选执行 | [ArchFlow](https://github.com/cogco1/ARCHFLOW_V4)，先读 [AGENTS.md](../AGENTS.md)，再按本文运行 | API/Web 连通，一次合成候选修改读回为 `2.2` |
| 修改某个已有能力 | ArchFlow 用 `python tools/devctl.py module <关键词>` 找 owner；工具箱用 `hgs skills list/show` 查具体契约 | 找到负责模块、真实调用方和相关测试，约定一个小修改 |
| 查看真实建筑、继续设计 | 由项目负责人提供可共享的完整项目副本和选定 run | 在自己的副本看见指定模型，再进行一次已约定的修改 |

**第一次默认任务：接入复现。** 使用应用从 Hub 开始；开发时只准备本次任务需要的仓库与环境。
把版本、实际结果和最卡的一步回传到约定的 Issue/PR。没有发现真实问题，就不为了“交第一个 PR”制造代码改动。

整合包按包内 `source-version.txt` 标明的版本运行，更新源码不会自动更新已安装的包。第二台干净 Windows 和第二位使用者的实际验收仍需完成；下面的源码接入回路也不替代真实项目试用。

## 今天按什么顺序收口

- [ ] **主线负责人：给出接入版本。** 给出所选仓库的准确提交、任务和审查人。不要让新成员猜维护者本机的未提交版本。
- [ ] **主线负责人：核对所选版本的检查结果。** ArchFlow 的 [远端工作流](../.github/workflows/verify.yml) 已包含架构检查、核心与 Studio API 测试、Web 检查及 Windows/Linux 首次接入检查；以该提交实际 Actions 结果为准。
- [ ] **新成员与她的 Agent：独立复现。** 从 GitHub 获取代码，在自己的环境完成所选仓库的最小回路。
- [ ] **双方：交接一个真实小任务。** 依据复现中实际遇到的问题，约定修改文件、完成动作和审查人，再开短分支。第二人复跑或审查后才算完成交接。

这四项完成后，再决定新的模型功能。第一天不要求跑完整建筑、接入外部模型服务或增加通用框架。

<details>
<summary>2026-09-06 历史快照（不作为当前接入基线）</summary>

| 仓库 | 当日 GitHub `main` | 已核实状态 |
| --- | --- | --- |
| 共享工具箱 | [`73f9ee4`](https://github.com/cogco1/huaguoshan-digital-infrastructure/commit/73f9ee423a6ae2509b7cd96a1645bf0fa139484b) | [该提交 Actions 通过](https://github.com/cogco1/huaguoshan-digital-infrastructure/actions/runs/33937511062)；已发布命令是 `research`，不是本地开发中的 `hgs` |
| ArchFlow | [`7b3d09f`](https://github.com/cogco1/ARCHFLOW_V4/commit/7b3d09fb9c6dd819d8e26a9b827f992527bb39da) | [该提交 Actions 失败](https://github.com/cogco1/ARCHFLOW_V4/actions/runs/33930349347)：旧 `archflow-runtime` 入口导入不存在的 `archflow.project.runtime`；该 CI 尚未覆盖 Studio API/Web |

当日维护者的 ArchFlow 本地提交为 `a587156`，比上述远端多 24 笔提交；另有未提交改动。工具箱本地提交为 `c3b54af`，另有 `hgs` 查询、目录及命名等未提交改动。本清单发布不等于这些代码已发布。

</details>

下列 ArchFlow 命令对应当前源码入口；工具箱保留其独立发布边界。每次接入记录所选仓库的实际提交并查看 Actions，不按历史快照回退代码。源码检查通过不等于已在新成员电脑完成接入。

## 1. 负责人先给什么

- [ ] 两个私有仓库的访问权限；看见 404 时先核对登录账号和仓库权限，不把私有资料转发到公开位置。
- [ ] 本次使用的源码提交、一个明确任务，以及负责回答问题和审查的人。
- [ ] 若要试真实建筑：完整项目副本、选定 run、适用源码版本和材料使用范围。只有一份 `.3dm` 不足以继续完整项目。

首轮合成演示不需要真实建筑材料、模型服务密钥或 Rhino。朋友自己选择短的源码路径和仓库外 Runtime；不要复制维护者的个人配置、凭据或整个运行目录。

## 2. 获取两个仓库

以下命令面向 Windows / PowerShell，需要 Git、Python 3.12；ArchFlow Web 使用 Node.js 24。示例路径可更换，但两个源码目录和两个 venv 分开；较短路径可避免旧文件树触发 Windows 长路径问题。

```powershell
$ArchSource = 'D:\code\ARCHFLOW_V4'
$ToolboxSource = 'D:\code\huaguoshan-digital-infrastructure'
$ArchRuntime = 'D:\runtime\archflow-first-trial'
$ToolboxRuntime = 'D:\runtime\toolbox-first-trial'

git clone https://github.com/cogco1/ARCHFLOW_V4.git $ArchSource
git clone https://github.com/cogco1/huaguoshan-digital-infrastructure.git $ToolboxSource
git -C $ArchSource rev-parse HEAD
git -C $ToolboxSource rev-parse HEAD
```

已有 clone 就先查看 branch、HEAD 和 `git status --short`，不要覆盖已有工作。首次运行不切换维护者的工作区，也不将旧 3DM 或私有项目复制进源码仓库。

## 3. 先跑工具箱

<details>
<summary>展开：安装到独立 venv，运行已发布的合成演示</summary>

沿用上一步路径，在 PowerShell 中执行：

```powershell
Set-Location $ToolboxSource
py -3.12 -m venv "$ToolboxRuntime\venv"
$ToolboxPython = "$ToolboxRuntime\venv\Scripts\python.exe"
$env:PYTHONUTF8 = '1'
$env:MPLBACKEND = 'Agg'
& $ToolboxPython -m pip install -r requirements-lock.txt
& $ToolboxPython -m pip install --no-deps -e .
& "$ToolboxRuntime\venv\Scripts\research.exe" --root examples/demo validate
& $ToolboxPython tools/demo.py --output "$ToolboxRuntime\first-demo"
```

`--output` 必须是新目录；再次运行换一个输出目录，不覆盖原实验。找到：

- `first-demo/workspace/generated/atlas/index.md`：来源与记录索引。
- `first-demo/workspace/generated/reports/HGR-DEMO-thread.md`：合成课题报告。
- `first-demo/workspace/generated/members/P-DEMO.md`：成员参与记录。
- `first-demo/HGR-DEMO.zip`：本地归档。

需要进一步核对修改后重建与归档恢复时，使用同一个入口：

```powershell
& $ToolboxPython tools/demo.py --benchmark --output "$ToolboxRuntime\first-benchmark"
```

检查 `benchmark.json` 的 `completed` 与各项 checks。这个演示只证明合成任务结果，不替代人的研究问题、判断或贡献确认。

能力说明从已发布的 `skills/<id>/SKILL.md` 和 `skill.yaml` 读取。待负责人提供含新目录功能的已发布版本后，再使用 `hgs skills list` / `hgs skills show <id>`；查看注册条目不等于已安装外部工具。

</details>

完成：另一人能从你的两个版本信息和执行命令定位报告及其来源，而不需要访问维护者电脑。

## 4. 参与建模时，再跑 ArchFlow

<details>
<summary>展开：独立环境 → 合成项目 → API/Web → 一次候选修改</summary>

### 安装与创建合成项目

```powershell
Set-Location $ArchSource
py -3.12 -m venv "$ArchRuntime\venv"
$ArchPython = "$ArchRuntime\venv\Scripts\python.exe"
$env:PATH = "$ArchRuntime\venv\Scripts;" + $env:PATH
& $ArchPython -m pip install -e '.[cad-inspection]'
& $ArchPython -m pip install -r apps/archflow-studio/api/requirements.txt httpx2
& $ArchPython -m pip check
npm.cmd ci --prefix apps/archflow-studio/web
npm.cmd ci --prefix apps/archflow-studio/web/tools/openapi-ts

Set-Location "$ArchSource\apps\archflow-studio\api"
$env:ARCHFLOW_ONBOARDING_PROJECTS = "$ArchRuntime\workspace\projects"
& $ArchPython -c "import os; from pathlib import Path; from tests.support import make_empty_project; print(make_empty_project(Path(os.environ['ARCHFLOW_ONBOARDING_PROJECTS'])).layout.root)"
```

项目创建于 `$ArchRuntime\workspace\projects\demo-project`，只初始化一次。它有可编辑状态但没有导出的 3DM，**空视口是预期结果**。要重新初始化就选择新的 Runtime，不删除原项目。

### 启动两个终端

终端 A 沿用上述变量：

```powershell
Set-Location "$ArchSource\apps\archflow-studio\api"
$env:ARCHFLOW_STUDIO_PROJECT_DIR = "$ArchRuntime\workspace\projects\demo-project"
$env:ARCHFLOW_STUDIO_REFERENCE_RUN = ''
$env:ARCHFLOW_STUDIO_MODE = 'local'
$env:ARCHFLOW_STUDIO_INTENT_PROVIDER = 'deterministic'
$env:ARCHFLOW_STUDIO_CAD_EXPORT = 'off'
& $ArchPython -m archflow_studio_api.main --host 127.0.0.1 --port 18080
```

终端 B 重新填写自己的源码路径：

```powershell
$ArchSource = 'D:\code\ARCHFLOW_V4'
Set-Location "$ArchSource\apps\archflow-studio\web"
$env:ARCHFLOW_STUDIO_API_URL = 'http://127.0.0.1:18080'
npm.cmd run dev -- --port 15174
```

打开 `http://127.0.0.1:15174`。端口已占用时改用空闲端口并同步代理地址，不停止别人的服务。首次采用手动入口，不调用仓库中带维护者本机路径的 `runtime.json`。结束时在两个终端按 Ctrl+C。

### 从 Web 代理完成候选回路

终端 C：

```powershell
$ArchRuntime = 'D:\runtime\archflow-first-trial'
$projectHead = "$ArchRuntime\workspace\projects\demo-project\HEAD"
$headBefore = Get-Content -LiteralPath $projectHead -Raw
$api = 'http://127.0.0.1:15174/api'
Invoke-RestMethod "$api/health"   # projectBound 必须为 true，不只检查 HTTP 200
Invoke-RestMethod "$api/protocol" # 应返回 archflow/2 协议
$state = Invoke-RestMethod "$api/state"
$body = @{
    stateDigest = $state.stateDigest
    targetComponentId = 'portico'
    elementId = 'portico-base'
    utterance = 'set height to 2.2'
} | ConvertTo-Json
$proposal = Invoke-RestMethod "$api/proposals" -Method Post -ContentType 'application/json' -Body $body
$accepted = Invoke-RestMethod "$api/proposals/$($proposal.proposalId)/candidate" -Method Post
Invoke-RestMethod "$api/jobs/$($accepted.jobId)"
```

若 job 仍为 `queued/running`，稍后只重读同一个 job，不重复提交。若失败，回传该 job 的错误，不改写成成功。`succeeded` 后执行：

```powershell
$result = Invoke-RestMethod "$api/state?run=$($accepted.candidateId)"
($result.elements | Where-Object elementId -eq 'portico-base').numericFields.height
((Get-Content -LiteralPath $projectHead -Raw) -eq $headBefore)
```

应依次得到 `2.2` 和 `True`。候选已保留，正式项目位置未变；关闭 CAD 导出时没有模型 artifact 不算导出失败。

### 相关检查

在非服务终端中重新设置自己的路径与 venv，运行：

```powershell
$ArchSource = 'D:\code\ARCHFLOW_V4'
$ArchRuntime = 'D:\runtime\archflow-first-trial'
$ArchPython = "$ArchRuntime\venv\Scripts\python.exe"
$env:PATH = "$ArchRuntime\venv\Scripts;" + $env:PATH
Set-Location "$ArchSource\apps\archflow-studio\api"
& $ArchPython -m unittest tests.test_health tests.test_protocol tests.test_candidate
Set-Location "$ArchSource\apps\archflow-studio\web"
npm.cmd test
npm.cmd run api:check
npm.cmd run build
```

`api:check` 使用 PATH 中的 `python` 导出实际 API，再用独立安装的生成器核对客户端；比较已处理 CRLF/LF 差异。涉及 API/DTO 的开发需保持这项检查通过。

</details>

完成：API 绑定自己的项目，通过 Web 代理生成候选，数值读回 `2.2`，项目 `HEAD` 不变。**这不是完整建筑功能、真实模型试用或正式发布的验收。**

## 5. Agent 接哪个模块

只选择与任务相关的一行，然后查看实际 owner 和调用方；不要把表里所有方向都开发一遍。

| 任务 | 现有入口 | 主要检查 |
| --- | --- | --- |
| 研究命令、输入与运行记录 | 工具箱 `src/hgr/cli.py`、`runner.py`、`store.py` | 对应 `tests/` 行为测试 |
| 图表、报告、来源与归档 | 工具箱 `src/hgr/projections.py`，相关 `skills/<id>/` | 合成演示；只有改动涉及恢复/重建时再跑 benchmark |
| MonkeyArch 交互 | ArchFlow `apps/archflow-studio/web/src/`，先查对应 feature | 交互测试、类型与构建；DTO 变化另查生成客户端 |
| API 用例与项目绑定 | ArchFlow `apps/archflow-studio/api/archflow_studio_api/` | 对应路由与用例测试，保留项目及候选绑定 |
| 几何、依赖、验证或项目存储 | ArchFlow [SYSTEM_MAP.md](SYSTEM_MAP.md) 的现有模块 | registry 中该模块的测试与 `tools/archcheck.py` |

新增 Skill 优先扩展真实已有条目，不复制两仓实现。研究问题和结论由成员提出；真实项目改动由项目任务确定。本清单不会自动派发新的模型算法、Skill 包或存储机制。

## 6. 把这段发给她的 Agent

```text
请先读取这份 GitHub 接入清单，帮我完成首次接入（代码版本仍按负责人指定）：
https://github.com/cogco1/ARCHFLOW_V4/blob/main/docs/TEAM_ONBOARDING.md
先读两个目标仓库的 AGENTS.md、这份清单和所选仓库 README，报告：
当前能访问的仓库与提交、我今天先跑哪个入口、成功时应看到什么。
不要从旧会话或维护者个人路径猜环境，也不要把本地开发功能当作 GitHub 已发布功能。

默认任务是“接入复现”：先跑共享工具箱合成演示；参与建模时再跑 ArchFlow 合成候选回路。
在我选定的独立源码目录和仓库外 Runtime 中安装必要的 venv 依赖、运行合成示例与相关检查。
需要启动应用服务时先向我说明端口和项目目录，由我确认；不要操作我已有的应用或项目。
完成后回传两个 SHA、实际成功的命令、产物位置、检查结果和最卡的一步。
若发现真实小问题，先复现并指出现有 owner、拟修改文件与验收动作；未约定任务时不新增模块。
修改保持在明确范围内；GitHub 提交、发布及任何真实资料上传前，先给我看具体 diff 和范围。
```

## 7. 怎么把结果交回来

在约定的 Issue/PR 中简短填写，不另建报告平台：

```text
任务：接入复现 / 已约定的小修改
源码：ArchFlow <SHA>；共享工具箱 <SHA>
环境：系统、Python、Node（若使用 Web）
结果：工具箱产物位置；或 candidate ID、height=2.2、HEAD 未变
检查：实际执行的命令及结果；未执行的不写通过
卡点：哪一步、准确错误、是否需要维护者提供版本/权限/项目资料
后续修改：现有 owner、明确文件和审查人；没有问题则写无需改代码
```

已有任务时从约定提交建短分支；只暂存自己的明确文件，检查 diff 后按仓库协作流程提交 PR。由另一人审查、必要时复跑，再交给集成人。不要用 `git add -A` 收进运行目录、生成物或别人的修改。

交付给人看的文件采用 `YYYYMMDD[-NN]_项目名称[_内容][_RNN].ext`，例如 `20260906_接入试用_结果.md`。可选 `-02` 区分同日批次，`R01` 表示内容修订。程序依赖的固定文件名、原始输入、历史产物和来源引用保持原状。

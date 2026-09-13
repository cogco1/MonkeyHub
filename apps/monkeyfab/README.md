# MonkeyFab

MonkeyHub 同仓的模型加工准备模块，完成 **等比例缩放 → 按拓竹打印空间封闭拆件 → 导出 STL 与装配位置表**，并可将 Bambu Studio 已切片的任务通过局域网发送到打印机。

当前为本地可运行的 `0.1.0` 实验版本。输入是封闭 STL／OBJ 网格；输出是毫米单位的独立分件。竹／木板拆件、排料和激光／CNC 输出留作后续方向。

## 安装和使用

桌面版和网页版的完整包已默认包含本模块与依赖。源码开发需要 Python 3.12 或更新版本，在 Hub 仓库的 `apps/monkeyfab/` 目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\monkeyfab.exe profiles
```

在 Hub 根目录也可运行 `python -m pip install -e "apps/monkeyfab[send,test]"`。独立 CLI 保留，源码与完整安装包统一使用 Hub 的提交版本，不再单独克隆 Fab。

假设原模型坐标单位为米，目标为 1:100：

```powershell
.\.venv\Scripts\monkeyfab.exe prepare "D:\models\building.stl" --input-unit m --scale 1:100 --printer x1c --output "D:\prints\building-100-x1c"
```

`--input-unit` 必填，可选 `mm`、`cm`、`m`、`in`；STL 本身不声明单位。`--scale` 是模型与原物的比例，支持 `1:100` 或 `0.01`，默认 `1:1`。XYZ 使用同一个缩放系数，保持朝向，Z 为打印高度方向。输出目录必须新建或为空。

## 打印范围参数

参数位于 [profiles.py](src/monkeyfab/profiles.py)，可用 `monkeyfab profiles --json` 查看尺寸、坐标和官方来源。下表单位为 mm，顺序为 X × Y × Z。

| 配置 | 扣除工具留量前的可用范围 | 默认拆件上限 | 说明 |
| --- | --- | --- | --- |
| `x1c` | 238 × 256 × 250 | 228 × 246 × 245 | 默认切片高度 250；采用 X=18…256 的矩形，避开左前角 18 × 28 禁区 |
| `h2s` | 340 × 320 × 340 | 330 × 310 × 335 | H2S 单喷头；原点为零，官方配置无排除区 |
| `h2d-left` | 325 × 320 × 320 | 315 × 310 × 315 | 左喷头 X=0…325，最高 Z=320 |
| `h2d-right` | 325 × 320 × 325 | 315 × 310 × 320 | 右喷头 X=25…350，最高 Z=325 |
| `h2d-dual` / `h2d` | 300 × 320 × 320 | 290 × 310 × 315 | 取两喷头共同覆盖范围；X=25…325、最高 Z=320 |

默认 `--xy-margin 5` 在 XY 每侧留 5 mm，`--z-clearance 5` 在顶部留 5 mm；均为可调整的工具参数。X1C 的 238 mm 宽与 H2D 的双喷头共同范围是依据官方限制推导的工具范围。

X1C 的标称体积为 256 × 256 × 256；默认配置与禁区依据 [官方 X1C 配置](https://github.com/bambulab/BambuStudio/blob/master/resources/profiles/BBL/machine/Bambu%20Lab%20X1%20Carbon%200.4%20nozzle.json)、[平台范围配置](https://github.com/bambulab/BambuStudio/blob/master/resources/profiles/BBL/machine/fdm_bbl_3dp_001_common.json)及[默认高度配置](https://github.com/bambulab/BambuStudio/blob/master/resources/profiles/BBL/machine/fdm_machine_common.json)。H2D 依据 [2026.02 官方手册第 69–70 页](https://csm.bblcdn.com/hub/4668d0ca43994ff3bff4b37f1a65c2e7.pdf#page=70)区分左右喷头；350 mm 是两喷头覆盖的并集宽度。

H2S 依据 [Bambu Studio v02.05.00.66 官方 H2S 配置](https://github.com/bambulab/BambuStudio/blob/v02.05.00.66/resources/profiles/BBL/machine/Bambu%20Lab%20H2S%200.4%20nozzle.json)，与 H2D 的左右喷头范围分别定义。

## 拆件和装配

工具按缩放后全模型的最小角建立 XYZ 网格，用 Manifold 的封闭平面切分生成切口面。每个连通分件独立导出，孔洞保留；检查封闭性、正体积、尺寸范围和拆件总体积。相互重叠的独立实体分别保留。

- `part_001.stl`、`part_002.stl`……：每件移到自身最小角 `(0, 0, 0)`，以毫米导出。导入切片软件时使用毫米。
- `parts.json`：记录尺寸、网格位置、缩放比例、打印参数和每件的 `assembly_offset_mm`。将该偏移加回分件顶点，即恢复它在缩放后原模型坐标系的位置。

每件都有能落入所选可用区域的摆放位置，建议平台原点写在 `suggested_bed_origin_mm`。在 Bambu Studio 中还需选择对应机器和喷头并确定支撑、裙边和实际摆放；`prepare` 交付几何分件，G-code 由 Bambu Studio 切片生成。

第一版按固定 XYZ 网格切割，不自动寻找建筑分缝、加定位接头、旋转优化或计算薄壁可打印性。开放网格、内向／不一致法线、分离的封闭内腔壳会明确报错；不自动补洞。3DM／STEP 需先在建模工具中导出封闭 STL／OBJ。

## 发送已切片的任务

在 Bambu Studio 选择目标机器、喷嘴、材料与支撑，完成切片并导出 `.gcode.3mf`。`send` 将该文件上传到指定机器，核对远端字节数后返回成功；不启动打印，也不改变已导出的文件。文件必须包含非空的 `Metadata/plate_N.gcode` 并通过 ZIP 完整性检查，普通模型 `.3mf` 或 STL 不可直接发送。

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[send]"
.\.venv\Scripts\monkeyfab.exe send "D:\prints\building-h2s.gcode.3mf" --host 192.168.1.50 --dry-run --json
# PowerShell 7：访问码只放入当前进程环境，不写入配置或命令参数
$env:BAMBU_ACCESS_CODE = Read-Host "打印机 LAN 访问码" -MaskInput
.\.venv\Scripts\monkeyfab.exe send "D:\prints\building-h2s.gcode.3mf" --host 192.168.1.50 --json
Remove-Item Env:BAMBU_ACCESS_CODE
```

`--dry-run` 只检查本地文件，无需依赖包、访问码或网络连接。多台机器可用 `--access-code-env H2S_ACCESS_CODE` 等分别指定环境变量。默认保留文件名；机器上已有同名文件时停止，可用 `--remote-name building-h2s-v2.gcode.3mf` 指定新名称。网络超时或上传未获确认时返回非零退出码，并提示机器上可能留有文件，不自动重试。

传输复用 [BambuTools 的 bambulabs_api 2.6.6](https://pypi.org/project/bambulabs-api/2.6.6/) 隐式 FTPS 实现，使用 TCP 990、被动数据连接及打印机的局域网访问码。连接采用打印机自签名证书，只在可信局域网使用。当前机器和固件必须允许此局域网接口；MonkeyFab 不切换设备联网模式，也不使用云账号或 Bambu Studio 会话。此实现已通过模拟传输和本地 TLS 传输检查，X1C/H2S 实机发送仍待实际任务验证。

## 复跑一个几何样件

在外部空工作目录创建一个 52 × 28 × 8 m 的测试长方体，按 1:100 生成 520 × 280 × 80 mm 模型：

```powershell
# 在 MonkeyFab 虚拟环境已激活、且当前为外部空工作目录时运行
python -c "import trimesh; trimesh.creation.box(extents=[52,28,8]).export('demo.stl')"
monkeyfab prepare demo.stl --input-unit m --scale 1:100 --printer x1c --output x1c-parts
monkeyfab prepare demo.stl --input-unit m --scale 1:100 --printer h2d --output h2d-parts
```

默认参数下，X1C 输出 6 件，H2D 共同区域输出 2 件。它是几何示例；实际建筑模型与机器打印尚需各自试用。

在源码目录运行行为检查：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
```

## Hub 接入与源码归属

Fab 在 `apps/monkeyfab/` 维护加工算法、CLI、打印参数与行为检查；Hub 的 `?view=fab` 页面通过现有 `/api/fab/profiles`、`prepare` 和 `send` 接口调用同一份 CLI，无需独立服务。桌面和网页包从同一个 Hub 提交构建，默认包含 Fab 及其 `send` 依赖。

此次合仓导入原 MonkeyFab 提交 `6128f99c8fbcc5db41539a225386a6495fa6c5ab` 的现有实现，保留模块边界。现有 CAD、出图与 Hub HTTP owner 不承担制造算法；`monkeyfab` 在 Hub 的 module registry 中登记已有职责，不引入第二套实现。

真实输入和加工结果仍位于调用方明确指定的目录，源文件不变。Fab 不建立项目状态；Stage 工件回写、实机发送、板材排料、激光／CNC 输出仍需后续工作。

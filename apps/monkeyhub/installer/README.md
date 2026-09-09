# MonkeyHub Windows 候选包

本包面向 Windows 10/11 x64，包含独立 Python 3.13.15 运行时、API 与几何依赖，以及已构建的前端。
使用者无需安装 Python、Node.js 或运行 npm。包内 `source-version.txt` 是本候选对应的完整源码提交。
内置运行时来自 [Python 官方 Windows embeddable package](https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip)，
构建器核对固定 SHA-256；包内 `build-info.json` 保存来源与版本。

## 第一次安装

1. 完整解压候选 ZIP，勿在压缩包预览中直接运行。
2. 双击 `INSTALL_MONKEYHUB.cmd`。默认复制到 `%LOCALAPPDATA%\MonkeyHub\versions\<源码版本>`，不需要管理员权限。
3. 从安装器显示的目录打开 `OPEN_MONKEYHUB.cmd`。Hub 可在没有项目时打开；MonkeyArch 和 MonkeyDiagram
   通过已选真实项目共用 Studio 服务，MonkeyMonitor 可独立启动。
4. 如需桌面入口，由使用者对安装目录内的 `OPEN_MONKEYHUB.cmd` 创建快捷方式。

安装目录也可在 PowerShell 中明确指定，例如：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File '.\apps\monkeyhub\installer\install.ps1' -InstallDirectory 'E:\我的应用\MonkeyHub 候选'
```

再次安装同一版本会返回原安装目录。目标存在其他文件时，安装器拒绝覆盖，请选择新目录。
安装器不改系统 Python、PATH、桌面或已有项目。不同源码版本各自安装；不自动更新或迁移项目。

## 使用与退出

Hub 的启动和退出统一由包内 `apps/monkeyhub/launch-hub.ps1` 与 `run.py` 负责。
重复打开和应用按钮沿同一服务管理入口执行。正常退出时，在系统托盘图标中选择 `Quit MonkeyHub`；
启动器会等待已接收的任务结束并关闭所属服务。关闭浏览器页不会退出服务，下次打开同一安装目录即可。
已有用户偏好继续由 `%APPDATA%\MonkeyArch\settings.json` 管理，安装器不复制或替换该文件。

模型服务凭据、Codex CLI 和 Rhino 不随本包分发。无需外部模型服务即可打开 Hub、Monitor 及已有设计资料；
使用具体模型提供者或 Rhino 兼容导出时，按既有设置配置使用者自己的工具与账号。
普通 OCCT 几何功能使用包内运行时，不要求 Rhino。

## 开发者构建

安装流程是本轮新增的分发工作；仓库原有启动器继续负责进程，项目存储继续由 ArchFlow 管理。
`tools/package_monkeyapps.py` 只从指定 Git 提交导出白名单源码、构建 Web 成品、安装完整 Windows wheels
并生成候选 ZIP。它不会把 working tree、runtime.json、凭据、用户项目或 node_modules 打入成品。
构建机需要 Git、Python 3.12+（含 pip）和 Node.js 24/npm；这些工具不是安装后的运行依赖。

```powershell
python tools/package_monkeyapps.py --source-ref <三条线集成后的完整提交> --staging-dir 'E:\MonkeyHubBuild\构建' --output-dir 'D:\MonkeyHub候选包'
```

`--staging-dir` 和 `--output-dir` 必须在源码工作区之外。缓存默认位于 staging 下，可用 `--cache-dir` 指定。
每次构建使用独立子目录，已有候选 ZIP 不会覆盖。打包前会实际导入包内 API、PDF、图像和 CAD 库，
并执行最小 OCCT/3DM 检查。`_runtime/requirements-lock.txt` 保存实际安装版本；原 wheel 许可及 metadata 保留。
额外的上游许可和来源见 `apps/monkeyhub/installer/third-party/`。

## 候选验收边界

候选包本身不代表已在第二台干净 Windows 机器或第二位使用者处验收。
交付时需随包给出实际完成的安装、中文/空格路径、重开、端口冲突、退出和数据保持检查结果，
并保留尚未完成的真实机器验收。此包没有自动更新、公开发布或用户项目迁移功能。

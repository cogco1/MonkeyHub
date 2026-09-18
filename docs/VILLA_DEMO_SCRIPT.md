# 圆厅别墅短 Demo：生成 → 修改 → 对比

目标：约 20 秒，16:9。看得出系统在做什么即可；生成、计算和加载等待直接剪掉。

## 拍摄脚本

| 时间 | 画面／操作 | 字幕 |
| --- | --- | --- |
| 0–5 秒 | 圆厅生成结果全景，完整显示入口楼梯。 | 基于已有资料生成圆厅别墅。 |
| 5–9 秒 | 显示楼梯加宽指令并点击「应用」。 | 楼梯加宽到 12 米。 |
| 9–17 秒 | 剪去计算等待，同视角切换新旧候选。 | 修改前 → 修改后。 |
| 17–20 秒 | 修改后的全景，结束。 | 在已有模型上继续设计。 |

不加长片头片尾，不展示 runner 日志、完整构件树或重启验证。正常跳切即可；不把剪辑后的片长当成实际生成耗时。

建议输入：

> 把入口楼梯加宽到 12 米，保持平台标高和台阶数量不变。

首个候选已生成：`villa-demo-20260905-a02`，源码为 `f454793dd3951cd5af392eaaf6950e3fb1ec6b0c`。参数 `parameter:stair_west_width` 当前为 10.71 米，修改目标为 12.00 米；组件 `exterior-stairs`，元素 `stair-west`。平台／门槛标高 3.57 米及原台阶数量保持不变。

## 实际怎么调用

1. **能力测试任务**整理输入，调用现有 `archflow.runtime.project_runner.run_project` 生成首个候选。不另写建筑编译器。
2. **主线任务**启动现有 Studio API 和 Web，把本次候选交给页面。
3. **汇报出图任务**录制真实页面：生成结果 → 修改 → 对比，输出带简短中文字幕的 WebM。

Studio 现有修改链是「提出改动」→「应用」→候选模型，可再选「从此版本继续」。首轮仍由现有 runner 真实生成；成片只展示结果，不必展示终端过程，也不为拍片开发新按钮。

### 本次服务

本次使用 `http://127.0.0.1:5175` 和 API 8001。用户已在两个 PowerShell 窗口启动；已验证页面代理的健康接口、A02 状态和 `format=3dm` 的 OCCT 预览均可读取。录制使用此页面，不使用仍运行的旧 5174／8000 服务。

### 隔离 API 与测试工作区命令

在两个 PowerShell 终端分别执行下面两段。已填入本次真实 reference run。演示使用独立的 8001／5175 端口；启动前确认未被占用，不结束已有服务。

API：

```powershell
# 下面的绝对路径是示例，按本机的源码、Python、npm 与工作区位置替换。
$SourceRoot = 'D:\Source\MonkeyHub'
$WorkspaceRoot = 'D:\MonkeyHubRuntime\workspace'
Set-Location -LiteralPath "$SourceRoot\apps\archflow-studio\api"
$env:PYTHONPATH = "$SourceRoot;$SourceRoot\apps\archflow-studio\api"
$env:ARCHFLOW_STUDIO_MODE = 'local'
$env:ARCHFLOW_STUDIO_CAD_EXPORT = 'occt'
$env:ARCHFLOW_STUDIO_REFERENCE_RUN = 'villa-demo-20260905-a02'
$env:ARCHFLOW_STUDIO_INTENT_PROVIDER = 'codex'
$env:ARCHFLOW_STUDIO_CODEX = 'C:\Program Files\nodejs\codex.cmd'
& 'C:\Program Files\Python312\python.exe' `
  -m archflow_studio_api.main --host 127.0.0.1 --port 8001 `
  --project-dir "$WorkspaceRoot\projects\villa-rotonda-reconstruction"
```

Web：

```powershell
Set-Location -LiteralPath "$SourceRoot\apps\monkeyhub\web"
$env:ARCHFLOW_STUDIO_API_URL = 'http://127.0.0.1:8001'
& 'C:\Program Files\nodejs\npx.cmd' vite --config workspaces/test/vite.config.ts --host 127.0.0.1 --port 5175 --strictPort
```

以上命令已由用户手动执行，不要重复启动。录制期间保持两个终端打开；演示结束后可分别 Ctrl+C 退出。

### 录屏与交付

现有录制任务用浏览器页面采集，加已有 VP8/WebM 编码器；先短试录确认能播放，再正式录。当前没有一键 `record-demo` 命令，不为本片另建录制平台。

交付约 20 秒的 `villa-demo-zh.webm`，保留原始录制素材。实际保存位置为本次新 run 的 `workspaces/demo-video`，通过现有项目写入接口保存，不在源码目录散放视频。

只需看清三件事：圆厅生成了、楼梯真的变宽了、前后差异明确。细部暂缺可标「演示候选」；等待直接剪掉，不用旧模型或补画的界面冒充本次生成。

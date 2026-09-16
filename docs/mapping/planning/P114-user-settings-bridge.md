# P114 — 启动器能读的用户设置

**状态:** active（2026-09-08，本地实现与验证已完成，随 P108 的完整源码版本交接收尾）。本机 API、启动器读取与现用设置面板已接通。
**方向:** [愿景](../../VISION.md);唯一 live 清单为 `governance/work_registry.json`。
**本次可交付范围:** 一个本机 API 能力、一处启动器读取、面板接线。不做远程多用户。

## 1. 先让什么问题消失

设置面板能改语言、主题、字号,但只存在浏览器 localStorage;启动器每次启动仍从 runtime.json 与环境变量取模型、
超时、codex 路径,用户在面板里改的东西不会跟着下次启动走。codex 正确拒绝了"浏览器静默写 %APPDATA%",
所以这项能力必须由本机 API 提供。

## 2. 修复前的核对

| 发现 | 动作与边界 |
| --- | --- |
| `StudioSettings` 已区分 local / remote,remote 要 token | 用户设置能力只在 local 模式声明与服务;remote 一律 404,不做半个多用户 |
| 写入点由 `governance/architecture_policy.json` 管 | 登记 API 的这一处写入,文件只有一个 owner(API 的 settings 模块),启动器只读 |
| 面板已有六类与来源徽章(codex) | 面板只多一个"保存到本机"的动作与来源徽章 `用户`,不改分类 |

## 3. 本次实现

1. API:`GET/PUT /api/settings/user`(language、theme、fontScale、intentModel 等模型默认值),文件
   `%APPDATA%\MonkeyArch\settings.json`,`/api/protocol` capabilities 加 `user-settings`;remote 模式不声明、请求 404。
2. 启动器:`launch-studio.ps1` 启动时读该文件,翻译成对应环境变量;文件缺失或坏了只告警不阻塞。
3. 面板:codex 把"保存"接到 PUT,来源徽章显示 `用户`。

## 4. 验收

- api 测试:local 模式读写往返;remote 模式 404 且 capabilities 不含 `user-settings`;坏文件返回 422 不崩。
- 启动器:写入一个设置后重启,进程环境变量与之一致(手工验证,记入回执)。
- archcheck(写入点已登记)、spine 与 api 套件通过;PROTOCOL.md 资源表加两条(provisional)。

**能交给用户试就停:** 改一次语言与模型默认值,重启后仍在。

## 5. 已完成的本地检查

六个可选设置共用 HTTP／文件校验；完整 PUT 支持省略或 null 清除。
面板保存前读取最新值并只合并本次改动，避免覆盖其他页面保存的默认值；坏文件可由明确保存操作替换。
保存的 provider／model／timeout 在下次启动生效，清除后恢复既有 runtime／环境优先级，当前编译器不会中途切换。

设置、协议与错误相关 57 项检查通过，包含临时 APPDATA／runtime 下的实际 PowerShell 与 Python 子进程读取。
前端保存与恢复回归通过；API 全套、核心测试、archcheck、主检出构建和 OpenAPI 客户端一致性检查通过。
现用设置面板已更新，验证没有改动用户真实偏好文件、项目输入或凭据。

> 2026-09-15（#126）：独立启动器及其 `runtime.json` 桥已退役。同样三个偏好（intentProvider / intentModel /
> intentTimeoutS）由 MonkeyHub 读取 `%APPDATA%\MonkeyArch\settings.json` 并在启动 Studio 子进程时注入环境
> （`apps/monkeyhub/api/monkeyhub_api/applications.py`），验收见 `apps/monkeyhub/api/tests/test_applications.py`。

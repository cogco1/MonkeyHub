# P114 — 启动器能读的用户设置

**状态:** ready(前置已齐:`/api/protocol` 能力表、local/remote 模式、codex 的设置面板已落地)
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

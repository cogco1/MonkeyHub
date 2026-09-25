# MonkeyHub Windows 候选包

本包面向 Windows 10/11 x64，包含独立 Python 3.13.15 运行时、API 与几何依赖，以及已构建的前端。
使用者无需安装 Python、Node.js 或运行 npm。包内 `source-version.txt` 是 ArchFlow／Hub 的完整源码提交。
内置运行时来自 [Python 官方 Windows embeddable package](https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip)，
构建器核对固定 SHA-256；包内 `build-info.json` 保存来源与版本。MonkeyFab 源码位于同仓的
`apps/monkeyfab/`，默认随桌面与网页包交付，共用同一套内置 Python 和同一个 Hub 源码提交。

## 第一次安装

1. 完整解压候选 ZIP，勿在压缩包预览中直接运行。
2. 双击 `INSTALL_MONKEYHUB.cmd`。默认复制到 `%LOCALAPPDATA%\MonkeyHub\versions\<源码版本>`，不需要管理员权限；
   版本目录使用 Hub 提交前缀，桌面包另带 `-desktop` 后缀。
3. 安装完成后，选择是否创建桌面快捷方式、是否立即打开 MonkeyHub。按 Enter 接受显示的选项，输入 `n` 跳过。
4. 桌面包创建指向 `MonkeyHub.exe` 的 `MonkeyHub` 快捷方式，将已识别的旧 `MonkeyHub` 网页入口更新为应用入口，并移除已识别的旧 `MonkeyArch` 快捷方式。无关快捷方式保留。纯网页包创建 `MonkeyHub` 网页快捷方式；也可运行安装目录中的 `OPEN_MONKEYHUB.cmd`。
   Hub 可在没有项目时打开；MonkeyArch、MonkeyDiagram 和 MonkeyBoard 通过已选真实项目共用 Studio 服务，MonkeyMonitor 可独立启动。

安装目录也可在 PowerShell 中明确指定，例如：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File '.\apps\monkeyhub\installer\install.ps1' -InstallDirectory '<安装目录>'
```

再次安装同一源码版本会返回原安装目录。提交不同或目标存在其他文件时，安装器拒绝覆盖，请选择新目录。
安装器按所选项创建快捷方式和打开应用，不改系统 Python、PATH 或已有项目。不同源码版本各自安装；安装器本身不更新已有版本、不迁移项目，已安装的桌面版按下文「自动更新」获取新版本。
直接运行 `install.ps1` 默认只安装；可用 `-Interactive` 显示完成选项，或明确指定 `-CreateDesktopShortcut`、`-OpenHub`。
隔离安装检查使用 `-CreateDesktopShortcut -DesktopDirectory '<临时桌面目录>'`，不传 `-Interactive` 和 `-OpenHub`，即可验证快捷方式而不启动应用。

安装器按 Windows 传统路径长度限制检查解压、安装与临时目录，无需开启 `LongPathsEnabled`。
如提示路径过长，请将 ZIP 解压到较短目录，或指定较短的安装目录。额外许可在包内采用较短文件名，
许可目录的 README 保留原始路径说明、来源链接及对应文本链接。

## 使用与退出

选择项目后，启动并打开 MonkeyBoard。一人编辑画布、在会议中共享这个窗口即可：
PDF／PNG／JPG 可上传、拖放或粘贴，PDF 可选页；新出的 MonkeyDiagram 图纸每约 5 秒自动接收。
图框、文字与箭头自动保存；图纸卡片可打开原始页面及其精确出图版本。
选中一张图纸或图框及其中的圈线，点击「提交设计意见」，填写修改要求后发送到设计。
意见会连同原图、圈线和准确模型版本进入 MonkeyArch 的提案、澄清与候选审阅流程；
原图页已有批注保留。同一白板标记再次发送会更新该标记；上传图纸需先在 MonkeyDiagram 关联模型。
目前支持圈、矩形、折线、普通箭头和手绘线；文字请填写到意见框，裁剪图及特殊线型需先还原。
字体随包提供，白板不需要连接外部字体服务。Zoom／腾讯会议的实时语音接入尚未配置。

Hub 的启动和退出统一由包内 `apps/monkeyhub/launch-hub.ps1` 与 `run.py` 负责。
重复打开和应用按钮沿同一服务管理入口执行。正常退出时，在系统托盘图标中选择 `Quit MonkeyHub`；
启动器会等待已接收的任务结束并关闭所属服务。关闭浏览器页不会退出服务，可从托盘的 `Open MonkeyHub`
重新打开页面；退出服务后再双击桌面入口。服务仍运行时重复启动会提示使用已有托盘，不会再启动一份。
已有用户偏好继续由 `%APPDATA%\MonkeyArch\settings.json` 管理，安装器不复制或替换该文件。

模型服务凭据、Codex CLI 和 Rhino 不随本包分发。无需外部模型服务即可打开 Hub、Monitor 及已有设计资料；
使用具体模型提供者或 Rhino 兼容导出时，按既有设置配置使用者自己的工具与账号。
普通 OCCT 几何功能使用包内运行时，不要求 Rhino。

包内 MonkeyFab 可准备封闭 STL／OBJ 的打印分件，并校验、发送已切片的 `.gcode.3mf`。
输出留在使用者明确选择的目录；Bambu Studio 切片和真实打印仍使用自己的机器与工具。
也可在安装目录直接运行命令行：

```powershell
.\_runtime\python\python.exe -m monkeyfab profiles --json
```

## 自动更新（未签名预发布通道）

已安装的桌面版默认开启「自动更新」。它和手动下载 GitHub 预发布版的信任等级相同：**未签名预发布通道**。

- **检查**：启动约 30 秒后检查一次，之后每 6 小时一次。Hub 不带任何凭据，通过 HTTPS 读取 cogco1/MonkeyHub 的公开发行列表
  （带 ETag，列表未变化时不计入 GitHub 匿名频率限制），选出版本号高于当前版本的最新桌面预发布版。
- **下载**：每个发行除完整候选 ZIP 外，还附带 `MonkeyHub-<新版本>-update-index.json`，以及最近三个已发布版本
  各自到新版本的 `MonkeyHub-<新版本>-from-<旧版本>.patch.zip`。Hub 只下载「起点提交等于当前安装提交」的那一个补丁，
  放入 `<运行根目录>\updates\incoming-*`（上限 256 MiB），核对索引中的 SHA-256，再核对补丁重建出的
  `build-info.json` 正是该发行 ReleaseManifest@1 绑定的那一份；之后走与本地补丁完全相同的准备路径：
  在当前版本旁边暂存新版本并逐文件校验，然后用新版本自带的 Python 运行一次 `run.py --help` 作为预检。
- **需要完整更新**：没有从当前版本出发的补丁时（例如已落后三个以上版本，或本机装的是本地构建），
  设置页显示「需要完整更新」和发布页地址，不猜测补丁，也不自动下载完整包。
- **生效**：准备好的更新不会中途重启正在进行的工作。正常退出 MonkeyHub 后，Hub 再核对一次暂存版本的文件表、
  大小与身份文件，然后运行新版本自带的 `install.ps1 -ActivateInstalled -CreateDesktopShortcut`，把桌面快捷方式
  切到新版本；下次启动即是新版。新版本启动后自己完成这次更新：先通过自身健康检查，再完整校验全部文件并确认入口，
  之后记为完成。任一步失败，它会把桌面快捷方式恢复到旧版本（旧版本目录一直保留），下次启动回到旧版本，
  自动检查也不会再安装同一版本（「立即检查」可以重试）。整个过程不会留下「更新中、写操作被拒绝」却无人完成的状态。
- **立即切换**：「重启并更新」仍然可用，沿用原有的桌面交接与启动回滚。

状态保存在 `<运行根目录>\updates\`：`state.json` 是更新事务，`check.json` 是上次检查结果和缓存的发行列表。

**信任等级**：HTTPS、SHA-256 与 ReleaseManifest@1 只能发现下载损坏或内容不一致，不能证明发行者身份；
能替换 GitHub 发行资产的人同样能替换索引和补丁。发行者签名验证仍属 Issue #58，将来接入同一检查流程。

**关闭**：在「设置 → 软件更新」关闭「自动更新」开关，保存为 `%APPDATA%\MonkeyArch\settings.json` 中的
`"autoUpdate": false`。关闭后不再后台检查，退出时也不切换版本；仍可点「立即检查」，
但准备好的更新只在点击「重启并更新」时生效。

注意：

- 只切换桌面快捷方式。固定在任务栏或开始菜单、直接指向旧版本 `MonkeyHub.exe` 的入口仍会打开旧版本，请改用桌面快捷方式或重新固定。
- 新版本若启动后未能完成检查，从 `versions` 中旧版本目录直接运行 `MonkeyHub.exe`：旧版本发现新版本启动过却没有完成，
  会自动恢复桌面快捷方式；否则在「设置 → 软件更新」点「重启并更新」，失败时原有交接会恢复旧版本入口。
- 每个版本在 `versions` 下占用一个完整目录（约 0.9 GB）。旧版本不会被自动删除，由使用者自行清理。
- 第一次使用自动更新前，需要先按现有方式装上包含此功能的版本。

## 开发者构建

已安装新版桌面客户端后，在「设置 → 软件更新」选择匹配当前版本的本地补丁 ZIP。
准备完成后点击「重启并更新」；未发送的草稿、附件或未同步模型需要先处理，运行任务需要先结束。
新版启动验证失败会恢复旧版。旧客户端第一次需要安装含此更新入口的完整版本。

### 本地补丁更新

两个已经构建完成的桌面版本可以生成文件级补丁，不必重新下载相同的 Python、Node 和几何依赖：

```powershell
python tools/package_monkeyapps.py --patch-from '<完整旧版本目录>' --patch-to '<完整新版本目录>' --patch-output '<外部输出目录>\MonkeyHub.patch.zip'
```

输入必须包含完整提交号、对应的 `build-info.json` 和桌面主程序。补丁 ZIP 保存完整旧／新文件表、
每个文件的字节数与 SHA-256、新增或修改文件的内容，以及删除列表。运行后产生的 `__pycache__`、
`.pyc` 不进入分发表，也不复制到新版本。输出明确标为 `local-developer-unsigned`；校验一致性不能证明
发行者身份，此入口只供明确选择的本地开发补丁使用，不是自动网络更新或签名发布的替代路径。

`apps/monkeyhub/installer/patch.py` 的 `describe_patch(path)` 仅读取容器与清单摘要；
`inspect_patch(path, base_root)` 校验完整旧版本与补丁内容；`stage_patch(path, base_root, destination_parent)`
在明确提供的版本父目录中复制并重建 `<新提交前 12 位>-desktop`，全部目标文件校验通过后才就位。
未变化文件通过独立复制复用，不硬链接或覆盖运行中的旧文件，因此仍需完整新版本的磁盘空间。
目标已经存在时，只有逐文件字节数、SHA-256 和完整版本身份均与补丁完全一致才复用，不重新复制或覆盖；
这允许暂存完成但更新状态保存失败后，重新选择同一补丁继续。未知、不完整、被修改或链接目录均拒绝，原目录保留。
越界路径、同名／大小写冲突、链接／reparse point、旧文件被改动、
缺失或损坏的内容都会拒绝更新；失败只清理本次创建的临时目录，旧版本和用户文件保留。
`verify_target(path, target_root)` 用同一清单重新校验已暂存版本，供重启前及新应用健康检查后使用。

版本切换与任务结束后的重启由 Hub 负责。新主程序通过健康检查后，可在该版本自己的安装脚本上调用
`-ActivateInstalled -CreateDesktopShortcut`，复用已有快捷方式逻辑，不再次复制安装包或打开第二个窗口。
该显式模式只接受 `versions/<准确版本>` 目录；普通安装仍拒绝把安装目录指向自身。
若同时指定 `-RequireSignedRelease`，原有完整签名、清单、归档与文件校验仍必须全部通过。

### 完整构建

安装流程是本轮新增的分发工作；仓库原有启动器继续负责进程，项目存储继续由 ArchFlow 管理。
`tools/package_monkeyapps.py` 只从指定 Git 提交导出白名单源码、构建 Web 成品、安装完整 Windows wheels
并生成候选 ZIP。它不会把 working tree、凭据、用户项目或 node_modules 打入成品；用户运行配置只保存在 Hub 运行根目录的 `config/` 下，从不进入源码树。
构建机需要 Git、Python 3.12+（含 pip）和 Node.js 24/npm；这些工具不是安装后的运行依赖。

```powershell
python tools/package_monkeyapps.py --source-ref <三条线集成后的完整提交> --staging-dir 'D:\ExampleRuntime\temp\package' --output-dir 'D:\ExampleRuntime\packages'
```

构建器从同一个 Hub 提交收集 `apps/monkeyfab/`，默认安装其基础与 `send` 依赖，
不再接受外部 Fab 仓库或第二个版本参数。Fab 行为检查随 Hub CI 运行。
`--staging-dir`、`--output-dir` 和缓存必须在源码工作区之外；配置开发根目录后可直接复用默认路径。
每次构建使用独立子目录，已有候选 ZIP 不会覆盖。打包前会实际导入包内 API、PDF、图像和 CAD 库，
并执行最小 OCCT/3DM 检查。整合包还用内置 Python 检查 H2S 参数、实际拆件与本地发送 dry-run，
样件和结果留在外部临时目录，不连接打印机。`_runtime/requirements-lock.txt` 保存实际安装版本；原 wheel 许可及 metadata 保留。
前端生产依赖的原始 LICENSE 也随构建保留。额外的上游许可和来源见 `apps/monkeyhub/installer/third-party/`。

## 发行证据与校验

每次构建在输出目录里除候选 ZIP 和 `.sha256` 外，另外生成两份证据文件：

- `<候选名>.cyclonedx.json`：CycloneDX 1.6 软件物料清单（SBOM），同一份也随包放在 ZIP 内的
  `sbom.cyclonedx.json`。内容从装配完成的包实际读取，不是手写清单。每个构件用两种属性之一
  说明它与本包的关系，两者强度不同：
  - `monkeyhub:shippedIn`：该构件以文件形式在包内的位置。包括内置 Python 的 site-packages
    实际安装的发行包、包内 `apps/monkeyhub/node_modules`、内置 Python/Node 运行时，
    以及 `MonkeyHub.exe` 本身。
  - `monkeyhub:buildInput`：某个 lock 文件为指定构建产物钉住的依赖，**不断言该构件确实进入
    了那个产物**。前端生产依赖由打包器决定哪些真正进入 `dist`；一份 `Cargo.lock` 覆盖所有
    目标平台、feature 和 build script。把它们写成「已编译进二进制」是没有证据的说法。

  文档本身不含时间戳和序列号，因此同一份实际清单每次生成的字节一致。这不等于「同一个源码提交
  必然产出同样的包」：打包的部分 Python 依赖是版本区间（如 `fastapi>=0.141,<1`、
  `Pillow>=12.3,<13`），由构建时的 pip 解析；Node 版本来自构建机。正因如此，SBOM 读的是
  实际安装树，而不是依赖声明。
- `<候选名>-candidate.zip.release-manifest.json`：`ReleaseManifest@1`。它是包内
  `build-info.json` 的派生视图，不另立一套版本来源：发行版本、通道、目标平台、源码提交、
  Python/Node/ACP/桌面版本全部从 `build-info.json` 读出。清单列出本次分发每个文件的
  `{path, size, sha256}` 构成封闭集合，并记录 `build-info.json` 与 SBOM 在 ZIP 内的路径
  和摘要，供校验实际打开压缩包核对。

校验已下载的发行目录：

```powershell
python tools/package_monkeyapps.py --verify '<候选名>-candidate.zip.release-manifest.json'
```

校验做三件事，逐条报错并以非零退出：分发文件的大小与 SHA-256 必须与封闭表一致；本次发行前缀下
不得出现未列入清单的文件；打开 ZIP 读出其中的 `build-info.json` 与 `sbom.cyclonedx.json`，
核对清单声明绑定的摘要，同时要求随包 SBOM 与外置 SBOM 摘要一致。清单自相矛盾（表内摘要与
`sbom` 块不一致）、路径越界或重复列项都会被拒绝，压缩包损坏时报错而不是抛异常。
构建结束时构建器本身也跑一次同样的校验。

SBOM 可用官方工具独立验证，例如 `cyclonedx-cli validate --input-file <文件>`；
仓库测试在设置 `CYCLONEDX_SCHEMA` 指向官方 `bom-1.6.schema.json` 时会用该 schema 校验生成结果。

**校验和不是签名。** 当前所有构建的 `trust.status` 都是 `candidate-unsigned`，清单里明确写出
`"signed": false`。校验通过只证明手里的文件与**这份清单**一致，不能证明来源：能替换 ZIP 的人
同样能替换旁边的清单。只有当清单本身来自你已经信任的渠道时，校验才有意义。

## 已签名安装（需要自备发行者证书）

安装器本身已经实现了「验证失败即终止安装」这条路径，但**本仓库不持有任何发行者私钥，
构建器也不签名**。下面的命令只有在你自己（或你信任的发行者）用某个证书对清单做了分离签名，
并且你已经通过可信渠道拿到该证书指纹时才有意义。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File '.\apps\monkeyhub\installer\install.ps1' `
  -RequireSignedRelease `
  -ReleaseManifest '<候选名>-candidate.zip.release-manifest.json' `
  -ReleaseSignature '<候选名>-candidate.zip.release-manifest.json.p7s' `
  -ReleaseArchive '<候选名>-candidate.zip' `
  -ExpectedPublisherThumbprint '<发行者证书指纹>'
```

签名是分离式 CMS/PKCS#7（`.p7s`），覆盖清单文件的**原始字节**。安装器在复制任何文件、创建任何
快捷方式、启动应用或让某个版本成为当前版本**之前**，按顺序核对一条完整链，任一环不符即拒绝退出：

1. 用钉住的证书指纹验证清单字节上的签名，且签名者必须恰好只有一个——多签名者直接拒绝，
   否则「钉住某个发行者」就失去意义。指纹只接受 64 位十六进制（SHA-256，推荐）或
   40 位（SHA-1，即 Windows 证书对话框显示的 Thumbprint）；长度不对或含非十六进制字符直接拒绝。
   两种指纹里 SHA-1 那种本身更弱，能选就选 SHA-256。
   指纹按证书 DER 字节自行计算，PowerShell 7 与 Windows PowerShell 5.1 行为一致。
   签名内的摘要算法同样被钉住，只接受 SHA-256/384/512；SHA-1 等弱摘要一律拒绝。
2. 清单必须是 `ReleaseManifest@1`，且其封闭构件表里列出的正是手上这个 ZIP：文件名、字节数、SHA-256 全部相符。
3. 清单的 `release.sourceCommit` 必须等于包内 `source-version.txt`，`buildInfo.sha256` 必须等于包内
   `build-info.json` 的摘要——一份签名有效但描述别的发行的清单不构成授权。
4. 解压出来的每一个文件都必须与该 ZIP 中同名成员逐字节一致，且不得多出或缺少文件；
   目录联接／符号链接（reparse point）一律拒绝——它们不会出现在文件枚举里，却会被递归复制带进安装。
   这一步把「清单封闭到 ZIP」延伸到「ZIP 封闭到即将安装的整棵目录树」，因此需要保留原始 ZIP。
   ZIP 只打开一次且不共享写入权限：算给清单核对的那份字节，与后面每次比对读到的字节是同一份，
   不会出现「校验完哈希之后、逐个成员比对之前被换掉」的窗口。
5. **真正落地的那棵树再核一遍**：新装时核对已复制完成的暂存目录（在它成为当前版本之前），
   重复安装同一版本时核对目标目录里已有的那份安装。只核对解压包是不够的——
   解压目录在校验之后仍可写，已有安装也可能在上次安装之后被改过，而快捷方式与启动指向的是它们。

`-RequireTrustedPublisherChain` 额外要求平台为签名者构建可信证书链（自签名证书会因此被拒绝）。
**安装器自身不做、也不声称做吊销检查**；证据 JSON 里 `revocationCheck` 恒为 `not-demonstrated`。
加了 `-RequireTrustedPublisherChain` 时链由平台按它自己的默认策略构建，Windows 可能因此访问
CRL／OCSP 端点；那是平台行为，这里既没有配置也不依赖它，更不能当作「已检查吊销」的凭据。
验证通过后打印 `ReleaseSignatureEvidence@1`，其中 `manifestTrustStatus` 仍会如实显示清单自称的
`candidate-unsigned`：签名证明的是「这份清单出自被钉住的发行者」，不是「这个构建是受支持的正式发行」。

只想在解压前检查一份下载到的清单，用 `-VerifyReleaseManifest`：它只验签名并打印证据，不绑定任何包、
不安装任何东西（证据里 `archiveSha256` 为 `null`、`boundPackageFiles` 为 `0`）。

不带 `-RequireSignedRelease` 的普通安装保持原样，并在结尾明确打印 `Trust: candidate-unsigned`。
一旦传了 `-RequireSignedRelease`，缺参数、文件不存在、指纹格式错误都会拒绝退出，**绝不回退成未签名安装**。

尚未解决的部分：没有可发布的发行者证书与签名密钥、发布流程未接入签名、没有吊销，也没有经签名验证的更新通道（现有自动更新走未签名预发布通道）；
也**没有回滚保护**——一份真实签名过的旧发行（连同它自己的 ZIP）仍会被完整接受，安装器不比较版本新旧、
不检查签名时间。这些属于 Issue #58 其余部分。安全报告途径见随包的 `SECURITY.md`，
或仓库根目录的 [SECURITY.md](https://github.com/cogco1/MonkeyHub/blob/main/SECURITY.md)：
目前没有私密上报渠道，也没有安全响应承诺。

## 候选验收边界

候选包本身不代表已在第二台干净 Windows 机器或第二位使用者处验收。
交付时需随包给出实际完成的安装、中文/空格路径、重开、端口冲突、退出和数据保持检查结果，
并保留尚未完成的真实机器验收。此包的自动更新只走未签名预发布通道；没有正式签名发布或用户项目迁移功能。

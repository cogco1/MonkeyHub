import { appearanceFromSearch, applyAppearance } from "/shared/appearance.js";
import { translateMessage } from "/shared/i18n.js";

const appearance = appearanceFromSearch(window.location.search);
applyAppearance(appearance);
const locale = appearance.language;
const english = {
  "缓存输入": "Cached input",
  "等待时间": "Wait time",
  "等待时间 · 请求往返 P50": "Wait time · request P50",
  "请求往返": "Request round trip",
  "中位数": "Median",
  "已知小计": "Known subtotal",
  "{recorded}/{total} 次已记录": "{recorded}/{total} calls recorded",
  "总输入（含缓存）：{total}": "Total input (incl. cache): {total}",
  "命中率基于 {count} 次完整记录": "Cache rate based on {count} complete records",
  "缓存输入：{recorded} 次已记录，{missing} 次未知。": "Cached input: {recorded} recorded, {missing} unknown.",
  "缓存输入属于总输入；未缓存输入 = 总输入 − 缓存输入，含新写入缓存。输出含推理。等待时间只比较已记录的请求往返，不含工具执行或人工停留。": "Cached input is part of total input. Uncached input = total minus cached input, including cache writes. Output includes reasoning. Wait time compares recorded request round trips, not tool execution or human dwell.",
  "MonkeyMonitor · 用量": "MonkeyMonitor · Usage",
  "跳到调用列表": "Skip to calls",
  "正在连接": "Connecting",
  "刷新": "Refresh",
  "费用估算": "Cost estimate",
  "调用用量": "Call usage",
  "用量来源": "Usage source",
  "全部": "All",
  "未缓存输入": "Uncached input",
  "输出": "Output",
  "耗时中位数": "Median duration",
  "记录说明": "About these records",
  "调用列表": "Calls",
  "排序": "Sort",
  "总输入": "Total input",
  "耗时": "Duration",
  "最近": "Latest",
  "暂无调用记录": "No calls yet",
  "接入的用量会显示在这里。": "Recorded usage will appear here.",
  "再显示 10 条": "Show 10 more",
  "按模型汇总": "By model",
  "模型用量汇总": "Usage by model",
  "模型": "Model",
  "次数": "Calls",
  "等待首次读取": "Waiting for first update",
  "页面可见时每 30 秒刷新": "Refreshes every 30 seconds while visible",
  "关闭费用估算": "Close cost estimate",
  "关闭": "Close",
  "费率": "Rates",
  "请选择，或填写自定义单价": "Select rates or enter your own",
  "修改用量和单价": "Edit usage and rates",
  "Token 用量": "Token usage",
  "未知项目留空，确认没有发生的项目填 0。": "Leave unknown values blank. Enter 0 only when you know there was no usage.",
  "总输出": "Total output",
  "缓存读取": "Cache read",
  "缓存写入总量": "Total cache write",
  "其中：1 小时写入": "Of which: 1-hour write",
  "其中：推理输出": "Of which: reasoning output",
  "单价 · 美元 / 百万 Token": "Rates · USD per million tokens",
  "普通输入": "Regular input",
  "普通缓存写入": "Regular cache write",
  "1 小时缓存写入": "1-hour cache write",
  "输出（含推理）": "Output (incl. reasoning)",
  "未知": "Unknown",
  "估算费用": "Estimated cost",
  "计算依据": "Calculation details",
  "填写用量并选择费率后计算。": "Enter usage and select rates to calculate.",
  "请确认模型、上下文长度和服务档位。": "Check the model, context length and service tier.",
  "总输入包含缓存读取和写入，推理输出包含在总输出中，不重复计费。普通缓存写入中，Anthropic 可填写 5 分钟档位。": "Total input includes cache reads and writes. Total output includes reasoning; neither is charged twice. For Anthropic, use the 5-minute tier for regular cache writes.",
  "订阅用量按单价换算的费用等值，不代表实际扣费。": "For subscription usage, this is a rate-based equivalent, not an actual charge.",
  "已完成": "Completed",
  "成功": "Succeeded",
  "失败": "Failed",
  "已取消": "Cancelled",
  "进行中": "Running",
  "已记录": "Recorded",
  "已观测": "Observed",
  "已编译": "Compiled",
  "待补充": "Needs input",
  "不支持": "Unsupported",
  "仅末次用量": "Last usage only",
  "历史不完整": "Partial history",
  "计数不连续": "Counter discontinuity",
  "计数重置未知": "Unknown counter reset",
  "API 用量": "API usage",
  "订阅用量 · 仅等值估算": "Subscription usage · equivalent estimate only",
  "计费方式未记录": "Billing mode not recorded",
  "普通输入用量": "regular input usage",
  "缓存读取用量": "cache read usage",
  "普通缓存写入用量": "regular cache write usage",
  "1 小时缓存写入用量": "1-hour cache write usage",
  "输出用量": "output usage",
  "普通输入单价": "regular input rate",
  "缓存读取单价": "cache read rate",
  "普通缓存写入单价": "regular cache write rate",
  "1 小时缓存写入单价": "1-hour cache write rate",
  "输出单价": "output rate",
  "未记录": "Not recorded",
  "模型未记录": "Model not recorded",
  "时间": "Time",
  "时间未知": "Unknown time",
  "查看 / 估算": "View / estimate",
  "输入中 {percent}% 命中缓存": "{percent}% of input was cached",
  "部分记录不完整": "Some records are incomplete",
  "来源归属未核实": "Source attribution unverified",
  "未缓存输入：{inputRecorded} 次已记录，{inputMissing} 次未知；输出：{outputRecorded} 次已记录，{outputMissing} 次未知。": "Uncached input: {inputRecorded} recorded, {inputMissing} unknown. Output: {outputRecorded} recorded, {outputMissing} unknown.",
  "未缓存输入为总输入减去缓存读取，包含新写入缓存的输入。只在两项均已知时计算；此排序用于比较用量，不代表费用排名。": "Uncached input is total input minus cache reads, including newly written cache input. It is calculated only when both values are known. This order compares usage, not cost.",
  "缓存比例使用输入和缓存读取同时已知的 {recorded} 次调用，{missing} 次缺失。缓存读取仍可能计费。": "The cache ratio uses {recorded} calls with both input and cache reads known; {missing} calls are missing those values. Cache reads may still be charged.",
  "耗时：{recorded} 次已记录，{missing} 次未知。": "Duration: {recorded} recorded, {missing} unknown.",
  "尚无耗时记录。": "No duration recorded yet.",
  "{count} 条记录仅保留部分历史或计数不连续，不能用于完整账单核对。": "{count} records contain partial history or counter discontinuities and cannot verify a complete bill.",
  "读取时有 {count} 条提示。": "Reading produced {count} notices.",
  "{count} 次未知": "{count} unknown",
  "查看 / 估算：{model}": "View / estimate: {model}",
  "显示 {shown} / {total} 条": "Showing {shown} / {total}",
  "请自行选择与模型、上下文长度及服务档位匹配的费率。": "Choose rates that match the model, context length and service tier.",
  "更新于 {time}": "Updated at {time}",
  "本地服务在线": "Local service online",
  "连接未确认": "Connection unconfirmed",
  "刷新失败，仍显示上次成功读取的数据。请稍后重试。": "Refresh failed. The last loaded data is still shown. Try again later.",
  "暂时无法读取用量。确认本地服务已启动后点击刷新。": "Usage could not be loaded. Check that the local service is running, then refresh.",
  "参考费率暂不可用，可手动填写单价；刷新后会重试读取。": "Reference rates are unavailable. Enter rates manually or refresh to retry.",
  "填写用量并选择费率后计算，未知项目保留为空。": "Enter usage and select rates to calculate. Leave unknown values blank.",
  "手动填写用量，并选择或填写单价。": "Enter usage manually, then select or enter rates.",
  "用量已修改": "Usage edited",
  "缓存写入": "Cache write",
  "日期未记录": "Date not recorded",
  "费率来源": "Rate source",
  "来源未记录": "Source not recorded",
  "。仅应用所选费率，不自动判断上下文长度或服务档位。": ". Only the selected rates apply; context length and service tier are not detected automatically.",
  "Token 数量请填写非负整数；未知项目保留空白。": "Enter non-negative whole numbers for tokens. Leave unknown values blank.",
  "总输入必须包含缓存读取与缓存写入，不能小于两者之和。": "Total input must include cache reads and writes and cannot be less than their sum.",
  "1 小时缓存写入不能超过缓存写入总量。": "1-hour cache writes cannot exceed total cache writes.",
  "推理输出不能超过总输出。": "Reasoning output cannot exceed total output.",
  "单价请填写非负十进制数，单位为美元 / 百万 Token；未知单价保留空白。": "Enter non-negative decimal rates in USD per million tokens. Leave unknown rates blank.",
  "正在计算": "Calculating",
  "正在按各项用量与单价计算。": "Calculating from each usage component and its rate.",
  "费用尚不完整": "Cost is incomplete",
  "其他用量或单价": "other usage or rates",
  "还缺少 {count} 项": "{count} items missing",
  "已知部分：${subtotal} USD。仍缺少：{missing}。": "Known subtotal: ${subtotal} USD. Still missing: {missing}.",
  "完整用量或单价": "complete usage or rates",
  "估算费用 · USD": "Estimated cost · USD",
  "本条为订阅用量，结果为单价换算的费用等值。": "This is subscription usage. The result is a rate-based cost equivalent.",
  "按填写的用量和单价计算。": "Calculated from the entered usage and rates.",
  "暂时无法计算": "Calculation unavailable",
  "请确认本地服务连接与输入内容后重试。": "Check the local connection and entered values, then retry.",
  "已修改为自定义单价。请确认它们适用于本次用量。": "Using custom rates. Check that they apply to this usage.",
  "项目": "Project",
  "全部项目": "All projects",
  "项目未记录": "Project not recorded",
  "模型调用耗时中位数": "Median model call duration",
  "操作与耗时": "Operations and timing",
  "各范围分别展示，不相加。候选生成耗时已包含几何导出；人工停留不计为模型调用。": "Timing scopes are shown separately, never added together. Candidate duration includes geometry export. Human dwell is not model call time.",
  "分组": "Group by",
  "候选轮次": "Candidate run",
  "精确来源": "Exact source",
  "会话家族": "Session family",
  "再显示 10 组": "Show 10 more groups",
  "模型调用": "Model call",
  "服务执行": "Service execution",
  "客户端等待": "Client wait",
  "代理整轮": "Agent turn",
  "耗时范围未知": "Unknown timing scope",
  "{count} 条 · 中位数 {duration}": "{count} records · median {duration}",
  "{count} 条耗时未知": "{count} unknown durations",
  "候选生成（含几何导出）": "Candidate generation (includes geometry export)",
  "几何导出": "Geometry export",
  "模型加载": "Model load",
  "Stage 保存": "Stage save",
  "意图理解": "Intent interpretation",
  "代理调用": "Agent call",
  "无模型调用": "No model call",
  "调用状态未知": "Model call status unknown",
  "候选轮次未记录": "Candidate run not recorded",
  "会话未记录": "Session not recorded",
  "父会话": "Parent session",
  "子会话": "Child session",
  "会话": "Session",
  "轮次": "Turn",
  "开始": "Started",
  "结束": "Ended",
  "关联的意图调用": "Linked intent call",
  "来源与记录详情": "Source and record details",
  "记录 ID": "Event ID",
  "关联记录": "Related event",
  "无操作记录": "No operations recorded",
  "{count} 条操作": "{count} operations",
  "Token 汇总仅计模型调用；模型耗时中位数只使用明确记录为 model_call 的时间。": "Token totals include model calls only. The model duration median uses only explicitly recorded model_call intervals.",
  "Codex 来源": "Codex sources",
  "会话 JSONL 文件": "Session JSONL files",
  "每行一个本地绝对路径；只读取明确列出的文件。留空并应用可清除来源，选择仅用于当前服务。": "One local absolute path per line. Only listed files are read. Apply an empty list to clear the selection. This selection lasts for the current service only.",
  "应用来源": "Apply sources",
  "读取当前来源": "Read current sources",
  "正在读取来源": "Reading sources",
  "已读取 {count} 个来源": "Loaded {count} sources",
  "正在应用来源": "Applying sources",
  "已应用 {count} 个来源": "Applied {count} sources",
  "来源读取失败，可重试。": "Could not read sources. Retry when the service is available.",
  "来源未更改：{error}": "Sources unchanged: {error}",
  "请求失败": "Request failed",
  "模型请求耗时中位数": "Median model request duration",
  "展开一次操作，查看总历时与阶段。父阶段已包含子阶段，嵌套时间不相加；操作间隔单独标注。": "Expand an action to see its elapsed time and stages. Parent intervals include their children, so nested times are not added together. Time between actions is shown separately.",
  "单次操作": "Single action",
  "各类耗时概况": "Timing by scope",
  "交互历时": "Interaction elapsed time",
  "设计修改": "Design edit",
  "图纸生成": "Drawing generation",
  "操作": "Action",
  "未关联到单次操作": "Not linked to an action",
  "{actions} 次操作 · {records} 条记录": "{actions} actions · {records} records",
  "{count} 条阶段记录": "{count} stage records",
  "总历时 {duration}": "Elapsed time {duration}",
  "实际等待 {duration}": "Active wait {duration}",
  "操作间隔 {duration}": "Between actions {duration}",
  "动作总历时未知；下方仅展示各段实测时间。": "Total action time is unknown. Only recorded stage times are shown below.",
  "从提交意图到候选可见；总历时包含操作间隔。": "From intent submission to a visible candidate, including time between actions.",
  "本次未记录图纸生成。": "No drawing generation was recorded for this action.",
  "关联来源（不计入本次阶段）": "Linked context (outside these stages)",
  "父阶段未记录": "Parent stage not recorded",
  "意图处理": "Intent processing",
  "模型请求往返": "Model request round trip",
  "意图请求等待": "Intent request wait",
  "候选请求等待": "Candidate request wait",
  "图纸请求等待": "Drawing request wait",
  "候选排队": "Candidate queue",
  "模型读取与解析": "Model download and parsing",
  "图纸模型读取": "Drawing model load",
  "图纸线条计算": "Drawing line computation",
  "SVG 输出": "SVG output",
  "PNG 输出": "PNG output",
  "图纸写入": "Drawing persistence",
  "图纸登记": "Drawing registration",
  "纯模型推理耗时未知；此处为请求往返时间。": "Pure model inference time is unknown; this is request round-trip time.",
  "纯模型推理耗时 {duration}": "Pure model inference time {duration}",
  "已中止": "Aborted",
  "实际重算 {count} 个对象": "Recomputed {count} objects",
  "复用 {count} 个对象": "Reused {count} objects",
  "已复用": "Reused",
  "未命中": "Cache miss",
  "部分复用": "Partial reuse",
  "未采用复用": "Reuse declined",
  "不适用缓存": "Cache not applicable",
  "缓存情况未知": "Cache status unknown",
  "已知重复执行；是否可避免仍待判断。": "Repeated execution recorded; whether it was avoidable remains undetermined.",
  "已记录的请求输入相同；是否可避免待定。": "Recorded request inputs match; whether it was avoidable remains undetermined.",
  "未标记重复执行": "No repeated execution flagged",
  "重复情况未知": "Repeat status unknown",
  "重算、复用与来源": "Recomputation, reuse and sources",
  "执行内容": "Executed work",
  "实际执行路径": "Actual execution path",
  "执行范围": "Execution scope",
  "输入对象": "Input objects",
  "输出对象": "Emitted objects",
  "实际重算对象": "Recomputed objects",
  "实际复用对象": "Reused objects",
  "实际执行阶段": "Executed stages",
  "缓存与复用": "Cache and reuse",
  "缓存状态": "Cache status",
  "未命中或复用理由": "Cache or reuse reason",
  "缓存检查": "Cache checks",
  "重复与复用机会": "Repeated work and reuse opportunities",
  "重复判断": "Repeat assessment",
  "判断依据": "Assessment basis",
  "输入是否相同": "Equivalent input",
  "复用机会": "Reuse opportunity",
  "对照记录": "Compared event",
  "输入身份与对照": "Input identity and comparisons",
  "输入身份": "Input identity",
  "对照来源": "Comparison references",
  "输出来源": "Output references",
  "操作 ID": "Action ID",
  "父阶段 ID": "Parent stage ID",
  "请求方式": "Request kind",
  "是": "Yes",
  "否": "No",
  "无": "None",
  "相同": "Same",
  "已变化": "Changed",
  "缺失": "Missing",
  "{count} 项": "{count} items",
  "其余 {count} 项": "{count} more items",
  "找到完全匹配的已登记图纸": "An exactly matching registered drawing was found",
  "没有已登记的图纸": "No registered drawing was found",
  "已登记图纸的输入发生变化": "The registered drawing inputs changed",
  "缓存图纸文件不可用": "The cached drawing file is unavailable",
  "源模型不可用": "The source model is unavailable",
  "沿用已登记图纸": "Retained registered drawing",
  "完整投影计算": "Full projection",
  "全局可见性计算": "Global visibility computation",
  "图纸 ID": "Drawing ID",
  "来源 Stage": "Source Stage",
  "模型来源": "Model source",
  "投影设置": "View settings",
  "图纸文件": "Drawing bytes",
  "无效": "Invalid",
  "Token 汇总仅计模型调用；请求耗时只使用明确记录为 model_call 的时间，不代表纯推理时间。": "Token totals include model calls only. Request durations use only recorded model_call intervals and do not represent pure inference time.",
  "服务阶段": "Service interval",
  "执行前排队等待": "Queue wait before execution",
  "未归类时间 {duration}": "Unattributed time {duration}",
  "请求与重试": "Request and retry",
  "请求尝试序号": "Request attempt",
  "重试原因": "Retry reason",
  "等待原因": "Wait reason",
  "HTTP 状态": "HTTP status",
  "输入字节": "Input bytes",
  "输出字节": "Output bytes",
  "机会对照来源": "Opportunity references",
  "保持一致的输入部分": "Stable input parts",
  "模型用量（请求时段未记录）": "Model usage (request interval not recorded)",
  "构件生成": "Element production", "导出缓存检查": "Export cache lookup", "来源导出检查": "Source export lookup",
  "几何内核初始化": "Geometry kernel initialization", "几何复用检查": "Geometry reuse check", "几何构建": "Geometry build",
  "STEP 写入": "STEP write", "STEP 读回校验": "STEP readback verification", "曲面网格化": "Tessellation",
  "预览模型写入": "Preview model write", "预览模型读回校验": "Preview model readback verification",
  "模型下载": "Model download", "模型解析": "Model parsing", "图纸下载": "Drawing download", "图纸显示": "Drawing display",
  "Stage 请求等待": "Stage request wait", "API 请求等待": "API request wait",
  "构件逐项生成": "Produce individual elements", "STEP 读取": "Read STEP", "STEP 校验": "Verify STEP", "预览模型检查": "Inspect preview model", "预览模型校验": "Verify preview model",
  "复用已保留结果": "Reused a retained result", "首次观察到这些输入": "First observation of these inputs",
  "缺少可比较的输入记录": "Comparable input records are missing", "再次请求同一资产": "Same asset requested again",
  "已记录的模型请求输入相同": "Recorded model request inputs match", "已记录的执行输入相同": "Recorded execution inputs match",
  "资产身份相同": "Asset identity matches", "供应商缓存适用条件仍需确认": "Provider cache eligibility still needs verification",
  "浏览器缓存是否避免了传输未知": "Whether browser caching avoided transfer is unknown",
  "仍需核对保留结果与当前执行的绑定": "Retained result binding to this execution still needs verification",
  "前次失败或取消，未得到可验证复用结果": "The previous attempt failed or was cancelled; no verified reusable result was obtained",
  "上下文相同；前缀缓存是否适用未知": "Shared context; prefix-cache eligibility is unknown",
  "等待执行线程接纳": "Waiting for a worker", "独占执行": "Exclusive execution", "并行执行": "Parallel execution",
  "OCCT 几何执行": "OCCT geometry execution", "OCCT 曲面网格化": "OCCT tessellation", "预览文件写入": "Preview file write",
  "几何程序": "Geometry program", "构件生产": "Element production", "导出流程": "Export process",
  "已校验的导出缓存": "Verified export cache", "来源导出缓存": "Source export cache", "已校验的来源形体": "Verified source shapes",
  "内核初始化": "Kernel initialization", "STEP 写入与文件摘要": "STEP write and file digest",
  "独立读回与校验": "Independent readback and verification", "累计实际工作时间": "Accumulated active time", "文件写入": "File write",
  "未选择复用来源": "No reuse source selected", "没有已保留导出": "No retained export", "多个缓存条件未满足": "Multiple cache conditions failed",
  "找到已校验的匹配导出": "A verified matching export was found", "来源导出缺失": "Source export missing",
  "来源记录绑定已变化": "Source record binding changed", "来源执行记录未通过校验": "Source execution record is not verified",
  "来源几何后端已变化": "Source geometry backend changed", "来源记录格式已变化": "Source record format changed",
  "来源文件已变化": "Source artifact changed", "来源导出已校验": "Source export verified", "来源记录或文件不可读": "Source record or artifact unreadable",
  "复用来源输入不完整": "Reuse source inputs incomplete", "长度单位已变化": "Length unit changed", "来源是符号链接": "Source is a symbolic link",
  "来源文件不可读": "Source artifact unreadable", "来源 STEP 读回失败": "Source STEP readback failed", "来源对象身份已变化": "Source object identities changed",
  "几何内容已变化": "Geometry changed", "对象已增加或移除": "Objects added or removed", "几何内容未变化": "Geometry unchanged",
  "构件输入未变化": "Element inputs unchanged", "各构件的复用条件不同": "Reuse conditions differ by element",
  "内核是否复用网格未被观测": "Kernel mesh reuse was not observed",
  "保留的构件结果缺失": "Retained element result missing", "生产代码已变化": "Producer code changed", "生产代码记录缺失": "Producer code record missing",
  "引用输入已变化": "Reference inputs changed", "引用输入缺失": "Reference inputs missing", "构件定义已变化": "Element definition changed", "构件定义缺失": "Element definition missing",
  "依赖输入已变化": "Dependency inputs changed", "依赖输入缺失": "Dependency inputs missing", "文件已变化": "Artifact changed", "文件缺失": "Artifact missing", "文件不可读": "Artifact unreadable",
  "绑定已变化": "Binding changed", "读回未通过校验": "Readback not verified", "读取失败": "Read failed", "已校验": "Verified", "不可读": "Unreadable",
  "几何程序摘要": "Geometry program digest", "来源几何程序摘要": "Source program digest", "来源 STEP 摘要": "Source STEP digest", "状态记录摘要": "State record digest",
  "生产代码": "Producer code", "构件席位": "Element seat", "上下文摘要": "Context digest", "供应商配置身份": "Provider configuration identity", "请求摘要": "Prompt digest", "增量导出": "Incremental export"
};
Object.assign(english, {
  "跳到任务概览": "Skip to task overview", "任务记录": "Task records", "任务耗时与用量": "Task timing and usage", "任务轮次": "Turn", "暂无任务记录": "No turns recorded yet",
  "从 MonkeyHub 发起任务后，可查看已关联的模型、工具与候选阶段记录。": "Start a turn in MonkeyHub to view its linked model, tool and candidate activity records.",
  "下载 Trace JSON": "Download trace JSON", "任务耗时": "Turn timeline", "实线：阻塞 · 虚线：后台 · 加粗：观测关键路径": "Solid: blocking · Dashed: background · Bold: observed critical path",
  "所有阶段共用时间轴。重叠与嵌套阶段不累加；点选色条可查看记录。": "All stages share one time axis. Overlapping and nested durations are not added. Select a bar to inspect its record.",
  "耗时分析": "Timing analysis", "基于已记录的执行阶段": "Based on recorded execution stages", "活动树": "Activity tree", "活动详情": "Activity details",
  "选择一个阶段查看模型、状态与关联记录。": "Select a stage to inspect its model, status and linked evidence.", "记录与价格依据": "Coverage and price basis",
  "调用记录与费用估算": "Call records and cost estimates", "任务每 3 秒刷新；原始用量每 30 秒刷新": "Turns refresh every 3 seconds; raw usage every 30 seconds",
  "总历时": "Elapsed", "首次可见": "First visible", "候选已验证": "Verified candidate", "模型轮次": "Model rounds", "工具调用": "Tool calls", "模型唤醒": "Model wake-ups", "费用 / 等值": "Price / equivalent",
  "阻塞": "Blocking", "后台": "Background", "阻塞属性未知": "Blocking status unknown", "观测关键路径": "Observed critical path", "尚无已计时阶段": "No timed stages recorded",
  "未记录完整时间范围的阶段仍列在活动树中。": "Stages without a complete time range remain in the activity tree.", "原始记录（已脱敏）": "Raw record (sanitized)",
  "起点": "Start offset", "阶段历时": "Stage duration", "关联记录": "Linked evidence", "未归因时间": "Unattributed time", "已知 Token 小计": "Known token subtotal",
  "实时更新": "Live updates", "已暂停更新": "Updates paused", "任务读取失败，保留上次记录。": "Turn refresh failed. Keeping the last loaded records.", "暂时无法读取任务记录。": "Turn records are unavailable.",
  "当前任务不在最新记录中，保留上次读取结果。": "This task is absent from the latest records. Keeping its last loaded snapshot.",
  "Trace 下载失败，请重试。": "Trace download failed. Try again.", "价格未核实": "Price unavailable", "没有已记录的诊断信号。": "No diagnostic signals recorded.",
  "关键路径需要完整的阻塞时段记录。": "The critical path needs recorded blocking intervals.", "父阶段缺失": "Parent stage unavailable", "未关联父阶段": "Unlinked parent stage",
  "用量记录数：{count}；其中 {missing} 条不完整。": "Usage records: {count}; {missing} are incomplete.",
  "按记录费率计算；订阅模型显示 API 等值，不代表实际扣款。": "Calculated from recorded rates. Subscription models show an API equivalent, not an actual charge.",
  "模型": "Model", "价格快照": "Rate snapshots", "详情": "Details", "尚无诊断摘要": "No diagnosis yet", "已知费用小计": "Known price subtotal",
  "用户请求": "User request", "构建上下文": "Build context", "模型活动": "Model activity", "模型请求": "Model request", "工具调用": "Tool calls", "最终回复": "Final response", "结果验证": "Result verification", "候选验证": "Candidate validation", "候选读回": "Candidate readback", "STEP 读回": "STEP readback", "预览读回": "Preview readback", "模型安装": "Install model", "视图投影": "Project view", "模型加载": "Load model", "原生模型用量": "Native model usage", "原生 Agent 回合": "Native agent turn",
  "订阅 API 等价值（非实际扣款）": "Subscription API equivalent (not an actual charge)", "API 费用估算（非账单）": "API estimate (not an invoice)", "订阅 API 等价值与 API 估算（均非实际扣款）": "Subscription API equivalent / API estimate (not an actual charge)",
  "重复 schema 查询": "Repeated schema reads", "重复状态查询": "Repeated state reads", "重复工具活动": "Repeated tool activity", "已记录重试": "Recorded retries", "模型重新活动": "Model wake-ups", "上下文估算超预算": "Context estimate over budget", "相同已记录输入再次执行": "Repeated recorded inputs", "状态": "Status",
  "记录显示重复查询；状态是否改变、查询是否必要仍需核对。": "Repeated reads were recorded. Whether state changed or each read was needed requires checking.",
  "工具名和请求参数散列相同；状态是否改变、查询是否必要仍需核对。": "The tool name and request argument hash match. Whether state changed or each read was needed requires checking.",
  "已归因的阻塞时间": "Attributed blocking time",
  "显示传输明细": "Show transport details", "传输请求": "Transport request", "OCCT 几何导出": "OCCT geometry export",
  "Agent 续行": "Agent resumes", "首段回复到达": "First response received", "Agent 活动区间": "Agent activity intervals",
  "工具或权限等待后恢复的活动段。": "Activity resumes after tools or permission waits.", "仅统计明确的模型请求边界": "Counted only from explicit model request boundaries",
  "首段回复到达记录的是服务端收文时点；首次可见来自客户端显示记录。": "First response is the server receipt time. First visible comes from client display records.",
  "宿主记录的 Agent 活动区间恢复，可能包含等待；不能据此证明模型被重新唤醒。": "The host recorded resumed agent activity, which may include waiting. It does not prove that another model call occurred.",
  "同名工具调用可能作用于不同输入，不能据此断言可以省略。": "Calls to the same tool may use different inputs; repetition alone does not show that they can be skipped.",
  "仅统计生产者明确标记的重试；失败后恢复不等于无效工作。": "Only explicitly recorded retries are counted. Recovery after a failure is not necessarily wasted work.",
  "活动区间由宿主边界记录，含等待；不是纯模型推理时长。": "The host records these activity intervals, including waits. They are not pure model inference time.",
  "文字估算仅用于上下文诊断，真实 token 仍以 provider 元数据为准。": "Text estimates diagnose context size. Actual tokens come from provider metadata.",
  "需要结合结果绑定、版本和缓存条件判断是否能复用。": "Reuse depends on result binding, version and cache conditions.",
  "缺少完整请求根区间，不能推定关键路径。": "A complete request interval is missing; the critical path cannot be determined.",
  "按明确阻塞事件的依赖层级分配非重叠区间；未观测部分保留未知，不能视为完整执行 DAG。": "Non-overlapping intervals follow the hierarchy of recorded blocking events. Unobserved time stays unknown; this is not a complete execution DAG.",
  "并行阻塞分支没有等待先后证据，其重叠区间未指定关键分支。": "Parallel blocking branches have no recorded wait ordering, so their overlap is not assigned to a critical branch."
});
const catalog = locale === "en" ? english : {};
const t = (key, parameters) => translateMessage(catalog, key, parameters);

for (const element of document.querySelectorAll("[data-i18n]")) {
  const content = element.firstChild;
  content.nodeValue = t(element.dataset.i18n) + (content.nodeValue.endsWith(" ") ? " " : "");
}
for (const attribute of ["aria-label", "placeholder"]) {
  for (const element of document.querySelectorAll(`[data-i18n-${attribute}]`)) {
    element.setAttribute(attribute, t(element.getAttribute(`data-i18n-${attribute}`)));
  }
}

(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("quote-form");
  const usageFields = ["input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens", "cache_write_1h_input_tokens", "reasoning_output_tokens"];
  const rateFields = ["input", "cached_input", "cache_write_input", "cache_write_1h_input", "output"];
  const state = { events: [], source: "all", project: "", group: "operation", visibleGroups: 10, openGroups: new Set(), sourcesLoaded: false, sourceBusy: false, sort: "uncached_input", loaded: false, loading: false, rates: [], ratesLoaded: false, selectedRate: null, quoteEvent: null, warnings: [], excluded: 0, visibleEvents: 10, quoteVersion: 0 };
  const traces = { items: [], warnings: [], project: "", selected: "", activeSpan: "", showTransport: false, loaded: false, loading: false, signature: "", open: new Set(), closed: new Set() };
  const traceLanes = ["agent", "hub", "studio", "cad", "client"];
  const traceLaneLabels = { agent: "Agent", hub: "Hub", studio: "Studio", cad: "CAD", client: "Client" };
  const controllers = new Set();
  const integerFormat = new Intl.NumberFormat(locale);
  const timeFormat = new Intl.DateTimeFormat(locale, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  const actionTimeFormat = new Intl.DateTimeFormat(locale, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const sourceLabels = { studio: "Studio", codex: "Codex", hub: "Hub" };
  const statusLabels = { completed: "已完成", succeeded: "成功", success: "成功", failed: "失败", error: "失败", cancelled: "已取消", aborted: "已中止", running: "进行中", recorded: "已记录", observed: "已观测", compiled: "已编译", question: "待补充", unsupported: "不支持", last_only: "仅末次用量", token_count: "已记录", partial_history: "历史不完整", counter_discontinuity: "计数不连续", counter_reset_unknown: "计数重置未知" };
  const partialStatuses = new Set(["partial_history", "counter_discontinuity", "counter_reset_unknown", "last_only"]);
  const scopeLabels = { interaction: "交互历时", model_call: "模型请求往返", service: "服务阶段", client_wait: "客户端等待", agent_turn: "代理整轮", unknown: "耗时范围未知" };
  const phaseLabels = { design_edit: "设计修改", intent_compile: "意图处理", model_request: "模型请求往返", model_usage: "模型用量（请求时段未记录）", intent_wait: "意图请求等待", candidate_wait: "候选请求等待", candidate_queue: "候选排队", drawing_wait: "图纸请求等待", drawing_generate: "图纸生成", "drawing.load": "图纸模型读取", "drawing.hlr": "图纸线条计算", "drawing.svg": "SVG 输出", "drawing.png": "PNG 输出", "drawing.persist": "图纸写入", "drawing.register": "图纸登记", candidate: "候选生成（含几何导出）", geometry_export: "几何导出", model_load: "模型读取与解析", stage_save: "Stage 保存", intent: "意图理解", agent: "代理调用", agent_turn: "代理整轮" };
  Object.assign(phaseLabels, { element_production: "构件生成", export_cache_lookup: "导出缓存检查", source_export_lookup: "来源导出检查", occt_initialization: "几何内核初始化", occt_reuse_check: "几何复用检查", geometry_build: "几何构建", step_write: "STEP 写入", step_readback: "STEP 读回校验", tessellation: "曲面网格化", preview_write: "预览模型写入", preview_readback: "预览模型读回校验", model_download: "模型下载", model_parse: "模型解析", document_load: "图纸下载", document_render: "图纸显示", stage_wait: "Stage 请求等待", api_wait: "API 请求等待", produce_rows: "构件逐项生成", build_program_shapes: "几何构建", write_step: "STEP 写入", read_step: "STEP 读取", verify_step: "STEP 校验", tessellate_shape: "曲面网格化", write_preview_three_dm: "预览模型写入", inspect_three_dm: "预览模型检查", verify_preview: "预览模型校验" });
  Object.assign(phaseLabels, { api_request: "传输请求", "geometry_export.occt.occt": "OCCT 几何导出" });
  const cacheLabels = { hit: "已复用", miss: "未命中", partial: "部分复用", refused: "未采用复用", not_applicable: "不适用缓存", unknown: "缓存情况未知" };
  const duplicateLabels = { repeated_execution: "已知重复执行；是否可避免仍待判断。", same_input_request: "已记录的请求输入相同；是否可避免待定。", same_asset_request: "再次请求同一资产", reused_result: "复用已保留结果", first_observed_input: "首次观察到这些输入", insufficient_input_identity: "缺少可比较的输入记录", none: "未标记重复执行", unknown: "重复情况未知" };
  const checkLabels = { same: "相同", changed: "已变化", missing: "缺失", unknown: "未知", verified: "已校验", unreadable: "不可读" };
  const diagnosticLabels = { exact_registered_drawing: "找到完全匹配的已登记图纸", no_registered_drawing: "没有已登记的图纸", registered_inputs_changed: "已登记图纸的输入发生变化", cached_document_unavailable: "缓存图纸文件不可用", source_unavailable: "源模型不可用", retained_drawing: "沿用已登记图纸", full_projection: "完整投影计算", global_visibility: "全局可见性计算", invalid: "无效" };
  Object.assign(diagnosticLabels, {
    same_observed_model_inputs: "已记录的模型请求输入相同", same_observed_execution_inputs: "已记录的执行输入相同", same_asset_identity: "资产身份相同",
    provider_cache_policy_requires_verification: "供应商缓存适用条件仍需确认", browser_cache_transfer_not_observed: "浏览器缓存是否避免了传输未知",
    retained_result_binding_requires_verification: "仍需核对保留结果与当前执行的绑定", previous_attempt_has_no_verified_result: "前次失败或取消，未得到可验证复用结果", shared_context_prefix_eligibility_unknown: "上下文相同；前缀缓存是否适用未知",
    worker_admission: "等待执行线程接纳", exclusive: "独占执行", parallel: "并行执行", occt: "OCCT 几何执行", reused: "复用已保留结果", incremental: "增量导出", occt_tessellation: "OCCT 曲面网格化", file3dm_write: "预览文件写入",
    program_geometry: "几何程序", producer_elements: "构件生产", cad_export: "导出流程", verified_export_cache: "已校验的导出缓存", source_export_cache: "来源导出缓存", verified_source_shapes: "已校验的来源形体", kernel_initialization: "内核初始化", step_write_and_hash: "STEP 写入与文件摘要", cold_read_and_verification: "独立读回与校验", aggregate_active_time: "累计实际工作时间", file_write: "文件写入",
    source_not_selected: "未选择复用来源", no_retained_export: "没有已保留导出", multiple_cache_conditions_failed: "多个缓存条件未满足", verified_matching_export: "找到已校验的匹配导出", source_export_missing: "来源导出缺失", source_record_binding_changed: "来源记录绑定已变化", source_receipt_not_verified: "来源执行记录未通过校验", source_backend_changed: "来源几何后端已变化", source_schema_changed: "来源记录格式已变化", source_artifact_changed: "来源文件已变化", verified_source_export: "来源导出已校验", source_receipt_or_artifact_unreadable: "来源记录或文件不可读",
    source_inputs_incomplete: "复用来源输入不完整", length_unit_changed: "长度单位已变化", source_symlink_refused: "来源是符号链接", source_artifact_unreadable: "来源文件不可读", source_step_readback_failed: "来源 STEP 读回失败", source_object_identity_changed: "来源对象身份已变化", geometry_changed: "几何内容已变化", objects_added_or_retired: "对象已增加或移除", unchanged_geometry: "几何内容未变化", unchanged_inputs: "构件输入未变化", mixed_element_inputs: "各构件的复用条件不同", kernel_mesh_reuse_unobserved: "内核是否复用网格未被观测",
    retained_result_missing: "保留的构件结果缺失", producer_code_changed: "生产代码已变化", producer_code_missing: "生产代码记录缺失", references_changed: "引用输入已变化", references_missing: "引用输入缺失", element_row_changed: "构件定义已变化", element_row_missing: "构件定义缺失", dependency_inputs_changed: "依赖输入已变化", dependency_inputs_missing: "依赖输入缺失", artifact_changed: "文件已变化", artifact_missing: "文件缺失", artifact_unreadable: "文件不可读", binding_changed: "绑定已变化", readback_not_verified: "读回未通过校验", read_failed: "读取失败"
  });
  const identityLabels = { drawing_id: "图纸 ID", source_stage_ref: "来源 Stage", model_source: "模型来源", view_recipe: "投影设置", bytes: "图纸文件" };
  Object.assign(identityLabels, { program_digest: "几何程序摘要", source_program_digest: "来源几何程序摘要", source_step_sha256: "来源 STEP 摘要", record_digest: "状态记录摘要", producer_code: "生产代码", seat_id: "构件席位", context_digest: "上下文摘要", provider_fingerprint: "供应商配置身份", prompt_sha256: "请求摘要" });
  const billingLabels = { api_estimate: "API 用量", subscription_equivalent: "订阅用量 · 仅等值估算", unknown: "计费方式未记录" };
  const missingLabels = { "tokens.input": "普通输入用量", "tokens.cached_input": "缓存读取用量", "tokens.cache_write_input": "普通缓存写入用量", "tokens.cache_write_1h_input": "1 小时缓存写入用量", "tokens.output": "输出用量", "rate.input": "普通输入单价", "rate.cached_input": "缓存读取单价", "rate.cache_write_input": "普通缓存写入单价", "rate.cache_write_1h_input": "1 小时缓存写入单价", "rate.output": "输出单价", rate_card: "费率" };

  function text(id, value, parameters) { $(id).textContent = t(value, parameters); }
  function node(tag, className, value) { const element = document.createElement(tag); if (className) element.className = className; if (value !== undefined) element.textContent = value; return element; }
  function known(value) { return Number.isSafeInteger(value) && value >= 0; }
  function count(value) { return known(value) ? integerFormat.format(value) : "—"; }
  function label(value, fallback = "未记录") { return typeof value === "string" && value.trim() && value !== "unknown" ? value : t(fallback); }
  function duration(value) { if (!known(value)) return "—"; return value < 1000 ? `${integerFormat.format(value)} ms` : `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value / 1000)} s`; }
  function timingScope(event) { return Object.hasOwn(scopeLabels, event.timing_scope) ? event.timing_scope : "unknown"; }
  function eventDetails(event) { return event.details && typeof event.details === "object" && !Array.isArray(event.details) ? event.details : {}; }
  function phaseLabel(event) { return t(phaseLabels[event.phase] || (event.phase?.startsWith("geometry_export.") ? "几何导出" : event.phase)); }
  function isModelCall(event) { return event.model_call === true || (event.model_call == null && event.phase !== "agent_turn" && ["intent", "agent"].includes(event.phase)); }
  function modelDuration(event) { return timingScope(event) === "model_call" && isModelCall(event) ? event.duration_ms : null; }
  function median(values) { const sorted = values.filter(known).sort((a, b) => a - b); const middle = Math.floor(sorted.length / 2); return sorted.length ? (sorted.length % 2 ? sorted[middle] : Math.round((sorted[middle - 1] + sorted[middle]) / 2)) : null; }
  function projectKey(event) { return event.project_id || "__unrecorded__"; }
  function aggregate(events, field) { let total = 0n; let recorded = 0; for (const event of events) { const value = metric(event, field); if (known(value)) { total += BigInt(value); recorded += 1; } } return { total, recorded, missing: events.length - recorded }; }
  function aggregateText(value) { return value.recorded ? integerFormat.format(value.total) : "—"; }
  function coverageText(recorded, total) { return t("{recorded}/{total} 次已记录", { recorded, total }); }
  function tokenStat(id, summary) {
    let display = aggregateText(summary);
    if (summary.recorded && summary.total >= (locale === "en" ? 1000n : 10000n)) {
      const [unit, suffix] = locale === "en"
        ? summary.total >= 1000000000000n ? [1000000000000n, "T"] : summary.total >= 1000000000n ? [1000000000n, "B"] : summary.total >= 1000000n ? [1000000n, "M"] : [1000n, "K"]
        : summary.total >= 1000000000000n ? [1000000000000n, "万亿"] : summary.total >= 100000000n ? [100000000n, "亿"] : [10000n, "万"];
      const hundredths = (summary.total * 100n + unit / 2n) / unit;
      display = `${hundredths / 100n}.${String(hundredths % 100n).padStart(2, "0")}${suffix}`;
    }
    text(id, display);
    const exact = summary.recorded ? `${integerFormat.format(summary.total)} Token` : t("未记录");
    $(id).title = exact; $(id).setAttribute("aria-label", exact);
    text(`${id}-coverage`, `${t("已知小计")} · ${coverageText(summary.recorded, summary.recorded + summary.missing)}`);
    $(`${id}-coverage`).hidden = !summary.missing;
  }

  async function request(path, options = {}) {
    const controller = new AbortController();
    controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(path, { ...options, signal: controller.signal, cache: "no-store" });
      if (!response.ok) { const problem = await response.json().catch(() => ({})); throw new Error(typeof problem.error === "string" ? problem.error : t("请求失败")); }
      return await response.json();
    } finally { clearTimeout(timeout); controllers.delete(controller); }
  }

  function metric(event, field) {
    if (field === "time") { const value = Date.parse(event.started_at); return Number.isFinite(value) ? value : null; }
    if (field === "uncached_input") {
      const usage = event.tokens;
      if (![usage?.input_tokens, usage?.cached_input_tokens].every(known)) return null;
      const value = usage.input_tokens - usage.cached_input_tokens;
      return known(value) ? value : null;
    }
    const value = field === "duration_ms" ? modelDuration(event) : event.tokens?.[field];
    return known(value) ? value : null;
  }

  function descending(left, right) {
    if (left === null || right === null) return Number(right !== null) - Number(left !== null);
    return left === right ? 0 : left > right ? -1 : 1;
  }

  function renderOverview() {
    const sourceEvents = state.events.filter((event) => state.source === "all" || event.source === state.source);
    const projects = [...new Set(sourceEvents.map(projectKey))].sort();
    if (state.project && !projects.includes(state.project)) state.project = "";
    const allProjects = node("option", "", t("全部项目")); allProjects.value = "";
    $("project-filter").replaceChildren(allProjects, ...projects.map((value) => { const option = node("option", "", value === "__unrecorded__" ? t("项目未记录") : value); option.value = value; return option; }));
    $("project-filter").value = state.project;
    const operations = sourceEvents.filter((event) => !state.project || projectKey(event) === state.project);
    const events = operations.filter(isModelCall);
    const input = aggregate(events, "uncached_input");
    const cached = aggregate(events, "cached_input_tokens");
    const totalInput = aggregate(events, "input_tokens");
    const output = aggregate(events, "output_tokens");
    tokenStat("stat-cached", cached); tokenStat("stat-input", input); tokenStat("stat-output", output);
    const durations = events.map(modelDuration).filter(known);
    text("stat-duration", duration(median(durations)));
    text("stat-duration-coverage", `${t("中位数")} · ${coverageText(durations.length, events.length)}`);
    $("stat-duration-coverage").hidden = !events.length;
    const paired = events.filter((event) => known(event.tokens?.input_tokens) && known(event.tokens?.cached_input_tokens));
    const pairedInput = aggregate(paired, "input_tokens");
    const pairedCache = aggregate(paired, "cached_input_tokens");
    const ratioTenths = pairedInput.total > 0n ? (pairedCache.total * 1000n + pairedInput.total / 2n) / pairedInput.total : null;
    const summary = [t("总输入（含缓存）：{total}", { total: aggregateText(totalInput) })];
    if (totalInput.missing) summary.push(coverageText(totalInput.recorded, events.length));
    if (ratioTenths !== null) summary.push(t("输入中 {percent}% 命中缓存", { percent: `${ratioTenths / 10n}.${ratioTenths % 10n}` }));
    if (ratioTenths !== null && paired.length < events.length) summary.push(t("命中率基于 {count} 次完整记录", { count: paired.length }));
    text("cache-summary", summary.join(" · "));
    $("cache-summary").hidden = !events.length;
    const partial = events.filter((event) => partialStatuses.has(event.status)).length;
    const incomplete = cached.missing || input.missing || output.missing || partial || state.warnings.length || state.excluded;
    const attributionUnknown = state.warnings.some((warning) => warning.includes("继承"));
    text("coverage-title", attributionUnknown ? "来源归属未核实" : incomplete ? "部分记录不完整" : "记录说明");
    const coverage = [
      "Token 汇总仅计模型调用；请求耗时只使用明确记录为 model_call 的时间，不代表纯推理时间。",
      t("缓存输入：{recorded} 次已记录，{missing} 次未知。", { recorded: cached.recorded, missing: cached.missing }),
      t("未缓存输入：{inputRecorded} 次已记录，{inputMissing} 次未知；输出：{outputRecorded} 次已记录，{outputMissing} 次未知。", { inputRecorded: input.recorded, inputMissing: input.missing, outputRecorded: output.recorded, outputMissing: output.missing }),
      "未缓存输入为总输入减去缓存读取，包含新写入缓存的输入。只在两项均已知时计算；此排序用于比较用量，不代表费用排名。",
      t("缓存比例使用输入和缓存读取同时已知的 {recorded} 次调用，{missing} 次缺失。缓存读取仍可能计费。", { recorded: paired.length, missing: events.length - paired.length }),
      durations.length ? t("耗时：{recorded} 次已记录，{missing} 次未知。", { recorded: durations.length, missing: events.length - durations.length }) : "尚无耗时记录。"
    ];
    if (partial) coverage.push(t("{count} 条记录仅保留部分历史或计数不连续，不能用于完整账单核对。", { count: partial }));
    if (state.warnings.length || state.excluded) coverage.push(t("读取时有 {count} 条提示。", { count: state.warnings.length + state.excluded }));
    coverage.push(...state.warnings);
    $("coverage-content").replaceChildren(...coverage.map((value) => node("p", "", t(value))));
    renderOperations(operations); renderEvents(events); renderBreakdown(events);
  }

  function renderOperations(events) {
    const actionCount = new Set(events.filter((event) => event.operation_id).map((event) => JSON.stringify([event.project_id, event.operation_id]))).size;
    text("operation-count", actionCount ? t("{actions} 次操作 · {records} 条记录", { actions: actionCount, records: events.length }) : `(${integerFormat.format(events.length)})`);
    const scopes = Object.entries(scopeLabels).map(([scope, title]) => {
      const group = events.filter((event) => timingScope(event) === scope);
      if (!group.length) return null;
      const item = node("div", "scope-stat"); item.dataset.scope = scope;
      item.append(node("strong", "", t(title)), node("span", "", t("{count} 条 · 中位数 {duration}", { count: group.length, duration: duration(median(group.map((event) => event.duration_ms))) })));
      const missing = group.filter((event) => !known(event.duration_ms)).length;
      if (missing) item.append(node("span", "secondary-text", t("{count} 条耗时未知", { count: missing })));
      return item;
    }).filter(Boolean);
    $("timing-scopes").replaceChildren(...scopes);
    // Session ancestry comes only from explicit metadata, never path or id spelling.
    const parents = new Map();
    for (const event of state.events) if (event.session_id && event.parent_session_id) parents.set(event.session_id, event.parent_session_id);
    function family(session) {
      const seen = new Set();
      while (parents.has(session) && !seen.has(session)) { seen.add(session); session = parents.get(session); }
      return seen.has(session) ? [...seen].sort()[0] : session;
    }
    const groups = new Map();
    for (const event of events) {
      const parts = state.group === "operation" ? [event.project_id || null, event.operation_id || null, event.operation_id ? null : event.run_id || null]
        : state.group === "source" ? [event.source, event.source_ref || null]
        : state.group === "session" ? [event.source, event.session_id ? family(event.session_id) : null]
        : [event.project_id || null, event.run_id || null];
      const key = JSON.stringify(parts);
      if (!groups.has(key)) groups.set(key, { parts, events: [] });
      groups.get(key).events.push(event);
    }
    const byId = new Map(state.events.map((event) => [event.event_id, event]));
    const fragment = document.createDocumentFragment();
    for (const [key, group] of [...groups].slice(0, state.visibleGroups)) {
      const detail = node("details", "operation-group"); detail.dataset.group = key;
      const action = state.group === "operation" && group.parts[1] !== null;
      const root = action ? actionRoot(group.events) : null;
      if (action) detail.dataset.operationId = group.parts[1];
      const openKey = `${state.group}:${key}`;
      detail.open = state.openGroups.has(openKey);
      detail.addEventListener("toggle", () => { if (!detail.isConnected) return; if (detail.open) state.openGroups.add(openKey); else state.openGroups.delete(openKey); });
      const title = state.group === "operation" ? actionTitle(group, root)
        : state.group === "run" ? `${label(group.parts[0], "项目未记录")} · ${label(group.parts[1], "候选轮次未记录")}`
          : `${sourceLabels[group.parts[0]]} · ${label(group.parts[1], state.group === "session" ? "会话未记录" : "来源未记录")}`;
      const heading = node("summary"); heading.append(node("strong", "", title), node("span", "secondary-text", action ? t("总历时 {duration}", { duration: known(root?.duration_ms) ? duration(root.duration_ms) : t("未知") }) : t("{count} 条阶段记录", { count: group.events.length })));
      detail.append(heading);
      if (action) detail.append(actionExplanation(group.events, root));
      const rows = [...group.events]; const linked = new Set();
      if (state.group === "run" || state.group === "operation") {
        for (const event of group.events) {
          const related = byId.get(event.related_event_id);
          if (event.phase === "candidate" && related?.phase === "intent" && !rows.some((row) => row.event_id === related.event_id)) { rows.push(related); linked.add(related.event_id); }
        }
      }
      rows.sort((a, b) => (Date.parse(a.started_at) || 0) - (Date.parse(b.started_at) || 0));
      // A family still shows individual sessions, so child usage stays attributable.
      const sessions = new Map();
      for (const event of rows) {
        const session = state.group === "session" ? event.session_id || "" : "";
        if (!sessions.has(session)) sessions.set(session, []);
        sessions.get(session).push(event);
      }
      for (const [session, entries] of sessions) {
        if (state.group === "session") {
          const sessionHeading = node("h3", "session-heading", session ? `${t(parents.has(session) ? "子会话" : "会话")} · ${session}` : t("会话未记录"));
          detail.append(sessionHeading);
        }
        const own = entries.filter((event) => !linked.has(event.event_id));
        detail.append(operationTree(own));
        const context = entries.filter((event) => linked.has(event.event_id));
        if (context.length) {
          const related = node("details", "linked-context"); related.append(node("summary", "", t("关联来源（不计入本次阶段）")));
          for (const event of context) related.append(operationRow(event, true));
          detail.append(related);
        }
      }
      fragment.append(detail);
    }
    if (!groups.size) fragment.append(node("p", "field-help", t("无操作记录")));
    $("operation-groups").replaceChildren(fragment);
    $("more-operations").hidden = state.visibleGroups >= groups.size;
  }

  function actionRoot(events) {
    const edits = events.filter((event) => event.phase === "design_edit" && timingScope(event) === "interaction");
    if (edits.length) return edits.length === 1 ? edits[0] : null;
    const drawingWaits = events.filter((event) => event.phase === "drawing_wait" && timingScope(event) === "client_wait");
    return drawingWaits.length === 1 ? drawingWaits[0] : null;
  }

  function actionTitle(group, root) {
    if (group.parts[1] === null) return `${t("未关联到单次操作")} · ${label(group.parts[0], "项目未记录")} · ${label(group.parts[2], "候选轮次未记录")}`;
    const first = root || group.events.at(-1);
    const started = new Date(first.started_at);
    const kind = root?.phase === "design_edit" ? "设计修改" : group.events.some((event) => event.phase === "drawing_generate" || event.phase === "drawing_wait") ? "图纸生成" : "操作";
    return `${t(kind)} · ${Number.isNaN(started.getTime()) ? t("时间未知") : actionTimeFormat.format(started)} · ${label(group.parts[0], "项目未记录")}`;
  }

  function actionExplanation(events, root) {
    const box = node("div", "action-explanation");
    if (!root || !known(root.duration_ms)) box.append(node("p", "action-total-unknown", t("动作总历时未知；下方仅展示各段实测时间。")));
    if (root?.phase === "design_edit") {
      const detail = eventDetails(root);
      const values = [["实际等待 {duration}", detail.active_wait_ms], ["操作间隔 {duration}", detail.between_actions_ms]];
      if (Object.hasOwn(detail, "unattributed_ms")) values.push(["未归类时间 {duration}", detail.unattributed_ms]);
      box.append(node("p", "action-waits", values.map(([key, value]) => t(key, { duration: known(value) ? duration(value) : t("未知") })).join(" · ")));
      box.append(node("p", "secondary-text", t("从提交意图到候选可见；总历时包含操作间隔。")));
    }
    if (!events.some((event) => event.phase === "drawing_generate" || event.phase?.startsWith("drawing."))) box.append(node("p", "drawing-absence secondary-text", t("本次未记录图纸生成。")));
    return box;
  }

  function operationTree(events) {
    const tree = node("div", "operation-tree"); tree.setAttribute("role", "list");
    const byId = new Map(events.map((event) => [event.event_id, event]));
    const children = new Map();
    for (const event of events) {
      if (!children.has(event.parent_event_id)) children.set(event.parent_event_id, []);
      children.get(event.parent_event_id).push(event);
    }
    const shown = new Set();
    function append(event, depth) {
      if (shown.has(event.event_id)) return;
      shown.add(event.event_id);
      const row = operationRow(event, false, (children.get(event.event_id)?.length || 0) > 0); row.setAttribute("role", "listitem"); row.dataset.depth = String(depth);
      row.style.setProperty("--timing-depth", Math.min(depth, 5));
      if (event.parent_event_id) row.dataset.parentEventId = event.parent_event_id;
      if (event.parent_event_id && !byId.has(event.parent_event_id)) row.append(node("p", "secondary-text", t("父阶段未记录")));
      tree.append(row);
      for (const child of children.get(event.event_id) || []) append(child, depth + 1);
    }
    for (const event of events) if (!event.parent_event_id || !byId.has(event.parent_event_id)) append(event, 0);
    // Retained incomplete records remain visible even if their parent links cycle.
    for (const event of events) append(event, 0);
    return tree;
  }

  function operationRow(event, linked, hasChildren = false) {
    const row = node("article", "operation-row"); row.dataset.eventId = event.event_id;
    const title = node("div", "operation-heading");
    title.append(node("strong", "", phaseLabel(event)), node("span", ["failed", "error", "cancelled", "aborted"].includes(event.status) ? "failure-text" : "secondary-text", t(statusLabels[event.status] || event.status)));
    row.append(title);
    if (linked) row.append(node("p", "secondary-text", t("关联的意图调用")));
    const callState = event.model_call === false ? "无模型调用" : isModelCall(event) ? "模型调用" : "调用状态未知";
    const scope = timingScope(event);
    const scopeLabel = event.phase === "candidate_queue" ? "执行前排队等待" : scopeLabels[scope];
    const showCallState = !hasChildren && !["interaction", "client_wait"].includes(scope) && event.phase !== "intent_compile" && !(scope === "model_call" && isModelCall(event));
    row.append(node("p", "operation-timing", [t(scopeLabel), duration(event.duration_ms), ...(showCallState ? [t(callState)] : [])].join(" · ")));
    if (isModelCall(event)) row.append(node("p", "secondary-text", `${label(event.model, "模型未记录")} · ${t("总输入")} ${known(event.tokens?.input_tokens) ? count(event.tokens.input_tokens) : t("未知")} · ${t("输出")} ${known(event.tokens?.output_tokens) ? count(event.tokens.output_tokens) : t("未知")}`));
    const diagnostics = eventDetails(event);
    if (event.phase === "model_request") row.append(node("p", "inference-note secondary-text", known(diagnostics.model_inference_ms) ? t("纯模型推理耗时 {duration}", { duration: duration(diagnostics.model_inference_ms) }) : t("纯模型推理耗时未知；此处为请求往返时间。")));
    const evidence = diagnosticSections(diagnostics);
    const hints = [];
    if (Array.isArray(diagnostics.recomputed_object_ids)) hints.push(t("实际重算 {count} 个对象", { count: diagnostics.recomputed_object_ids.length }));
    if (Array.isArray(diagnostics.reused_object_ids)) hints.push(t("复用 {count} 个对象", { count: diagnostics.reused_object_ids.length }));
    if (Object.hasOwn(cacheLabels, diagnostics.cache_status)) hints.push(t(cacheLabels[diagnostics.cache_status]));
    if (Object.hasOwn(duplicateLabels, diagnostics.duplicate_status) && ["repeated_execution", "same_input_request"].includes(diagnostics.duplicate_status)) hints.push(t(duplicateLabels[diagnostics.duplicate_status]));
    if (hints.length) row.append(node("p", "diagnostic-hint secondary-text", hints.join(" · ")));
    const detail = node("details", "event-diagnostics"); detail.append(node("summary", "", t(evidence.length ? "重算、复用与来源" : "来源与记录详情")), ...evidence);
    const values = node("dl");
    const fields = [["开始", event.started_at], ["结束", event.ended_at], ["项目", event.project_id], ["候选轮次", event.run_id], ["操作 ID", event.operation_id], ["父阶段 ID", event.parent_event_id], ["精确来源", event.source_ref], ["记录 ID", event.event_id], ["关联记录", event.related_event_id], ["会话", event.session_id], ["父会话", event.parent_session_id], ["轮次", event.turn_id], ["phase", event.phase], ["请求方式", diagnostics.request_kind], ["Token 用量", event.model_call === false ? t("未知") : null]];
    if (isModelCall(event)) {
      for (const [field, name] of [["cached_input_tokens", "缓存读取"], ["cache_write_input_tokens", "缓存写入总量"], ["cache_write_1h_input_tokens", "其中：1 小时写入"], ["reasoning_output_tokens", "其中：推理输出"]]) fields.push([name, known(event.tokens?.[field]) ? count(event.tokens[field]) : t("未知")]);
    }
    for (const [name, value] of fields) {
      if (value == null && !["结束", "精确来源"].includes(name)) continue;
      const item = node("div"); item.append(node("dt", "", t(name)), node("dd", "", value == null ? t("未知") : value)); values.append(item);
    }
    detail.append(values); row.append(detail); return row;
  }

  function diagnosticSections(detail) {
    const sections = [];
    const section = (title, fields, folded = false) => {
      const present = fields.filter(([key]) => Object.hasOwn(detail, key));
      if (!present.length) return;
      const box = node(folded ? "details" : "section", "diagnostic-section");
      box.append(node(folded ? "summary" : "h4", "", t(title)));
      const values = node("dl");
      for (const [key, title, labels] of present) {
        const item = node("div"); item.dataset.detail = key;
        const value = node("dd"); value.append(diagnosticValue(detail[key], labels));
        item.append(node("dt", "", t(title)), value); values.append(item);
      }
      box.append(values); sections.push(box);
    };
    section("执行内容", [["execution_path", "实际执行路径", diagnosticLabels], ["scope", "执行范围", diagnosticLabels], ["input_object_ids", "输入对象"], ["emitted_object_ids", "输出对象"], ["recomputed_object_ids", "实际重算对象"], ["reused_object_ids", "实际复用对象"], ["executed_stages", "实际执行阶段", phaseLabels]]);
    section("缓存与复用", [["cache_status", "缓存状态", cacheLabels], ["cache_reason", "未命中或复用理由", diagnosticLabels], ["cache_checks", "缓存检查", { ...checkLabels, invalid: "无效" }]]);
    section("请求与重试", [["retry_attempt", "请求尝试序号"], ["retry_reason", "重试原因", diagnosticLabels], ["wait_reason", "等待原因", diagnosticLabels], ["http_status", "HTTP 状态"], ["input_bytes", "输入字节"], ["output_bytes", "输出字节"]]);
    section("重复与复用机会", [["duplicate_status", "重复判断", duplicateLabels], ["duplicate_reason", "判断依据", diagnosticLabels], ["input_equivalent", "输入是否相同"], ["reuse_opportunity", "复用机会", diagnosticLabels], ["comparison_event_id", "对照记录"], ["opportunity_refs", "机会对照来源"], ["stable_input_parts", "保持一致的输入部分"]]);
    section("输入身份与对照", [["input_identity", "输入身份"], ["comparison_refs", "对照来源"], ["output_refs", "输出来源"]], true);
    return sections;
  }

  function diagnosticValue(value, labels = {}) {
    if (value === null || value === undefined) return node("span", "", t("未知"));
    if (typeof value === "boolean") return node("span", "", t(value ? "是" : "否"));
    if (typeof value === "string") return node("span", "", Object.hasOwn(labels, value) ? t(labels[value]) : value);
    if (typeof value === "number") return node("span", "", Number.isFinite(value) ? integerFormat.format(value) : t("未知"));
    const entries = Array.isArray(value) ? value.map((entry) => [null, entry]) : Object.entries(value);
    if (!entries.length) return node("span", "", t("无"));
    const container = node("div", "diagnostic-values");
    if (Array.isArray(value)) container.append(node("span", "reference-count", t("{count} 项", { count: entries.length })));
    function listing(items) {
      const list = node("ul", "reference-list");
      for (const [key, entry] of items) {
        const item = node("li");
        if (key !== null) item.append(node("span", "reference-key", `${t(identityLabels[key] || key)}: `));
        item.append(diagnosticValue(entry, labels)); list.append(item);
      }
      return list;
    }
    container.append(listing(entries.slice(0, 6)));
    if (entries.length > 6) {
      const more = node("details", "more-references"); more.append(node("summary", "", t("其余 {count} 项", { count: entries.length - 6 })));
      more.addEventListener("toggle", () => { if (more.open && more.children.length === 1) more.append(listing(entries.slice(6))); });
      container.append(more);
    }
    return container;
  }

  async function readSources() {
    if (state.sourceBusy) return;
    state.sourceBusy = true; $("reload-sources").disabled = true; $("apply-sources").disabled = true;
    text("source-status", "正在读取来源");
    try {
      const result = await request("/api/sources/codex");
      if (!Array.isArray(result.paths) || !result.paths.every((path) => typeof path === "string")) throw new Error("invalid_sources");
      $("source-paths").value = result.paths.join("\n"); state.sourcesLoaded = true;
      text("source-status", "已读取 {count} 个来源", { count: result.paths.length });
    } catch { text("source-status", "来源读取失败，可重试。"); }
    finally { state.sourceBusy = false; $("source-paths").disabled = !state.sourcesLoaded; $("reload-sources").disabled = false; $("apply-sources").disabled = !state.sourcesLoaded; }
  }

  async function applySources(event) {
    event.preventDefault(); if (state.sourceBusy || !state.sourcesLoaded) return;
    const paths = $("source-paths").value.split(/\r?\n/).map((path) => path.trim()).filter(Boolean);
    state.sourceBusy = true; $("source-paths").disabled = true; $("apply-sources").disabled = true; $("reload-sources").disabled = true;
    text("source-status", "正在应用来源");
    try {
      const result = await request("/api/sources/codex", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paths }) });
      if (!Array.isArray(result.paths) || !result.paths.every((path) => typeof path === "string")) throw new Error("invalid_sources");
      $("source-paths").value = result.paths.join("\n");
      text("source-status", "已应用 {count} 个来源", { count: result.paths.length });
      await refresh();
    } catch (error) { text("source-status", "来源未更改：{error}", { error: error.message }); }
    finally { state.sourceBusy = false; $("source-paths").disabled = false; $("apply-sources").disabled = false; $("reload-sources").disabled = false; }
  }

  function renderBreakdown(events) {
    const groups = new Map();
    for (const event of events) {
      const key = JSON.stringify([event.provider, event.model]);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(event);
    }
    $("model-summary").hidden = groups.size === 0;
    if (!groups.size) { $("breakdown-body").replaceChildren(); return; }
    const ordered = [...groups.values()].map((group) => ({ group, input: aggregate(group, "uncached_input") }));
    ordered.sort((a, b) => descending(a.input.recorded ? a.input.total : null, b.input.recorded ? b.input.total : null));
    const fragment = document.createDocumentFragment();
    for (const { group, input } of ordered) {
      const row = node("tr"); const model = node("td", "", label(group[0].model, "模型未记录"));
      row.append(model, node("td", "number", integerFormat.format(group.length)));
      for (const field of ["cached_input_tokens", "uncached_input", "output_tokens"]) {
        const value = field === "uncached_input" ? input : aggregate(group, field);
        const cell = node("td", "number", aggregateText(value)); cell.dataset.metric = field;
        if (value.missing) cell.append(node("span", "secondary-text", `${t("已知小计")} · ${coverageText(value.recorded, group.length)}`));
        row.append(cell);
      }
      const durations = group.map(modelDuration).filter(known);
      const waitCell = node("td", "number", duration(median(durations))); waitCell.dataset.metric = "duration_ms";
      waitCell.append(node("span", "secondary-text", coverageText(durations.length, group.length))); row.append(waitCell);
      fragment.append(row);
    }
    $("breakdown-body").replaceChildren(fragment); text("group-count", `(${groups.size})`);
  }

  function renderEvents(events) {
    const titles = { cached_input_tokens: "缓存输入", uncached_input: "未缓存输入", input_tokens: "总输入", output_tokens: "输出", duration_ms: "等待时间" };
    const fields = ["cached_input_tokens", "uncached_input", "output_tokens", "duration_ms"];
    if (state.sort === "input_tokens") fields.push("input_tokens");
    const ordered = [...events].sort((a, b) => descending(metric(a, state.sort), metric(b, state.sort)));
    const shown = ordered.slice(0, state.visibleEvents);
    const maxMetric = shown.reduce((max, event) => Math.max(max, metric(event, state.sort) ?? 0), 0);
    const showSource = state.source === "all" && new Set(events.map((event) => event.source)).size > 1;
    const header = node("tr");
    for (const [name, className] of [["时间", "time-cell"], ["模型", "model-cell"], ...fields.map((field) => [titles[field], "number"]), ["", "action-cell"]]) { const cell = node("th", className, t(name)); cell.scope = "col"; header.append(cell); }
    $("events-head").replaceChildren(header);
    const fragment = document.createDocumentFragment();
    for (const event of shown) {
      const row = node("tr"); row.dataset.eventId = event.event_id; const timestamp = new Date(event.started_at);
      const timeCell = node("td", "time-cell");
      const time = node("time", "", Number.isNaN(timestamp.getTime()) ? t("时间未知") : timeFormat.format(timestamp));
      if (!Number.isNaN(timestamp.getTime())) { time.dateTime = timestamp.toISOString(); time.title = timestamp.toLocaleString(locale); }
      timeCell.append(time);
      if (showSource) timeCell.append(node("span", "secondary-text", sourceLabels[event.source]));
      const modelCell = node("td", "model-cell", label(event.model, "模型未记录"));
      if (["failed", "error", "cancelled", "unsupported"].includes(event.status) || partialStatuses.has(event.status)) {
        modelCell.append(node("span", "secondary-text failure-text", t(statusLabels[event.status] || "已记录")));
      }
      row.append(timeCell, modelCell);
      for (const field of fields) {
        const value = metric(event, field);
        const metricCell = node("td", "metric-cell"); metricCell.dataset.metric = field;
        metricCell.append(node("span", "metric-caption", t(titles[field])), node("span", "metric-value", field === "duration_ms" ? duration(value) : count(value)));
        metricCell.title = value === null ? t("未记录") : `${integerFormat.format(value)}${field === "duration_ms" ? ` ms · ${t("请求往返")}` : " Token"}`;
        if (field === state.sort && value !== null && maxMetric > 0) { const track = node("div", "metric-track"); track.setAttribute("aria-hidden", "true"); const bar = node("span"); bar.style.width = `${Math.min(100, value / maxMetric * 100)}%`; track.append(bar); metricCell.append(track); }
        row.append(metricCell);
      }
      const actionCell = node("td", "action-cell"); const button = node("button", "button event-quote", t("查看 / 估算"));
      button.type = "button"; button.setAttribute("aria-label", t("查看 / 估算：{model}", { model: label(event.model, "模型未记录") }));
      button.addEventListener("click", () => fillEvent(event)); actionCell.append(button); row.append(actionCell); fragment.append(row);
    }
    $("events-body").replaceChildren(fragment); $("events-empty").hidden = Boolean(events.length); $("events-wrap").hidden = !events.length;
    text("event-count", state.loaded ? `(${integerFormat.format(events.length)})` : "");
    text("visible-count", events.length ? t("显示 {shown} / {total} 条", { shown: shown.length, total: integerFormat.format(events.length) }) : "");
    $("more-events").hidden = shown.length >= events.length;
  }

  function traceDuration(value) { return typeof value === "number" && Number.isFinite(value) && value >= 0 ? duration(Math.round(value)) : "—"; }
  function traceLabel(span) {
    const semanticPhase = !["agent", "agent_turn"].includes(span.phase) && (Object.hasOwn(phaseLabels, span.phase) || span.phase?.startsWith("geometry_export."));
    const parts = (semanticPhase || span.label === span.phase ? phaseLabel(span) : label(span.label, phaseLabel(span) || "未记录")).split(" · ");
    return parts.map((part) => t(part)).join(" · ");
  }
  function visibleTraceSpans(trace) { return (trace.spans || []).filter((span) => traces.showTransport || span.phase !== "api_request"); }
  function selectedTrace() { return traces.items.find((item) => item.trace_id === traces.selected); }
  function traceStatus(status) { return t(statusLabels[status] || (status === "interrupted" ? "待补充" : "未记录")); }
  function traceStat(title, value, note = "") {
    const item = node("div"); item.dataset.metric = title;
    item.append(node("dt", "", t(title)), node("dd", "", value));
    if (note) item.append(node("small", "", note));
    return item;
  }
  function traceTokenText(usage) {
    const tokens = usage?.tokens || {};
    return known(tokens.input_tokens) && known(tokens.output_tokens) ? count(tokens.input_tokens + tokens.output_tokens) : "—";
  }
  function tracePriceText(price) {
    return price?.currency === "USD" && typeof price.amount_usd === "string" && /^\d+(?:\.\d+)?$/.test(price.amount_usd) ? `$${price.amount_usd}` : t("价格未核实");
  }
  function renderTraceSelectors() {
    const projects = [...new Set(traces.items.map((trace) => trace.project_id).filter(Boolean))].sort();
    if (traces.project && !projects.includes(traces.project)) traces.project = "";
    const all = node("option", "", t("全部项目")); all.value = "";
    const options = projects.map((project) => { const option = node("option", "", project); option.value = project; return option; });
    const items = traces.items.filter((trace) => !traces.project || trace.project_id === traces.project);
    if (!items.some((trace) => trace.trace_id === traces.selected)) { traces.selected = items[0]?.trace_id || ""; traces.activeSpan = ""; }
    const signature = JSON.stringify([projects, traces.project, traces.selected, items.map((trace) => [trace.trace_id, trace.status, trace.started_at, trace.turn_id])]);
    if (traces.selectorSignature === signature) return; traces.selectorSignature = signature;
    $("trace-project").replaceChildren(all, ...options); $("trace-project").value = traces.project;
    const turns = items.map((trace) => {
      const parsed = new Date(trace.started_at);
      const stamp = Number.isNaN(parsed.getTime()) ? t("时间未知") : actionTimeFormat.format(parsed);
      const option = node("option", "", `${stamp} · ${traceStatus(trace.status)} · ${label(trace.turn_id || trace.trace_id)}`);
      option.value = trace.trace_id; return option;
    });
    $("trace-select").replaceChildren(...(turns.length ? turns : [node("option", "", t("暂无任务记录"))]));
    $("trace-select").disabled = !items.length; $("trace-select").value = traces.selected;
    $("trace-download").disabled = !items.length;
  }
  function showTraceEvidence(span, focus = false) {
    traces.activeSpan = span.event_id;
    for (const button of $("trace-waterfall").querySelectorAll("button[data-span-id]")) button.setAttribute("aria-pressed", String(button.dataset.spanId === span.event_id));
    for (const button of $("trace-tree").querySelectorAll("button[data-span-id]")) button.setAttribute("aria-pressed", String(button.dataset.spanId === span.event_id));
    const title = node("h3", "", traceLabel(span));
    const metadata = node("dl", "trace-evidence-metrics");
    for (const [key, value] of [["状态", traceStatus(span.status)], ["模型", span.model && span.model !== "none" ? span.model : t("未记录")], ["起点", traceDuration(span.offset_ms)], ["阶段历时", traceDuration(span.duration_ms)], ["费用 / 等值", tracePriceText(span.price)]]) metadata.append(traceStat(key, value));
    const binding = node("p", "field-help", [traceLaneLabels[span.lane] || span.lane, span.blocking === true ? t("阻塞") : span.blocking === false ? t("后台") : t("阻塞属性未知")].filter(Boolean).join(" · "));
    const previousRaw = $("trace-evidence").querySelector(".trace-raw");
    const previousScroll = previousRaw?.dataset.spanId === span.event_id ? previousRaw.querySelector("pre") : null;
    const rawScroll = [previousScroll?.scrollLeft || 0, previousScroll?.scrollTop || 0];
    const raw = node("details", "trace-raw"); raw.dataset.spanId = span.event_id; raw.open = previousRaw?.dataset.spanId === span.event_id && previousRaw.open; raw.append(node("summary", "", t("原始记录（已脱敏）")), node("pre", "", JSON.stringify(span, null, 2)));
    const links = node("dl", "trace-bindings");
    for (const key of ["operation_id", "proposal_id", "candidate_id", "run_id", "source_ref"]) {
      const value = span[key] ?? span.details?.[key];
      if (typeof value === "string" && value) { const item = node("div"); item.append(node("dt", "", key), node("dd", "", value)); links.append(item); }
    }
    $("trace-evidence").replaceChildren(title, binding, metadata, links, raw);
    raw.querySelector("pre").scrollTo(...rawScroll);
    if (focus) { $("trace-evidence-title").tabIndex = -1; $("trace-evidence-title").focus({ preventScroll: true }); $("trace-evidence-panel").scrollIntoView({ block: "nearest", behavior: "auto" }); }
  }
  function renderTraceWaterfall(trace) {
    const spans = trace.spans || [];
    const visible = visibleTraceSpans(trace);
    const timed = visible.filter((span) => Number.isFinite(span.offset_ms) && span.offset_ms >= 0 && Number.isFinite(span.duration_ms) && span.duration_ms >= 0);
    const extent = Math.max(1, trace.summary?.timeline_ms || trace.summary?.elapsed_ms || 0, ...timed.map((span) => span.offset_ms + span.duration_ms));
    const critical = new Set((trace.critical_path?.segments || []).map((segment) => segment.event_id));
    const fragment = document.createDocumentFragment();
    const axis = node("div", "trace-axis"); axis.append(node("span", "", "")); const ticks = node("div", "trace-ticks");
    for (let tick = 0; tick <= 4; tick += 1) ticks.append(node("span", "", traceDuration(extent * tick / 4)));
    axis.append(ticks); fragment.append(axis);
    for (const lane of traceLanes) {
      const row = node("div", "trace-lane"); row.dataset.lane = lane; row.append(node("strong", "trace-lane-name", traceLaneLabels[lane]));
      const track = node("div", "trace-track"); const occupied = [];
      const laneSpans = timed.filter((span) => span.lane === lane).sort((a, b) => a.offset_ms - b.offset_ms || b.duration_ms - a.duration_ms);
      for (const span of laneSpans) {
        let slot = occupied.findIndex((end) => end <= span.offset_ms); if (slot < 0) slot = occupied.length;
        occupied[slot] = span.offset_ms + Math.max(span.duration_ms, extent * 0.003);
        const bar = node("button", `trace-bar${span.blocking === false ? " is-background" : ""}${span.blocking == null ? " is-unclassified" : ""}${critical.has(span.event_id) ? " is-critical" : ""}${span.duration_ms === 0 ? " is-milestone" : ""}`, span.duration_ms === 0 ? "" : traceLabel(span));
        bar.type = "button"; bar.dataset.spanId = span.event_id; bar.dataset.status = span.status;
        bar.style.left = `${span.offset_ms / extent * 100}%`; bar.style.width = `${span.duration_ms / extent * 100}%`; bar.style.top = `${slot * 34 + 5}px`;
        const description = `${traceLabel(span)} · ${traceDuration(span.offset_ms)} → ${traceDuration(span.offset_ms + span.duration_ms)} · ${traceDuration(span.duration_ms)} · ${traceStatus(span.status)} · ${span.blocking === true ? t("阻塞") : span.blocking === false ? t("后台") : t("阻塞属性未知")}${critical.has(span.event_id) ? ` · ${t("观测关键路径")}` : ""}`;
        bar.title = description; bar.setAttribute("aria-label", description); bar.setAttribute("aria-pressed", String(traces.activeSpan === span.event_id));
        bar.addEventListener("click", () => showTraceEvidence(span, true)); track.append(bar);
      }
      track.style.height = `${Math.max(1, occupied.length) * 34 + 10}px`;
      if (!laneSpans.length) track.append(node("span", "trace-lane-empty", "—")); row.append(track); fragment.append(row);
    }
    const path = trace.critical_path;
    if (path?.segments?.length) {
      const row = node("div", "trace-lane trace-critical"); row.append(node("strong", "trace-lane-name", t("观测关键路径")));
      const track = node("div", "trace-track");
      for (const segment of path.segments) {
        if (!Number.isFinite(segment.offset_ms) || !Number.isFinite(segment.duration_ms)) continue;
        const span = spans.find((item) => item.event_id === segment.event_id); const bar = node("span", "trace-critical-segment");
        if (span) bar.dataset.lane = span.lane;
        bar.style.left = `${segment.offset_ms / extent * 100}%`; bar.style.width = `${segment.duration_ms / extent * 100}%`;
        bar.title = `${span ? traceLabel(span) : segment.event_id} · ${traceDuration(segment.duration_ms)}`; track.append(bar);
      }
      row.append(track); fragment.append(row);
    }
    if (timed.length < visible.length) fragment.append(node("p", "field-help", t("未记录完整时间范围的阶段仍列在活动树中。")));
    $("trace-waterfall").replaceChildren(fragment);
    const pathNote = path?.note ? path.note.split(/(?<=。)/).filter(Boolean).map((sentence) => t(sentence)).join(locale === "en" ? " " : "") : t("关键路径需要完整的阻塞时段记录。");
    $("trace-critical-note").textContent = [pathNote, `${t("未归因时间")} ${traceDuration(path?.unattributed_ms ?? trace.summary?.unattributed_ms)}`].join(" · ");
  }
  function renderTraceTree(trace) {
    // Read the visible state before replacing nodes; a pending toggle event
    // may not have updated the sets yet when a poll finishes.
    for (const summary of $("trace-tree").querySelectorAll("summary[data-branch-id]")) {
      const id = summary.dataset.branchId;
      if (summary.parentElement.open) { traces.open.add(id); traces.closed.delete(id); }
      else { traces.open.delete(id); traces.closed.add(id); }
    }
    const spans = visibleTraceSpans(trace); const byId = new Map((trace.spans || []).map((span) => [span.event_id, span])); const visibleIds = new Set(spans.map((span) => span.event_id)); const children = new Map(); const visited = new Set();
    function visibleParent(span) {
      let parent = span.parent_event_id; const seen = new Set([span.event_id]);
      while (parent && byId.has(parent) && !visibleIds.has(parent) && !seen.has(parent)) { seen.add(parent); parent = byId.get(parent).parent_event_id; }
      return visibleIds.has(parent) && !seen.has(parent) ? parent : "";
    }
    for (const span of spans) { const parent = visibleParent(span); if (!children.has(parent)) children.set(parent, []); children.get(parent).push(span); }
    function branch(span) {
      if (visited.has(span.event_id)) return null; visited.add(span.event_id);
      const item = node("li", "trace-activity"); item.dataset.spanId = span.event_id; item.dataset.parentId = visibleParent(span);
      const descendants = children.get(span.event_id) || [];
      const heading = node("div", "trace-activity-heading"); const button = node("button", "trace-activity-button", traceLabel(span));
      button.type = "button"; button.dataset.spanId = span.event_id; button.setAttribute("aria-pressed", String(traces.activeSpan === span.event_id));
      button.addEventListener("click", () => showTraceEvidence(span, true));
      const meta = node("span", "trace-activity-meta", `${traceStatus(span.status)} · ${traceDuration(span.duration_ms)}${span.blocking === false ? ` · ${t("后台")}` : ""}`);
      heading.append(button, meta);
      if (descendants.length) {
        const details = node("details"); details.open = !traces.closed.has(span.event_id) && (traces.open.has(span.event_id) || !visibleParent(span));
        const summary = node("summary", "trace-activity-branch", `${traceLabel(span)} · ${traceStatus(span.status)} · ${traceDuration(span.duration_ms)}`);
        summary.dataset.branchId = span.event_id;
        details.append(summary, heading); details.addEventListener("toggle", () => { if (!details.isConnected) return; if (details.open) { traces.open.add(span.event_id); traces.closed.delete(span.event_id); } else { traces.open.delete(span.event_id); traces.closed.add(span.event_id); } });
        const list = node("ul"); for (const child of descendants) { const result = branch(child); if (result) list.append(result); } details.append(list); item.append(details);
      } else item.append(heading);
      if (span.parent_event_id && !byId.has(span.parent_event_id)) item.append(node("small", "field-help", t("父阶段缺失")));
      return item;
    }
    const list = node("ul", "trace-activity-root");
    for (const span of [...(children.get("") || []), ...spans]) { const item = branch(span); if (item) list.append(item); }
    $("trace-tree").replaceChildren(list);
  }
  function renderTraceDiagnostics(trace) {
    const attribution = (trace.attribution || []).filter((item) => item.duration_ms > 0).sort((a, b) => b.duration_ms - a.duration_ms);
    text("trace-attribution", attribution.length ? `${t("已归因的阻塞时间")} · ${attribution.map((item) => `${traceLaneLabels[item.lane] || item.lane} ${traceDuration(item.duration_ms)}`).join(" · ")}` : "关键路径需要完整的阻塞时段记录。");
    const list = node("ul", "trace-diagnosis-list");
    for (const diagnostic of trace.diagnostics || []) {
      const item = node("li"); item.dataset.code = diagnostic.code;
      item.append(node("strong", "", `${t(diagnostic.label)}${known(diagnostic.count) ? ` · ${count(diagnostic.count)}` : ""}`));
      if (diagnostic.note) item.append(node("p", "field-help", t(diagnostic.note)));
      for (const id of diagnostic.event_ids || []) {
        const span = trace.spans.find((span) => span.event_id === id); if (!span) continue;
        const button = node("button", "text-button", traceLabel(span)); button.type = "button"; button.addEventListener("click", () => showTraceEvidence(span, true)); item.append(button);
      }
      list.append(item);
    }
    $("trace-diagnostics").replaceChildren(list.children.length ? list : node("p", "field-help", t("没有已记录的诊断信号。")));
  }
  function renderTraceCoverage(trace) {
    const fragment = document.createDocumentFragment();
    const usage = trace.usage || {};
    fragment.append(node("p", "", `${t("首段回复到达")} ${traceDuration(trace.summary?.first_response_ms)} · ${t("Agent 活动区间")} ${count(trace.summary?.provider_rounds)}`));
    fragment.append(node("p", "field-help", t("首段回复到达记录的是服务端收文时点；首次可见来自客户端显示记录。")));
    fragment.append(node("p", "", t("用量记录数：{count}；其中 {missing} 条不完整。", { count: count(usage.events_count), missing: count(usage.missing_events_count) })));
    for (const warning of [...traces.warnings, ...(trace.warnings || []), ...(trace.price?.missing || [])]) fragment.append(node("p", "field-help", warning));
    const seen = new Set();
    for (const snapshot of trace.price?.rate_snapshots || []) {
      const rate = snapshot.rate; if (!rate || seen.has(JSON.stringify(rate))) continue; seen.add(JSON.stringify(rate));
      const paragraph = node("p", "field-help", [rate.provider, rate.model, rate.billing_plan, rate.effective_date].filter(Boolean).join(" · "));
      try { const url = new URL(rate.source_url); if (url.protocol === "https:" && !url.username && !url.password) { const link = node("a", "", t("费率来源")); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; paragraph.append(document.createTextNode(" · "), link); } } catch { /* Unavailable sources remain in the sanitized snapshot. */ }
      fragment.append(paragraph);
    }
    const rates = node("details", "trace-raw"); rates.open = Boolean($("trace-coverage").querySelector("details[open]")); rates.append(node("summary", "", t("价格快照")), node("pre", "", JSON.stringify(trace.price || {}, null, 2))); fragment.append(rates);
    $("trace-coverage").replaceChildren(fragment);
  }
  function renderTrace(force = false) {
    const scroll = [window.scrollX, window.scrollY];
    renderTraceSelectors(); const trace = selectedTrace();
    $("trace-content").hidden = !trace; $("trace-empty").hidden = Boolean(trace);
    if (!trace) { traces.signature = ""; return; }
    const signature = JSON.stringify(trace); if (!force && traces.signature === signature) return; traces.signature = signature;
    const active = document.activeElement; const focusedSpan = active?.dataset?.spanId; const focusedBranch = active?.dataset?.branchId; const focusedTree = active?.closest("#trace-tree"); const focusedRaw = active?.tagName === "SUMMARY" && active.closest("#trace-evidence");
    const summary = trace.summary || {}; const usage = trace.usage || {}; const subtotal = usage.known_subtotal_tokens || {};
    const tokenNote = traceTokenText(usage) === "—" ? `${t("已知 Token 小计")} ${known(subtotal.input_tokens) && known(subtotal.output_tokens) ? count(subtotal.input_tokens + subtotal.output_tokens) : "—"}` : `${t("缓存输入")} ${count(usage.tokens?.cached_input_tokens)}`;
    $("trace-summary").replaceChildren(
      traceStat("总历时", traceDuration(summary.elapsed_ms)), traceStat("首次可见", traceDuration(summary.first_visible_ms)), traceStat("候选已验证", traceDuration(summary.verified_ms)),
      traceStat("模型轮次", count(summary.model_rounds), t("仅统计明确的模型请求边界")), traceStat("工具调用", count(summary.tool_rounds)), traceStat("Agent 续行", count(summary.agent_resumes), t("工具或权限等待后恢复的活动段。")),
      traceStat("Token 用量", traceTokenText(usage), tokenNote), traceStat("费用 / 等值", tracePriceText(trace.price), t(trace.price?.label || "价格未核实"))
    );
    text("trace-identity", [trace.project_id, trace.turn_id || trace.trace_id].filter(Boolean).join(" · "));
    text("trace-status", traceStatus(trace.status)); $("trace-status").dataset.status = trace.status;
    text("trace-price-note", "按记录费率计算；订阅模型显示 API 等值，不代表实际扣款。");
    renderTraceWaterfall(trace); renderTraceTree(trace); renderTraceDiagnostics(trace); renderTraceCoverage(trace);
    const span = visibleTraceSpans(trace).find((span) => span.event_id === traces.activeSpan);
    if (span) showTraceEvidence(span); else { traces.activeSpan = ""; $("trace-evidence").replaceChildren(node("p", "field-help", t("选择一个阶段查看模型、状态与关联记录。"))); }
    if (focusedSpan) { const container = focusedTree ? $("trace-tree") : $("trace-waterfall"); [...container.querySelectorAll("button[data-span-id]")].find((button) => button.dataset.spanId === focusedSpan)?.focus({ preventScroll: true }); }
    if (focusedBranch) [...$("trace-tree").querySelectorAll("summary[data-branch-id]")].find((summary) => summary.dataset.branchId === focusedBranch)?.focus({ preventScroll: true });
    if (focusedRaw) $("trace-evidence").querySelector(".trace-raw > summary")?.focus({ preventScroll: true });
    if (!force) window.scrollTo(...scroll);
  }
  async function refreshTraces() {
    if (traces.loading || document.visibilityState === "hidden") return;
    traces.loading = true;
    try {
      const payload = await request("/api/traces"); if (!Array.isArray(payload.traces)) throw new Error("invalid_traces");
      const previous = selectedTrace();
      const items = payload.traces.filter((trace) => trace && typeof trace.trace_id === "string" && Array.isArray(trace.spans)).sort((a, b) => (Date.parse(b.started_at) || 0) - (Date.parse(a.started_at) || 0));
      let missingSelection = false;
      if (previous && !items.some((trace) => trace.trace_id === previous.trace_id)) {
        // A late parent can move the same events under a new trace id. Follow
        // only an unambiguous exact event match within this project/session.
        const ids = new Set(previous.spans.map((span) => span.event_id));
        const matches = items.filter((trace) => trace.project_id === previous.project_id && trace.session_id === previous.session_id && trace.spans.some((span) => ids.has(span.event_id)));
        if (matches.length === 1) traces.selected = matches[0].trace_id;
        else { items.push(previous); missingSelection = true; }
      }
      traces.items = items;
      traces.warnings = Array.isArray(payload.warnings) ? payload.warnings.filter((warning) => typeof warning === "string") : [];
      traces.loaded = true; renderTrace();
      text("trace-notice", "当前任务不在最新记录中，保留上次读取结果。"); $("trace-notice").hidden = !missingSelection;
      text("trace-live", `${t("实时更新")} · ${new Date().toLocaleTimeString(locale, { hour12: false })}`);
    } catch {
      text("trace-notice", traces.loaded ? "任务读取失败，保留上次记录。" : "暂时无法读取任务记录。"); $("trace-notice").hidden = false;
      text("trace-live", "连接未确认");
    } finally { traces.loading = false; }
  }
  async function downloadTrace() {
    const trace = selectedTrace(); if (!trace) return;
    $("trace-download").disabled = true;
    try {
      const payload = await request(`/api/traces/export?trace_id=${encodeURIComponent(trace.trace_id)}`);
      const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
      const link = node("a"); link.href = url; link.download = `turn-${trace.trace_id.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 100)}.json`; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch { text("trace-notice", "Trace 下载失败，请重试。"); $("trace-notice").hidden = false; }
    finally { $("trace-download").disabled = false; }
  }

  function populateRates(payload) {
    if (!Array.isArray(payload.rates)) throw new Error("invalid_rates");
    state.rates = payload.rates.filter((rate) => rate && typeof rate.provider === "string" && typeof rate.model === "string");
    const select = $("rate-preset");
    for (const [index, rate] of state.rates.entries()) { const option = node("option", "", label(rate.label, `${rate.provider} / ${rate.model}`)); option.value = String(index); select.append(option); }
    state.ratesLoaded = true;
    if (!state.selectedRate) text("rate-source", "请自行选择与模型、上下文长度及服务档位匹配的费率。");
  }

  async function refresh() {
    if (state.loading || document.visibilityState === "hidden") return;
    state.loading = true; $("refresh").disabled = true; $("overview").setAttribute("aria-busy", "true");
    const shouldLoadRates = !state.ratesLoaded;
    const requests = [request("/api/events"), request("/api/health")];
    if (shouldLoadRates) requests.push(request("/api/rates"));
    const results = await Promise.allSettled(requests);
    const [eventsResult, healthResult, ratesResult] = results;
    let eventsValid = false;
    if (eventsResult.status === "fulfilled" && Array.isArray(eventsResult.value.events)) {
      const unique = new Map(); let excluded = 0;
      for (const event of eventsResult.value.events) {
        if (!event || typeof event.event_id !== "string" || !event.event_id || !["studio", "codex", "hub"].includes(event.source)) { excluded += 1; continue; }
        unique.set(event.event_id, event);
      }
      state.events = [...unique.values()].sort((a, b) => (Date.parse(b.started_at) || 0) - (Date.parse(a.started_at) || 0));
      state.warnings = Array.isArray(eventsResult.value.warnings) ? eventsResult.value.warnings.filter((warning) => typeof warning === "string" && warning.trim()) : [];
      state.excluded = excluded; state.loaded = true; eventsValid = true;
      text("sync-time", t("更新于 {time}", { time: new Date().toLocaleTimeString(locale, { hour12: false }) }));
    }
    const online = healthResult.status === "fulfilled" && healthResult.value.status === "ok";
    text("health", online ? "本地服务在线" : "连接未确认"); $("health").classList.toggle("online", online);
    if (shouldLoadRates && ratesResult?.status === "fulfilled") { try { populateRates(ratesResult.value); } catch { /* Manual prices remain usable. */ } }
    text("load-notice", state.loaded ? "刷新失败，仍显示上次成功读取的数据。请稍后重试。" : "暂时无法读取用量。确认本地服务已启动后点击刷新。");
    $("load-notice").hidden = eventsValid;
    if (!state.ratesLoaded) text("rate-source", "参考费率暂不可用，可手动填写单价；刷新后会重试读取。");
    renderOverview(); state.loading = false; $("refresh").disabled = false; $("overview").setAttribute("aria-busy", "false");
  }

  function invalidateQuote() {
    state.quoteVersion += 1;
    text("quote-label", "估算费用"); text("quote-amount", "—"); text("quote-detail-title", "计算依据"); text("quote-detail", "填写用量并选择费率后计算，未知项目保留为空。");
    $("form-error").hidden = true;
    for (const element of form.querySelectorAll("[aria-invalid]")) element.removeAttribute("aria-invalid");
  }

  function quoteContext(edited = false) {
    const event = state.quoteEvent;
    if (!event) { text("quote-context", "手动填写用量，并选择或填写单价。"); $("event-usage").hidden = true; return; }
    text("quote-context", `${label(event.model, "模型未记录")} · ${sourceLabels[event.source]} · ${t(statusLabels[event.status] || "已记录")} · ${t(billingLabels[event.billing_mode] || billingLabels.unknown)}${edited ? ` · ${t("用量已修改")}` : ""}`);
    const summary = [];
    for (const [field, title] of [["input_tokens", "总输入"], ["output_tokens", "总输出"], ["cached_input_tokens", "缓存读取"], ["cache_write_input_tokens", "缓存写入"]]) {
      const raw = form.elements.namedItem(field).value.trim();
      const value = raw !== "" && /^\d+$/.test(raw) && known(Number(raw)) ? Number(raw) : null;
      const item = node("div"); item.append(node("dt", "", t(title)), node("dd", "", count(value))); summary.push(item);
    }
    $("event-usage").replaceChildren(...summary); $("event-usage").hidden = false;
  }

  function fillEvent(event = null) {
    state.quoteEvent = event; form.reset(); state.selectedRate = null; selectRate();
    for (const field of usageFields) form.elements.namedItem(field).value = known(event?.tokens?.[field]) ? String(event.tokens[field]) : "";
    $("advanced-fields").open = !event; $("estimate-details").open = false;
    quoteContext();
    if (!$("calculator").open) $("calculator").showModal();
    if (event) $("rate-preset").focus(); else form.elements.namedItem("input_tokens").focus();
  }

  function selectRate() {
    const value = $("rate-preset").value;
    state.selectedRate = value === "" ? null : state.rates[Number(value)];
    for (const field of rateFields) form.elements.namedItem(`rate_${field}`).value = state.selectedRate?.[field] ?? "";
    const source = $("rate-source"); source.replaceChildren();
    if (!state.selectedRate) { source.textContent = t("请自行选择与模型、上下文长度及服务档位匹配的费率。"); }
    else {
      const rate = state.selectedRate;
      source.append(document.createTextNode(`${label(rate.effective_date, "日期未记录")} · `));
      try { const url = new URL(rate.source_url); if (url.protocol !== "https:") throw new Error("invalid_source"); const link = node("a", "", t("费率来源")); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; source.append(link); } catch { source.append(document.createTextNode(t("来源未记录"))); }
      source.append(document.createTextNode(t("。仅应用所选费率，不自动判断上下文长度或服务档位。")));
    }
    invalidateQuote();
  }

  function fieldError(name, message) { $("advanced-fields").open = true; const element = form.elements.namedItem(name); element.setAttribute("aria-invalid", "true"); element.focus(); text("form-error", message); $("form-error").hidden = false; return null; }

  function quotePayload() {
    const usage = {}; const rate = { provider: state.selectedRate?.provider || state.quoteEvent?.provider || "custom", model: state.selectedRate?.model || state.quoteEvent?.model || "custom" };
    for (const field of usageFields) {
      const raw = form.elements.namedItem(field).value.trim();
      if (raw !== "" && (!/^\d+$/.test(raw) || !Number.isSafeInteger(Number(raw)))) return fieldError(field, "Token 数量请填写非负整数；未知项目保留空白。");
      usage[field] = raw === "" ? null : Number(raw);
    }
    if (usage.input_tokens !== null && (usage.cached_input_tokens ?? 0) + (usage.cache_write_input_tokens ?? 0) > usage.input_tokens) return fieldError("input_tokens", "总输入必须包含缓存读取与缓存写入，不能小于两者之和。");
    if (usage.cache_write_1h_input_tokens !== null && usage.cache_write_input_tokens !== null && usage.cache_write_1h_input_tokens > usage.cache_write_input_tokens) return fieldError("cache_write_1h_input_tokens", "1 小时缓存写入不能超过缓存写入总量。");
    if (usage.reasoning_output_tokens !== null && usage.output_tokens !== null && usage.reasoning_output_tokens > usage.output_tokens) return fieldError("reasoning_output_tokens", "推理输出不能超过总输出。");
    for (const field of rateFields) {
      const raw = form.elements.namedItem(`rate_${field}`).value.trim();
      if (raw !== "" && !/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(raw)) return fieldError(`rate_${field}`, "单价请填写非负十进制数，单位为美元 / 百万 Token；未知单价保留空白。");
      rate[field] = raw === "" ? null : raw;
    }
    return { usage, rate };
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault(); if ($("calculate").disabled) return;
    const payload = quotePayload(); if (!payload) return;
    const version = state.quoteVersion; $("calculate").disabled = true;
    text("quote-label", "正在计算"); text("quote-amount", "—"); text("quote-detail", "正在按各项用量与单价计算。");
    try {
      const result = await request("/api/quote", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (version !== state.quoteVersion) return;
      const validAmount = (value) => typeof value === "string" && /^\d+(?:\.\d+)?$/.test(value);
      if (result.currency !== "USD" || !validAmount(result.known_subtotal_usd) || (result.amount_usd !== null && !validAmount(result.amount_usd)) || !Array.isArray(result.missing)) throw new Error("invalid_quote");
      if (result.amount_usd === null) {
        text("quote-label", "费用尚不完整"); text("quote-amount", "—");
        const missing = [...new Set(result.missing.map((key) => t(missingLabels[key] || "其他用量或单价")))];
        text("quote-detail-title", t("还缺少 {count} 项", { count: missing.length }));
        text("quote-detail", t("已知部分：${subtotal} USD。仍缺少：{missing}。", { subtotal: result.known_subtotal_usd, missing: missing.join(locale === "en" ? ", " : "、") || t("完整用量或单价") }));
      } else {
        text("quote-label", "估算费用 · USD"); text("quote-amount", `$${result.amount_usd}`); text("quote-detail-title", "计算依据");
        text("quote-detail", state.quoteEvent?.billing_mode === "subscription_equivalent" ? "本条为订阅用量，结果为单价换算的费用等值。" : "按填写的用量和单价计算。");
      }
    } catch { if (version === state.quoteVersion) { text("quote-label", "暂时无法计算"); text("quote-amount", "—"); text("quote-detail", "请确认本地服务连接与输入内容后重试。"); } }
    finally { $("calculate").disabled = false; }
  });

  form.addEventListener("input", (event) => {
    if (event.target === $("rate-preset")) return;
    if (rateFields.some((field) => event.target.name === `rate_${field}`) && state.selectedRate) { state.selectedRate = null; $("rate-preset").value = ""; text("rate-source", "已修改为自定义单价。请确认它们适用于本次用量。"); }
    if (usageFields.includes(event.target.name)) quoteContext(true);
    invalidateQuote();
  });
  $("rate-preset").addEventListener("change", selectRate);
  $("event-sort").addEventListener("change", () => { state.sort = $("event-sort").value; state.visibleEvents = 10; renderOverview(); });
  $("project-filter").addEventListener("change", () => { state.project = $("project-filter").value; state.visibleEvents = 10; state.visibleGroups = 10; renderOverview(); });
  $("operation-group").addEventListener("change", () => { state.group = $("operation-group").value; state.visibleGroups = 10; renderOverview(); });
  $("more-operations").addEventListener("click", () => { state.visibleGroups += 10; renderOverview(); });
  $("codex-sources").addEventListener("toggle", () => { if ($("codex-sources").open && !state.sourcesLoaded) readSources(); });
  $("reload-sources").addEventListener("click", readSources);
  $("source-form").addEventListener("submit", applySources);
  for (const button of document.querySelectorAll("[data-source]")) button.addEventListener("click", () => { state.source = button.dataset.source; state.visibleEvents = 10; for (const filter of document.querySelectorAll("[data-source]")) filter.setAttribute("aria-pressed", String(filter === button)); renderOverview(); });
  $("open-calculator").addEventListener("click", () => fillEvent());
  $("close-calculator").addEventListener("click", () => $("calculator").close());
  $("refresh").addEventListener("click", () => { refresh(); refreshTraces(); });
  $("trace-select").addEventListener("change", () => { traces.selected = $("trace-select").value; traces.activeSpan = ""; renderTrace(true); });
  $("trace-project").addEventListener("change", () => { traces.project = $("trace-project").value; renderTrace(true); });
  $("trace-show-transport").addEventListener("change", () => { traces.showTransport = $("trace-show-transport").checked; renderTrace(true); });
  $("trace-download").addEventListener("click", downloadTrace);
  $("more-events").addEventListener("click", () => { state.visibleEvents += 10; renderOverview(); });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { refresh(); refreshTraces(); } else text("trace-live", "已暂停更新"); });
  const timer = setInterval(() => { if (document.visibilityState === "visible") refresh(); }, 30000);
  const traceTimer = setInterval(refreshTraces, 3000);
  window.addEventListener("pagehide", () => { clearInterval(timer); clearInterval(traceTimer); for (const controller of controllers) controller.abort(); }, { once: true });
  refresh(); refreshTraces();
})();

import { useEffect, useRef, useState, type FormEvent } from "react";
import { applicationUrl, type AppearancePreferences } from "../../../shared-web/src/appearance.js";
import type { createClient } from "./api/generated/client";
import {
  getFabProfilesApiFabProfilesGet, prepareFabApiFabPreparePost, sendFabApiFabSendPost,
  type FabProfile, type FabPrepareResult, type FabSendResult,
} from "./api/generated";

type Props = {
  preferences: AppearancePreferences;
  client: ReturnType<typeof createClient>;
  readResult: <T>(request: Promise<unknown>) => Promise<T>;
};
const copy = {
  "zh-CN": {
    back: "返回 MonkeyHub", intro: "缩放与拆件，或发送已切片的打印任务。",
    prepare: "准备打印分件", prepareHelp: "粘贴本机 STL / OBJ 文件的完整路径，并选择原模型单位。",
    source: "源模型文件", unit: "原模型单位", chooseUnit: "请选择单位", scale: "模型比例", printer: "目标打印机",
    output: "输出目录（新建或空目录）", advanced: "拆件余量", xy: "XY 每侧余量（mm）", z: "顶部留量（mm）",
    prepareAction: "生成分件", preparing: "正在生成分件…", prepared: "分件已输出", next: "在 Bambu Studio 中按毫米导入 STL，选择材料、支撑并完成切片。",
    send: "发送切片文件", sendHelp: "在 Bambu Studio 导出 .gcode.3mf 后上传到机器。发送文件不会启动打印。",
    job: "已切片的文件", host: "打印机 IP 或主机名", access: "LAN 访问码", accessHelp: "访问码仅用于这次发送，不保存到设置。",
    remote: "机器端文件名（可选）", remoteHelp: "留空时保留原名，已有同名文件时停止。", timeout: "网络等待时间（秒）", sendOptions: "更多发送选项",
    check: "检查文件", sendAction: "发送文件", checking: "正在检查…", sending: "正在上传…",
    checked: "本地检查通过，尚未发送", sent: "文件已上传，打印尚未启动", target: "目标", size: "文件大小", plates: "包含打印板",
    loading: "正在读取打印机配置…", retry: "重新读取", failed: "操作未完成", accessRequired: "发送前请输入这台打印机的 LAN 访问码。",
    busyLeave: "正在处理文件，请等待操作完成。", bytes: "字节", settings: "显示设置",
  },
  en: {
    back: "Back to MonkeyHub", intro: "Scale and split models, or upload a sliced print job.",
    prepare: "Prepare print parts", prepareHelp: "Paste the full path of a local STL / OBJ file and choose its source units.",
    source: "Source model", unit: "Source units", chooseUnit: "Choose units", scale: "Model scale", printer: "Target printer",
    output: "Output directory (new or empty)", advanced: "Part clearances", xy: "Margin on each XY side (mm)", z: "Top clearance (mm)",
    prepareAction: "Create parts", preparing: "Creating parts…", prepared: "Parts exported", next: "Import the STLs in millimeters into Bambu Studio, choose material and supports, then slice.",
    send: "Upload a sliced job", sendHelp: "Export a .gcode.3mf from Bambu Studio, then upload it to the printer. Uploading does not start printing.",
    job: "Sliced file", host: "Printer IP or hostname", access: "LAN access code", accessHelp: "The access code is used for this upload and is not saved in settings.",
    remote: "Filename on printer (optional)", remoteHelp: "Leave blank to keep the original name. Existing files are not overwritten.", timeout: "Network timeout (seconds)", sendOptions: "More upload options",
    check: "Check file", sendAction: "Upload file", checking: "Checking…", sending: "Uploading…",
    checked: "Local check passed; file not sent", sent: "File uploaded; printing has not started", target: "Destination", size: "File size", plates: "Included plates",
    loading: "Reading printer profiles…", retry: "Retry", failed: "Operation did not complete", accessRequired: "Enter this printer's LAN access code before uploading.",
    busyLeave: "A file operation is still running. Wait for it to finish.", bytes: "bytes", settings: "Display settings",
  },
} as const;
const cleanPath = (value: string) => value.trim().replace(/^"(.*)"$/, "$1");
const detailOf = (cause: unknown) => {
  if (cause && typeof cause === "object" && "detail" in cause && typeof cause.detail === "string") return cause.detail;
  return cause instanceof Error ? cause.message : "The request did not complete.";
};

export function FabPage({ preferences, client, readResult }: Props) {
  const t = copy[preferences.language];
  const [profiles, setProfiles] = useState<Record<string, FabProfile> | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [profileAttempt, setProfileAttempt] = useState(0);
  const [source, setSource] = useState("");
  const [unit, setUnit] = useState("");
  const [scale, setScale] = useState("1:1");
  const [printer, setPrinter] = useState("h2s");
  const [output, setOutput] = useState("");
  const [xy, setXy] = useState("5");
  const [z, setZ] = useState("5");
  const [job, setJob] = useState("");
  const [host, setHost] = useState("");
  const [access, setAccess] = useState("");
  const [remote, setRemote] = useState("");
  const [timeout, setTimeoutSeconds] = useState("30");
  const [busy, setBusy] = useState<"prepare" | "check" | "send" | null>(null);
  const busyRef = useRef(false);
  const [prepareError, setPrepareError] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [prepared, setPrepared] = useState<FabPrepareResult | null>(null);
  const [sent, setSent] = useState<FabSendResult | null>(null);

  useEffect(() => {
    document.title = "MonkeyFab";
    let active = true;
    setProfileError(null);
    void readResult<Record<string, FabProfile>>(getFabProfilesApiFabProfilesGet({ client }))
      .then((value) => { if (active) setProfiles(value); })
      .catch((cause) => { if (active) setProfileError(detailOf(cause)); });
    return () => { active = false; };
  }, [client, readResult, profileAttempt]);
  useEffect(() => {
    const protectActiveWork = (event: BeforeUnloadEvent) => {
      if (!busyRef.current) return;
      event.preventDefault();
      event.returnValue = t.busyLeave;
    };
    window.addEventListener("beforeunload", protectActiveWork);
    return () => window.removeEventListener("beforeunload", protectActiveWork);
  }, [t.busyLeave]);

  const prepare = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busyRef.current) return;
    busyRef.current = true; setBusy("prepare"); setPrepareError(null); setPrepared(null);
    try {
      setPrepared(await readResult<FabPrepareResult>(prepareFabApiFabPreparePost({ client, body: {
        source: cleanPath(source), outputDir: cleanPath(output), printer,
        inputUnit: unit as "mm" | "cm" | "m" | "in", scale: scale.trim(), xyMarginMm: Number(xy), zClearanceMm: Number(z),
      } })));
    } catch (cause) { setPrepareError(detailOf(cause)); }
    finally { busyRef.current = false; setBusy(null); }
  };
  const send = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busyRef.current) return;
    const dryRun = (event.nativeEvent as SubmitEvent).submitter?.getAttribute("value") !== "send";
    if (!dryRun && !access.trim()) { setSendError(t.accessRequired); return; }
    const code = dryRun ? null : access;
    if (!dryRun) setAccess("");
    busyRef.current = true; setBusy(dryRun ? "check" : "send"); setSendError(null); setSent(null);
    try {
      setSent(await readResult<FabSendResult>(sendFabApiFabSendPost({ client, body: {
        source: cleanPath(job), host: host.trim(), accessCode: code,
        remoteName: remote.trim() || null, timeout: Number(timeout), dryRun,
      } })));
    } catch (cause) { setSendError(detailOf(cause)); }
    finally { busyRef.current = false; setBusy(null); }
  };
  const home = applicationUrl(window.location.origin + "/", preferences);
  return <>
    <header className="toolbar"><a className="btn" href={home}>{t.back}</a><strong className="wordmark">MonkeyFab</strong><a className="btn fab-settings-link" href={home + "#settings"}>{t.settings}</a></header>
    <main className="hub fab-page"><div className="section-heading"><div><h1>MonkeyFab</h1><p className="help">{t.intro}</p></div></div>
      {profileError ? <div className="error-message" role="alert"><p>{profileError}</p><button className="btn" type="button" onClick={() => setProfileAttempt((value) => value + 1)}>{t.retry}</button></div> : !profiles ? <p role="status">{t.loading}</p> : null}
      <div className="fab-forms">
        <form className="app-card fab-form" onSubmit={(event) => void prepare(event)} aria-busy={busy === "prepare"}>
          <h2>{t.prepare}</h2><p className="help">{t.prepareHelp}</p>
          <fieldset disabled={busy !== null || profiles === null}><label htmlFor="fab-source">{t.source}<input id="fab-source" required value={source} onChange={(event) => setSource(event.target.value)} placeholder="D:\models\building.stl" spellCheck={false} /></label>
            <div className="form-grid"><label htmlFor="fab-unit">{t.unit}<select id="fab-unit" required value={unit} onChange={(event) => setUnit(event.target.value)}><option value="" disabled>{t.chooseUnit}</option>{["mm", "cm", "m", "in"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label><label htmlFor="fab-scale">{t.scale}<input id="fab-scale" required value={scale} onChange={(event) => setScale(event.target.value)} placeholder="1:100" /></label></div>
            <label htmlFor="fab-printer">{t.printer}<select id="fab-printer" required value={printer} onChange={(event) => setPrinter(event.target.value)}>{Object.entries(profiles ?? {}).map(([key, profile]) => <option key={key} value={key}>{profile.label}</option>)}</select></label>
            {profiles?.[printer] ? <p className="help">{profiles[printer].usable_volume_mm.join(" × ")} mm</p> : null}
            <label htmlFor="fab-output">{t.output}<input id="fab-output" required value={output} onChange={(event) => setOutput(event.target.value)} placeholder="D:\prints\building-h2s" spellCheck={false} /></label>
            <details className="advanced"><summary>{t.advanced}</summary><div className="form-grid"><label htmlFor="fab-xy">{t.xy}<input id="fab-xy" type="number" required min="0" step="any" value={xy} onChange={(event) => setXy(event.target.value)} /></label><label htmlFor="fab-z">{t.z}<input id="fab-z" type="number" required min="0" step="any" value={z} onChange={(event) => setZ(event.target.value)} /></label></div></details>
            <div className="actions"><button className="btn btn--primary" type="submit">{busy === "prepare" ? t.preparing : t.prepareAction}</button></div>
          </fieldset>
          {prepareError ? <div className="error-message" role="alert"><strong>{t.failed}</strong><p>{prepareError}</p></div> : null}
          {prepared ? <div className="fab-result" role="status"><h3>{t.prepared}</h3><code>{prepared.outputDir}</code><pre>{prepared.stdout}</pre><p className="help">{t.next}</p></div> : null}
        </form>
        <form className="app-card fab-form" onSubmit={(event) => void send(event)} aria-busy={busy === "send" || busy === "check"}>
          <h2>{t.send}</h2><p className="help">{t.sendHelp}</p>
          <fieldset disabled={busy !== null || profiles === null}><label htmlFor="fab-job">{t.job}<input id="fab-job" required value={job} onChange={(event) => setJob(event.target.value)} placeholder="D:\prints\building-h2s.gcode.3mf" spellCheck={false} /></label>
            <label htmlFor="fab-host">{t.host}<input id="fab-host" required value={host} onChange={(event) => setHost(event.target.value)} placeholder="192.168.1.50" spellCheck={false} autoComplete="off" /></label>
            <label htmlFor="fab-access">{t.access}<input id="fab-access" type="password" value={access} onChange={(event) => setAccess(event.target.value)} autoComplete="off" /></label><p className="help">{t.accessHelp}</p>
            <details className="advanced"><summary>{t.sendOptions}</summary><label htmlFor="fab-remote">{t.remote}<input id="fab-remote" value={remote} onChange={(event) => setRemote(event.target.value)} placeholder="building-h2s-v2.gcode.3mf" spellCheck={false} /></label><p className="help">{t.remoteHelp}</p><label htmlFor="fab-timeout">{t.timeout}<input id="fab-timeout" type="number" required min="1" step="1" value={timeout} onChange={(event) => setTimeoutSeconds(event.target.value)} /></label></details>
            <div className="actions"><button className="btn" type="submit" value="check">{busy === "check" ? t.checking : t.check}</button><button className="btn btn--primary" type="submit" value="send">{busy === "send" ? t.sending : t.sendAction}</button></div>
          </fieldset>
          {sendError ? <div className="error-message" role="alert"><strong>{t.failed}</strong><p>{sendError}</p></div> : null}
          {sent ? <div className="fab-result" role="status"><h3>{sent.status === "uploaded" ? t.sent : t.checked}</h3><dl><dt>{t.target}</dt><dd>{sent.host}{sent.remote_path}</dd><dt>{t.size}</dt><dd>{sent.bytes.toLocaleString()} {t.bytes}</dd><dt>{t.plates}</dt><dd>{sent.plates.join(", ")}</dd></dl></div> : null}
        </form>
      </div>
    </main>
  </>;
}

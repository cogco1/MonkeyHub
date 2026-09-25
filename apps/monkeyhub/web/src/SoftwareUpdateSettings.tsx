import { useCallback, useEffect, useRef, useState } from "react";
import type { Language } from "../../../shared-web/src/appearance.js";
import { translateMessage } from "../../../shared-web/src/i18n.js";
import { hubCopyCatalog } from "./i18n/catalogs";
import type { UpdateStatus } from "./api/generated/types.gen";

export type RestartBlocker = "drafts" | "model" | "settings" | "busy" | null;
type Props = { language: Language; open: boolean; restartBlocker: RestartBlocker; onRestarting: (restarting: boolean) => void };
const maxPatchBytes = 256 * 1024 * 1024;
const sizeOf = (bytes: number) => bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KiB` : `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
const tagged = (version: string) => /^\d+\.\d+\.\d+$/.test(version) ? `v${version}` : version;
// New lines of this view stay here until the Hub catalogs are free to edit
// (GH-244 holds them). The English ready line is the catalog's wording.
const localCopy = {
  en: {
    readyNextLaunch: (version: string) => `${version} is ready and takes effect the next time MonkeyHub starts.`,
    readyRestart: (version: string) => `${version} is ready. Restart to update when your work is saved.`,
    verifying: (version: string) => `verifying and preparing ${version}…`,
    failed: "The update did not finish.",
    checkAgain: "Check again",
  },
  "zh-CN": {
    readyNextLaunch: (version: string) => `${tagged(version)} 已准备好 · 下次启动生效`,
    readyRestart: (version: string) => `${tagged(version)} 已准备好 · 保存工作后即可重启更新`,
    verifying: (version: string) => `正在校验并准备 ${version}…`,
    failed: "更新未完成。",
    checkAgain: "重新检查",
  },
} satisfies Record<Language, unknown>;

async function updateRequest(path: string, options?: RequestInit): Promise<UpdateStatus> {
  const response = await fetch(`/api/updates/${path}`, { cache: "no-store", ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
  return data;
}

/** The existing desktop lifecycle applies the staged version; this page never reloads itself. */
export function SoftwareUpdateSettings({ language, open, restartBlocker, onRestarting }: Props) {
  const t = hubCopyCatalog[language];
  const copy = localCopy[language];
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [request, setRequest] = useState<"status" | "upload" | "apply" | "check" | "auto" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [restartRequested, setRestartRequested] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const requestLock = useRef(false);
  const waitingForRestart = useRef(false);
  const blockerRef = useRef(restartBlocker); blockerRef.current = restartBlocker;

  const reconcileStatus = useCallback((current: UpdateStatus) => {
    setStatus(current);
    // Only a successful read of a settled server state can release an
    // uncertain restart. A lost POST response is not a rejection.
    if (["idle", "ready", "failed"].includes(current.state)) {
      waitingForRestart.current = false; setRestartRequested(false); onRestarting(false);
    }
  }, [onRestarting]);
  const refresh = useCallback(async () => {
    if (requestLock.current) return;
    requestLock.current = true; setRequest("status");
    try {
      reconcileStatus(await updateRequest("status")); setError(null);
    } catch (cause) {
      // The owned server disappears during a normal restart. Keep waiting for
      // its window to close or a status read to confirm it did not restart.
      if (!waitingForRestart.current) setError(cause instanceof Error ? cause.message : String(cause));
    }
    finally { requestLock.current = false; setRequest(null); }
  }, [reconcileStatus]);
  useEffect(() => { if (open) void refresh(); }, [open, refresh]);
  // A check, download or prepared patch can take time. Status reads never
  // apply it or restart the application, even when this dialog is closed.
  const checking = status?.check?.state === "checking" || status?.check?.state === "downloading";
  useEffect(() => {
    if (!open || (status?.state !== "preparing" && status?.state !== "applying" && !restartRequested && !checking)) return;
    const timer = window.setInterval(() => { void refresh(); }, 1000);
    return () => window.clearInterval(timer);
  }, [open, status?.state, restartRequested, checking, refresh]);

  async function send(kind: "check" | "auto", path: string, options: RequestInit) {
    if (requestLock.current || restartRequested) return;
    requestLock.current = true; setRequest(kind); setError(null);
    try { setStatus(await updateRequest(path, options)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { requestLock.current = false; setRequest(null); }
  }
  async function prepare(file: File) {
    if (requestLock.current || restartRequested) return;
    if (!file.name.toLowerCase().endsWith(".zip")) { setError(t.updateZipRequired); return; }
    if (file.size > maxPatchBytes) { setError(t.updateTooLarge); return; }
    requestLock.current = true; setRequest("upload"); setError(null); setSelectedFile(file.name);
    try {
      const bytes = await file.arrayBuffer();
      setStatus(await updateRequest("prepare", { method: "POST", headers: {
        "Content-Type": "application/octet-stream", "X-MonkeyHub-Local-Patch": "1",
      }, body: bytes }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { requestLock.current = false; setRequest(null); }
  }
  async function apply() {
    if (requestLock.current || blockerRef.current || !status?.canApply || restartRequested || error) return;
    requestLock.current = true; setRequest("apply"); setError(null);
    try {
      // Refresh immediately before the destructive boundary. A busy runtime,
      // stale stage, or a newly edited local draft must still prevent restart.
      const current = await updateRequest("status"); setStatus(current);
      if (blockerRef.current || !current.canApply) return;
      waitingForRestart.current = true; setRestartRequested(true); onRestarting(true);
      setStatus(await updateRequest("apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      if (waitingForRestart.current) {
        // Admission may already have succeeded. Reconcile by reading, never
        // replay the mutation; polling keeps the lock if this read also fails.
        try { reconcileStatus(await updateRequest("status")); } catch { /* Still uncertain. */ }
      } else onRestarting(false);
    }
    finally { requestLock.current = false; setRequest(null); }
  }
  const blocked = restartBlocker === "drafts" ? t.updateDraftsBlocked : restartBlocker === "model" ? t.updateModelBlocked
    : restartBlocker === "settings" ? t.updateSettingsBlocked : restartBlocker === "busy" ? t.updateBusyBlocked : null;
  const working = request === "upload" || request === "apply" || status?.state === "preparing" || status?.state === "applying" || restartRequested;
  const failed = status?.state === "failed";
  // A failed transaction offers no prepared version, whatever an older reply named.
  const prepared = failed ? null : status?.prepared ?? null;
  const preparedName = prepared?.releaseVersion ?? prepared?.targetVersion ?? "";
  const check = status?.check;
  const latest = check?.latestVersion ?? "";
  const version = { version: latest };
  // Once downloaded, a check verifies, stages and loads its version while the update is preparing.
  const checkResult = !check || check.state === "never" ? t.updateNeverChecked
    : check.state === "checking" ? (status?.state === "preparing" && latest ? copy.verifying(latest) : t.updateChecking)
      : check.state === "downloading" ? translateMessage(t, "updateDownloading", version)
        : check.state === "ready" ? translateMessage(t, "updateCheckReady", version)
          : check.state === "needs-full-update" ? translateMessage(t, "updateNeedsFull", version)
            : check.state === "error" ? t.updateCheckFailed : t.updateUpToDate;
  // Only a finished check has a time; one in progress has not got one yet.
  const checkedAt = check?.checkedAt && !checking ? new Date(check.checkedAt).toLocaleString(language, { dateStyle: "medium", timeStyle: "short" }) : null;
  const lastCheck = status?.mode === "local"
    ? translateMessage(t, "updateLastCheck", { result: checkedAt ? `${checkedAt} · ${checkResult}` : checkResult }) : null;
  // One line says where the update stands. While nothing is prepared,
  // preparing by hand or applying, the check line is that line.
  const phase = request === "status" && !status ? t.updateLoading : request === "upload" ? t.updateUploading
    : restartRequested || status?.state === "applying" ? t.updateApplying
      : status?.state === "preparing" ? (checking ? null : t.updatePreparing)
        : status?.state === "ready" ? (preparedName && status.nextLaunch ? copy.readyNextLaunch(preparedName)
          : prepared?.releaseVersion ? copy.readyRestart(preparedName) : t.updateReady)
          : failed ? copy.failed : null;
  const line = phase ?? lastCheck ?? status?.message ?? "";
  const reason = error ?? status?.error?.detail ?? (failed ? t.updateFailed : null);
  const checkDetails = <>
    {check?.state === "needs-full-update" && check.releaseUrl && <p className="help">{t.updateFullHelp} <span className="software-update__url">{check.releaseUrl}</span></p>}
    {check?.state === "error" && check.detail && check.detail !== reason && <p className="help">{check.detail}</p>}
  </>;
  // The next action after a failure is to check again.
  const retry = failed || check?.state === "error";
  return <section className="settings software-update" aria-labelledby="software-update-heading">
    <div className="settings-section">
      <h2 id="software-update-heading">{t.softwareUpdate}</h2>
      <dl className="software-update__versions">
        <div><dt>{t.updateCurrent}</dt><dd title={status?.currentRevision ?? undefined}>{status?.releaseVersion
          ? `${status.releaseVersion} · ${status.currentVersion}` : status?.currentVersion ?? "—"}</dd></div>
        {prepared && <>
          <div><dt>{t.updateTarget}</dt><dd title={prepared.targetRevision}>{prepared.releaseVersion
            ? `${prepared.releaseVersion} · ${prepared.targetVersion}` : prepared.targetVersion}</dd></div>
          <div><dt>{t.updateChangedBytes}</dt><dd>{sizeOf(prepared.changedBytes)}</dd></div>
        </>}
      </dl>
      {status?.mode === "unsupported" && <p className="help">{t.updateUnsupported}</p>}
      <p className="software-update__status" role="status" aria-live="polite">{line}</p>
      {phase === null && checkDetails}
      {status?.message && line !== status.message && <p className="help">{status.message}</p>}
      {reason && <p className="error-message" role="alert">{reason}</p>}
      {blocked && prepared && <p className="software-update__blocker" id="software-update-blocker" role="status">{blocked}</p>}
      {selectedFile && <p className="software-update__file">{selectedFile}</p>}
      <input ref={fileInput} type="file" accept=".zip,application/zip" hidden aria-label={t.updateChoosePatch}
        disabled={working || status?.mode !== "local"} onChange={(event) => {
          const file = event.target.files?.[0]; event.target.value = ""; if (file) void prepare(file);
        }} />
      <div className="actions software-update__actions">
        {prepared && <button type="button" className="btn btn--primary" disabled={Boolean(request) || working || !status?.canApply || Boolean(blocked) || Boolean(error)}
          aria-describedby={blocked ? "software-update-blocker" : "software-update-recovery"} onClick={() => void apply()}>{t.updateRestart}</button>}
        {status?.mode === "local" && <button type="button" className={retry && !prepared ? "btn btn--primary" : "btn"}
          disabled={Boolean(request) || working || checking}
          onClick={() => void send("check", "check", { method: "POST" })}>{retry ? copy.checkAgain : t.updateCheckNow}</button>}
        <button type="button" className="btn" disabled={Boolean(request) || working} onClick={() => void refresh()}>{t.updateRefresh}</button>
        {status?.mode === "local" && <button type="button" className="btn" disabled={Boolean(request) || working}
          aria-describedby="software-update-trust" onClick={() => fileInput.current?.click()}>{t.updateChoosePatch}</button>}
      </div>
      {status?.mode === "local" && <div className="software-update__auto">
        <label className="software-update__toggle">
          <input type="checkbox" role="switch" checked={Boolean(status.autoUpdate)} disabled={Boolean(request) || working}
            aria-describedby="software-update-auto-help" onChange={(event) => void send("auto", "settings", {
              method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ autoUpdate: event.target.checked }),
            })} />
          <span>{status.autoUpdate ? t.updateAutoOn : t.updateAutoOff}</span>
        </label>
        {phase !== null && lastCheck && <><p className="software-update__check">{lastCheck}</p>{checkDetails}</>}
        <p className="help" id="software-update-auto-help">{t.updateAutoHelp}</p>
      </div>}
      {status?.mode === "local" && <p className="help" id="software-update-trust">{t.updateTrustLocal}</p>}
      <p className="help" id="software-update-recovery">{t.updateRecovery}</p>
    </div>
  </section>;
}

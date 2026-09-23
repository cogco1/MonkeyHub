import { useCallback, useEffect, useRef, useState } from "react";
import type { Language } from "../../../shared-web/src/appearance.js";
import { hubCopyCatalog } from "./i18n/catalogs";
import type { UpdateStatus } from "./api/generated/types.gen";

export type RestartBlocker = "drafts" | "model" | "settings" | "busy" | null;
type Props = { language: Language; open: boolean; restartBlocker: RestartBlocker; onRestarting: (restarting: boolean) => void };
const maxPatchBytes = 256 * 1024 * 1024;
const sizeOf = (bytes: number) => bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KiB` : `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;

async function updateRequest(path: string, options?: RequestInit): Promise<UpdateStatus> {
  const response = await fetch(`/api/updates/${path}`, { cache: "no-store", ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
  return data;
}

/** The existing desktop lifecycle applies the staged version; this page never reloads itself. */
export function SoftwareUpdateSettings({ language, open, restartBlocker, onRestarting }: Props) {
  const t = hubCopyCatalog[language];
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [request, setRequest] = useState<"status" | "upload" | "apply" | null>(null);
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
  // A prepared patch can take time to verify. Status reads never apply it or
  // restart the application, even when the user closes this settings dialog.
  useEffect(() => {
    if (!open || (status?.state !== "preparing" && status?.state !== "applying" && !restartRequested)) return;
    const timer = window.setInterval(() => { void refresh(); }, 1000);
    return () => window.clearInterval(timer);
  }, [open, status?.state, restartRequested, refresh]);

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
  const phase = request === "upload" ? t.updateUploading : restartRequested || status?.state === "applying" ? t.updateApplying
    : status?.state === "preparing" ? t.updatePreparing : status?.state === "ready" ? t.updateReady
      : status?.state === "failed" ? t.updateFailed : request === "status" && !status ? t.updateLoading : null;
  return <section className="settings software-update" aria-labelledby="software-update-heading">
    <div className="settings-section">
      <h2 id="software-update-heading">{t.softwareUpdate}</h2>
      <dl className="software-update__versions">
        <div><dt>{t.updateCurrent}</dt><dd title={status?.currentRevision ?? undefined}>{status?.currentVersion ?? "—"}</dd></div>
        {status?.prepared && <>
          <div><dt>{t.updateTarget}</dt><dd title={status.prepared.targetRevision}>{status.prepared.targetVersion}</dd></div>
          <div><dt>{t.updateChangedBytes}</dt><dd>{sizeOf(status.prepared.changedBytes)}</dd></div>
        </>}
      </dl>
      {status && <p className="help">{status.mode === "unsupported" ? t.updateUnsupported : t.updateLocalMode}</p>}
      {status?.mode === "local" && <p className="help" id="software-update-trust">{t.updateTrustLocal}</p>}
      {selectedFile && <p className="software-update__file">{selectedFile}</p>}
      <p className="software-update__status" role="status" aria-live="polite">{phase ?? status?.message ?? ""}</p>
      {status?.message && phase && <p className="help">{status.message}</p>}
      {(error || status?.error) && <p className="error-message" role="alert">{error ?? status?.error?.detail}</p>}
      {blocked && status?.prepared && <p className="software-update__blocker" id="software-update-blocker" role="status">{blocked}</p>}
      <input ref={fileInput} type="file" accept=".zip,application/zip" hidden aria-label={t.updateChoosePatch}
        disabled={working || status?.mode !== "local"} onChange={(event) => {
          const file = event.target.files?.[0]; event.target.value = ""; if (file) void prepare(file);
        }} />
      <div className="actions software-update__actions">
        <button type="button" className="btn" disabled={Boolean(request) || working} onClick={() => void refresh()}>{t.updateRefresh}</button>
        {status?.mode === "local" && <button type="button" className="btn" disabled={Boolean(request) || working}
          aria-describedby="software-update-trust" onClick={() => fileInput.current?.click()}>{t.updateChoosePatch}</button>}
        {status?.prepared && <button type="button" className="btn btn--primary" disabled={Boolean(request) || working || !status.canApply || Boolean(blocked) || Boolean(error)}
          aria-describedby={blocked ? "software-update-blocker" : "software-update-recovery"} onClick={() => void apply()}>{t.updateRestart}</button>}
      </div>
      <p className="help" id="software-update-recovery">{t.updateRecovery}</p>
    </div>
  </section>;
}

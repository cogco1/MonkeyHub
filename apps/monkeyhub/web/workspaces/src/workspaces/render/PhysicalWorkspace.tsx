import { useCallback, useEffect, useRef, useState } from "react";
import { useConnection, useStudio } from "../../api/ProjectRuntimeContext";
import type { RenderJobDto, SourceDocumentDto } from "../../api/generated";
import PhysicalPreview from "./PhysicalPreview";
import { ImageThumbnail } from "./RenderResults";
import { type PhysicalScene, type Geometry, type SceneState, type Vec3, type ImageRef, type Material, penguinSuggestions } from "./sceneTypes";

type Drawing = { document: SourceDocumentDto; status: string; reason: string | null; recipe: { view: string; geometryRevision: string; drawingRevision: string; dimensions: { id: string; valueMeters: number; status: string }[]; featureReferences: { id: string; status: string; reason: string }[] } };
function Numeric({ label, value, onChange, step = .1 }: { label: string; value: number; onChange(v: number): void; step?: number }) {
  return <label>{label}<input type="number" step={step} value={value} onChange={event => { const v = event.target.valueAsNumber;if (Number.isFinite(v)) onChange(v); }} /></label>;
}
function VectorInput({ label, value, onChange }: { label: string; value: Vec3; onChange(v: Vec3): void }) {
  return <fieldset className="physical-vector"><legend>{label}</legend>{value.map((v, i) => <Numeric key={i} label={`${label} ${["X", "Y", "Z"][i]}`} value={v} onChange={n => { const next = [...value] as Vec3;next[i] = n;onChange(next); }} />)}</fieldset>;
}

export default function PhysicalWorkspace({ projectId, active, zh }: { projectId: string; active: boolean; zh: boolean }) {
  const connection = useConnection(), studio = useStudio();
  const [state, setState] = useState<SceneState | null>(null), [value, setValue] = useState<PhysicalScene | null>(null), [geometry, setGeometry] = useState<Geometry | null>(null);
  const [dirty, setDirty] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const [documents, setDocuments] = useState<SourceDocumentDto[]>([]), [drawings, setDrawings] = useState<Drawing[]>([]), [jobs, setJobs] = useState<RenderJobDto[]>([]);
  const [selectedMaterial, setSelectedMaterial] = useState("body"), [suggestion, setSuggestion] = useState(false), [features, setFeatures] = useState("");
  const urls = useRef(new Map<string, Promise<string>>());
  const readSequence = useRef(0), editSequence = useRef(0);
  const label = (en: string, cn: string) => zh ? cn : en;
  const request = useCallback(async <T,>(path: string, method: "GET" | "POST" | "PUT" = "GET", body?: unknown): Promise<T> => {
    const result = await connection.client.request<T>({ url: path, method, ...(body === undefined ? {} : { body, headers: { "Content-Type": "application/json" } }) });
    if (result.error) throw new Error((result.error as { detail?: string }).detail ?? "Project request failed");
    return result.data as T;
  }, [connection]);
  const act = async (work: () => Promise<void>) => { setBusy(true);setError(null);try { await work(); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(false); } };
  const refresh = useCallback(async () => {
    const read = ++readSequence.current, edit = editSequence.current;
    const scene = await request<SceneState>("/api/render/scene");
    const [geometry, documents, drawings, jobs] = await Promise.all([
      scene.scene ? request<Geometry>("/api/render/geometry") : Promise.resolve(null),
      studio.documents(), request<Drawing[]>("/api/render/drawings"), studio.renderJobs(),
    ]);
    // Apply a coherent read only if no newer read or local edit overtook it.
    if (read !== readSequence.current || edit !== editSequence.current) return;
    setState(scene);setValue(scene.scene);setDirty(false);setGeometry(geometry);
    setDocuments(documents.documents);setDrawings(drawings);setJobs(jobs.jobs.filter(j => j.execution === "host"));
  }, [request, studio]);
  useEffect(() => () => { readSequence.current++; }, [refresh]);
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty;
  useEffect(() => { if (active && !dirtyRef.current) void act(refresh); }, [active, refresh]);
  useEffect(() => {
    if (!active || !jobs.some(j => j.status === "queued" || j.status === "running")) return;
    const timer = window.setInterval(() => { void studio.renderJobs().then(result => setJobs(result.jobs.filter(j => j.execution === "host"))).catch(cause => setError(String(cause))); }, 2000);
    return () => window.clearInterval(timer);
  }, [active, jobs, studio]);
  useEffect(() => () => { for (const url of urls.current.values()) void url.then(URL.revokeObjectURL);urls.current.clear(); }, []);
  const imageUrl = useCallback((ref: ImageRef) => {
    const key = JSON.stringify(ref);let promise = urls.current.get(key);
    if (!promise) { promise = studio.documentFile(ref.runId, ref.assetSha256, "scene-image.png", ref.revisionRef).then(file => URL.createObjectURL(file));urls.current.set(key, promise); }
    return promise;
  }, [studio]);
  const edit = (change: (scene: PhysicalScene) => void) => { editSequence.current++;setValue(old => { if (!old) return old; const next = structuredClone(old);change(next);return next; });setDirty(true); };
  const save = () => act(async () => {
    if (!value) return;const next = await request<SceneState>("/api/render/scene", "PUT", { expectedRevision: state?.sceneRevision ?? null, scene: value });
    setState(old => ({ ...next, cyclesAvailable: old?.cyclesAvailable }));setValue(next.scene);setDirty(false);
  });
  const importModel = (file: File | undefined) => act(async () => {
    if (!file) return;
    const data = new Uint8Array(await file.arrayBuffer());let binary = "";
    for (let i = 0; i < data.length; i += 16384) binary += String.fromCharCode(...data.subarray(i, i+16384));
    const started = await request<{ exportId: string; statusPath: string }>("/api/exports", "POST", { targetFormat: "3dm", upload: { fileName: file.name, contentBase64: btoa(binary) } });
    for (let i = 0; i < 300; i++) {
      const result = await request<{ status: string; failureReason?: string }>(started.statusPath);
      if (result.status === "succeeded") break;
      if (["failed", "interrupted"].includes(result.status)) throw new Error(result.failureReason ?? "Conversion failed");
      if (i === 299) throw new Error("Conversion is still running. Check export history.");
      await new Promise(resolve => window.setTimeout(resolve, 300));
    }
    await request("/api/render/geometry", "PUT", { exportId: started.exportId, expectedRevision: geometry?.source.geometryRevision ?? null });
    await refresh();
  });
  const imageSelect = (name: string, selected: ImageRef | null, apply: (ref: ImageRef | null) => void) => <label>{name}<select aria-label={name} value={selected?.assetSha256 ?? ""} onChange={e => {
    const doc = documents.find(d => d.assetSha256 === e.target.value);apply(doc ? { runId: doc.runId, assetSha256: doc.assetSha256, revisionRef: doc.revisionRef ?? null } : null);
  }}><option value="">{label("None", "无")}</option>{documents.filter(d => ["image/png", "image/jpeg"].includes(d.mimeType)).map(d => <option key={d.runId+d.assetSha256} value={d.assetSha256}>{d.fileName}</option>)}</select></label>;
  const material = value?.materials.find(m => m.id === selectedMaterial) ?? value?.materials[0];
  const editMaterial = (patch: Partial<Material>) => edit(v => { const found = v.materials.find(m => m.id === material?.id);if (found) Object.assign(found, patch); });
  return <section className="physical-workspace" aria-label="Physical Render Scene">
    <div className="physical-actions"><label>{label("Import / replace geometry (GLB or 3DM)", "导入／替换几何（GLB 或 3DM）")}<input aria-label="Import geometry" type="file" accept=".glb,.3dm" disabled={busy} onChange={e => { void importModel(e.target.files?.[0]);e.target.value = ""; }} /></label>
      <button disabled={busy} onClick={() => void act(refresh)}>{label("Reload saved scene", "重读已保存场景")}</button>
      <button disabled={busy || !value || state?.status === "stale"} onClick={() => void save()}>{label("Save scene", "保存场景")}</button></div>
    <p role="status">{busy ? label("Working…", "处理中…") : dirty || state?.status === "unsaved" ? label("Unsaved scene edits", "场景有未保存修改") : state?.sceneRevision ? label("Saved scene", "已保存场景") : label("Import geometry to begin", "请先导入几何")} · geometry_revision: {geometry?.source.geometryRevision.slice(0,12) ?? "—"} · scene_revision: {state?.sceneRevision?.slice(0,12) ?? "—"}</p>
    {error && <p role="alert">{error}</p>}
    {state?.status === "stale" && <div role="alert"><p>{label("Geometry changed. Material regions and camera require review. Old regions are unresolved.", "几何已改变。材质区域和相机需要复核，旧区域未绑定。")}</p><button onClick={() => void act(async () => { const next = await request<PhysicalScene>("/api/render/scene/default");setValue(next);setState(old => old ? { ...old, status: "unsaved" } : old);setDirty(true); })}>{label("Rebind with neutral materials; clear old regions", "重新绑定为中性材质，清除旧区域")}</button></div>}
    {geometry && value && geometry.source.geometryRevision === value.geometryRevision && <PhysicalPreview geometry={geometry} value={value} imageUrl={imageUrl} onCamera={camera => edit(v => { v.camera = camera; })} />}
    {value && <div className="physical-controls">
      <details open><summary>{label("Materials and regions", "材质与区域")}</summary>
        <label>{label("Material", "材质")}<select value={material?.id ?? ""} onChange={e => setSelectedMaterial(e.target.value)}>{value.materials.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>
        {material && <><label>{label("Base color", "基础颜色")}<input aria-label="Base color" type="color" value={material.baseColor} onChange={e => editMaterial({ baseColor: e.target.value })} /></label><Numeric label="Roughness" value={material.roughness} step={.05} onChange={roughness => editMaterial({ roughness })} /><Numeric label="Metallic" value={material.metallic} step={.05} onChange={metallic => editMaterial({ metallic })} />
          {imageSelect("Base color texture (UV required)", material.texture, texture => editMaterial({ texture }))}{imageSelect("Normal map (UV required)", material.normalMap, normalMap => editMaterial({ normalMap }))}</>}
        <button onClick={() => edit(v => { const id = "material-"+Date.now();v.materials.push({ id, name: id, baseColor: "#aaaaaa", roughness: .65, metallic: 0, texture: null, normalMap: null });setSelectedMaterial(id); })}>{label("Add material", "添加材质")}</button>
        {Object.keys(value.assignments).map(id => <label key={id}>Mesh {id}<select value={value.assignments[id]} onChange={e => edit(v => { v.assignments[id] = e.target.value; })}>{value.materials.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>)}
        <button onClick={() => setSuggestion(true)}>{label("Suggest Penguin regions", "建议企鹅材质区域")}</button>
        {suggestion && <aside><p>{label("Heuristic suggestion for the Phase 1 Penguin in metres; inspect and adjust masks. It is not verified segmentation.", "这是针对第一阶段企鹅（米单位）的启发式区域建议，请检查并调整范围；不是已验证的分割。")}</p><button onClick={() => { edit(v => Object.assign(v, penguinSuggestions(v)));setSuggestion(false); }}>{label("Apply suggested regions", "采用建议区域")}</button><button onClick={() => setSuggestion(false)}>{label("Cancel", "取消")}</button></aside>}
        <button onClick={() => edit(v => { v.regions.push({ id: "region-"+Date.now(), name: "Region", materialId: material?.id ?? v.materials[0]!.id, mesh: 0, shape: "box", center: [0,0,.5], radius: [.1,.1,.1], geometryRevision: v.geometryRevision }); })}>{label("Add region", "添加区域")}</button>
        {value.regions.map((r, index) => <details key={r.id}><summary>{r.name}</summary><label>Material<select value={r.materialId} onChange={e => edit(v => { v.regions[index]!.materialId = e.target.value; })}>{value.materials.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label><label>Shape<select value={r.shape} onChange={e => edit(v => { v.regions[index]!.shape = e.target.value as "box" | "ellipsoid"; })}><option>box</option><option>ellipsoid</option></select></label><VectorInput label="Region center" value={r.center} onChange={center => edit(v => { v.regions[index]!.center = center; })} /><VectorInput label="Region radius" value={r.radius} onChange={radius => edit(v => { v.regions[index]!.radius = radius; })} /><button onClick={() => edit(v => { v.regions.splice(index,1); })}>Remove region</button></details>)}
      </details>
      <details><summary>{label("Lighting", "灯光")}</summary>{value.lights.map((light, index) => <fieldset key={light.id}><legend>{light.id}</legend><label>Type<select value={light.type} onChange={e => edit(v => { v.lights[index]!.type = e.target.value as typeof light.type; })}><option>area</option><option>point</option><option>sun</option></select></label><Numeric label={`${light.id} intensity`} value={light.intensity} step={10} onChange={n => edit(v => { v.lights[index]!.intensity = n; })} /><Numeric label="Light size" value={light.size} onChange={n => edit(v => { v.lights[index]!.size = n; })} /><label>Light color<input type="color" value={light.color} onChange={e => edit(v => { v.lights[index]!.color = e.target.value; })} /></label><VectorInput label="Light position" value={light.position} onChange={n => edit(v => { v.lights[index]!.position = n; })} /><VectorInput label="Light target" value={light.target} onChange={n => edit(v => { v.lights[index]!.target = n; })} /></fieldset>)}</details>
      <details><summary>{label("Environment and exposure", "环境与曝光")}</summary><label>Background color<input type="color" value={value.environment.color} onChange={e => edit(v => { v.environment.color = e.target.value; })} /></label><Numeric label="Environment strength" value={value.environment.strength} onChange={n => edit(v => { v.environment.strength = n; })} /><Numeric label="Exposure EV" value={value.exposure} onChange={n => edit(v => { v.exposure = n; })} />{imageSelect("Background image", value.environment.background, n => edit(v => { v.environment.background = n; }))}{imageSelect("Equirectangular environment map", value.environment.environmentMap, n => edit(v => { v.environment.environmentMap = n; }))}<small>PNG/JPEG maps. HDR/EXR and preview cast shadows are not supported in this slice.</small></details>
      <details><summary>{label("Camera and quality", "相机与质量")}</summary><label>Projection<select value={value.camera.projection} onChange={e => edit(v => { v.camera.projection = e.target.value as "perspective" | "orthographic"; })}><option>perspective</option><option>orthographic</option></select></label><VectorInput label="Camera position" value={value.camera.position} onChange={n => edit(v => { v.camera.position = n; })} /><VectorInput label="Camera target" value={value.camera.target} onChange={n => edit(v => { v.camera.target = n; })} /><Numeric label="FOV" value={value.camera.fov} onChange={n => edit(v => { v.camera.fov = n; })} /><Numeric label="Orthographic height" value={value.camera.orthoScale} onChange={n => edit(v => { v.camera.orthoScale = n; })} />{(["width", "height", "samples"] as const).map(key => <Numeric key={key} label={key} step={1} value={value.settings[key]} onChange={n => edit(v => { v.settings[key] = n; })} />)}<label><input type="checkbox" checked={value.settings.denoise} onChange={e => edit(v => { v.settings.denoise = e.target.checked; })} />Denoise</label><small>DOF is not implemented.</small></details>
      <button disabled={busy || dirty || !state?.sceneRevision || !state.cyclesAvailable || state.status === "stale"} onClick={() => void act(async () => { const job = await request<RenderJobDto>("/api/render/cycles", "POST", { requestId: crypto.randomUUID(), sceneRevision: state?.sceneRevision, geometryRevision: value.geometryRevision });setJobs(old => [job, ...old]); })}>{label("Render saved scene with Cycles", "使用 Cycles 渲染已保存场景")}</button>
      {!state?.cyclesAvailable && <p>Cycles host is not configured.</p>}
      <details open><summary>{label("Associated drawings", "关联图纸")}</summary><label>Feature references (optional)<input value={features} onChange={e => setFeatures(e.target.value)} /></label><button disabled={busy || !geometry} onClick={() => void act(async () => { await request("/api/render/drawings", "POST", { geometryRevision: geometry?.source.geometryRevision, featureReferences: features.split(",").map(v => v.trim()).filter(Boolean) });setDrawings(await request<Drawing[]>("/api/render/drawings")); })}>{label("Generate / update four views", "生成／更新四视图")}</button>{drawings.map(d => <article key={d.document.runId+d.document.assetSha256}><strong>{d.recipe.view} · {d.status === "outdated" ? "STALE / 已过期" : d.status === "current" ? "CURRENT / 当前" : "UNAVAILABLE / 不可用"}</strong><p>{d.recipe.geometryRevision.slice(0,12)} · {d.recipe.dimensions.map(m => `${m.id}: ${(m.valueMeters*1000).toFixed(3)} mm`).join("; ")}</p><details><summary>{label("View drawing", "查看图纸")}</summary><ImageThumbnail image={d.document} active={active} /></details>{d.recipe.featureReferences.map(r => <p role="alert" key={r.id}>{r.id}: {r.status} — {r.reason}</p>)}</article>)}</details>
    </div>}
    {jobs.map(job => <article key={job.jobId}><strong>Cycles · {job.status} · {job.sourceState}</strong><p>{job.error}</p>{job.document && <ImageThumbnail image={job.document} active={active} />}<small>{job.jobId}</small></article>)}
  </section>;
}

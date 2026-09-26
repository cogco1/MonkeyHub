import { useEffect, useRef, useState } from "react";
import { useStudio } from "../../api/ProjectRuntimeContext";
import { asStudioApiError } from "../../api/client";
import type { DecisionDto, RecipeGraphicsDto, RecipeInspectDto } from "../../api/generated";
import { usePreferences } from "../../features/settings/preferences";
import "./RecipeTransfer.css";

const words = {
  en: {
    title: "Reuse drawing recipes", refresh: "Refresh saved recipes", export: "Export recipe", empty: "No saved project recipes. Save a drawing's expression as a project recipe first.",
    hint: "Export one saved recipe at a time. Files carry only expression values and version references.", file: "Choose a recipe file", confirm: "Import as project preference", cancel: "Cancel",
    preview: "New drawings use these values as a soft default. Explicit drawing values and stronger project decisions take precedence.",
    imported: "Imported as a project preference. New drawings can now use it.", exported: "Recipe exported.", working: "Working…", version: "Version and source", source: "Source revision", fileVersion: "Recipe version", decision: "Source decision", project: "Project", stage: "Stage",
    importWords: "Import as project preference", invalid: "Choose a JSON recipe file up to 64 KiB.",
    pens: { cutLineMm: "Cut line", visibleLineMm: "Visible line", hatchSpacingMm: "Hatch spacing" },
  },
  "zh-CN": {
    title: "复用图面配方", refresh: "刷新已存配方", export: "导出配方", empty: "尚未保存项目配方。请先将一张图的表达存为项目设定。",
    hint: "每次导出一条已存配方；文件仅含表达数值和版本引用。", file: "选择配方文件", confirm: "导入为项目偏好", cancel: "取消",
    preview: "新图将这些数值作为弱默认值；单张图的明确设置和更强的项目决定优先。",
    imported: "已导入为项目偏好，新图可使用这些数值。", exported: "配方已导出。", working: "处理中…", version: "版本与来源", source: "来源修订", fileVersion: "配方版本", decision: "来源决定", project: "项目", stage: "阶段",
    importWords: "导入为项目偏好", invalid: "请选择不超过 64 KiB 的 JSON 配方文件。",
    pens: { cutLineMm: "剖切线宽", visibleLineMm: "可见线宽", hatchSpacingMm: "填充间距" },
  },
};

/** Transfer an explicitly saved recipe, never a drawing, model or automatic preference. */
export default function RecipeTransfer({ projectId, active = true }: { projectId: string; active?: boolean }) {
  const studio = useStudio(), { language } = usePreferences(), text = words[language];
  const [open, setOpen] = useState(false), [recipes, setRecipes] = useState<DecisionDto[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const [pending, setPending] = useState<{ content: string; preview: RecipeInspectDto } | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    generation.current += 1;
    setRecipes([]); setPending(null); setError(null); setNote(null); setOpen(false); setBusy(false);
    return () => { generation.current += 1; };
  }, [studio, projectId]);

  const values = (graphics: RecipeGraphicsDto) => (Object.keys(text.pens) as (keyof typeof text.pens)[])
    .filter(key => graphics[key] != null).map(key => `${text.pens[key]} ${graphics[key]} mm`).join(" · ");
  async function readRecipes() {
    const response = await studio.decisions();
    if (response.projectId !== projectId) throw new Error("The recipes belong to another project.");
    return response.decisions.filter(row => row.status === "active" && row.typedBinding?.kind === "recipe");
  }
  async function refresh() {
    const token = generation.current;
    setBusy(true); setError(null);
    try { const rows = await readRecipes(); if (token === generation.current) setRecipes(rows); }
    catch (cause) { if (token === generation.current) setError(asStudioApiError(cause).detail); }
    finally { if (token === generation.current) setBusy(false); }
  }
  async function download(row: DecisionDto) {
    const token = generation.current;
    setBusy(true); setError(null); setNote(null);
    try {
      const file = await studio.exportDrawingRecipe(row.decisionId, row.revisionRef);
      if (token !== generation.current) return;
      const url = URL.createObjectURL(new Blob([file.content], { type: "application/json" }));
      const anchor = window.document.createElement("a");
      anchor.href = url; anchor.download = file.fileName;
      anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNote(text.exported);
    } catch (cause) { if (token === generation.current) setError(asStudioApiError(cause).detail); }
    finally { if (token === generation.current) setBusy(false); }
  }
  async function inspect(file: File) {
    const token = generation.current;
    setPending(null); setNote(null); setError(null); setBusy(true);
    try {
      if (file.size > 65536) throw new Error(text.invalid);
      const content = await file.text();
      const preview = await studio.inspectDrawingRecipe({ projectId, content });
      if (preview.projectId !== projectId) throw new Error("The recipe preview belongs to another project.");
      if (token === generation.current) setPending({ content, preview });
    } catch (cause) { if (token === generation.current) setError(asStudioApiError(cause).detail); }
    finally { if (token === generation.current) setBusy(false); }
  }
  async function confirm() {
    if (!pending || busy || !active) return;
    const token = generation.current;
    setBusy(true); setError(null); setNote(null);
    try {
      await studio.importDrawingRecipe({ projectId, content: pending.content, confirmed: true, sourceKind: "human",
        rawLanguage: `${text.importWords}: ${values(pending.preview.graphics)} (${pending.preview.exportSha256}).` });
      if (token !== generation.current) return;
      // Mark the write as complete before refreshing; a failed read must not invite a duplicate import.
      setPending(null); setNote(text.imported);
      const rows = await readRecipes();
      if (token === generation.current) setRecipes(rows);
    } catch (cause) { if (token === generation.current) setError(asStudioApiError(cause).detail); }
    finally { if (token === generation.current) setBusy(false); }
  }

  return <details className="recipe-transfer" open={open} onToggle={event => {
    const expanded = event.currentTarget.open; setOpen(expanded);
    if (expanded && !open && active && !busy) void refresh();
  }}>
    <summary>{text.title}</summary>
    {open && <div className="recipe-transfer__body">
      <p>{text.hint}</p>
      <button type="button" disabled={busy || !active} onClick={() => void refresh()}>{text.refresh}</button>
      {recipes.length === 0 && !busy && <p>{text.empty}</p>}
      <ul>{recipes.map(row => <li key={row.decisionId}>
        <span>{row.typedBinding?.kind === "recipe" && values(row.typedBinding.graphics)} · {row.scope.extent === "stage" ? text.stage : text.project}</span>
        <button type="button" disabled={busy || !active} onClick={() => void download(row)}>{text.export}</button>
        {row.source.kind === "recipe-export" && <details><summary>{text.version}</summary><p>{text.fileVersion}: {row.source.exportSha256}</p></details>}
      </li>)}</ul>
      <label className="recipe-transfer__file">{text.file}<input type="file" accept=".json,application/json" disabled={busy || !active} onChange={event => {
        const file = event.target.files?.[0]; event.target.value = ""; if (file) void inspect(file);
      }} /></label>
      {pending && <div className="recipe-transfer__preview">
        <strong>{values(pending.preview.graphics)}</strong><p>{text.preview}</p>
        <details><summary>{text.version}</summary><p>{text.fileVersion}: {pending.preview.exportSha256}</p>
          <p>{text.decision}: {pending.preview.sourceDecisionId}</p><p>{text.source}: {pending.preview.sourceRevisionSha256}</p></details>
        <button type="button" disabled={busy || !active} onClick={() => void confirm()}>{text.confirm}</button>
        <button type="button" disabled={busy} onClick={() => setPending(null)}>{text.cancel}</button>
      </div>}
      {busy && <p role="status">{text.working}</p>}
      {note && <p role="status">{note}</p>}
      {error && <p role="alert">{error}</p>}
    </div>}
  </details>;
}

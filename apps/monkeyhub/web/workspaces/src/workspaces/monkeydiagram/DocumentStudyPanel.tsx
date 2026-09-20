import { cloneElement, useId, useState, type ReactElement } from "react";
import type { DocumentGestureDto, StudySourceRequestDto, StudyViewDto } from "../../api/generated";
import { usePreferences } from "../../features/settings/preferences";
import {
  addStudyTrace, newStudyItemId, studyComparisonDefinitionKey, studyDraftChanged, studyResearchResult, studyTraceIsClosed,
  type StudyComparison, type StudyComparisonTarget, type StudyCounterfactualActual, type StudyDraft,
  type StudyEvidence, type StudyResearch,
} from "./documentStudy";
import "./DocumentStudyPanel.css";

export interface DocumentStudyPanelProps {
  source: StudySourceRequestDto;
  sourceName: string;
  draft: StudyDraft;
  study: StudyViewDto | null;
  onChange(draft: StudyDraft): void;
  onSave(): void;
  onReopen(): void;
  selectedTrace?: DocumentGestureDto | null;
  onSelectEvidence?(evidenceId: string): void;
  comparisonTargets?: readonly StudyComparisonTarget[];
  onCompare?(target: StudyComparisonTarget): void;
  onPropose?(kind: "trace" | "reason"): void;
  proposing?: "trace" | "reason" | null;
  dirty?: boolean;
  busy?: boolean;
  loading?: boolean;
  error?: string | null;
}

function Field({ label, children, hint }: { label: string; children: ReactElement<{ id?: string; "aria-describedby"?: string }>; hint?: string }) {
  const id = useId();
  return <div className="document-study__field"><label htmlFor={id}>{label}</label>
    {cloneElement(children, { id, ...(hint ? { "aria-describedby": `${id}-hint` } : {}) })}
    {hint && <small id={`${id}-hint`}>{hint}</small>}</div>;
}

function TextField({ label, value, onChange, hint, multiline = true }: {
  label: string; value: string; onChange(value: string): void; hint?: string; multiline?: boolean;
}) {
  return <Field label={label} hint={hint}>{multiline
    ? <textarea rows={3} value={value} onChange={event => onChange(event.target.value)} />
    : <input value={value} onChange={event => onChange(event.target.value)} />}</Field>;
}

function Choices({ label, options, value, onChange, empty }: {
  label: string; options: { id: string; label: string }[]; value: string[];
  onChange(value: string[]): void; empty: string;
}) {
  return <fieldset className="document-study__choices"><legend>{label}</legend>
    {options.length === 0 && <p>{empty}</p>}
    {options.map(option => <label key={option.id}><input type="checkbox" checked={value.includes(option.id)}
      onChange={event => onChange(event.target.checked ? [...value, option.id] : value.filter(id => id !== option.id))} />
      <span>{option.label}</span></label>)}
  </fieldset>;
}

function GeometryPreview({ evidence, label, width, height }: {
  evidence: readonly StudyEvidence[]; label: string; width: number; height: number;
}) {
  const ratio = width > 0 && height > 0 ? height / width : 1;
  return <figure className="document-study__preview"><svg viewBox={`0 0 100 ${100 * ratio}`} role="img" aria-label={label}>
    {evidence.filter(row => row.status === "confirmed").map(row => <polygon key={row.evidenceId}
      data-kind={row.kind} points={row.geometry.points.map(([x, y]) => `${x * 100},${y * 100 * ratio}`).join(" ")}>
      <title>{`${row.kind}: ${row.evidenceId}`}</title></polygon>)}
  </svg><figcaption>{label}</figcaption></figure>;
}

const formatted = (value: unknown) => typeof value === "number" ? value.toFixed(3) : "—";

/** The same document and corrected page geometry remain visible beside this research form. */
export function DocumentStudyPanel({ source, sourceName, draft, study, onChange, onSave, onReopen,
  selectedTrace, onSelectEvidence, comparisonTargets = [], onCompare, onPropose,
  proposing, dirty: draftDirty, busy = false, loading = false, error }: DocumentStudyPanelProps) {
  const { language } = usePreferences();
  const zh = language === "zh-CN";
  const text = (en: string, cn: string) => zh ? cn : en;
  const [step, setStep] = useState(0);
  const [compareId, setCompareId] = useState("");
  const [discardPrompt, setDiscardPrompt] = useState(false);
  const research = draft.research;
  const retained = studyResearchResult(study);
  const dirty = draftDirty ?? studyDraftChanged(draft, study);
  const locked = busy || loading || !!proposing;
  const changeResearch = (patch: Partial<StudyResearch>) => onChange({ ...draft, research: { ...research, ...patch } });
  const evidenceName = (id: string) => {
    const index = draft.evidence.findIndex(item => item.evidenceId === id);
    return index < 0 ? id : `${text("Trace", "轮廓")} ${index + 1} · ${kindLabel(draft.evidence[index].kind)}`;
  };
  function kindLabel(kind: StudyEvidence["kind"]): string {
    return { envelope: text("Envelope", "包络"), mass: text("Mass", "实体"), void: text("Void", "空洞"),
      floor_plate: text("Floor plate", "楼板") }[kind];
  }
  const evidenceOptions = draft.evidence.filter(item => item.status === "confirmed").map(item => ({ id: item.evidenceId, label: evidenceName(item.evidenceId) }));
  const hypothesisOptions = research.hypotheses.map((item, index) => ({ id: item.hypothesisId,
    label: `${text("Explanation", "解释")} ${index + 1}: ${item.statement || text("Untitled", "未填写")}` }));
  const noEvidence = text("Confirm a trace in Evidence first.", "请先在证据步骤中确认轮廓。");
  const steps = [text("Evidence", "证据"), text("Measurements", "测量与关系"), text("Explanations", "竞争解释"),
    text("Counterfactuals", "反事实"), text("Compare", "比较"), text("Pattern & prior", "模式与先验")];
  const target = comparisonTargets.find(item => `${item.studyId}:${item.ledgerRef}` === compareId);
  const modelInvocation = study?.modelInvocations?.at(-1);
  const width = Number(study?.source.pageWidth ?? 1);
  const height = Number(study?.source.pageHeight ?? 1);
  const updateEvidence = (id: string, patch: Partial<StudyEvidence>) => onChange({ ...draft,
    evidence: draft.evidence.map(item => item.evidenceId === id ? { ...item, ...patch } : item) });
  const relationName = (kind: unknown) => ({ distance: text("distance", "间距"), intersection_area: text("intersection area", "重叠面积"),
    shared_boundary: text("shared boundary", "共享边界"), contains: text("contains", "包含"), touches: text("touches", "相接") }[String(kind)] ?? String(kind).replaceAll("_", " "));
  const comparisonFact = (fact: { fact: string; count: number }) => `${fact.fact} × ${fact.count}`;
  const comparisonDefinitions = research.comparisons ?? [];
  const comparisonStudyName = (studyId: string) => studyId === draft.studyId ? sourceName
    : comparisonTargets.find(row => row.studyId === studyId)?.label ?? studyId;

  function comparisonResult(result: StudyComparison) {
    return <div className="document-study__actual">
      {(result.pairwise ?? []).map((pair, index) => <div key={index}>
        <p>{comparisonStudyName(pair.left.studyId)} ↔ {comparisonStudyName(pair.right.studyId)}</p>
        <dl><dt>{text("Topology similarity", "拓扑相似度")}</dt><dd>{formatted(pair.topologySimilarity)}</dd>
          <dt>{text("Largest proportion difference", "最大比例差异")}</dt><dd>{formatted(pair.maxProportionDelta)}</dd></dl>
        <details><summary>{text("Exact saved versions & unique relations", "确切保存版本及独有关系")}</summary>
          <strong>{text("Left saved version", "左侧保存版本")}</strong><small>{pair.left.ledgerRef}</small>
          <ul>{(pair.leftOnlyTopology ?? []).map((item, i) => <li key={i}>{comparisonFact(item)}</li>)}</ul>
          <strong>{text("Right saved version", "右侧保存版本")}</strong><small>{pair.right.ledgerRef}</small>
          <ul>{(pair.rightOnlyTopology ?? []).map((item, i) => <li key={i}>{comparisonFact(item)}</li>)}</ul>
        </details></div>)}
      <small>{result.method}</small>
    </div>;
  }

  function polygonMeasurements(rows: Record<string, unknown>[], caption: string, baseline?: Record<string, unknown>[]) {
    return <div className="document-study__table-scroll"><table><caption>{caption}</caption>
      <thead><tr><th>{text("Trace", "轮廓")}</th><th>{text("Area", "面积")}</th><th>{text("Perimeter", "周长")}</th>
        {baseline && <th>{text("Area change", "面积变化")}</th>}</tr></thead>
      <tbody>{rows.map(row => <tr key={String(row.evidenceId)}><th>{evidenceName(String(row.evidenceId))}</th><td>{formatted(row.area)}</td><td>{formatted(row.perimeter)}</td>
        {baseline && <td>{typeof row.area === "number" && typeof baseline.find(old => old.evidenceId === row.evidenceId)?.area === "number"
          ? formatted(row.area - Number(baseline.find(old => old.evidenceId === row.evidenceId)?.area)) : "—"}</td>}</tr>)}</tbody>
    </table>{rows.filter(row => typeof row.clearArea === "number").map(row => <p key={String(row.evidenceId)}>
      {evidenceName(String(row.evidenceId))}: {text("clear region", "净空区域")} {formatted(row.clearArea)} · {String(row.clearComponents)} {text("components", "个连通部分")}
      <small>{text("Declared void minus confirmed masses. Passability is not established.", "已标注空洞扣除已确认实体；尚不能据此证明通行性。")}</small>
    </p>)}</div>;
  }

  function relationFacts(rows: Record<string, unknown>[]) {
    return rows.length === 0 ? <p>{text("No relations found among confirmed traces.", "已确认轮廓之间尚无关系结果。")}</p> :
      <ul className="document-study__facts">{rows.map(row => <li key={String(row.relationId)}>
        {evidenceName(String(row.subjectEvidenceId))} <strong>{relationName(row.kind)}</strong> {evidenceName(String(row.objectEvidenceId))}
        {typeof row.value === "number" && ` · ${formatted(row.value)}`}</li>)}</ul>;
  }

  function actualResult(actual: StudyCounterfactualActual, index: number) {
    return <div className="document-study__actual">
      <strong>{text("Saved actual result", "已保存的实际结果")}</strong>
      {actual.status === "unsupported" ? <p>{text("Unsupported", "不支持")}: {actual.reason}</p> : <>
        <div className="document-study__preview-pair">
          <GeometryPreview evidence={study?.evidence as unknown as StudyEvidence[] ?? []} width={width} height={height}
            label={text("Saved baseline", "已保存的原形")} />
          <GeometryPreview evidence={actual.evidence ?? []} width={width} height={height}
            label={`${text("Variant", "改动")} ${index + 1}`} />
        </div>
        {polygonMeasurements(actual.measurements ?? [], text("Actual polygon measurements", "改动后的多边形测量"), actual.baselineMeasurements)}
        <p>{text("Relation similarity", "关系相似度")}: {formatted(actual.relationSignatureSimilarity)}</p>
        <p>{text("The architectural interpretation remains open; geometric changes do not decide which explanation or preference is right.",
          "建筑解释仍待判断；几何变化不能自动决定哪一个解释或偏好成立。")}</p>
        <details><summary>{text("Actual polygon relations", "改动后的多边形关系")}</summary>{relationFacts(actual.relations ?? [])}</details>
        <details><summary>{text("Changed geometric facts", "改变的几何事实")}</summary>
          <strong>{text("Removed", "移除")}</strong><ul>{(actual.removedFacts ?? []).map(item => <li key={item}>{item}</li>)}</ul>
          <strong>{text("Added", "新增")}</strong><ul>{(actual.addedFacts ?? []).map(item => <li key={item}>{item}</li>)}</ul>
        </details>
      </>}
      <small>{actual.method}</small>
    </div>;
  }

  return <aside className="document-study" aria-label={text("Document study", "文档研究")} aria-busy={locked}>
    <header className="document-study__header"><div><h2>{text("Study this page", "研究本页")}</h2>
      <p>{sourceName} · {text("Page", "第")} {source.pageIndex + 1}{zh ? " 页" : ""}</p></div>
      <span className="document-study__badge">{text("Research draft", "研究草稿")}</span></header>
    <nav className="document-study__steps" aria-label={text("Study steps", "研究步骤")}>
      {steps.map((label, index) => <button key={label} type="button" aria-current={index === step ? "step" : undefined}
        onClick={() => setStep(index)}><span>{index + 1}</span>{label}</button>)}
    </nav>
    <div className="document-study__body">
      <p className="document-study__note">{text("Manual tracing baseline. Geometry describes this drawing; it does not establish the original architect’s intentions.",
        "当前以人工描图为基线。几何描述本页图纸，不代表原作者的实际意图。")}</p>
      {modelInvocation && <details className="document-study__model"><summary>{text("Model suggestions · review required", "模型建议，需人工审阅")}</summary>
        <p>{text("A model proposal has been retained. Its interpretations remain hypotheses until you challenge and revise them.", "已保留模型提案。其中的解释仍是假设，需要进一步质疑与修订。")}</p>
        <dl><dt>{text("Provider", "服务")}</dt><dd>{String(modelInvocation.providerId ?? "—")}</dd>
          <dt>{text("Model", "模型")}</dt><dd>{String(modelInvocation.modelId ?? text("Configured default", "配置默认值"))}</dd>
          <dt>{text("Provider version", "服务版本")}</dt><dd>{String(modelInvocation.providerVersion ?? "—")}</dd>
          <dt>{text("Duration", "耗时")}</dt><dd>{typeof modelInvocation.durationMs === "number" ? `${(modelInvocation.durationMs / 1000).toFixed(1)} s` : "—"}</dd></dl>
      </details>}
      {dirty && study && <p role="status" className="document-study__notice">{text("Unsaved corrections. Measurements and experiment results below still show the saved revision.",
        "有未保存的校正。下方测量和实验结果仍对应已保存版本。")}</p>}
      <fieldset className="document-study__editor" disabled={locked}><legend className="document-study__sr-only">{steps[step]}</legend>
        {step === 0 && <section aria-label={steps[0]}>
          <h3>{text("Correct before interpreting", "先校正，再解释")}</h3>
          <p>{text("Draw closed contours with the page’s existing polyline tool. Select and move their vertices to correct them. Classify and confirm each contour here.",
            "用页面已有的多段线工具绘制闭合轮廓，再选择顶点调整。此处为每条轮廓分类并确认。")}</p>
          {selectedTrace && <button type="button" disabled={!studyTraceIsClosed(selectedTrace)} onClick={() => onChange(addStudyTrace(draft, selectedTrace))}>
            {text("Use selected closed trace", "使用选中的闭合轮廓")}</button>}
          {onPropose && <details><summary>{text("Optional model proposal", "可选的模型提案")}</summary>
            <p>{text("Send this registered page to the configured model provider to propose contours. Review every proposal before confirming it.",
              "将本张已登记页面发送给已配置的模型服务，提出轮廓。每条提案都需校正后确认。")}</p>
            <button type="button" disabled={!study || dirty} onClick={() => onPropose("trace")}>{text("Propose traces with model", "模型提议轮廓")}</button>
            {(!study || dirty) && <small>{text("Save this page’s current study before requesting a proposal.", "先保存本页当前研究，再请求模型提案。")}</small>}
          </details>}
          {draft.evidence.length === 0 && <p className="document-study__empty">{text("No trace evidence yet. Begin with a closed contour on the page.", "尚无轮廓证据。先在页面绘制一条闭合轮廓。")}</p>}
          {draft.evidence.map((item, index) => <article className="document-study__card" key={item.evidenceId}>
            <div className="document-study__card-title"><h4>{text("Trace", "轮廓")} {index + 1}</h4>
              {onSelectEvidence && <button type="button" onClick={() => onSelectEvidence(item.evidenceId)}>{text("Locate on page", "在图上定位")}</button>}</div>
            <Field label={text("Geometric classification", "几何分类")}><select value={item.kind}
              onChange={event => updateEvidence(item.evidenceId, { kind: event.target.value as StudyEvidence["kind"], status: "proposed" })}>
              {(["envelope", "mass", "void", "floor_plate"] as const).map(kind => <option key={kind} value={kind}>{kindLabel(kind)}</option>)}
            </select></Field>
            <Field label={text("Evidence decision", "证据决定")}><select value={item.status}
              onChange={event => updateEvidence(item.evidenceId, { status: event.target.value as StudyEvidence["status"] })}>
              <option value="proposed">{text("Proposed · needs correction", "待校正的提案")}</option>
              <option value="confirmed">{text("Confirmed by me", "由我确认")}</option>
              <option value="rejected">{text("Rejected · retained for reference", "拒绝并保留记录")}</option>
            </select></Field>
            <small>{text("Origin", "来源")}: {item.origin === "user" ? text("Manual trace", "人工描图") : item.origin === "machine" ? text("Model proposal", "模型提案") : text("Imported trace", "导入轮廓")}
              {` · ${item.geometry.points.length} `}{text("vertices", "个顶点")}</small>
          </article>)}
          <h3>{text("Historical evidence", "历史依据")}</h3>
          <p>{text("Record a publication or archive separately from an interpretation. Cite the exact page, drawing or passage.", "将出版物或档案与个人解释分开记录，并标明页码、图号或段落。")}</p>
          {research.historicalSources.map((item, index) => <article className="document-study__card" key={item.sourceId}>
            <h4>{text("Source", "依据")} {index + 1}</h4>
            {(["citation", "url", "locator", "summary"] as const).map(key => <TextField key={key}
              label={{ citation: text("Citation", "文献出处"), url: text("Public source URL", "公开来源链接"),
                locator: text("Page / drawing / passage", "页码／图号／段落"), summary: text("What the source actually says", "来源实际陈述的内容") }[key]}
              value={item[key]} multiline={key === "summary"} onChange={value => changeResearch({ historicalSources:
                research.historicalSources.map(row => row.sourceId === item.sourceId ? { ...row, [key]: value } : row) })} />)}
          </article>)}
          <button type="button" onClick={() => changeResearch({ historicalSources: [...research.historicalSources,
            { sourceId: newStudyItemId("source"), citation: "", url: "", locator: "", summary: "" }] })}>{text("Add historical source", "添加历史依据")}</button>
        </section>}

        {step === 1 && <section aria-label={steps[1]}>
          <h3>{text("Saved measurements & relations", "已保存的测量与关系")}</h3>
          <p>{text("Save confirmed traces to measure their polygons in the page’s correct aspect ratio. Length is normalized by the page’s long edge; area by its square. These are drawing facts, not calibrated physical dimensions or building performance.",
            "保存已确认轮廓后，按页面真实宽高比测量多边形。长度除以页面长边，面积除以长边的平方。这是图纸事实，不是校准后的物理尺寸或建筑性能。")}</p>
          {!study && <p className="document-study__empty">{text("Save the evidence to inspect its measurements.", "先保存证据，再查看测量结果。")}</p>}
          {!!study && <>
            {retained?.observations ? <>
              {polygonMeasurements(retained.observations.measurements, text("Corrected polygon measurements", "校正后的多边形测量"))}
              <h4>{text("Computed polygon relations", "已计算的多边形关系")}</h4>{relationFacts(retained.observations.relations)}
            </> : <p>{text("This saved revision has no polygon observations. Save a research revision to compute them.", "本保存版本尚无多边形观察。保存研究版本后计算。")}</p>}
            <details><summary>{text("Earlier bounding-box method", "原有包围框方法")}</summary>
            <p>{text("Bounds are only a geometric proxy; their area is not polygon area.", "包围框仅是几何近似，其面积不代表多边形面积。")}</p>
            <div className="document-study__table-scroll"><table><caption>{text("Normalized bounding measurements", "归一化包围框测量")}</caption>
              <thead><tr><th>{text("Trace", "轮廓")}</th><th>{text("Width", "宽")}</th><th>{text("Height", "高")}</th><th>{text("Bounds area", "框面积")}</th></tr></thead>
              <tbody>{study.measurements.map(row => <tr key={String(row.measurementId)}><th>{evidenceName(String(row.evidenceId))}</th>
                <td>{formatted(row.width)}</td><td>{formatted(row.height)}</td><td>{formatted(row.area)}</td></tr>)}</tbody>
            </table></div>
            <h4>{text("Geometric relation proposals", "几何关系提案")}</h4>
            {relationFacts(study.relations)}
            <details><summary>{text("Automatic geometric hypotheses", "自动生成的几何假设")}</summary>
              <p>{text("These are method-generated suggestions. Author a challengeable explanation in the next step.", "这些是方法生成的提示。请在下一步提出可挑战的解释。")}</p>
              <ul>{study.hypotheses.map(row => <li key={String(row.hypothesisId)}>{String(row.rule).replaceAll("_", " ")}</li>)}</ul>
            </details>
            <small>{text("Derivation", "推导方法")}: {study.derivationMethod ?? text("Older unstamped revision", "未标注方法的旧版本")}</small>
            </details>
          </>}
        </section>}

        {step === 2 && <section aria-label={steps[2]}>
          <TextField label={text("Research question", "研究问题")} value={research.question} onChange={question => changeResearch({ question })} />
          <p>{text("Keep at least two competing explanations. State what could disprove each; leave missing evidence visible.",
            "保留至少两个竞争解释。写明各自可能被什么推翻，并显式保留证据缺口。")}</p>
          {onPropose && <button type="button" disabled={!study || dirty || evidenceOptions.length === 0} onClick={() => onPropose("reason")}>
            {text("Propose testable explanations", "提出可检验解释")}</button>}
          {research.hypotheses.map((item, index) => {
            const update = (patch: Partial<typeof item>) => changeResearch({ hypotheses: research.hypotheses.map(row => row.hypothesisId === item.hypothesisId ? { ...row, ...patch } : row) });
            return <article className="document-study__card" key={item.hypothesisId}><h4>{text("Explanation", "解释")} {index + 1}</h4>
              <TextField label={text("Challengeable claim", "可挑战的判断")} value={item.statement} onChange={statement => update({ statement })} />
              <Choices label={text("Geometric support", "几何支持")} options={evidenceOptions} value={item.evidenceIds} empty={noEvidence} onChange={evidenceIds => update({ evidenceIds })} />
              <Choices label={text("Opposing traces", "反证轮廓")} options={evidenceOptions} value={item.counterEvidenceIds ?? []} empty={noEvidence}
                onChange={counterEvidenceIds => update({ counterEvidenceIds })} />
              <Choices label={text("Historical support", "历史支持")} options={research.historicalSources.map((row, i) => ({ id: row.sourceId, label: row.citation || `${text("Source", "依据")} ${i + 1}` }))}
                value={item.historicalSourceIds} empty={text("No historical sources recorded.", "尚未记录历史来源。")} onChange={historicalSourceIds => update({ historicalSourceIds })} />
              <TextField label={text("Assumptions / applicability conditions", "假设前提／适用条件")} hint={text("One condition per line", "每行一个条件")}
                value={item.assumptions.join("\n")} onChange={value => update({ assumptions: value.split("\n") })} />
              <TextField label={text("What would disprove this?", "什么结果会推翻此解释？")} value={item.falsification} onChange={falsification => update({ falsification })} />
              <Choices label={text("Competing explanations", "竞争解释")} options={hypothesisOptions.filter(row => row.id !== item.hypothesisId)} value={item.competesWith}
                empty={text("Add another explanation to compare.", "再添加一个解释以建立竞争关系。")} onChange={competesWith => update({ competesWith })} />
              <Field label={text("Current judgement", "当前判断")}><select value={item.status} onChange={event => update({ status: event.target.value as typeof item.status })}>
                <option value="open">{text("Open", "待检验")}</option><option value="revised">{text("Revised", "已修订")}</option><option value="rejected">{text("Rejected", "已拒绝")}</option>
              </select></Field>
            </article>;
          })}
          <button type="button" onClick={() => changeResearch({ hypotheses: [...research.hypotheses, { hypothesisId: newStudyItemId("hypothesis"), statement: "",
            evidenceIds: [], historicalSourceIds: [], assumptions: [], falsification: "", competesWith: [], status: "open" }] })}>{text("Add competing explanation", "添加竞争解释")}</button>
          <h3>{text("Evidence gaps", "证据缺口")}</h3>
          {research.gaps.map((item, index) => <article className="document-study__card" key={item.gapId}>
            <TextField label={`${text("Gap", "缺口")} ${index + 1}`} value={item.description} onChange={description => changeResearch({ gaps: research.gaps.map(row => row.gapId === item.gapId ? { ...row, description } : row) })} />
            <Choices label={text("Related traces", "相关轮廓")} options={evidenceOptions} value={item.evidenceIds} empty={noEvidence}
              onChange={evidenceIds => changeResearch({ gaps: research.gaps.map(row => row.gapId === item.gapId ? { ...row, evidenceIds } : row) })} />
          </article>)}
          <button type="button" onClick={() => changeResearch({ gaps: [...research.gaps, { gapId: newStudyItemId("gap"), description: "", evidenceIds: [] }] })}>{text("Add evidence gap", "添加证据缺口")}</button>
        </section>}

        {step === 3 && <section aria-label={steps[3]}>
          <h3>{text("Predict, change, inspect", "预测、改动、检查")}</h3>
          <p>{text("Create 3–5 verifiable changes. Write the prediction before requesting a run. Save to execute the selected changes; source geometry stays intact.",
            "设置 3–5 个可核查改动。先写预测，再请求运行。保存时执行勾选的改动，原始来源几何保持不变。")}</p>
          {research.counterfactuals.map((item, index) => {
            const update = (patch: Partial<typeof item>) => changeResearch({ counterfactuals: research.counterfactuals.map(row => row.counterfactualId === item.counterfactualId ? { ...row, ...patch } : row) });
            const actual = retained?.counterfactuals.find(row => row.counterfactualId === item.counterfactualId)?.actual;
            return <article className="document-study__card" key={item.counterfactualId}><div className="document-study__card-title"><h4>{text("Change", "改动")} {index + 1}</h4>
              <button type="button" onClick={() => changeResearch({ counterfactuals: research.counterfactuals.filter(row => row.counterfactualId !== item.counterfactualId) })}>
                {text("Remove from draft", "从草稿移除")}</button></div>
              <Choices label={text("Explanations tested", "被检验的解释")} options={hypothesisOptions} value={item.hypothesisIds}
                empty={text("Add an explanation first.", "先添加一个解释。")} onChange={hypothesisIds => update({ hypothesisIds })} />
              <Field label={text("Target trace", "目标轮廓")}><select value={item.targetEvidenceId} onChange={event => update({ targetEvidenceId: event.target.value })}>
                <option value="">{text("Choose a confirmed trace", "选择已确认轮廓")}</option>{evidenceOptions.map(row => <option key={row.id} value={row.id}>{row.label}</option>)}
              </select></Field>
              <Field label={text("Operation", "改动方式")}><select value={item.operation} onChange={event => update({ operation: event.target.value as typeof item.operation,
                parameters: event.target.value === "translate" ? { dx: 0.08, dy: 0 } : event.target.value === "scale" ? { scale: 0.82 } : {} })}>
                <option value="translate">{text("Translate", "平移")}</option><option value="scale">{text("Scale about centre", "绕中心缩放")}</option><option value="remove">{text("Remove", "移除")}</option>
              </select></Field>
              {item.operation === "translate" && <div className="document-study__row">{(["dx", "dy"] as const).map(axis => <Field key={axis}
                label={axis === "dx" ? text("Horizontal shift / page width", "水平位移／页面宽度") : text("Vertical shift / page height", "垂直位移／页面高度")}>
                <input type="number" step="0.01" min="-1" max="1" value={item.parameters[axis] ?? 0} onChange={event => update({ parameters: { ...item.parameters, [axis]: Number(event.target.value) } })} />
              </Field>)}</div>}
              {item.operation === "scale" && <Field label={text("Scale factor", "缩放倍数")}><input type="number" min="0.01" step="0.01" value={item.parameters.scale ?? 1}
                onChange={event => update({ parameters: { scale: Number(event.target.value) } })} /></Field>}
              <TextField label={text("Conditions held / changed", "保持／改变的条件")} hint={text("One condition per line", "每行一个条件")} value={item.conditions.join("\n")}
                onChange={value => update({ conditions: value.split("\n") })} />
              <TextField label={text("Prediction before execution", "执行前的预测")} value={item.prediction} onChange={prediction => update({ prediction })} />
              <label className="document-study__check"><input type="checkbox" checked={item.execute} onChange={event => update({ execute: event.target.checked })} />
                <span>{text("Run this change on save", "保存时运行此改动")}</span></label>
              {actual ? actualResult(actual, index) : <p>{text("No retained result yet.", "尚无已保存结果。")}</p>}
            </article>;
          })}
          <button type="button" disabled={evidenceOptions.length === 0 || research.counterfactuals.length >= 5} onClick={() => changeResearch({ counterfactuals: [...research.counterfactuals, { counterfactualId: newStudyItemId("change"),
            hypothesisIds: [], targetEvidenceId: evidenceOptions[0].id, operation: "translate", parameters: { dx: 0.08, dy: 0 }, conditions: [], prediction: "", execute: false }] })}>
            {text("Add counterfactual change", "添加反事实改动")}</button>
        </section>}

        {step === 4 && <section aria-label={steps[4]}>
          <h3>{text("Compare retained studies", "比较已保存研究")}</h3>
          <p>{text("Compare normalized topology and proportions. A visual resemblance does not prove a common design intention or an applicable prior.",
            "比较归一化拓扑与比例。外观相似不能证明设计意图相同或先验可用。")}</p>
          <Field label={text("Comparison study", "比较对象")}><select value={compareId} onChange={event => setCompareId(event.target.value)}>
            <option value="">{text("Choose a saved study", "选择已保存研究")}</option>{comparisonTargets.filter(item => item.ledgerRef !== study?.ledgerRef).map(item =>
              <option key={`${item.studyId}:${item.ledgerRef}`} value={`${item.studyId}:${item.ledgerRef}`}>{item.label}</option>)}
          </select></Field>
          {comparisonTargets.filter(item => item.ledgerRef !== study?.ledgerRef).length === 0 && <p>{text("Save a study on another page to make it available here.", "在另一页面保存研究后，可在此选择比较。")}</p>}
          <button type="button" disabled={!target || !study || !onCompare || dirty || comparisonDefinitions.length >= 6} onClick={() => { if (target) onCompare?.(target); }}>
            {text("Compare saved revisions", "比较已保存版本")}</button>
          {comparisonDefinitions.length >= 6 && <p>{text("Six comparison groups are retained. Remove a group and save before adding another.", "已保留六组比较。移除一组并保存后可添加新的比较。")}</p>}
          {comparisonDefinitions.length > 0 && <h4>{text("Comparison result", "比较结果")}</h4>}
          {comparisonDefinitions.length > 0 && <p>{text("Archived comparisons refer to the exact saved inputs shown below. Later edits to either study do not change these results.",
            "归档比较对应下方列出的确切保存版本。任一研究后续发生的编辑，不会改变这些结果。")}</p>}
          {comparisonDefinitions.map((definition, index) => {
            const key = studyComparisonDefinitionKey(definition);
            const retainedIndex = (retained?.comparisons ?? []).findIndex(row => studyComparisonDefinitionKey(row) === key);
            const result = retainedIndex < 0 ? null : retained?.comparisonResults?.[retainedIndex];
            return <article className="document-study__card" key={key}>
              <div className="document-study__card-title"><h4>{text("Archived comparison", "归档比较")} {index + 1}</h4>
                <button type="button" onClick={() => changeResearch({ comparisons: comparisonDefinitions.filter((_, position) => position !== index) })}>
                  {text("Remove archived comparison", "移除归档比较")}</button></div>
              <details><summary>{text("Saved inputs", "保存的输入版本")}</summary>
                {definition.studies.map(row => <p key={`${row.studyId}:${row.ledgerRef}`}>{comparisonStudyName(row.studyId)}<small>{row.ledgerRef}</small></p>)}
              </details>
              {result ? comparisonResult(result) : <p>{text("This comparison has no saved result yet. Save the study to compute and retain it.", "此比较尚无保存结果。保存研究以计算并保留结果。")}</p>}
            </article>;
          })}
        </section>}

        {step === 5 && <section aria-label={steps[5]}>
          <h3>{text("Editable composition pattern", "可编辑的构成模式")}</h3>
          <p>{text("Describe a transferable relationship and its limits, not a style label.", "说明可迁移的关系及其限制，不以风格标签替代机制。")}</p>
          {!research.compositionPattern ? <button type="button" onClick={() => changeResearch({ compositionPattern: {
            patternId: newStudyItemId("pattern"), name: "", rule: "", evidenceIds: [], conditions: [], exceptions: [] } })}>{text("Create composition pattern", "创建构成模式")}</button> : (() => {
            const item = research.compositionPattern;
            const update = (patch: Partial<typeof item>) => changeResearch({ compositionPattern: { ...item, ...patch } });
            return <article className="document-study__card">
              <TextField label={text("Pattern name", "模式名称")} value={item.name} multiline={false} onChange={name => update({ name })} />
              <TextField label={text("Relationship / mechanism", "关系／机制")} value={item.rule} onChange={rule => update({ rule })} />
              <Choices label={text("Supporting traces", "支持轮廓")} options={evidenceOptions} value={item.evidenceIds} empty={noEvidence} onChange={evidenceIds => update({ evidenceIds })} />
              <TextField label={text("Applicable conditions", "适用条件")} value={item.conditions.join("\n")} onChange={value => update({ conditions: value.split("\n") })} />
              <TextField label={text("Exceptions / failure conditions", "例外／失效条件")} value={item.exceptions.join("\n")} onChange={value => update({ exceptions: value.split("\n") })} />
            </article>;
          })()}
          <h3>{text("Conditional design prior", "有条件的设计先验")}</h3>
          {!research.designPrior ? <button type="button" disabled={!research.compositionPattern} onClick={() => changeResearch({ designPrior: {
            priorId: newStudyItemId("prior"), statement: "", patternId: research.compositionPattern!.patternId, hypothesisIds: [], conditions: [],
            preference: "", preferenceStatus: "unresolved", changedContext: null } })}>{text("Create design prior", "创建设计先验")}</button> : (() => {
            const item = research.designPrior;
            const update = (patch: Partial<typeof item>) => changeResearch({ designPrior: { ...item, ...patch } });
            return <article className="document-study__card">
              <TextField label={text("Prior to test in design", "待在设计中检验的先验")} value={item.statement} onChange={statement => update({ statement })} />
              <Choices label={text("Supporting explanations", "支持解释")} options={hypothesisOptions} value={item.hypothesisIds}
                empty={text("No explanations recorded.", "尚未记录解释。")} onChange={hypothesisIds => update({ hypothesisIds })} />
              <TextField label={text("Applicable conditions", "适用条件")} value={item.conditions.join("\n")} onChange={value => update({ conditions: value.split("\n") })} />
              <Field label={text("Human preference status", "人的偏好状态")}><select value={item.preferenceStatus} onChange={event => update({ preferenceStatus: event.target.value as typeof item.preferenceStatus })}>
                <option value="unresolved">{text("Unresolved · no endorsement", "未定，不代表签认")}</option><option value="stated">{text("Explicitly stated by me", "由我明确陈述")}</option>
              </select></Field>
              <TextField label={text("Preference / value choice", "偏好／价值选择")} hint={text("Leave unresolved when substantive human judgement is still needed.", "仍需人的实质判断时，保持未定。")}
                value={item.preference} onChange={preference => update({ preference })} />
              <h4>{text("Test a changed context", "检验改变后的适用情境")}</h4>
              {!item.changedContext ? <button type="button" onClick={() => update({ changedContext: { changedConditions: [], decision: "unresolved", reason: "", revisedStatement: "" } })}>
                {text("Change applicability conditions", "改变适用条件")}</button> : (() => {
                const context = item.changedContext;
                const change = (patch: Partial<typeof context>) => update({ changedContext: { ...context, ...patch } });
                return <>
                  <TextField label={text("Changed conditions", "改变的条件")} value={context.changedConditions.join("\n")} onChange={value => change({ changedConditions: value.split("\n") })} />
                  <Field label={text("Decision under changed conditions", "改变条件后的决定")}><select value={context.decision} onChange={event => change({ decision: event.target.value as typeof context.decision })}>
                    <option value="unresolved">{text("Unresolved", "未定")}</option><option value="retain">{text("Retain", "保留")}</option>
                    <option value="revise">{text("Revise", "修订")}</option><option value="reject">{text("Reject", "拒绝")}</option>
                  </select></Field>
                  <TextField label={text("Reason tied to evidence / experiment", "与证据／实验相连的理由")} value={context.reason} onChange={reason => change({ reason })} />
                  {context.decision === "revise" && <TextField label={text("Revised prior", "修订后的先验")} value={context.revisedStatement} onChange={revisedStatement => change({ revisedStatement })} />}
                </>;
              })()}
            </article>;
          })()}
          {retained?.completion && <details><summary>{retained.completion.ready ? text("Saved research cycle complete", "已保存研究循环完整") : text("Remaining in the saved research cycle", "已保存研究中仍待完成的项")}</summary>
            <ul>{retained.completion.missing.map(item => <li key={item}>{item}</li>)}</ul></details>}
        </section>}
      </fieldset>
      <details className="document-study__provenance"><summary>{text("Source & saved revision", "来源与保存版本")}</summary>
        <dl><dt>{text("Source page", "来源页")}</dt><dd>{source.pageIndex + 1}</dd><dt>{text("Source fingerprint", "来源指纹")}</dt><dd>{source.assetSha256}</dd>
          <dt>{text("Saved revision", "保存版本")}</dt><dd>{study?.ledgerRef ?? text("Not saved", "尚未保存")}</dd></dl>
      </details>
    </div>
    <footer className="document-study__footer">
      {error && <p role="alert">{error}</p>}
      <p role="status">{loading ? text("Loading saved study…", "正在读取研究…") : proposing ? text("Requesting a model proposal…", "正在请求模型提案…")
        : busy ? text("Saving / checking…", "正在保存／检查…") : dirty || !study ? text("Unsaved draft", "草稿未保存") : text("Saved · can reopen", "已保存，可重开")}</p>
      <div className="document-study__actions"><button type="button" disabled={busy || !!proposing || (loading && !error) || (!error && (!study || dirty))}
        onClick={dirty ? () => setDiscardPrompt(true) : onReopen}>{error ? text("Retry reading saved study", "重试读取研究") : text("Reopen saved", "重新读取")}</button>
        <button type="button" className="document-study__save" disabled={locked} onClick={onSave}>{text("Save study & run selected changes", "保存研究并运行所选改动")}</button></div>
      {dirty && !discardPrompt && <button type="button" className="document-study__discard" disabled={locked} onClick={() => setDiscardPrompt(true)}>
        {text("Discard unsaved draft and reopen…", "放弃未存草稿并重开…")}</button>}
      {dirty && discardPrompt && <div className="document-study__notice" role="group" aria-label={text("Discard draft confirmation", "放弃草稿确认")}>
        <p>{text("Replace this unsaved draft with the latest saved study? Unsaved corrections will be lost.", "用最新保存的研究替换当前草稿？尚未保存的校正会丢失。")}</p>
        <div className="document-study__actions"><button type="button" disabled={locked} onClick={() => { setDiscardPrompt(false); onReopen(); }}>{text("Discard draft and reopen", "放弃草稿并重开")}</button>
          <button type="button" disabled={locked} onClick={() => setDiscardPrompt(false)}>{text("Keep editing", "继续编辑")}</button></div>
      </div>}
      {step < steps.length - 1 && <button type="button" className="document-study__next" onClick={() => setStep(step + 1)}>{text("Continue", "继续")} · {steps[step + 1]}</button>}
    </footer>
  </aside>;
}

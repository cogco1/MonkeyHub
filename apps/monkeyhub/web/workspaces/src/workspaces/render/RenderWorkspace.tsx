import {useCallback,useEffect,useRef,useState} from 'react';
import {useConnection} from '../../api/ProjectRuntimeContext';
import {call} from '../../api/error';
import {asStudioApiError} from '../../api/client';
import {readProjectVisualizationApiVisualizationGet as readState, saveProjectVisualizationApiVisualizationPut as saveState,
  setVisualizationSourceApiVisualizationSourcePost as setSource, readVisualizationSourceApiVisualizationSourceGet as readSource,
  listRenderJobsApiRenderJobsGet as listJobs, type RenderCameraDto} from '../../api/generated';
import {createNativeRenderApiRenderNativeJobsPost as createNative, completeNativeRenderApiRenderNativeJobsJobIdCompletePost as completeNative, failNativeRenderApiRenderNativeJobsJobIdFailPost as failNative} from '../../api/generated';
import {renderNativePng} from './nativeRender';
import type {RenderSelection} from './renderCamera';
import type {ProjectVisualizationState,VisualizationRecord} from './visualization';
import NativePreview,{type NativePreviewHandle,type NativeScene} from './NativePreview';
import LookDevelopment from './LookDevelopment';
import RenderResults from './RenderResults';
import './render.css';

export default function RenderWorkspace({active,refreshKey,selection,onModel}:{
  active:boolean;refreshKey:number;selection:RenderSelection|null;onModel():void;
}) {
  const connection=useConnection(),client=connection.client;
  const [record,setRecord]=useState<VisualizationRecord|null>(null),[draft,setDraft]=useState<ProjectVisualizationState|null>(null);
  const current=useRef<VisualizationRecord|null>(null),draftRef=useRef<ProjectVisualizationState|null>(null);
  const [bytes,setBytes]=useState<ArrayBuffer|null>(null),[error,setError]=useState<string|null>(null);
  const [dirty,setDirty]=useState(false),[saving,setSaving]=useState(false),[loading,setLoading]=useState(true);
  const [mode,setMode]=useState<'preview'|'result'>('preview'),[legacyJob,setLegacyJob]=useState<string|null>(null);
  const [objects,setObjects]=useState<{id:string;name:string}[]>([]),[ready,setReady]=useState(false);
  const preview=useRef<NativePreviewHandle>(null),native=useRef<NativeScene|null>(null),savingRef=useRef(false);
  const [pan,setPan]=useState(false);
  const [rendering,setRendering]=useState(false),[resultVersion,setResultVersion]=useState(0),[renderStatus,setRenderStatus]=useState('');
  const consumed=useRef<RenderSelection|null>(null);
  const change=useCallback((s:ProjectVisualizationState)=>{draftRef.current=s;setDraft(s);setDirty(true);},[]);
  const load=useCallback(async()=>{
    setLoading(true);setError(null);
    try {
      const r=await call('GET visualization',readState({client})) as unknown as VisualizationRecord;
      current.current=r;draftRef.current=r.state;setRecord(r);setDraft(r.state);setDirty(false);
      if(r.source){const b=await call('GET visualization source',readSource({client,parseAs:'arrayBuffer'}));setBytes(b as ArrayBuffer);}
      else {const jobs=await call('GET render jobs',listJobs({client}));setLegacyJob(jobs.jobs.sort((a,b)=>b.createdAt.localeCompare(a.createdAt))[0]?.jobId??null);}
    } catch(e){setError(asStudioApiError(e).detail);} finally{setLoading(false);}
  },[client]);
  useEffect(()=>{void load();},[load]);
  const save=useCallback(async()=>{
    if(!draftRef.current||!current.current||savingRef.current)return null;
    const value=draftRef.current; savingRef.current=true;setSaving(true);
    try {
      const r=await call('PUT visualization',saveState({client,body:{expectedRevision:current.current.revision,state:value as unknown as Record<string,unknown>}})) as unknown as VisualizationRecord;
      current.current=r;setRecord(r);if(draftRef.current===value)setDirty(false);setError(null);return r;
    } catch(e){setError(asStudioApiError(e).detail);return null;} finally{savingRef.current=false;setSaving(false);}
  },[client]);
  useEffect(()=>{if(!dirty||saving||error)return;const timer=setTimeout(()=>void save(),700);return()=>clearTimeout(timer);},[draft,dirty,saving,error,save]);
  const importSource=useCallback(async(jobId?:string,selected?:RenderSelection)=>{
    if(!current.current)return;setLoading(true);setError(null);
    try{
      let contentBase64:string|undefined;
      if(selected?.localFile)contentBase64=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(reader.error);reader.readAsDataURL(selected.localFile!);});
      await call('POST visualization source',setSource({client,body:{expectedRevision:current.current.revision,jobId,
        modelSource:selected?.modelSource,fileName:selected?.localFile?.name,contentBase64,camera:selected?.camera as RenderCameraDto|undefined}}));
      await load();setMode('preview');
    }catch(e){setError(asStudioApiError(e).detail);setLoading(false);}
  },[client,load]);
  useEffect(()=>{if(active&&selection&&record&&!loading&&!saving&&!dirty&&!rendering&&consumed.current!==selection){consumed.current=selection;void importSource(undefined,selection);}},[active,selection,record,loading,saving,dirty,rendering,importSource]);
  const onReady=useCallback((rt:NativeScene|null)=>{native.current=rt;setReady(!!rt);},[]);
  const render=async()=>{
    if(!native.current||!current.current||dirty||saving||rendering)return;
    setRendering(true);setError(null);setRenderStatus('正在创建渲染任务…');
    let jobId:string|undefined;
    native.current.controls.enabled=false;
    try {
      const job=await call('POST native render',createNative({client,body:{requestId:crypto.randomUUID(),revision:current.current.revision}}));
      jobId=job.jobId;
      if(!job.snapshot||!job.snapshotSha256||job.sourceSha256!==current.current.source?.artifact.sha256)throw new Error('渲染源与当前预览不一致，请重新读取项目。');
      setRenderStatus('正在使用 WebGL2 渲染…');
      const blob=await renderNativePng(native.current,job.snapshot as unknown as ProjectVisualizationState);
      const contentBase64=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(reader.error);reader.readAsDataURL(blob);});
      setRenderStatus('正在保存 PNG…');
      await call('POST native result',completeNative({client,path:{job_id:jobId},body:{snapshotSha256:job.snapshotSha256,contentBase64}}));
      setResultVersion(v=>v+1);setMode('result');setRenderStatus('Native Render 已完成');
    }catch(e){
      const detail=e instanceof Error?e.message:String(e);setError(detail);setRenderStatus('渲染失败');
      if(jobId)try{await call('POST native failure',failNative({client,path:{job_id:jobId},body:{detail:detail.slice(0,1000)}}));}catch{/* Keep the original failure; abandoned tasks expire. */}
    }finally{if(native.current)native.current.controls.enabled=true;setRendering(false);}
  };
  return <section className="render-workspace native-workspace" aria-label="渲染">
    <header className="render-toolbar"><strong>Native Render</strong><button onClick={onModel}>建模</button>
      <button aria-pressed={mode==='preview'} onClick={()=>setMode('preview')}>实时 3D 预览</button>
      <button aria-pressed={mode==='result'} onClick={()=>setMode('result')}>History · 渲染结果</button>
      <button disabled={!ready||rendering||loading} aria-pressed={pan} onClick={()=>{setPan(!pan);preview.current?.pan(!pan);}}>平移模式</button>
      <button disabled={!ready||rendering||loading} onClick={()=>preview.current?.fit()}>Fit to View</button>
      <button disabled={!dirty||saving} onClick={()=>void save()}>保存表现</button>
      <button disabled={!ready||dirty||saving||loading||rendering} onClick={()=>void render()}>Native Render → PNG</button>
      <span role="status">{renderStatus}</span><span role="status">{loading?'正在读取项目…':saving?'正在保存…':dirty?'未保存':record?.state?`已保存 · 修订 ${record.revision}`:''}</span>
    </header>
    {error&&<div role="alert" className="render-error">{error} <button onClick={()=>void load()}>重新读取项目状态</button></div>}
    <div className="native-workspace-body" hidden={mode!=='preview'}>
      {draft&&bytes?<><div className="native-preview-area"><NativePreview ref={preview} bytes={bytes} state={draft}
        active={active&&mode==='preview'} onReady={onReady} onObjects={setObjects} onCamera={camera=>change({...draftRef.current!,camera})}/>
        <p className="native-preview-hint">拖动旋转 · 右键平移 · 滚轮缩放 · 仅修改表现</p></div>
        <fieldset className="native-lookdev-lock" disabled={rendering||loading}><LookDevelopment state={draft} onChange={change} objects={objects}/></fieldset></>:
        <div className="render-empty"><h2>打开模型，开始实时预览</h2><p>从建模区发送当前模型，或使用项目内保留的模型。</p>
          {legacyJob&&<button disabled={loading} onClick={()=>void importSource(legacyJob)}>打开最近渲染的模型</button>}</div>}
    </div>
    {mode==='result'&&<RenderResults active={active} refreshKey={refreshKey+resultVersion} onPreview={()=>setMode('preview')}/>}
  </section>;
}

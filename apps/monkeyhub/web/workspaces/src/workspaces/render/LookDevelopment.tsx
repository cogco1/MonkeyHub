import type { ProjectVisualizationState as State, Vec3, VisualLight } from './visualization';
function NumberField({label,value,onChange,min=-1e6,max=1e6,step=.1}: {
  label:string;value:number;onChange(v:number):void;min?:number;max?:number;step?:number;
}) {return <label>{label}<input aria-label={label} type="number" min={min} max={max} step={step} value={value}
  onChange={e=>{if(e.target.value!=='' && Number.isFinite(e.target.valueAsNumber))onChange(Math.max(min,Math.min(max,e.target.valueAsNumber)));}} /></label>;}
function VectorField({label,value,onChange}:{label:string;value:Vec3;onChange(v:Vec3):void}) {
  return <div className="visual-vector">{value.map((v,i)=><NumberField key={i} label={`${label} ${'XYZ'[i]}`} value={v}
    onChange={n=>{const next=[...value] as Vec3;next[i]=n;onChange(next);}} />)}</div>;
}
function ColorField({label,value,onChange}:{label:string;value:string;onChange(v:string):void}){
  return <label>{label}<span className="color-fields"><input aria-label={`${label} 色盘`} type="color" value={value} onInput={e=>onChange(e.currentTarget.value)} /><input aria-label={label} type="text" value={value} maxLength={7} onChange={e=>{if(/^#[0-9a-fA-F]{6}$/.test(e.target.value))onChange(e.target.value);}} /></span></label>;
}
export default function LookDevelopment({state,onChange,objects}:{state:State;onChange(s:State):void;objects:{id:string;name:string}[]}) {
  const camera=(patch:Partial<State['camera']>)=>onChange({...state,camera:{...state.camera,...patch}});
  const material=(index:number,patch:Partial<State['materials'][number]>)=>onChange({...state,materials:state.materials.map((m,i)=>i===index?{...m,...patch}:m)});
  const light=(index:number,patch:Partial<VisualLight>)=>onChange({...state,lights:state.lights.map((l,i)=>i===index?{...l,...patch}:l)});
  return <aside className="look-development" aria-label="表现设置">
    <details open><summary>Camera · 相机</summary>
      <label>投影<select aria-label="投影" value={state.camera.projection} onChange={e=>camera({projection:e.target.value as 'perspective'|'orthographic'})}>
        <option value="perspective">Perspective · 透视</option><option value="orthographic">Orthographic · 正交</option></select></label>
      <NumberField label="FOV" value={state.camera.fov} min={1} max={175} step={1} onChange={fov=>camera({fov})}/>
      {state.camera.projection==='orthographic'&&<NumberField label="正交高度" value={state.camera.orthoHeight} min={.001} onChange={orthoHeight=>camera({orthoHeight})}/>}
      <VectorField label="相机位置" value={state.camera.position} onChange={position=>camera({position})}/>
      <VectorField label="相机目标" value={state.camera.target} onChange={target=>camera({target})}/>
      <button disabled={state.savedCameras.length>=32} onClick={()=>onChange({...state,savedCameras:[...state.savedCameras,{name:`机位 ${state.savedCameras.length+1}`,camera:structuredClone(state.camera)}]})}>保存机位</button>
      {state.savedCameras.map((c,i)=><div className="saved-camera" key={i}><button onClick={()=>camera(c.camera)}>{c.name}</button>
        <button aria-label={`删除${c.name}`} onClick={()=>onChange({...state,savedCameras:state.savedCameras.filter((_,n)=>n!==i)})}>×</button></div>)}
    </details>
    <details open><summary>Material · 材质</summary>
      {state.materials.map((m,i)=><div className="visual-card" key={m.objectId}>
        <strong>{m.objectId==='*'?'默认材质':objects.find(o=>o.id===m.objectId)?.name??'缺失的对象'}</strong>
        <ColorField label={i===0?'Base Color':`对象 ${i} Base Color`} value={m.baseColor} onChange={baseColor=>material(i,{baseColor})}/>
        {(['roughness','metallic','opacity'] as const).map(k=><NumberField key={k} label={`${i?`对象 ${i} `:''}${({roughness:'Roughness',metallic:'Metallic',opacity:'Opacity'})[k]}`} value={m[k]} min={0} max={1} step={.05} onChange={v=>material(i,{[k]:v})}/>)}
        {i>0&&<button onClick={()=>onChange({...state,materials:state.materials.filter((_,n)=>n!==i)})}>移除覆盖</button>}
      </div>)}
      <label>对象材质覆盖<select aria-label="对象材质覆盖" value="" onChange={e=>{if(e.target.value)onChange({...state,materials:[...state.materials,{...state.materials[0],objectId:e.target.value}]});}}>
        <option value="">选择对象…</option>{objects.filter(o=>!state.materials.some(m=>m.objectId===o.id)).map(o=><option key={o.id} value={o.id}>{o.name}</option>)}</select></label>
    </details>
    <details open><summary>Lighting · 灯光</summary>
      {state.lights.map((l,i)=><div className="visual-card" key={l.id}>
        <label>灯光 {i+1}<select aria-label={`灯光 ${i+1} 类型`} value={l.type} onChange={e=>light(i,{type:e.target.value as VisualLight['type']})}>
          <option value="directional">Directional</option><option value="point">Point</option><option value="area">Area</option></select></label>
        <NumberField label={`灯光 ${i+1} 强度`} value={l.intensity} min={0} max={10000} onChange={intensity=>light(i,{intensity})}/>
        <ColorField label={`灯光 ${i+1} 颜色`} value={l.color} onChange={color=>light(i,{color})}/>
        <VectorField label={`灯光 ${i+1} 位置`} value={l.position} onChange={position=>light(i,{position})}/>
        {l.type!=='point'&&<VectorField label={`灯光 ${i+1} 目标`} value={l.target} onChange={target=>light(i,{target})}/>}
        {l.type==='area'&&<><NumberField label={`灯光 ${i+1} 宽度`} value={l.width} min={.001} onChange={width=>light(i,{width})}/>
          <NumberField label={`灯光 ${i+1} 高度`} value={l.height} min={.001} onChange={height=>light(i,{height})}/></>}
        <label><input aria-label={`灯光 ${i+1} 阴影`} type="checkbox" checked={l.shadow} onChange={e=>light(i,{shadow:e.target.checked})}/>Shadow · 阴影</label>
        {l.type==='area'&&l.shadow&&<small>Area 阴影使用四点采样近似。</small>}
        <button onClick={()=>onChange({...state,lights:state.lights.filter((_,n)=>n!==i)})}>删除灯光 {i+1}</button>
      </div>)}
      <button disabled={state.lights.length>=8} onClick={()=>onChange({...state,lights:[...state.lights,{id:crypto.randomUUID(),type:'point',position:[2,-2,3],target:[0,0,0],color:'#ffffff',intensity:10,width:2,height:2,shadow:true}]})}>添加灯光</button>
    </details>
    <details open><summary>Environment · 环境</summary>
      <ColorField label="背景颜色" value={state.environment.background} onChange={background=>onChange({...state,environment:{...state.environment,background}})}/>
      <ColorField label="环境光颜色" value={state.environment.color} onChange={color=>onChange({...state,environment:{...state.environment,color}})}/>
      <NumberField label="环境光强度" value={state.environment.intensity} min={0} max={10} onChange={intensity=>onChange({...state,environment:{...state.environment,intensity}})}/>
    </details>
    <details open><summary>Render Settings · 输出</summary>
      <NumberField label="输出宽度" value={state.renderSettings.width} min={64} max={4096} step={1} onChange={width=>onChange({...state,renderSettings:{...state.renderSettings,width:Math.round(width)}})}/>
      <NumberField label="输出高度" value={state.renderSettings.height} min={64} max={4096} step={1} onChange={height=>onChange({...state,renderSettings:{...state.renderSettings,height:Math.round(height)}})}/>
      <NumberField label="曝光" value={state.renderSettings.exposure} min={.05} max={10} onChange={exposure=>onChange({...state,renderSettings:{...state.renderSettings,exposure}})}/>
    </details>
  </aside>;
}

import type { Camera, Geometry, PhysicalScene } from "./sceneTypes";
import type { RenderView } from "../monkeyarch/viewer/renderView";

/** Transient connection between projections of the existing Physical scene draft. No writer. */
export class CameraLink {
  private listeners=new Set<()=>void>();
  subscribe(listener:()=>void){this.listeners.add(listener);return ()=>{this.listeners.delete(listener);};}
  linked(){return !!this.view();}
  private notify(){this.listeners.forEach(fn=>fn());}
  private physical: { geometry: Geometry; scene: PhysicalScene; edit(camera: Camera): void } | null = null;
  private model: { read(): RenderView | null; apply(camera: Camera, aspect: number): void } | null = null;
  attachModel(model: NonNullable<CameraLink["model"]>) { this.model=model; this.restore(); this.notify(); return ()=>{if(this.model===model){this.model=null;this.notify();}}; }
  attachPhysical(physical: NonNullable<CameraLink["physical"]>) { this.physical=physical; this.restore();this.notify(); return ()=>{if(this.physical===physical){this.physical=null;this.notify();}}; }
  private view() {
    const p=this.physical,v=this.model?.read();
    const displayed=v?.displaySource ?? v?.modelSource;
    if(!p || !v || !displayed || v.sourceIssue || (!Number.isFinite(v.metersPerUnit) || v.metersPerUnit! <= 0) || p.scene.geometryRevision!==p.geometry.source.geometryRevision)return null;
    const s=p.geometry.source, expected=s.preview ?? s.modelSource ?? s;
    return expected.runId===displayed.runId && expected.assetSha256===displayed.assetSha256 ? v : null;
  }
  restore() { const p=this.physical;if(p && this.view())this.model?.apply(p.scene.camera,p.scene.settings.width/p.scene.settings.height); }
  changed(kind: "gesture" | "resize" = "gesture") {
    if(kind==='resize'){this.restore();return;}
    const view=this.view(),p=this.physical;if(!view || !p)return;
    const camera=view.camera,scale=view.metersPerUnit!, gate=p.scene.settings.width/p.scene.settings.height;
    const margin=Math.max(1,gate/view.aspect);
    const orthographic='isOrthographicCamera' in camera;
    const next:Camera={...p.scene.camera,position:camera.position.toArray().map(v=>v*scale) as Camera['position'],
      target:view.target.map(v=>v*scale) as Camera['target'],up:camera.up.toArray() as Camera['up'],
      projection:orthographic?'orthographic':'perspective',
      fov:orthographic?p.scene.camera.fov:2*Math.atan(Math.tan(camera.fov*Math.PI/360)/camera.zoom/margin)*180/Math.PI,
      orthoScale:orthographic?(camera.top-camera.bottom)/camera.zoom*scale/margin:p.scene.camera.orthoScale};
    if(JSON.stringify(next)!==JSON.stringify(p.scene.camera))p.edit(next);
  }
}

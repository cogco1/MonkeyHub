import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { MOUSE, HalfFloatType, WebGLRenderTarget, ACESFilmicToneMapping, AmbientLight, Box3, Color, DirectionalLight, Group, Mesh,
  MeshStandardMaterial, OrthographicCamera, PerspectiveCamera, PointLight, RectAreaLight,
  Scene, Sphere, SpotLight, SRGBColorSpace, Vector3, WebGLRenderer, PCFSoftShadowMap, type Object3D } from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { Rhino3dmLoader } from 'three/examples/jsm/loaders/3DMLoader.js';
import { RectAreaLightUniformsLib } from 'three/examples/jsm/lights/RectAreaLightUniformsLib.js';
import { disposeScene } from '../monkeyarch/viewer/sceneInspection';
import { prepareLoadedModel } from '../monkeyarch/viewer/modelDisplay';
import { fitDistance } from '../monkeyarch/viewer/fitCamera';
import type { ProjectVisualizationState, VisualCamera, Vec3 } from './visualization';

export interface NativePreviewHandle { fit(): void; pan(enabled:boolean):void }
export interface NativeScene {
  renderer: WebGLRenderer; scene: Scene; model: Object3D; lights: Group;
  camera: PerspectiveCamera | OrthographicCamera; controls: OrbitControls; sphere: Sphere;
}
export function cameraFor(c: VisualCamera, aspect: number) {
  const h=c.orthoHeight;
  const camera=c.projection==='perspective' ? new PerspectiveCamera(c.fov,aspect,c.near,c.far)
    : new OrthographicCamera(-h*aspect/2,h*aspect/2,h/2,-h/2,c.near,c.far);
  camera.position.fromArray(c.position); camera.up.fromArray(c.up); camera.lookAt(new Vector3(...c.target));
  camera.updateMatrixWorld(); return camera;
}
export function applyVisualization(rt: NativeScene, state: ProjectVisualizationState) {
  const aspect=state.renderSettings.width/state.renderSettings.height;
  rt.camera=cameraFor(state.camera,aspect); rt.controls.object=rt.camera;
  rt.controls.target.fromArray(state.camera.target); rt.controls.update();
  rt.renderer.toneMappingExposure=state.renderSettings.exposure;
  rt.scene.background=new Color(state.environment.background);
  rt.model.traverse(obj=>{
    if (!(obj instanceof Mesh)) return;
    const id=String(obj.userData.attributes?.id ?? '');
    const m=state.materials.find(m=>m.objectId===id) ?? state.materials.find(m=>m.objectId==='*')!;
    const old=Array.isArray(obj.material)?obj.material:[obj.material]; old.forEach(m=>m.dispose());
    obj.material=new MeshStandardMaterial({color:m.baseColor,roughness:m.roughness,metalness:m.metallic,
      opacity:m.opacity,transparent:m.opacity<1,depthWrite:m.opacity===1});
    obj.castShadow=true; obj.receiveShadow=true;
  });
  rt.lights.traverse(o=>{ if ('dispose' in o && typeof o.dispose==='function') o.dispose(); });
  rt.lights.clear();
  rt.lights.add(new AmbientLight(state.environment.color,state.environment.intensity));
  const radius=Math.max(rt.sphere.radius,.01);
  for (const v of state.lights) {
    const setup=(light: DirectionalLight | PointLight | SpotLight, position: Vector3, intensity: number)=>{
      light.color.set(v.color); light.intensity=intensity; light.position.copy(position);
      light.castShadow=v.shadow; light.shadow.mapSize.set(1024,1024);
      light.shadow.bias=-.0002; light.shadow.normalBias=radius*.001;
      light.shadow.camera.near=Math.max(radius*.001,.0001);
      light.shadow.camera.far=position.distanceTo(rt.sphere.center)+radius*6;
      if (light instanceof DirectionalLight) {
        Object.assign(light.shadow.camera,{left:-radius*2,right:radius*2,top:radius*2,bottom:-radius*2});
      }
      if ('target' in light) { light.target.position.fromArray(v.target); rt.lights.add(light.target); }
      rt.lights.add(light);
    };
    if (v.type==='area' && !v.shadow) {
      const light=new RectAreaLight(v.color,v.intensity,v.width,v.height);
      light.position.fromArray(v.position); light.up.set(0,0,1); light.lookAt(new Vector3(...v.target)); rt.lights.add(light);
    } else if (v.type==='area') {
      // Four directional emission samples approximate an area emitter with shadows.
      // Explicitly identified as an approximation in the light editor.
      const center=new Vector3(...v.position), dir=new Vector3(...v.target).sub(center).normalize();
      const right=new Vector3().crossVectors(dir,Math.abs(dir.z)>.99?new Vector3(0,1,0):new Vector3(0,0,1)).normalize();
      const up=new Vector3().crossVectors(right,dir).normalize();
      for (const x of [-.25,.25]) for (const y of [-.25,.25]) {
        const light=new SpotLight(v.color,v.intensity/4,0,Math.PI/2,.5,2);
        setup(light,center.clone().addScaledVector(right,x*v.width).addScaledVector(up,y*v.height),v.intensity/4);
      }
    } else setup(v.type==='point'?new PointLight():new DirectionalLight(),new Vector3(...v.position),v.intensity);
  }
}

export default forwardRef<NativePreviewHandle, { bytes: ArrayBuffer; state: ProjectVisualizationState;
  active: boolean; onCamera(c: VisualCamera): void; onReady(rt: NativeScene | null): void;
  onObjects?(objects: {id:string;name:string}[]):void;
}>(function NativePreview({bytes,state,active,onCamera,onReady,onObjects},ref) {
  const host=useRef<HTMLDivElement>(null), runtime=useRef<NativeScene|null>(null);
  const latest=useRef({state,onCamera,onReady,onObjects,active}); latest.current={state,onCamera,onReady,onObjects,active};
  const [error,setError]=useState<string|null>(null), [ready,setReady]=useState(false);
  const capture=()=>{
    const rt=runtime.current; if (!rt) return;
    latest.current.onCamera({...latest.current.state.camera,position:rt.camera.position.toArray() as Vec3,
      target:rt.controls.target.toArray() as Vec3,up:rt.camera.up.toArray() as Vec3,
      orthoHeight:rt.camera instanceof OrthographicCamera?(rt.camera.top-rt.camera.bottom)/rt.camera.zoom:latest.current.state.camera.orthoHeight});
  };
  useImperativeHandle(ref,()=>({pan(enabled){if(runtime.current)runtime.current.controls.mouseButtons.LEFT=enabled?MOUSE.PAN:MOUSE.ROTATE;},fit(){
    const rt=runtime.current; if(!rt)return;
    const c=latest.current.state.camera, aspect=latest.current.state.renderSettings.width/latest.current.state.renderSettings.height;
    const d=fitDistance({radius:rt.sphere.radius,fovDegrees:c.fov,aspect});
    const direction=rt.camera.position.clone().sub(rt.controls.target).normalize();
    latest.current.onCamera({...c,position:rt.sphere.center.clone().addScaledVector(direction,d).toArray() as Vec3,
      target:rt.sphere.center.toArray() as Vec3,orthoHeight:rt.sphere.radius*2.4/Math.min(aspect,1)});
  }}),[]);
  useEffect(()=>{
    let disposed=false; let cleanup=()=>{}; setReady(false); setError(null);
    const loader=new Rhino3dmLoader(); loader.setLibraryPath('/rhino3dm/'); loader.setWorkerLimit(2);
    loader.parse(bytes.slice(0),model=>{
      if(disposed){disposeScene(model);return;}
      try {
        RectAreaLightUniformsLib.init(); prepareLoadedModel(model);
        const sphere=new Box3().setFromObject(model).getBoundingSphere(new Sphere());
        if(!Number.isFinite(sphere.radius)||sphere.radius<=0)throw new Error('模型没有可显示的几何');
        const renderer=new WebGLRenderer({antialias:true}); renderer.outputColorSpace=SRGBColorSpace;
        renderer.toneMapping=ACESFilmicToneMapping; renderer.shadowMap.enabled=true; renderer.shadowMap.type=PCFSoftShadowMap;
        renderer.setPixelRatio(Math.min(window.devicePixelRatio,2)); renderer.domElement.setAttribute('aria-label','实时渲染预览');
        const scene=new Scene(), lights=new Group(); scene.add(model,lights);
        const camera=cameraFor(latest.current.state.camera,1), controls=new OrbitControls(camera,renderer.domElement);
        controls.screenSpacePanning=true; controls.enableDamping=false;
        const rt={renderer,scene,model,lights,camera,controls,sphere}; runtime.current=rt;
        applyVisualization(rt,latest.current.state);
        host.current!.appendChild(renderer.domElement);
        const target=new WebGLRenderTarget(1,1,{type:HalfFloatType,samples:Math.min(4,renderer.capabilities.maxSamples)});
        const composer=new EffectComposer(renderer,target), renderPass=new RenderPass(scene,rt.camera), outputPass=new OutputPass();
        composer.addPass(renderPass); composer.addPass(outputPass);
        let lastWidth=0,lastHeight=0;
        const resize=()=>{if(host.current){
          const aspect=latest.current.state.renderSettings.width/latest.current.state.renderSettings.height;
          const w=Math.max(1,Math.min(host.current.clientWidth,host.current.clientHeight*aspect)),h=w/aspect;
          if(w===lastWidth&&h===lastHeight)return;lastWidth=w;lastHeight=h;
          renderer.setSize(w,h,false); renderer.domElement.style.width=`${w}px`;renderer.domElement.style.height=`${h}px`;composer.setSize(w,h);
        }};
        const observer=new ResizeObserver(resize); observer.observe(host.current!); resize();
        controls.addEventListener('end',capture);
        renderer.setAnimationLoop(()=>{if(latest.current.active && !disposed){resize();renderPass.camera=rt.camera;composer.render();}});
        const objects: {id:string;name:string}[]=[];
        model.traverse(o=>{if(o instanceof Mesh && o.userData.attributes?.id)objects.push({id:String(o.userData.attributes.id),name:o.name||String(o.userData.attributes.id)});});
        latest.current.onObjects?.(objects); latest.current.onReady(rt); setReady(true);
        cleanup=()=>{observer.disconnect(); renderer.setAnimationLoop(null); controls.dispose();
          lights.traverse(o=>{if('dispose' in o && typeof o.dispose==='function')o.dispose();});
          disposeScene(model); outputPass.dispose(); renderPass.dispose(); composer.dispose(); renderer.dispose(); renderer.domElement.remove();};
      } catch(e){disposeScene(model);setError(String(e));}
    },e=>{if(!disposed)setError(String(e));});
    return()=>{disposed=true;loader.dispose();cleanup();runtime.current=null;latest.current.onReady(null);};
  },[bytes]);
  useEffect(()=>{if(runtime.current)applyVisualization(runtime.current,state);},[state]);
  return <div className="native-preview-frame" style={{aspectRatio:`${state.renderSettings.width}/${state.renderSettings.height}`}}>
    <div ref={host} className="native-preview-canvas" />
    {!ready&&!error&&<p role="status">正在加载 3D 模型…</p>}{error&&<p role="alert">{error}</p>}
  </div>;
});

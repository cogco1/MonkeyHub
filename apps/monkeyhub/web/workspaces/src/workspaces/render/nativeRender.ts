import { HalfFloatType, Vector4, WebGLRenderTarget } from 'three';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { applyVisualization, cameraFor, type NativeScene } from './NativePreview';
import type { ProjectVisualizationState } from './visualization';

/** Render the retained request at its own resolution, independent of the UI canvas. */
export async function renderNativePng(rt: NativeScene, snapshot: ProjectVisualizationState): Promise<Blob> {
  const {renderer}=rt, {width,height}=snapshot.renderSettings;
  const gl=renderer.getContext();
  if (!renderer.capabilities.isWebGL2 || gl.isContextLost()) throw new Error('Native Render 需要可用的 WebGL2。');
  if (Math.max(width,height)>renderer.capabilities.maxTextureSize || !renderer.extensions.has('EXT_color_buffer_float'))
    throw new Error('当前 GPU 不支持所选渲染尺寸或浮点渲染目标。');
  const previous=renderer.getRenderTarget(), viewport=renderer.getViewport(new Vector4());
  const scissor=renderer.getScissor(new Vector4()), scissorTest=renderer.getScissorTest();
  const linear=new WebGLRenderTarget(width,height,{type:HalfFloatType,samples:Math.min(4,renderer.capabilities.maxSamples)});
  const output=new WebGLRenderTarget(width,height,{depthBuffer:false});
  const pass=new OutputPass();
  try {
    applyVisualization(rt,snapshot);
    renderer.setScissorTest(false);
    renderer.setRenderTarget(linear); renderer.clear();
    renderer.render(rt.scene,cameraFor(snapshot.camera,width/height));
    // Scene target stays linear; apply the same ACES/exposure/sRGB transform as preview once.
    pass.render(renderer,output,linear,0,false);
    const pixels=new Uint8Array(width*height*4);
    renderer.readRenderTargetPixels(output,0,0,width,height,pixels);
    if(gl.isContextLost())throw new Error('GPU 上下文丢失，请重新打开预览后重试。');
    const canvas=document.createElement('canvas'); canvas.width=width; canvas.height=height;
    const ctx=canvas.getContext('2d'); if(!ctx)throw new Error('PNG 编码器不可用。');
    const image=ctx.createImageData(width,height), stride=width*4;
    for(let y=0;y<height;y++)image.data.set(pixels.subarray((height-y-1)*stride,(height-y)*stride),y*stride);
    ctx.putImageData(image,0,0);
    return await new Promise<Blob>((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error('PNG 编码失败。')),'image/png'));
  } finally {
    renderer.setRenderTarget(previous); renderer.setViewport(viewport);
    renderer.setScissor(scissor); renderer.setScissorTest(scissorTest);
    pass.dispose(); linear.dispose(); output.dispose();
  }
}

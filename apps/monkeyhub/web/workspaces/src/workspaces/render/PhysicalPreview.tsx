import { useEffect, useRef, useState } from "react";
import { WebGLRenderer, Scene, Color, BufferGeometry, Float32BufferAttribute, Mesh, MeshStandardMaterial,
  PerspectiveCamera, OrthographicCamera, RectAreaLight, DirectionalLight, PointLight, AmbientLight,
  TextureLoader, SRGBColorSpace, ACESFilmicToneMapping, EquirectangularReflectionMapping, Texture, Vector3 } from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RectAreaLightUniformsLib } from "three/examples/jsm/lights/RectAreaLightUniformsLib.js";
import { triangleMaterials, type Geometry, type PhysicalScene, type ImageRef, type Camera } from "./sceneTypes";

export default function PhysicalPreview({ geometry, value, imageUrl, onCamera }: {
  geometry: Geometry; value: PhysicalScene; imageUrl(ref: ImageRef): Promise<string>; onCamera(camera: Camera): void;
}) {
  const host = useRef<HTMLDivElement>(null), changed = useRef(onCamera); changed.current = onCamera;
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!host.current || geometry.source.geometryRevision !== value.geometryRevision) return;
    const container = host.current; let renderer: WebGLRenderer;
    try { renderer = new WebGLRenderer({ antialias: true, alpha: true }); } catch { setError("WebGL preview unavailable"); return; }
    setError(null); RectAreaLightUniformsLib.init();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));renderer.outputColorSpace = SRGBColorSpace;
    renderer.toneMapping = ACESFilmicToneMapping;renderer.toneMappingExposure = Math.pow(2, value.exposure);
    renderer.domElement.setAttribute("aria-label", "Interactive physical render scene");container.appendChild(renderer.domElement);
    const scene = new Scene();scene.background = new Color(value.environment.color);scene.environmentIntensity = value.environment.strength;
    const textures: Texture[] = [], geometries: BufferGeometry[] = [];let alive = true;
    const loader = new TextureLoader();
    const load = (ref: ImageRef, done: (texture: Texture) => void, color = true) => {
      void imageUrl(ref).then(url => { if (alive) loader.load(url, texture => { if (!alive) { texture.dispose(); return; } textures.push(texture); if (color) texture.colorSpace = SRGBColorSpace;done(texture); }, undefined, () => { if (alive) setError("A registered scene image could not be loaded"); }); }).catch(() => { if (alive) setError("A registered scene image could not be read"); });
    };
    const materials = value.materials.map(row => {
      const material = new MeshStandardMaterial({ color: row.baseColor, roughness: row.roughness, metalness: row.metallic });
      if (row.texture) load(row.texture, texture => { material.map = texture; material.needsUpdate = true; });
      if (row.normalMap) load(row.normalMap, texture => { material.normalMap = texture; material.needsUpdate = true; }, false);
      return material;
    });
    geometry.meshes.forEach((row, meshIndex) => {
      const meshGeometry = new BufferGeometry();geometries.push(meshGeometry);
      meshGeometry.setAttribute("position", new Float32BufferAttribute(row.vertices.flat(), 3));
      if (row.normals) meshGeometry.setAttribute("normal", new Float32BufferAttribute(row.normals.flat(), 3));
      if (row.texcoords) meshGeometry.setAttribute("uv", new Float32BufferAttribute(row.texcoords.flat(), 2));
      const assignment = triangleMaterials(row, meshIndex, value), indices: number[] = [];
      materials.forEach((_, material) => { const start = indices.length; row.triangles.forEach((triangle, index) => { if (assignment[index] === material) indices.push(...triangle); }); if (indices.length > start) meshGeometry.addGroup(start, indices.length-start, material); });
      meshGeometry.setIndex(indices);if (!row.normals) meshGeometry.computeVertexNormals();
      scene.add(new Mesh(meshGeometry, materials));
    });
    scene.add(new AmbientLight(value.environment.color, value.environment.strength));
    value.lights.forEach(row => {
      if (row.type === "area") { const light = new RectAreaLight(row.color, 1, row.size, row.size);light.power = row.intensity;light.position.fromArray(row.position);light.lookAt(new Vector3(...row.target));scene.add(light); }
      else if (row.type === "sun") { const light = new DirectionalLight(row.color, row.intensity);light.position.fromArray(row.position);light.target.position.fromArray(row.target);scene.add(light, light.target); }
      else { const light = new PointLight(row.color);light.power = row.intensity;light.position.fromArray(row.position);scene.add(light); }
    });
    if (value.environment.background) load(value.environment.background, texture => {
      const pixels = texture.image as HTMLImageElement;
      const aspect = value.settings.width/value.settings.height, native = pixels.width/pixels.height;
      if (native>aspect) { texture.repeat.x=aspect/native;texture.offset.x=(1-texture.repeat.x)/2; }
      else { texture.repeat.y=native/aspect;texture.offset.y=(1-texture.repeat.y)/2; }
      texture.updateMatrix();scene.background = texture;
    });
    if (value.environment.environmentMap) load(value.environment.environmentMap, texture => { texture.mapping = EquirectangularReflectionMapping;scene.environment = texture; });
    const aspect = value.settings.width / value.settings.height, camera = value.camera.projection === "perspective"
      ? new PerspectiveCamera(value.camera.fov, aspect, .001, 100000)
      : new OrthographicCamera(-value.camera.orthoScale*aspect/2, value.camera.orthoScale*aspect/2, value.camera.orthoScale/2, -value.camera.orthoScale/2, .001, 100000);
    camera.up.fromArray(value.camera.up);camera.position.fromArray(value.camera.position);camera.lookAt(new Vector3(...value.camera.target));
    const controls = new OrbitControls(camera, renderer.domElement);controls.target.fromArray(value.camera.target);controls.update();
    controls.addEventListener("end", () => { if (alive) changed.current({ ...value.camera, position: camera.position.toArray() as Camera["position"], target: controls.target.toArray() as Camera["target"], orthoScale: value.camera.orthoScale/camera.zoom }); });
    const resize = () => { const width = Math.max(120, container.clientWidth);renderer.setSize(width, width/aspect); };
    const observer = new ResizeObserver(resize);observer.observe(container);resize();
    renderer.setAnimationLoop(() => renderer.render(scene, camera));
    return () => { alive = false; observer.disconnect();controls.dispose();renderer.setAnimationLoop(null);geometries.forEach(g => g.dispose());materials.forEach(m => m.dispose());textures.forEach(t => t.dispose());renderer.dispose();renderer.domElement.remove(); };
  }, [geometry, value, imageUrl]);
  return <div className="physical-preview"><div ref={host} />{error && <p role="alert">{error}</p>}</div>;
}

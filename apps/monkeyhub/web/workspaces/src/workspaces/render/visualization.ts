export type Vec3 = [number, number, number];
export interface VisualCamera {
  position: Vec3; target: Vec3; up: Vec3; projection: 'perspective' | 'orthographic';
  fov: number; orthoHeight: number; near: number; far: number;
}
export interface VisualMaterial { objectId: string; baseColor: string; roughness: number; metallic: number; opacity: number }
export interface VisualLight {
  id: string; type: 'point' | 'directional' | 'area'; position: Vec3; target: Vec3;
  color: string; intensity: number; width: number; height: number; shadow: boolean;
}
export interface ProjectVisualizationState {
  camera: VisualCamera; savedCameras: { name: string; camera: VisualCamera }[];
  materials: VisualMaterial[]; lights: VisualLight[];
  environment: { background: string; color: string; intensity: number };
  renderSettings: { width: number; height: number; exposure: number };
}
export interface VisualizationRecord {
  revision: number; state: ProjectVisualizationState | null;
  source: { fileName: string; artifact: { sha256: string }; modelSource: unknown } | null;
}

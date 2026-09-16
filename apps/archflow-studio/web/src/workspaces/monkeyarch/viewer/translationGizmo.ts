import { Object3D, Vector3, type Camera, type Scene } from "three";
import { TransformControls } from "three/examples/jsm/controls/TransformControls.js";

export const TRANSLATION_CONSTRAINTS = ["X", "Y", "Z", "XY", "XZ", "YZ"] as const;
export type TranslationConstraint = typeof TRANSLATION_CONSTRAINTS[number];
type Vec3 = [number, number, number];
// Three consumes the plain NDC value returned by its _getPointer helper. The
// upstream typings incorrectly call that value a DOM PointerEvent. Cast only
// at that adapter boundary, never pass client pixels into the control math.
type Pointer = { x: number; y: number; button: number };
export interface TranslationGizmoSpec {
  origin: Vec3;
  translation: Vec3;
  constraint: TranslationConstraint | null;
}
export type TranslationSample = { constraint: TranslationConstraint; translation: Vec3 };

export function isTranslationConstraint(value: unknown): value is TranslationConstraint {
  return typeof value === "string" && (TRANSLATION_CONSTRAINTS as readonly string[]).includes(value);
}

/** Numeric entry and pointer results obey exactly the same World constraint. */
export function constrainedTranslation(values: readonly number[], constraint: TranslationConstraint): Vec3 {
  if (values.length !== 3 || !values.every(Number.isFinite)) throw new Error("Enter finite distances in metres.");
  return values.map((value, index) => constraint.includes("XYZ"[index]!) ? value : 0) as Vec3;
}

/**
 * A disposable handle projection, never attached to a design object. Three's
 * existing controls own handle picking and drag math; Stage owns the gesture,
 * preview and the one typed action. No DOM listeners, camera controls or model
 * writes are installed here: the existing Stage overlay forwards its pointer.
 */
export class TranslationGizmo {
  readonly controls: TransformControls;
  readonly proxy = new Object3D();
  private origin = new Vector3();
  private constraint: TranslationConstraint | null = null;

  constructor(scene: Scene, camera: Camera) {
    this.controls = new TransformControls(camera);
    this.controls.setMode("translate");
    this.controls.setSpace("world");
    this.controls.setSize(0.9);
    this.proxy.name = "monkeyarch-translation-pivot";
    const helper = this.controls.getHelper();
    helper.name = "monkeyarch-translation-gizmo";
    // The centre/free XYZ handle has no explicit constraint in this slice.
    // Layer masks suppress both its drawing and picking; no private Three
    // fields are used and its geometry remains owned by the helper's disposer.
    helper.traverse(object => { if (object.name === "XYZ") object.layers.disableAll(); });
    scene.add(this.proxy, helper);
    this.controls.attach(this.proxy);
  }

  setCamera(camera: Camera): void {
    this.controls.camera = camera;
    this.update();
  }

  set(spec: TranslationGizmoSpec): void {
    if (![...spec.origin, ...spec.translation].every(Number.isFinite)) throw new Error("Invalid transform coordinates.");
    this.origin.fromArray(spec.origin);
    this.constraint = spec.constraint;
    const delta = spec.constraint ? constrainedTranslation(spec.translation, spec.constraint) : [0, 0, 0];
    this.proxy.position.copy(this.origin).add(new Vector3(...delta));
    if (!this.controls.dragging) this.controls.axis = spec.constraint;
    this.update();
  }

  private update(): void {
    this.controls.camera.updateMatrixWorld();
    this.proxy.updateMatrixWorld();
    this.controls.getHelper().updateMatrixWorld(true);
  }

  hover(pointer: Pointer): TranslationConstraint | null {
    this.update();
    this.controls.pointerHover(pointer as PointerEvent);
    return isTranslationConstraint(this.controls.axis) ? this.controls.axis : null;
  }

  start(pointer: Pointer): TranslationSample | null {
    if (this.controls.dragging) return null;
    const constraint = this.hover(pointer);
    if (!constraint) return null;
    // Changing handles starts a new constrained preview from the original
    // pivot; otherwise a previous X move could leak into a later Y constraint.
    if (constraint !== this.constraint) this.proxy.position.copy(this.origin);
    this.constraint = constraint;
    this.update();
    this.controls.axis = constraint;
    this.controls.pointerDown({ ...pointer, button: 0 } as PointerEvent);
    return this.controls.dragging ? this.sample() : null;
  }

  move(pointer: Pointer): TranslationSample | null {
    if (!this.controls.dragging) return null;
    this.controls.pointerMove({ ...pointer, button: -1 } as PointerEvent);
    return this.sample();
  }

  end(): void {
    this.controls.pointerUp({ x: 0, y: 0, button: 0 } as PointerEvent);
    this.controls.axis = this.constraint;
  }

  private sample(): TranslationSample | null {
    if (!this.constraint) return null;
    const offset = this.proxy.position.clone().sub(this.origin).toArray();
    if (!offset.every(Number.isFinite)) return null;
    return { constraint: this.constraint, translation: constrainedTranslation(offset, this.constraint) };
  }

  dispose(): void {
    this.end();
    this.controls.detach();
    this.proxy.removeFromParent();
    const helper = this.controls.getHelper();
    helper.removeFromParent();
    // controls.dispose() unconditionally disconnects its DOM element in the
    // pinned Three version. There is no DOM connection here; dispose the owned
    // helper directly, including every picker geometry and material.
    helper.dispose();
  }
}

/**
 * How far the camera stands so that the whole model is inside the frame.
 *
 * A perspective frame sees a vertical angle and a horizontal one, and the
 * horizontal angle is the vertical angle scaled by the frame's aspect. A tall
 * narrow panel therefore sees *less* across than it does down: fitting to the
 * vertical angle alone puts a wide building's ends outside the picture, which
 * is what a Hub tool panel at 620 px showed. Both angles are honoured here,
 * and the model's bounding-sphere radius is used so the answer holds however
 * the camera is turned around it.
 */

export interface FitFrame {
  /** Bounding-sphere radius of the model, in model units. */
  readonly radius: number;
  /** The camera's vertical field of view, in degrees. */
  readonly fovDegrees: number;
  /** Frame width divided by frame height. */
  readonly aspect: number;
  /** How much room to leave around the model; 1 is exactly touching. */
  readonly margin?: number;
}

export function fitDistance({ radius, fovDegrees, aspect, margin = 1.12 }: FitFrame): number {
  const half = (Math.max(fovDegrees, 1) * Math.PI) / 360;
  const tangentVertical = Math.tan(half);
  const tangentHorizontal = tangentVertical * Math.max(aspect, 0.01);
  const safeRadius = Math.max(radius, 0.5);
  return Math.max(safeRadius / tangentVertical, safeRadius / tangentHorizontal) * margin;
}

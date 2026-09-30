/**
 * Which scene rectangle a point falls in.
 *
 * A project canvas names what was clicked in scene coordinates: MonkeyBoard's
 * double click finds the registered page under the pointer, and the Design
 * Tree's click finds the node or action under it. Both test against the
 * element's own rectangle, rotation included, and the topmost one wins.
 * Nothing here imports Excalidraw, so it runs in unit tests as well.
 */

export interface SceneBox {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  /** Radians, about the rectangle's centre, as Excalidraw stores it. */
  readonly angle?: number;
}

export interface ScenePoint {
  readonly x: number;
  readonly y: number;
}

/** Does this rectangle, turned about its own centre, contain the point? */
export function sceneBoxContains(box: SceneBox, point: ScenePoint): boolean {
  const { x, y, width, height } = box;
  const angle = box.angle ?? 0;
  if (![x, y, width, height, angle].every((value) => Number.isFinite(value))) return false;
  // A rotated box keeps its own rectangle; the point enters that frame first.
  const centreX = x + width / 2, centreY = y + height / 2;
  const cos = Math.cos(-angle), sin = Math.sin(-angle);
  const dx = point.x - centreX, dy = point.y - centreY;
  const localX = dx * cos - dy * sin + centreX;
  const localY = dx * sin + dy * cos + centreY;
  if (localX < Math.min(x, x + width) || localX > Math.max(x, x + width)) return false;
  return localY >= Math.min(y, y + height) && localY <= Math.max(y, y + height);
}

/** The last box in drawing order that contains the point: what the eye sees on top. */
export function topmostAt<T extends SceneBox>(boxes: readonly T[], point: ScenePoint): T | null {
  for (let index = boxes.length - 1; index >= 0; index -= 1) {
    if (sceneBoxContains(boxes[index], point)) return boxes[index];
  }
  return null;
}

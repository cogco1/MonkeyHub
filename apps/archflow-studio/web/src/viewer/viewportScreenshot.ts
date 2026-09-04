/** A canvas-shaped surface that can be encoded without importing the DOM in tests. */
export interface PngCanvas {
  readonly width: number;
  readonly height: number;
  toBlob(callback: (blob: Blob | null) => void, type?: string): void;
}

/** Redraw the current WebGL view, then encode its retained pixels as PNG. */
export function encodeViewportPng(
  canvas: PngCanvas,
  render: () => void,
): Promise<Blob> {
  if (canvas.width <= 0 || canvas.height <= 0) {
    return Promise.reject(new Error("The viewport has no drawable pixels."));
  }
  // The owning renderer retains its drawing buffer. Redrawing immediately
  // before toBlob keeps the async encoder on the exact picture now on screen.
  render();
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob === null || blob.size === 0) {
        reject(new Error("The viewport did not produce a PNG."));
        return;
      }
      resolve(blob);
    }, "image/png");
  });
}

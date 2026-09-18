import type { PDFDocumentLoadingTask, PDFPageProxy } from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import type { DocumentGestureDto, DocumentPageDto } from "../../api/generated";
import { inkPath } from "./documentInk";

/** The saved visible page and its saved marks, independent of the editor viewport. */
export async function renderDocumentVisual(
  file: File,
  page: DocumentPageDto,
  annotations: readonly DocumentGestureDto[],
  maxEdge = 2048,
): Promise<{ pagePngBase64: string; annotatedPngBase64: string | null; width: number; height: number }> {
  if (!Number.isFinite(maxEdge) || maxEdge < 1) throw new RangeError("The visual input size must be at least one pixel.");
  if (![page.width, page.height].every((value) => Number.isFinite(value) && value > 0)) {
    throw new RangeError("The document page must have positive finite dimensions.");
  }
  let edge = Math.min(2048, Math.floor(maxEdge));
  const canvas = document.createElement("canvas");
  let pdf: PDFDocumentLoadingTask | null = null;
  let pdfPage: PDFPageProxy | null = null;
  let viewport: ReturnType<PDFPageProxy["getViewport"]> | null = null;
  let image: HTMLImageElement | null = null;
  let imageUrl: string | null = null;
  try {
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas 2D is unavailable");

    if (file.type === "application/pdf") {
      const renderer = await import("pdfjs-dist");
      renderer.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
      pdf = renderer.getDocument({ data: await file.arrayBuffer() });
      const document = await pdf.promise;
      pdfPage = await document.getPage(page.pageIndex + 1);
      // The viewport applies the PDF CropBox and the saved page rotation once.
      viewport = pdfPage.getViewport({ scale: 1, rotation: page.rotation });
    } else {
      image = new Image();
      imageUrl = URL.createObjectURL(file);
      image.src = imageUrl;
      // Browser image decoding, as in DocumentSurface, applies EXIF orientation.
      await image.decode();
    }

    const sourceWidth = viewport?.width ?? page.width;
    const sourceHeight = viewport?.height ?? page.height;
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const scale = Math.min(image === null ? Infinity : 1, edge / Math.max(sourceWidth, sourceHeight));
      canvas.width = Math.max(1, Math.min(edge, Math.round(sourceWidth * scale)));
      canvas.height = Math.max(1, Math.min(edge, Math.round(sourceHeight * scale)));
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, canvas.width, canvas.height);
      if (pdfPage !== null && viewport !== null) {
        await pdfPage.render({
          canvas, canvasContext: context, viewport, background: "#ffffff",
          transform: [canvas.width / viewport.width, 0, 0, canvas.height / viewport.height, 0, 0],
        }).promise;
      } else if (image !== null) {
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
      }

      const { width, height } = canvas;
      const pagePngBase64 = pngBase64(canvas);
      const shortSide = Math.min(width, height);
      context.save();
      context.beginPath();
      context.rect(0, 0, width, height);
      context.clip();
      context.lineCap = "round";
      context.lineJoin = "round";
      for (const mark of annotations) {
        if (mark.kind === "text") continue;
        context.strokeStyle = mark.color;
        context.lineWidth = mark.lineWidth * shortSide;
        context.stroke(new Path2D(inkPath(mark, width, height)));
      }
      // The editor's text layer sits above saved vector strokes.
      context.textAlign = "left";
      context.textBaseline = "top";
      for (const mark of annotations) {
        if (mark.kind !== "text") continue;
        if (mark.fontSize == null || mark.label == null || !mark.points[0]) {
          throw new Error("A saved text annotation is missing its font size, text, or anchor.");
        }
        const fontSize = mark.fontSize * shortSide;
        const [x, y] = mark.points[0];
        context.fillStyle = mark.color;
        context.font = `${fontSize}px system-ui, sans-serif`;
        mark.label.split(/\r\n|\r|\n/).forEach((line, index) => {
          context.fillText(line, x * width, y * height + index * fontSize * 1.25);
        });
      }
      context.restore();
      const annotatedPngBase64 = annotations.length > 0 ? pngBase64(canvas) : null;
      if ([pagePngBase64, annotatedPngBase64].every((value) => value === null || pngBytes(value) <= 4 * 1024 * 1024)) {
        return { pagePngBase64, annotatedPngBase64, width, height };
      }
      // Re-render both from the already loaded source, preserving their alignment.
      edge = Math.max(1, Math.floor(Math.max(width, height) * 0.75));
    }
    throw new Error("The document visual exceeds the 4 MiB PNG limit.");
  } finally {
    if (imageUrl !== null) URL.revokeObjectURL(imageUrl);
    canvas.width = 0;
    canvas.height = 0;
    if (pdf !== null) await pdf.destroy();
  }
}

function pngBase64(canvas: HTMLCanvasElement): string {
  const prefix = "data:image/png;base64,";
  const dataUrl = canvas.toDataURL("image/png");
  if (!dataUrl.startsWith(prefix)) throw new Error("The document page could not be encoded as PNG.");
  return dataUrl.slice(prefix.length);
}

function pngBytes(base64: string): number {
  return base64.length * 3 / 4 - (base64.endsWith("==") ? 2 : base64.endsWith("=") ? 1 : 0);
}

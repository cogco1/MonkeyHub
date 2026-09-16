/**
 * Annotation ink is screen-space evidence tied to one recorded camera. While
 * the annotation palette is open, navigation is therefore locked by default:
 * orbiting, panning or zooming would move the model out from under the ink.
 *
 * Space is a hold-to-inspect escape hatch. Annotate already treats Space as a
 * temporary orbit gesture when an ink tool is armed; this guard also covers
 * the palette-open / no-tool-yet state and stops the Stage shortcut from
 * turning Space into Select. Releasing Space immediately restores the lock.
 *
 * This policy lives at the event boundary rather than in saved annotations:
 * persistence is unchanged, and the recorded camera remains the source of
 * truth for every stroke.
 */

const ANNOTATION_PANEL = "#annotation-tools";
const VIEWPORT_CANVAS = ".viewport-canvas";
const ANNOTATION_CANVAS = ".annotate";

let installed = false;
let spaceHeld = false;

function activePanel(): HTMLElement | null {
  const panel = document.querySelector<HTMLElement>(ANNOTATION_PANEL);
  if (!panel || panel.closest("[inert], [aria-hidden='true']")) return null;
  return panel;
}

function writingTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement &&
    (target.isContentEditable || target.closest("input, textarea, select, [contenteditable]") !== null);
}

function inside(target: EventTarget | null, selector: string): boolean {
  return target instanceof Element && (target.matches(selector) || target.closest(selector) !== null);
}

function lockAttribute(locked: boolean) {
  const stage = activePanel()?.closest<HTMLElement>(".stage");
  if (!stage) return;
  if (locked) stage.dataset.annotationViewLocked = "true";
  else delete stage.dataset.annotationViewLocked;
}

function releaseTemporaryView() {
  spaceHeld = false;
  lockAttribute(activePanel() !== null);
}

/** Install once for the lifetime of the Studio page. */
export function installAnnotationViewLock(): void {
  if (installed) return;
  installed = true;

  window.addEventListener("keydown", (event) => {
    if (event.code !== "Space" || event.repeat || writingTarget(event.target) || !activePanel()) return;
    // Stage's global Space shortcut checks defaultPrevented before selecting.
    // Do not stop propagation: Annotate's own listener still needs to switch
    // its overlay to temporary orbit so the WebGL canvas receives the pointer.
    event.preventDefault();
    spaceHeld = true;
    lockAttribute(false);
  }, true);

  window.addEventListener("keyup", (event) => {
    if (event.code !== "Space") return;
    releaseTemporaryView();
  }, true);
  window.addEventListener("blur", releaseTemporaryView, true);

  window.addEventListener("pointerdown", (event) => {
    if (!activePanel() || spaceHeld) return;
    // With no ink tool armed the WebGL canvas is exposed, so stop every
    // navigation press there. Over the annotation canvas, left-click remains
    // the drawing/eraser gesture; only middle/right navigation is refused.
    const navigatingViewport = inside(event.target, VIEWPORT_CANVAS);
    const navigatingOverInk = inside(event.target, ANNOTATION_CANVAS) && event.button !== 0;
    if (!navigatingViewport && !navigatingOverInk) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    lockAttribute(true);
  }, true);

  window.addEventListener("wheel", (event) => {
    if (!activePanel() || spaceHeld ||
      (!inside(event.target, VIEWPORT_CANVAS) && !inside(event.target, ANNOTATION_CANVAS))) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    lockAttribute(true);
  }, { capture: true, passive: false });

  window.addEventListener("contextmenu", (event) => {
    if (!activePanel() || spaceHeld ||
      (!inside(event.target, VIEWPORT_CANVAS) && !inside(event.target, ANNOTATION_CANVAS))) return;
    event.preventDefault();
  }, true);

  // Keep a cheap DOM observer only for the lock marker. Navigation itself is
  // guarded synchronously by the event handlers above, so a React render can
  // never leave a one-frame hole in the lock.
  const observer = new MutationObserver(() => lockAttribute(activePanel() !== null && !spaceHeld));
  observer.observe(document.body, { childList: true, subtree: true });
}

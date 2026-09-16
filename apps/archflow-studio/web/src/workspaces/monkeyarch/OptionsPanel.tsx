import type { ReactNode } from "react";

import type {
  MassingOptionDto,
  MassingOptionRequestDto,
  OptionsDto,
  VolumeDto,
  VolumesDto,
} from "../../api/generated";
import type { Loadable } from "../../app/loadable";

/**
 * Legacy Studio massing-panel helpers are kept because deterministic massing
 * algorithms remain useful to runtime/search consumers. The dedicated human
 * product surface is retired by #138; MonkeyArch no longer asks the architect
 * to drive a fixed add-floor/shift/scale/split mini-workflow.
 */

/** The cut a split operation asks for: the first cell of the far half. */
export function midCell(volume: VolumeDto, axis: 0 | 2): number {
  const low = Math.ceil(volume.min[axis]);
  const high = Math.floor(volume.max[axis]);
  return Math.min(Math.max(low + 1, Math.round((low + high + 1) / 2)), high);
}

/** Whether a volume is wide enough on this axis to be cut in two at all. */
export function splittable(volume: VolumeDto, axis: 0 | 2): boolean {
  return Math.floor(volume.max[axis]) - Math.ceil(volume.min[axis]) >= 1;
}

/**
 * Retained only as a compatibility boundary while App/Project Runtime cleanup
 * removes the old panel props. It intentionally renders nothing. Programmatic
 * massing generation/evaluation continues through the existing runtime APIs.
 */
export function OptionsPanel(_props: {
  table: Loadable<OptionsDto>;
  volumes: Loadable<VolumesDto>;
  stateDigest: string | null;
  busy: boolean;
  canSelect?(option: MassingOptionDto): boolean;
  readOnlyReason?: ReactNode;
  onMake(body: MassingOptionRequestDto): void;
  onSelect(optionId: string): void;
  onClose(): void;
}) {
  return null;
}

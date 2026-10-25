/**
 * How this shell shortens the server's values for display, in one place.
 *
 * A digest is abbreviated for reading and never for comparing: every request
 * carries the full value the server sent, and nothing here is ever parsed back.
 */

/** The first eight characters of a digest, or an em dash when there is none. */
export function sha8(value: string | null | undefined): string {
  return value ? value.slice(0, 8) : "—";
}

/**
 * The parts an imported model's objects are read as (``model_parts.PARTS``), by the word a made design's ids
 * carry: ``imported-<part>`` is that part's component, ``imported-<part>-<n>`` its n-th object (#300 item 22 B).
 */
const IMPORTED_PARTS: Readonly<Record<string, readonly [string, string]>> = {
  floor: ["楼板", "Floor"], cabinet: ["柜子", "Cabinets"], "private-room": ["个人房间", "Private room"],
  shared: ["共享空间", "Shared space"], "micro-public": ["微公共", "Micro-public"], roof: ["屋顶", "Roof"],
  wall: ["墙", "Wall"], stair: ["楼梯", "Stair"], guard: ["栏杆", "Railing"], glazing: ["玻璃窗", "Glazing"],
  door: ["门", "Door"], structure: ["结构", "Structure"], building: ["建筑体块", "Building mass"], other: ["其它", "Other"],
};

/** Read an existing semantic id as a compact object label; it is never sent back. */
export function designObjectLabel(value: string | null | undefined, zh = false): string | null {
  if (!value) return null;
  const imported = /^imported-(.+?)(?:-(\d+(?:-\d+)*))?$/.exec(value);
  const part = imported ? IMPORTED_PARTS[imported[1]!] : undefined;
  if (part) return [part[zh ? 0 : 1], ...(imported![2] ? [imported![2].replace(/-/g, " ")] : [])].join(" ");
  return value.split(/[-_\s]+/).filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

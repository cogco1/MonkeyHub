// What an element is, from the words its id is made of, for a person reading the reference list (S15): an id
// such as "wallbase.wall.head-joint-c2-1-body" is the model's own name and means nothing to the architect.
const KINDS: readonly [RegExp, string, string][] = [
  [/roof/, "屋顶", "Roof"], [/stair/, "楼梯", "Stair"], [/window|glazing/, "窗", "Window"], [/door/, "门", "Door"],
  [/column|post/, "柱", "Column"], [/beam|joist|lintel/, "梁", "Beam"], [/slab|floor|deck|subfloor/, "楼板", "Floor"],
  [/wall|stud|sill|plate/, "墙", "Wall"], [/footing|foundation|plinth/, "基础", "Footing"],
];
export function elementWords(elementId: string, zh: boolean): string {
  const id = elementId.toLowerCase();
  const kind = KINDS.find(([pattern]) => pattern.test(id));
  return kind ? (zh ? kind[1] : kind[2]) : (zh ? "构件" : "Element");
}

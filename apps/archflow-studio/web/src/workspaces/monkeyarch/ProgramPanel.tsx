import type {
  ProgramDto,
  ProgramSheetDto,
  ProgramSpaceDto,
  SemanticsDto,
} from "../../api/generated";
import type { Loadable } from "../../app/loadable";

/** One edit of one structured Program space. */
export type SpaceEdit = {
  departmentId: string;
  spaceId: string;
  field: "name" | "function" | "targetAreaM2" | "count" | "clearHeightM";
  value: string;
};

/**
 * Keep the structured Program correction rule independent of the retired
 * manual-intake panel. Program remains a project/runtime representation that
 * Agent, evaluator and future review projections may consume.
 */
export function edited(sheet: ProgramSheetDto, edit: SpaceEdit): ProgramSheetDto {
  return {
    ...sheet,
    departments: sheet.departments.map((department) =>
      department.departmentId !== edit.departmentId
        ? department
        : {
            ...department,
            spaces: department.spaces.map((space) =>
              space.spaceId !== edit.spaceId ? space : withField(space, edit),
            ),
          },
    ),
  };
}

function withField(space: ProgramSpaceDto, edit: SpaceEdit): ProgramSpaceDto {
  const text = edit.value.trim();
  switch (edit.field) {
    case "name":
      return { ...space, name: edit.value };
    case "function":
      return { ...space, function: text === "" ? null : text };
    case "count": {
      const count = Number.parseInt(text, 10);
      return {
        ...space,
        count: Number.isFinite(count) && count >= 1 ? count : space.count,
      };
    }
    default: {
      const value = Number.parseFloat(text);
      return {
        ...space,
        [edit.field]:
          text === "" || !Number.isFinite(value) || value < 0 ? null : value,
      };
    }
  }
}

/**
 * Compatibility boundary for App.tsx while the legacy Studio shell is being
 * folded into MonkeyHub (#127). The duplicate specialist Program form is no
 * longer a product surface; briefs enter through Board/Chat and structured
 * Program facts remain available underneath for inspection and evaluation.
 */
export function ProgramPanel(_props: {
  program: Loadable<ProgramDto>;
  semantics: Loadable<SemanticsDto>;
  sheet: ProgramSheetDto | null;
  applying: boolean;
  canApply: boolean;
  canSave: boolean;
  onEdit(edit: SpaceEdit): void;
  onApply(sheet: ProgramSheetDto, saveInput: boolean): void;
  onReread(): void;
  onClose(): void;
}) {
  return null;
}

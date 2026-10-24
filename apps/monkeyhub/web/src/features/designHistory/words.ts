/** The history page's copy in both languages. */
import type { Language } from "../settings/preferences";

export interface HistoryWords {
  readonly backToDefault: string;
  readonly backToDefaultHint: string;
  readonly wholeTree: string;
  readonly goTo: string;
  readonly name: string;
  readonly nameLabel: string;
  readonly save: string;
  readonly cancel: string;
  readonly current: string;
  readonly unnamed: string;
  readonly stage: string;
  readonly you: string;
  readonly agent: string;
  readonly loading: string;
  readonly empty: string;
  readonly unreadable: string;
  readonly movedTo: (name: string) => string;
  readonly undo: string;
  readonly undone: string;
  readonly undoMoved: string;
  readonly unsynced: string;
  readonly failed: string;
}

export const HISTORY_WORDS: Record<Language, HistoryWords> = {
  "zh-CN": {
    backToDefault: "回到当前方案",
    backToDefaultHint: "回到这条线上已采用的阶段",
    wholeTree: "看全图",
    goTo: "回到这一步",
    name: "起名",
    nameLabel: "这一步的名字",
    save: "保存",
    cancel: "取消",
    current: "当前",
    unnamed: "未命名的一步",
    stage: "已采用为阶段",
    you: "你",
    agent: "AI",
    loading: "正在读取修改历史…",
    empty: "还没有修改记录。",
    unreadable: "更早的记录有一部分读不出来。",
    movedTo: (name) => `已回到「${name}」`,
    undo: "撤销",
    undone: "已撤销，回到原来那一版。",
    undoMoved: "当前方案已经又换过了，这次没有撤销。",
    unsynced: "建模里还有没记录的修改，先记录或撤销它们，再切换方案。",
    failed: "没有切换成功，请再试一次。",
  },
  en: {
    backToDefault: "Back to the current design",
    backToDefaultHint: "Back to the Stage this line accepted",
    wholeTree: "Whole tree",
    goTo: "Go back to this step",
    name: "Name",
    nameLabel: "Name of this step",
    save: "Save",
    cancel: "Cancel",
    current: "Current",
    unnamed: "Unnamed step",
    stage: "Accepted as a Stage",
    you: "You",
    agent: "AI",
    loading: "Reading the history…",
    empty: "No changes yet.",
    unreadable: "Part of the earlier history could not be read.",
    movedTo: (name) => `Back to “${name}”`,
    undo: "Undo",
    undone: "Undone: back to the version before.",
    undoMoved: "The design has changed again since, so nothing was undone.",
    unsynced: "Modeling holds unrecorded edits; record or undo them before switching.",
    failed: "The switch did not go through. Try again.",
  },
};

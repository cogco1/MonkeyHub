/** Caller-approved English prose only. Failed preparation/translation retains the source. */
export function prepareEnglishToChinese(): Promise<boolean>;
export function translateEnglishToChinese(source: string): Promise<string | null>;
export function subscribeToEnglishToChineseReadiness(listener: () => void): () => void;

export type MessageParameters = Readonly<Record<string, string | number>>;
export function translateMessage<Key extends string>(catalog: Readonly<Record<Key, string>>, key: Key, parameters?: MessageParameters): string;

import { useCallback } from "react";

import { usePreferences } from "../features/settings/preferences";
import { messagesEn, type MessageKey } from "./messages.en";
import { messagesZhCN } from "./messages.zh-CN";

export type MessageParameters = Readonly<Record<string, string | number>>;
export type TFunction = (
  key: MessageKey,
  parameters?: MessageParameters,
) => string;

const catalogs = {
  en: messagesEn,
  "zh-CN": messagesZhCN,
} as const;

function interpolate(message: string, parameters?: MessageParameters): string {
  if (parameters === undefined) return message;
  return message.replace(/\{([A-Za-z][\w.-]*)\}/g, (placeholder, name: string) =>
    Object.prototype.hasOwnProperty.call(parameters, name)
      ? String(parameters[name])
      : placeholder,
  );
}

export function useT(): TFunction {
  const { language } = usePreferences();

  return useCallback(
    (key, parameters) => interpolate(catalogs[language][key], parameters),
    [language],
  );
}

import { useCallback } from "react";

import { usePreferences } from "../features/settings/preferences";
import { messagesEn, type MessageKey } from "./messages.en";
import { messagesZhCN } from "./messages.zh-CN";
import { translateMessage } from "../../../../shared-web/src/i18n.js";

export type MessageParameters = Readonly<Record<string, string | number>>;
export type TFunction = (
  key: MessageKey,
  parameters?: MessageParameters,
) => string;

const catalogs = {
  en: messagesEn,
  "zh-CN": messagesZhCN,
} as const;

const PRODUCT_TERMS: Partial<Record<MessageKey, string>> = {
  // Camera-registered review layer: conceptually a sheet of tracing paper over
  // the current model view, rather than a generic annotation mode.
  "stage.tools.annotate": "Tracing Paper",
};

export function useT(): TFunction {
  const { language } = usePreferences();

  return useCallback(
    (key, parameters) => PRODUCT_TERMS[key] ?? translateMessage(catalogs[language], key, parameters),
    [language],
  );
}

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

// Product term, not a generic annotation mode: this layer is registered to
// one camera view like a sheet of tracing paper laid over the model.
const catalogs = {
  en: { ...messagesEn, "stage.tools.annotate": "Tracing Paper" },
  "zh-CN": { ...messagesZhCN, "stage.tools.annotate": "Tracing Paper" },
} as const;

export function useT(): TFunction {
  const { language } = usePreferences();

  return useCallback(
    (key, parameters) => translateMessage(catalogs[language], key, parameters),
    [language],
  );
}

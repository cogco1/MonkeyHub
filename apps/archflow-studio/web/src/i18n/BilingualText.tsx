import { useEffect, useState } from "react";

import { usePreferences } from "../features/settings/preferences";
import {
  subscribeToEnglishToChineseReadiness,
  translateEnglishToChinese,
} from "./browserTranslator";
import "./BilingualText.css";

type TranslationStatus = "idle" | "loading" | "ready" | "failed";

interface TranslationAttempt {
  source: string;
  translated: string | null;
  status: TranslationStatus;
}

export interface BilingualTextProps {
  /** Caller-approved English prose. Do not pass code, hashes, ids, or grammar. */
  source: string;
  className?: string;
  /** Adds a small control for temporarily revealing the retained English. */
  showSourceToggle?: boolean;
}

export function BilingualText({
  source,
  className,
  showSourceToggle = false,
}: BilingualTextProps) {
  const { language } = usePreferences();
  const [attempt, setAttempt] = useState<TranslationAttempt>(() => ({
    source,
    translated: null,
    status: "idle",
  }));
  const [showSource, setShowSource] = useState(false);
  const [translatorRevision, setTranslatorRevision] = useState(0);

  useEffect(
    () =>
      subscribeToEnglishToChineseReadiness(() => {
        setTranslatorRevision((revision) => revision + 1);
      }),
    [],
  );

  useEffect(() => {
    let cancelled = false;
    setShowSource(false);

    if (language !== "zh-CN") return () => {
      cancelled = true;
    };

    setAttempt((current) =>
      current.source === source && current.status === "ready"
        ? current
        : { source, translated: null, status: "loading" },
    );

    void translateEnglishToChinese(source).then((translated) => {
      if (cancelled) return;
      setAttempt({
        source,
        translated,
        status: translated === null ? "failed" : "ready",
      });
    });

    return () => {
      cancelled = true;
    };
  }, [language, source, translatorRevision]);

  const currentAttempt = attempt.source === source ? attempt : null;
  const hasTranslation =
    language === "zh-CN" &&
    currentAttempt?.status === "ready" &&
    currentAttempt.translated !== null;
  const sourceIsActive = language === "en" || !hasTranslation || showSource;
  const translationIsActive = hasTranslation && !showSource;

  let status: string | null = null;
  if (translationIsActive === false && hasTranslation) {
    status = "正在显示英文原文";
  }

  const rootClassName = className
    ? `bilingual-text ${className}`
    : "bilingual-text";

  return (
    <span className={rootClassName}>
      <span className="bilingual-text__stack">
        <span
          className={`bilingual-text__layer ${
            sourceIsActive
              ? "bilingual-text__layer--active"
              : "bilingual-text__layer--inactive"
          }`}
          lang="en"
          aria-hidden={!sourceIsActive}
          inert={!sourceIsActive}
        >
          {source}
        </span>
        <span
          className={`bilingual-text__layer ${
            translationIsActive
              ? "bilingual-text__layer--active"
              : "bilingual-text__layer--inactive"
          }`}
          lang="zh-CN"
          aria-hidden={!translationIsActive}
          inert={!translationIsActive}
        >
          {currentAttempt?.translated ?? ""}
        </span>
      </span>

      {status !== null && (
        <span
          className="bilingual-text__status"
          lang="zh-CN"
        >
          {status}
        </span>
      )}

      {showSourceToggle && hasTranslation && (
        <button
          className="bilingual-text__toggle"
          type="button"
          lang="zh-CN"
          aria-pressed={showSource}
          onClick={() => setShowSource((visible) => !visible)}
        >
          {showSource ? "显示中文" : "查看原文"}
        </button>
      )}
    </span>
  );
}

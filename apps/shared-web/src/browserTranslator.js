const ENGLISH_TO_CHINESE = {
  sourceLanguage: "en",
  targetLanguage: "zh",
};

const translationCache = new Map();
const readinessListeners = new Set();

const TECHNICAL_TOKEN =
  /`[^`]+`|"[^"]*"|'[^']*'|https?:\/\/[^\s]+|[A-Za-z]:\\[^\r\n,;]+|(?:GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+\/[^\s,;]+|\/api\/[^\s,;]+|\b[a-f0-9]{12,}\b|\b[a-f0-9]{8}-[a-f0-9-]{27,}\b|\barchflow\/\d+\b|\b[A-Z][A-Z0-9_]{2,}\b|\b[A-Za-z][A-Za-z0-9_-]*@\d+\b|\b[A-Z][a-z]+(?:[A-Z][A-Za-z0-9]*)+\b|\b[a-z][A-Za-z0-9]*(?:Id|ID|Sha|SHA|Digest|Version|Ref)\b|\b[A-Za-z][A-Za-z0-9._-]*=[A-Za-z0-9._:/-]+\b|\b\d+(?:\.\d+)?\s?(?:mm|cm|m|km|ft|in|ms|s|%)\b|\b[A-Za-z][A-Za-z0-9.]*[-_:][A-Za-z0-9._:-]+\b/gi;

/** Keep protocol facts byte-for-byte even when prose around them is translated. */
function protectTechnicalTokens(source) {
  const tokens = [];
  const masked = source.replace(TECHNICAL_TOKEN, (token) => {
    const index = tokens.push(token) - 1;
    return `__MONKEYARCH_TOKEN_${index}__`;
  });

  return {
    masked,
    restore(translated) {
      let restored = translated;
      for (let index = 0; index < tokens.length; index += 1) {
        const placeholder = `__MONKEYARCH_TOKEN_${index}__`;
        if (restored.split(placeholder).length !== 2) return null;
        restored = restored.replace(placeholder, tokens[index]);
      }
      return restored;
    },
  };
}

let translator = null;
let translatorPreparation = null;
let automaticPreparationFailed = false;
let translationQueue = Promise.resolve();

function installTranslator(created) {
  const becameReady = translator === null;
  translator = created;
  automaticPreparationFailed = false;
  if (becameReady) {
    readinessListeners.forEach((listener) => listener());
  }
}

/** Re-run retained bilingual layers when an explicit pack preparation succeeds. */
export function subscribeToEnglishToChineseReadiness(
  listener,
) {
  readinessListeners.add(listener);
  return () => {
    readinessListeners.delete(listener);
  };
}

function translatorFactory() {
  const candidate = globalThis.Translator;
  if (
    candidate === null ||
    candidate === undefined ||
    (typeof candidate !== "function" && typeof candidate !== "object")
  ) {
    return null;
  }

  const factory = candidate;
  if (typeof factory.create !== "function") {
    return null;
  }
  return factory;
}

async function createEnglishToChineseTranslator() {
  const factory = translatorFactory();
  if (factory === null) return null;

  try {
    // Invoke create before the first await. Chrome may require transient user
    // activation when this call starts a language-pack download.
    return await factory.create(ENGLISH_TO_CHINESE);
  } catch {
    return null;
  }
}

function englishToChineseTranslator() {
  if (translator !== null) return Promise.resolve(translator);
  if (automaticPreparationFailed) return Promise.resolve(null);
  if (translatorPreparation !== null) return translatorPreparation;

  translatorPreparation = createEnglishToChineseTranslator()
    .then((created) => {
      if (created !== null) {
        installTranslator(created);
      } else if (translator === null) {
        automaticPreparationFailed = true;
      }
      return created;
    })
    .finally(() => {
      translatorPreparation = null;
    });

  return translatorPreparation;
}

function enqueue(work) {
  const result = translationQueue.then(work, work);
  translationQueue = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

/**
 * Starts feature detection and any browser-managed language-pack preparation.
 * Call this directly from the user's language-selection event so Chrome can
 * use that transient activation when a pack still needs downloading.
 * Unsupported browsers and preparation failures resolve to false.
 */
export async function prepareEnglishToChinese() {
  try {
    if (translator !== null) return true;

    // Always make an explicit attempt here, even if an automatic attempt is
    // pending or previously failed: this function is called from a user event
    // and therefore carries the activation a first download may require.
    const created = await createEnglishToChineseTranslator();
    if (created === null) {
      if (translator === null) automaticPreparationFailed = true;
      return translator !== null;
    }
    installTranslator(created);
    return true;
  } catch {
    return false;
  }
}

/**
 * Translates caller-approved prose only. Callers deliberately decide which
 * strings are safe to pass; identifiers, hashes, code, and accepted grammar
 * must remain outside this function.
 */
export function translateEnglishToChinese(source) {
  if (source.length === 0) return Promise.resolve("");

  const cached = translationCache.get(source);
  if (cached !== undefined) return Promise.resolve(cached);

  return enqueue(async () => {
    const cachedAfterWait = translationCache.get(source);
    if (cachedAfterWait !== undefined) return cachedAfterWait;

    try {
      const activeTranslator = await englishToChineseTranslator();
      if (activeTranslator === null) return null;

      const protectedSource = protectTechnicalTokens(source);
      const translated = await activeTranslator.translate(protectedSource.masked);
      if (typeof translated !== "string") return null;

      const restored = protectedSource.restore(translated);
      if (restored === null) return null;

      translationCache.set(source, restored);
      return restored;
    } catch {
      return null;
    }
  });
}

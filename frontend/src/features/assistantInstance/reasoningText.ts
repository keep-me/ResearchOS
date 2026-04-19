const ATTACH_RIGHT_PUNCT = new Set(["(", "[", "{", "/", "\\"]);
const ATTACH_LEFT_PUNCT = new Set([",", ".", "!", "?", ";", ":", "%", ")", "]", "}", "/", "\\"]);
const WORD_GAP_AFTER_PUNCT = new Set([",", ".", ";", ":", "!", "?"]);

function isCjk(char: string): boolean {
  if (!char) return false;
  const code = char.codePointAt(0) ?? 0;
  return (
    (code >= 0x4e00 && code <= 0x9fff)
    || (code >= 0x3400 && code <= 0x4dbf)
    || (code >= 0x3040 && code <= 0x30ff)
    || (code >= 0xac00 && code <= 0xd7af)
  );
}

function shouldInsertAsciiWordGap(previous: string, current: string): boolean {
  if (!previous || !current) return false;
  const last = previous.slice(-1);
  const first = current[0];
  if (!last || !first) return false;
  if (/\s/.test(last) || /\s/.test(first)) return false;
  if (WORD_GAP_AFTER_PUNCT.has(last) && /^[\x00-\x7F]$/.test(first) && /[A-Za-z0-9]/.test(first)) return true;
  if (ATTACH_RIGHT_PUNCT.has(last) || ATTACH_LEFT_PUNCT.has(first)) return false;
  if (isCjk(last) || isCjk(first)) return false;
  if (!/^[\x00-\x7F]$/.test(last) || !/^[\x00-\x7F]$/.test(first)) return false;
  if (!/[A-Za-z0-9]/.test(last) || !/[A-Za-z0-9]/.test(first)) return false;
  const previousCompact = previous.trim();
  const currentCompact = current.trim();
  if (!previousCompact || !currentCompact) return false;
  if (previousCompact.endsWith("-") || previousCompact.endsWith("_") || previousCompact.endsWith("/")) return false;
  if (currentCompact.startsWith("-") || currentCompact.startsWith("_") || currentCompact.startsWith("/")) return false;
  return true;
}

export function appendReasoningChunk(current: string, next: string): string {
  if (!next) return current;
  if (!current) return next;
  return `${current}${shouldInsertAsciiWordGap(current, next) ? " " : ""}${next}`;
}

export function normalizeReasoningDisplay(value: string): string {
  return String(value || "")
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.trimEnd())
    .join("\n")
    .trim();
}

export function shouldHideRawReasoning(value: string): boolean {
  void value;
  return false;
}

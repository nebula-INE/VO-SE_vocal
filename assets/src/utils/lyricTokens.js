const SMALL_KANA = new Set(
  Array.from("ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮー"),
);

/**
 * Split Japanese text into mora-like tokens, preserving small kana and ー.
 * Keep behavior in sync with modules/utils/lyric_tokens.py using
 * specs/lyric_tokenization_cases.json as the cross-platform contract.
 */
export function segmentJapaneseCharacters(text) {
  const cleaned = String(text ?? "").replace(/[\r\n\t]/g, "").trim();
  const result = [];
  let index = 0;

  while (index < cleaned.length) {
    let token = cleaned[index];
    if (/\s/u.test(token)) {
      index += 1;
      continue;
    }
    index += 1;
    if (index < cleaned.length && SMALL_KANA.has(cleaned[index])) {
      token += cleaned[index];
      index += 1;
    }
    result.push(token);
  }
  return result;
}

/**
 * Split lyrics using auto, char, space, or line mode.
 * "auto" splits whitespace-delimited input, otherwise it segments morae.
 */
export function parseLyricTokens(rawText, delimiterMode = "auto") {
  const text = String(rawText ?? "");
  if (!["auto", "char", "space", "line"].includes(delimiterMode)) {
    throw new RangeError(`Unsupported lyric delimiter mode: ${delimiterMode}`);
  }
  if (!text.trim()) return [];

  if (delimiterMode === "space") {
    return text.trim().split(/\s+/u).filter(Boolean);
  }
  if (delimiterMode === "line") {
    return text.split(/\r?\n/u).map((line) => line.trim()).filter(Boolean);
  }
  if (delimiterMode === "char") {
    return segmentJapaneseCharacters(text);
  }
  if (/\s/u.test(text.trim())) {
    return text.trim().split(/\s+/u).filter(Boolean);
  }
  return segmentJapaneseCharacters(text);
}

export type LyricDelimiterMode = "auto" | "char" | "space" | "line";

export function segmentJapaneseCharacters(text: string): string[];
export function parseLyricTokens(
  rawText: string,
  delimiterMode?: LyricDelimiterMode,
): string[];

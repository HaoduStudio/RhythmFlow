import type { Language } from "./types";
import enSubtitles from "./subtitles.en.txt?raw";
import zhSubtitles from "./subtitles.zh.txt?raw";

function parseLines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

const SUBTITLE_LINES: Record<Language, string[]> = {
  en: parseLines(enSubtitles),
  zh: parseLines(zhSubtitles),
};

export function pickSubtitle(language: Language): string {
  const lines = SUBTITLE_LINES[language] ?? SUBTITLE_LINES.zh;
  return lines[Math.floor(Math.random() * lines.length)] ?? "";
}

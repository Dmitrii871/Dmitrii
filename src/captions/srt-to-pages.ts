import { parseSrt } from "@remotion/captions";
import type { Caption } from "@remotion/captions";

export type CaptionToken = {
  text: string;
  fromMs: number;
  toMs: number;
};

export type CaptionPage = {
  tokens: CaptionToken[];
  startMs: number;
  endMs: number;
};

// Если между двумя соседними страницами субтитров пауза короче этого
// значения, предыдущая страница висит до начала следующей — иначе текст
// мигает на каждой запятой.
const HOLD_GAP_MS = 400;

// Приводим srt к виду, который переваривает parseSrt():
// убираем BOM, переводим CRLF в LF и подставляем номера блоков, если
// экспортёр их не проставил (так делают некоторые редакторы субтитров).
const normalizeSrt = (input: string): string => {
  const lines = input
    .replace(/^\uFEFF/, "")
    .replace(/\r\n?/g, "\n")
    .trimStart()
    .split("\n");

  const output: string[] = [];
  let cueIndex = 0;

  for (const line of lines) {
    if (line.includes(" --> ")) {
      const previous = output[output.length - 1] ?? "";

      if (!/^\d+$/.test(previous.trim())) {
        if (previous.trim() !== "") {
          output.push("");
        }

        output.push(String(cueIndex + 1));
      }

      cueIndex++;
    }

    output.push(line);
  }

  return `${output.join("\n")}\n`;
};

// В srt время проставлено на всю реплику, а подсветка нужна по словам.
// Раздаём каждому слову долю реплики пропорционально его длине —
// без транскрипции точнее не получится, а на слух совпадает хорошо.
const splitIntoWords = (caption: Caption): CaptionToken[] => {
  const words = caption.text.split(/\s+/).filter((word) => word !== "");

  if (words.length === 0) {
    return [];
  }

  const totalLength = words.reduce((sum, word) => sum + word.length, 0);
  const durationMs = Math.max(0, caption.endMs - caption.startMs);

  let cursorMs = caption.startMs;

  return words.map((word, index) => {
    const isLast = index === words.length - 1;
    const fromMs = cursorMs;
    cursorMs = isLast ? caption.endMs : cursorMs + (word.length / totalLength) * durationMs;

    return { text: word, fromMs, toMs: cursorMs };
  });
};

// Режем реплику на страницы по 2-3 слова, распределяя слова равномерно:
// 4 слова превращаются в 2+2, 5 — в 3+2, 7 — в 3+2+2. Так не остаётся
// висящих страниц из одного слова.
const chunkEvenly = (tokens: CaptionToken[], maxWordsPerPage: number): CaptionToken[][] => {
  if (tokens.length === 0) {
    return [];
  }

  const pageCount = Math.ceil(tokens.length / maxWordsPerPage);
  const baseSize = Math.floor(tokens.length / pageCount);
  const remainder = tokens.length % pageCount;

  const chunks: CaptionToken[][] = [];
  let offset = 0;

  for (let page = 0; page < pageCount; page++) {
    const size = baseSize + (page < remainder ? 1 : 0);
    chunks.push(tokens.slice(offset, offset + size));
    offset += size;
  }

  return chunks;
};

export const parseSrtToPages = ({
  input,
  maxWordsPerPage,
}: {
  input: string;
  maxWordsPerPage: number;
}): CaptionPage[] => {
  const normalized = normalizeSrt(input);

  if (!normalized.includes(" --> ")) {
    return [];
  }

  const { captions } = parseSrt({ input: normalized });

  const pages: CaptionPage[] = captions
    .flatMap((caption) => chunkEvenly(splitIntoWords(caption), maxWordsPerPage))
    .filter((tokens) => tokens.length > 0)
    .map((tokens) => ({
      tokens,
      startMs: tokens[0].fromMs,
      endMs: tokens[tokens.length - 1].toMs,
    }));

  return pages.map((page, index) => {
    const nextPage = pages[index + 1];

    if (!nextPage) {
      return page;
    }

    return {
      ...page,
      endMs: Math.min(nextPage.startMs, page.endMs + HOLD_GAP_MS),
    };
  });
};

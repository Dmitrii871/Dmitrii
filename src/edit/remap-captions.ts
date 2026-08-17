import type { CaptionPage, CaptionToken } from "../captions/srt-to-pages";
import { originalMsToEditedFrame } from "./timeline";
import type { Timeline } from "./timeline";

// Субтитры размечены по исходнику. После вырезания пауз таймкоды съезжают,
// поэтому каждое слово переносим на новый таймлайн, а слова, попавшие
// в вырезанные куски, просто выбрасываем.
export const remapCaptionPages = (pages: CaptionPage[], timeline: Timeline): CaptionPage[] => {
  const toMs = (frame: number) => (frame / timeline.fps) * 1000;
  const remapped: CaptionPage[] = [];

  for (const page of pages) {
    const tokens: CaptionToken[] = [];

    for (const token of page.tokens) {
      const middleMs = (token.fromMs + token.toMs) / 2;
      const middleFrame = originalMsToEditedFrame(timeline, middleMs);

      if (middleFrame === null) {
        continue;
      }

      const fromFrame = originalMsToEditedFrame(timeline, token.fromMs) ?? middleFrame;
      const toFrame = originalMsToEditedFrame(timeline, token.toMs) ?? middleFrame;

      tokens.push({
        text: token.text,
        fromMs: toMs(Math.min(fromFrame, middleFrame)),
        toMs: toMs(Math.max(toFrame, middleFrame)),
      });
    }

    if (tokens.length === 0) {
      continue;
    }

    const startMs = tokens[0].fromMs;
    const endMs = Math.max(tokens[tokens.length - 1].toMs, startMs + 200);

    remapped.push({ tokens, startMs, endMs });
  }

  return remapped;
};

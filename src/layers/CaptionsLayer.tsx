import { useMemo } from "react";
import { AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig } from "remotion";
import { useSrtCaptions } from "../captions/use-srt-captions";
import type { CaptionPage } from "../captions/srt-to-pages";
import { remapCaptionPages } from "../edit/remap-captions";
import type { Timeline } from "../edit/timeline";
import { fontFamily } from "../lib/font";

// Сколько слов показываем за раз.
const MAX_WORDS_PER_PAGE = 3;

// Одна страница субтитров: 2-3 слова, активное слово подсвечено акцентом.
const CaptionPageLayer: React.FC<{
  page: CaptionPage;
  accentColor: string;
}> = ({ page, accentColor }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Внутри Sequence кадр считается от её начала, поэтому возвращаем
  // абсолютное время, чтобы сравнить его с таймкодами слов из srt.
  const absoluteTimeMs = page.startMs + (frame / fps) * 1000;

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        paddingLeft: 90,
        paddingRight: 90,
        // Нижний отступ под интерфейс инстаграма: подпись, аватар,
        // кнопки справа и панель «Смотреть в Reels».
        paddingBottom: 430,
      }}
    >
      <div
        style={{
          fontFamily,
          fontSize: 68,
          fontWeight: 800,
          lineHeight: 1.15,
          textAlign: "center",
          whiteSpace: "pre-wrap",
          color: "#FFFFFF",
          textShadow: "0px 4px 20px rgba(0, 0, 0, 0.9)",
          backgroundColor: "rgba(0, 0, 0, 0.42)",
          borderRadius: 28,
          padding: "18px 32px",
        }}
      >
        {page.tokens.map((token, index) => {
          const isActive = token.fromMs <= absoluteTimeMs && token.toMs > absoluteTimeMs;

          return (
            <span
              key={`${token.fromMs}-${token.text}`}
              style={{
                color: isActive ? accentColor : "#FFFFFF",
              }}
            >
              {index === 0 ? token.text : ` ${token.text}`}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

export const CaptionsLayer: React.FC<{
  src: string;
  accentColor: string;
  timeline: Timeline;
}> = ({ src, accentColor, timeline }) => {
  const { fps } = useVideoConfig();
  const parsed = useSrtCaptions({ src, maxWordsPerPage: MAX_WORDS_PER_PAGE });

  // Субтитры размечены по исходнику, а ролик смонтирован — переносим
  // тайминги на новый таймлайн, иначе текст разъедется с речью.
  const pages = useMemo(() => {
    if (!parsed) {
      return null;
    }

    return remapCaptionPages(parsed, timeline);
  }, [parsed, timeline]);

  if (!pages) {
    return null;
  }

  return (
    <AbsoluteFill name="Субтитры">
      {pages.map((page) => {
        const from = Math.round((page.startMs / 1000) * fps);
        const durationInFrames = Math.max(1, Math.round((page.endMs / 1000) * fps) - from);

        return (
          <Sequence
            key={page.startMs}
            name={page.tokens.map((token) => token.text).join(" ")}
            from={from}
            durationInFrames={durationInFrames}
            layout="none"
          >
            <CaptionPageLayer page={page} accentColor={accentColor} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

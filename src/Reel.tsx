import { zColor } from "@remotion/zod-types";
import { AbsoluteFill } from "remotion";
import type { CalculateMetadataFunction } from "remotion";
import { z } from "zod";
import { CaptionsLayer } from "./layers/CaptionsLayer";
import { CtaLayer } from "./layers/CtaLayer";
import { TitleLayer } from "./layers/TitleLayer";
import { VideoLayer } from "./layers/VideoLayer";
import { getVideoDuration } from "./lib/get-video-duration";
import { resolveSrc } from "./lib/resolve-src";

export const reelSchema = z.object({
  // Путь к видео внутри public/, например "example/video.mp4".
  // Полный https-адрес тоже подойдёт.
  videoSrc: z.string(),
  // Заголовок первых двух секунд. Перенос строки — обычный \n.
  title: z.string(),
  // Путь к srt внутри public/, например "example/captions.srt".
  captionsSrc: z.string(),
  // Цвет подсветки активного слова и плашки CTA.
  accentColor: zColor(),
  // Текст финальной плашки. Если не задан, слой не рисуется.
  ctaText: z.string().optional(),
});

export type ReelProps = z.infer<typeof reelSchema>;

// Длительность композиции = длительность видео. Ничего не хардкодим:
// поменяли videoSrc — таймлайн пересчитался сам.
//
// Число 30 здесь должно совпадать с fps в <Composition> (см. Root.tsx).
export const calculateReelMetadata: CalculateMetadataFunction<ReelProps> = async ({ props }) => {
  const durationInSeconds = await getVideoDuration(resolveSrc(props.videoSrc));

  return {
    durationInFrames: Math.max(1, Math.round(durationInSeconds * 30)),
  };
};

export const Reel: React.FC<ReelProps> = ({ videoSrc, title, captionsSrc, accentColor, ctaText }) => {
  return (
    <AbsoluteFill name="Рилс" style={{ backgroundColor: "#000000" }}>
      <VideoLayer src={videoSrc} />
      <TitleLayer text={title} accentColor={accentColor} durationInSeconds={2} />
      <CaptionsLayer src={captionsSrc} accentColor={accentColor} />
      {ctaText ? <CtaLayer text={ctaText} accentColor={accentColor} durationInSeconds={2} /> : null}
    </AbsoluteFill>
  );
};

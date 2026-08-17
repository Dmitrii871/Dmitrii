import { zColor } from "@remotion/zod-types";
import { AbsoluteFill } from "remotion";
import type { CalculateMetadataFunction } from "remotion";
import { z } from "zod";
import { analyzeLoudness } from "./edit/analyze-audio";
import type { LoudnessEnvelope } from "./edit/analyze-audio";
import { buildShots } from "./edit/build-shots";
import { buildTimeline } from "./edit/timeline";
import type { Timeline } from "./edit/timeline";
import { CaptionsLayer } from "./layers/CaptionsLayer";
import { CtaLayer } from "./layers/CtaLayer";
import { EditedVideoLayer } from "./layers/EditedVideoLayer";
import { MusicLayer } from "./layers/MusicLayer";
import { TitleLayer } from "./layers/TitleLayer";
import { getVideoDuration } from "./lib/get-video-duration";
import { resolveSrc } from "./lib/resolve-src";

// Частота кадров композиции. Держим рядом с расчётом таймлайна,
// потому что calculateMetadata не получает fps из <Composition>.
const FPS = 30;

// Шаг разбора громкости. 40 мс достаточно, чтобы поймать паузу между
// фразами, и при этом не раздувает данные даже на трёхминутном ролике.
const LOUDNESS_WINDOW_MS = 40;

// Длительность перехода между планами.
const TRANSITION_FRAMES = 8;

export const reelSchema = z.object({
  // Путь к видео внутри public/, например "my-reel/video.mp4".
  // Полный https-адрес тоже подойдёт.
  videoSrc: z.string(),
  // Заголовок первых двух секунд. Перенос строки — обычный \n.
  title: z.string(),
  // Путь к srt внутри public/. Тайминги указываются по исходнику:
  // после нарезки они пересчитываются сами.
  captionsSrc: z.string(),
  // Цвет подсветки активного слова и плашки CTA.
  accentColor: zColor(),
  // Текст финальной плашки. Если не задан, слой не рисуется.
  ctaText: z.string().optional(),
  // Вырезать паузы, где человек молчит.
  autoCutSilence: z.boolean(),
  // Потолок длины готового ролика.
  maxDurationSeconds: z.number(),
  // Склейка планов: встык, затемнение или сдвиг.
  shotTransition: z.enum(["none", "fade", "slide"]),
  // Наезд внутри плана и разная крупность на соседних планах.
  dynamicZoom: z.boolean(),
  // Фоновая музыка, путь внутри public/. Без неё слой не рисуется.
  musicSrc: z.string().optional(),
  // Громкость музыки в паузах, 0..1. Под голосом она прижимается сама.
  musicVolume: z.number(),
});

// Помимо пропсов из схемы компонент получает результат разбора звука:
// его считает calculateMetadata один раз и кладёт сюда.
export type ReelProps = z.infer<typeof reelSchema> & {
  timeline?: Timeline;
  envelope?: LoudnessEnvelope;
};

const EMPTY_ENVELOPE: LoudnessEnvelope = { windowMs: LOUDNESS_WINDOW_MS, values: [] };

// Здесь собирается весь монтаж: длина ролика, границы планов и данные
// для приглушения музыки. Дальше слои только рисуют готовое решение.
export const calculateReelMetadata: CalculateMetadataFunction<ReelProps> = async ({ props }) => {
  const src = resolveSrc(props.videoSrc);
  const durationMs = (await getVideoDuration(src)) * 1000;
  const maxTotalMs = Math.max(1000, props.maxDurationSeconds * 1000);

  const needsAudio = props.autoCutSilence || Boolean(props.musicSrc);

  // Если дорожки нет или её не удалось разобрать — работаем дальше
  // без нарезки, а не роняем весь ролик.
  const envelope = needsAudio
    ? await analyzeLoudness({ src, windowMs: LOUDNESS_WINDOW_MS }).catch(() => EMPTY_ENVELOPE)
    : EMPTY_ENVELOPE;

  const shots = props.autoCutSilence
    ? buildShots({
        envelope,
        durationMs,
        silenceThreshold: 0.06,
        minSilenceMs: 400,
        paddingMs: 130,
        minShotMs: 350,
        maxTotalMs,
      })
    : [{ fromMs: 0, toMs: Math.min(durationMs, maxTotalMs) }];

  const timeline = buildTimeline({
    shots,
    fps: FPS,
    transitionFrames: props.shotTransition === "none" ? 0 : TRANSITION_FRAMES,
  });

  return {
    durationInFrames: timeline.durationInFrames,
    props: { ...props, timeline, envelope },
  };
};

export const Reel: React.FC<ReelProps> = ({
  videoSrc,
  title,
  captionsSrc,
  accentColor,
  ctaText,
  shotTransition,
  dynamicZoom,
  musicSrc,
  musicVolume,
  timeline,
  envelope,
}) => {
  // Страховка на случай, если компонент отрисовался раньше расчёта:
  // показываем ролик одним планом, без монтажа.
  const resolvedTimeline =
    timeline ?? buildTimeline({ shots: [{ fromMs: 0, toMs: 10_000 }], fps: FPS, transitionFrames: 0 });

  return (
    <AbsoluteFill name="Рилс" style={{ backgroundColor: "#000000" }}>
      <EditedVideoLayer
        src={videoSrc}
        timeline={resolvedTimeline}
        shotTransition={shotTransition}
        dynamicZoom={dynamicZoom}
      />
      {musicSrc ? (
        <MusicLayer
          src={musicSrc}
          volume={musicVolume}
          timeline={resolvedTimeline}
          envelope={envelope ?? EMPTY_ENVELOPE}
        />
      ) : null}
      <TitleLayer text={title} accentColor={accentColor} durationInSeconds={2} />
      <CaptionsLayer src={captionsSrc} accentColor={accentColor} timeline={resolvedTimeline} />
      {ctaText ? <CtaLayer text={ctaText} accentColor={accentColor} durationInSeconds={2} /> : null}
    </AbsoluteFill>
  );
};

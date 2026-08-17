import { Audio } from "@remotion/media";
import { zColor } from "@remotion/zod-types";
import { AbsoluteFill, getStaticFiles } from "remotion";
import type { CalculateMetadataFunction } from "remotion";
import { z } from "zod";
import { analyzeLoudness } from "./edit/analyze-audio";
import type { LoudnessEnvelope } from "./edit/analyze-audio";
import { buildPlaylist, isVideoPath, sortClipPaths } from "./edit/build-playlist";
import type { ClipShot } from "./edit/build-playlist";
import { buildTimeline } from "./edit/timeline";
import type { Timeline } from "./edit/timeline";
import { CaptionsLayer } from "./layers/CaptionsLayer";
import { CtaLayer } from "./layers/CtaLayer";
import { MusicLayer } from "./layers/MusicLayer";
import { ShotSeries } from "./layers/ShotSeries";
import { TitleLayer } from "./layers/TitleLayer";
import { getVideoDuration } from "./lib/get-video-duration";
import { resolveSrc } from "./lib/resolve-src";

const FPS = 30;
const LOUDNESS_WINDOW_MS = 40;
const TRANSITION_FRAMES = 8;

export const clipsReelSchema = z.object({
  // Папка внутри public/ с короткими клипами. Порядок — по имени файла,
  // так что нумерация 01-, 02-, 03- задаёт монтаж.
  clipsFolder: z.string(),
  // Явный список клипов, если нужен свой порядок или выборка.
  // Непустой список важнее папки.
  clips: z.array(z.string()),
  // Сколько секунд берём от каждого клипа.
  maxClipSeconds: z.number(),
  // Закадровый голос или любая основная дорожка. Если задан, ролик длится
  // ровно столько же: клипы подгоняются под звук.
  voiceSrc: z.string().optional(),
  // Фоновая музыка, прижимается под голос.
  musicSrc: z.string().optional(),
  musicVolume: z.number(),
  // Оставить собственный звук клипов. Обычно не нужен — сверху идёт озвучка.
  keepClipSound: z.boolean(),
  // Оформление — то же, что и у обычного рилса.
  title: z.string(),
  captionsSrc: z.string(),
  accentColor: zColor(),
  ctaText: z.string().optional(),
  shotTransition: z.enum(["none", "fade", "slide"]),
  dynamicZoom: z.boolean(),
  maxDurationSeconds: z.number(),
});

export type ClipsReelProps = z.infer<typeof clipsReelSchema> & {
  playlist?: ClipShot[];
  timeline?: Timeline;
  envelope?: LoudnessEnvelope;
};

const EMPTY_ENVELOPE: LoudnessEnvelope = { windowMs: LOUDNESS_WINDOW_MS, values: [] };

// Список клипов: либо явный, либо всё видео из указанной папки public/.
const collectClipPaths = ({ clipsFolder, clips }: { clipsFolder: string; clips: string[] }) => {
  if (clips.length > 0) {
    return clips;
  }

  const prefix = clipsFolder.replace(/^\/+|\/+$/g, "");

  const found = getStaticFiles()
    .map((file) => file.name)
    .filter((name) => name.startsWith(`${prefix}/`) && isVideoPath(name));

  return sortClipPaths(found);
};

export const calculateClipsReelMetadata: CalculateMetadataFunction<ClipsReelProps> = async ({
  props,
}) => {
  const paths = collectClipPaths({ clipsFolder: props.clipsFolder, clips: props.clips });

  const clips = await Promise.all(
    paths.map(async (src) => ({
      src,
      durationMs: (await getVideoDuration(resolveSrc(src))) * 1000,
    })),
  );

  const maxTotalMs = Math.max(1000, props.maxDurationSeconds * 1000);
  const maxClipMs = Math.max(500, props.maxClipSeconds * 1000);

  // Длину задаёт озвучка, если она есть: клипы под неё подгоняются.
  // Без озвучки берём всё, что есть, по одному разу.
  const clipsTotalMs = clips.reduce((sum, clip) => sum + Math.min(clip.durationMs, maxClipMs), 0);
  const voiceMs = props.voiceSrc
    ? (await getVideoDuration(resolveSrc(props.voiceSrc)).catch(() => 0)) * 1000
    : 0;

  const targetMs = Math.min(maxTotalMs, voiceMs > 0 ? voiceMs : clipsTotalMs);

  const playlist = buildPlaylist({ clips, maxClipMs, targetMs });

  const timeline = buildTimeline({
    shots: playlist,
    fps: FPS,
    transitionFrames: props.shotTransition === "none" ? 0 : TRANSITION_FRAMES,
  });

  // Приглушать музыку есть смысл только под голос.
  const envelope =
    props.voiceSrc && props.musicSrc
      ? await analyzeLoudness({
          src: resolveSrc(props.voiceSrc),
          windowMs: LOUDNESS_WINDOW_MS,
        }).catch(() => EMPTY_ENVELOPE)
      : EMPTY_ENVELOPE;

  return {
    durationInFrames: timeline.durationInFrames,
    props: { ...props, playlist, timeline, envelope },
  };
};

export const ClipsReel: React.FC<ClipsReelProps> = ({
  title,
  captionsSrc,
  accentColor,
  ctaText,
  shotTransition,
  dynamicZoom,
  voiceSrc,
  musicSrc,
  musicVolume,
  keepClipSound,
  playlist,
  timeline,
  envelope,
}) => {
  if (!playlist || !timeline || playlist.length === 0) {
    // Клипы ещё не найдены — показываем чёрный кадр вместо падения.
    return <AbsoluteFill style={{ backgroundColor: "#000000" }} />;
  }

  // Субтитры здесь размечены по озвучке, а она идёт вместе с роликом
  // один в один. Пересчитывать нечего, поэтому таймлайн для них ровный.
  const captionsTimeline = buildTimeline({
    shots: [{ fromMs: 0, toMs: (timeline.durationInFrames / FPS) * 1000 }],
    fps: FPS,
    transitionFrames: 0,
  });

  return (
    <AbsoluteFill name="Рилс из клипов" style={{ backgroundColor: "#000000" }}>
      <ShotSeries
        shots={playlist}
        timeline={timeline}
        shotTransition={shotTransition}
        dynamicZoom={dynamicZoom}
        muted={!keepClipSound}
      />
      {voiceSrc ? <Audio name="Озвучка" src={resolveSrc(voiceSrc)} /> : null}
      {musicSrc ? (
        <MusicLayer
          src={musicSrc}
          volume={musicVolume}
          envelope={envelope ?? EMPTY_ENVELOPE}
          // Озвучка идёт линейно вместе с роликом, пересчитывать нечего.
          sourceMsAtFrame={(frame) => (frame / FPS) * 1000}
        />
      ) : null}
      <TitleLayer text={title} accentColor={accentColor} durationInSeconds={2} />
      <CaptionsLayer src={captionsSrc} accentColor={accentColor} timeline={captionsTimeline} />
      {ctaText ? <CtaLayer text={ctaText} accentColor={accentColor} durationInSeconds={2} /> : null}
    </AbsoluteFill>
  );
};

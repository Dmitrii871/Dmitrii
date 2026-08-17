import { Video } from "@remotion/media";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { slide } from "@remotion/transitions/slide";
import { AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import type { Shot } from "../edit/build-shots";
import type { Timeline } from "../edit/timeline";
import { resolveSrc } from "../lib/resolve-src";

export type TransitionKind = "none" | "fade" | "slide";

// Крупности планов. Соседние планы получают разную — из одного кадра
// с телефона это читается как смена ракурса, хотя камера была одна.
// Внутри плана кадр ещё и медленно едет, чтобы картинка не замирала.
const SHOT_VARIANTS = [
  { fromScale: 1, toScale: 1.06, fromShift: "0% 0%", toShift: "0% 0%" },
  { fromScale: 1.18, toScale: 1.24, fromShift: "0% -3%", toShift: "0% -4%" },
  { fromScale: 1.38, toScale: 1.3, fromShift: "0% -8%", toShift: "0% -7%" },
  { fromScale: 1.12, toScale: 1.12, fromShift: "-3% 0%", toShift: "3% 0%" },
];

const STATIC_VARIANT = { fromScale: 1, toScale: 1, fromShift: "0% 0%", toShift: "0% 0%" };

const ShotView: React.FC<{
  src: string;
  shot: Shot;
  durationInFrames: number;
  variantIndex: number;
  dynamicZoom: boolean;
}> = ({ src, shot, durationInFrames, variantIndex, dynamicZoom }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Значения крупности приходят из массива, поэтому Studio не даст
  // тянуть их мышкой. Это осознанно: планов в ролике десятки,
  // руками их всё равно не настраивают, правится общий список выше.
  const variant = dynamicZoom ? SHOT_VARIANTS[variantIndex % SHOT_VARIANTS.length] : STATIC_VARIANT;

  return (
    <AbsoluteFill name="План" style={{ overflow: "hidden", backgroundColor: "#000000" }}>
      <Video
        name="Видео"
        src={src}
        trimBefore={Math.round((shot.fromMs / 1000) * fps)}
        trimAfter={Math.round((shot.toMs / 1000) * fps)}
        objectFit="cover"
        style={{
          width: "100%",
          height: "100%",
          scale: interpolate(frame, [0, durationInFrames], [variant.fromScale, variant.toScale], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
            easing: Easing.bezier(0.33, 0, 0.67, 1),
            output: "perceptual-scale",
          }),
          translate: interpolate(frame, [0, durationInFrames], [variant.fromShift, variant.toShift], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
            easing: Easing.bezier(0.33, 0, 0.67, 1),
          }),
        }}
      />
    </AbsoluteFill>
  );
};

export const EditedVideoLayer: React.FC<{
  src: string;
  timeline: Timeline;
  shotTransition: TransitionKind;
  dynamicZoom: boolean;
}> = ({ src, timeline, shotTransition, dynamicZoom }) => {
  const resolved = resolveSrc(src);
  const useTransitions = shotTransition !== "none" && timeline.transitionFrames > 0;

  return (
    <AbsoluteFill name="Видеоряд" style={{ backgroundColor: "#000000" }}>
      <TransitionSeries>
        {timeline.shots.flatMap((shot, index) => {
          const sequence = (
            <TransitionSeries.Sequence
              key={`shot-${index}`}
              durationInFrames={timeline.shotDurations[index]}
            >
              <ShotView
                src={resolved}
                shot={shot}
                durationInFrames={timeline.shotDurations[index]}
                variantIndex={index}
                dynamicZoom={dynamicZoom}
              />
            </TransitionSeries.Sequence>
          );

          if (!useTransitions || index === timeline.shots.length - 1) {
            return [sequence];
          }

          return [
            sequence,
            <TransitionSeries.Transition
              key={`transition-${index}`}
              presentation={shotTransition === "slide" ? slide() : fade()}
              timing={linearTiming({ durationInFrames: timeline.transitionFrames })}
            />,
          ];
        })}
      </TransitionSeries>
    </AbsoluteFill>
  );
};

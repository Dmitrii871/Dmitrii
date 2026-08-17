import { Audio } from "@remotion/media";
import { interpolate, useVideoConfig } from "remotion";
import { speechLevelAt } from "../edit/analyze-audio";
import type { LoudnessEnvelope } from "../edit/analyze-audio";
import { editedFrameToOriginalMs } from "../edit/timeline";
import type { Timeline } from "../edit/timeline";
import { resolveSrc } from "../lib/resolve-src";

// Фоновая музыка, которая сама уходит на второй план под голос.
// Уровень речи берём из разбора исходной дорожки: там, где человек
// говорит, музыка прижимается, в паузах и на заставке звучит в полную.
export const MusicLayer: React.FC<{
  src: string;
  volume: number;
  timeline: Timeline;
  envelope: LoudnessEnvelope;
}> = ({ src, volume, timeline, envelope }) => {
  const { durationInFrames } = useVideoConfig();

  return (
    <Audio
      name="Музыка"
      src={resolveSrc(src)}
      loop
      volume={(frame) => {
        const speech = speechLevelAt({
          envelope,
          ms: editedFrameToOriginalMs(timeline, frame),
          smoothMs: 260,
        });

        const ducking = interpolate(speech, [0.05, 0.2], [1, 0.22], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });

        const fades = interpolate(
          frame,
          [0, 20, durationInFrames - 25, durationInFrames],
          [0, 1, 1, 0],
          {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          },
        );

        return volume * ducking * fades;
      }}
    />
  );
};

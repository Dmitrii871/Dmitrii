import { Audio } from "@remotion/media";
import { interpolate, useVideoConfig } from "remotion";
import { speechLevelAt } from "../edit/analyze-audio";
import type { LoudnessEnvelope } from "../edit/analyze-audio";
import { resolveSrc } from "../lib/resolve-src";

// Фоновая музыка, которая сама уходит на второй план под голос.
//
// Уровень голоса известен по разобранной дорожке, но её время — это время
// исходника или озвучки, а не готового ролика. Поэтому пересчёт кадра
// в момент дорожки передаётся снаружи: у нарезанной записи он один,
// у сборки под озвучку — другой.
export const MusicLayer: React.FC<{
  src: string;
  volume: number;
  envelope: LoudnessEnvelope;
  sourceMsAtFrame: (frame: number) => number;
}> = ({ src, volume, envelope, sourceMsAtFrame }) => {
  const { durationInFrames } = useVideoConfig();

  return (
    <Audio
      name="Музыка"
      src={resolveSrc(src)}
      loop
      volume={(frame) => {
        const speech = speechLevelAt({
          envelope,
          ms: sourceMsAtFrame(frame),
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

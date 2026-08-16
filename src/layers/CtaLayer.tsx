import { AbsoluteFill, Easing, Interactive, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { fontFamily } from "../lib/font";

// Плашка призыва к действию на последние секунды ролика.
// Старт считается от durationInFrames, поэтому при смене видео её
// не нужно двигать руками.
export const CtaLayer: React.FC<{
  text: string;
  accentColor: string;
  durationInSeconds: number;
}> = ({ text, accentColor, durationInSeconds }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  return (
    <AbsoluteFill
      name="Призыв к действию"
      style={{
        justifyContent: "center",
        alignItems: "center",
        paddingLeft: 90,
        paddingRight: 90,
      }}
    >
      <Interactive.Div
        name="Плашка CTA"
        from={durationInFrames - durationInSeconds * fps}
        durationInFrames={durationInSeconds * fps}
        style={{
          fontFamily,
          fontSize: 72,
          fontWeight: 900,
          lineHeight: 1.1,
          letterSpacing: -1,
          textAlign: "center",
          textWrap: "balance",
          whiteSpace: "pre-wrap",
          color: "#0B0B0B",
          backgroundColor: accentColor,
          borderRadius: 40,
          padding: "40px 52px",
          boxShadow: "0px 24px 80px rgba(0, 0, 0, 0.55)",
          opacity: interpolate(
            frame,
            [durationInFrames - durationInSeconds * fps, durationInFrames - durationInSeconds * fps + 10],
            [0, 1],
            {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            },
          ),
          translate: interpolate(
            frame,
            [durationInFrames - durationInSeconds * fps, durationInFrames - durationInSeconds * fps + 18],
            ["0px 70px", "0px 0px"],
            {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            },
          ),
          scale: interpolate(
            frame,
            [durationInFrames - durationInSeconds * fps, durationInFrames - durationInSeconds * fps + 18],
            [0.9, 1],
            {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
              output: "perceptual-scale",
            },
          ),
        }}
      >
        {text}
      </Interactive.Div>
    </AbsoluteFill>
  );
};

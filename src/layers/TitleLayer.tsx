import { AbsoluteFill, Easing, Interactive, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { fontFamily } from "../lib/font";

// Заголовок первых двух секунд.
// Держится на любом фоне за счёт затемнённой подложки, размытия под ней
// и тени у текста — сразу три страховки, чтобы белый текст не потерялся
// на светлом кадре.
export const TitleLayer: React.FC<{
  text: string;
  accentColor: string;
  durationInSeconds: number;
}> = ({ text, accentColor, durationInSeconds }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill
      name="Заголовок"
      style={{
        justifyContent: "flex-start",
        alignItems: "center",
        // Отступ сверху — под интерфейс инстаграма (аватар, кнопка «Ещё»).
        paddingTop: 300,
        paddingLeft: 80,
        paddingRight: 80,
      }}
    >
      <Interactive.Div
        name="Плашка заголовка"
        durationInFrames={durationInSeconds * fps}
        style={{
          fontFamily,
          fontSize: 96,
          fontWeight: 800,
          lineHeight: 1.08,
          letterSpacing: -1,
          textAlign: "center",
          textWrap: "balance",
          whiteSpace: "pre-wrap",
          color: "#FFFFFF",
          textShadow: "0px 8px 32px rgba(0, 0, 0, 0.75)",
          backgroundColor: "rgba(0, 0, 0, 0.45)",
          backdropFilter: "blur(18px)",
          borderRadius: 36,
          borderBottom: `10px solid ${accentColor}`,
          padding: "36px 44px 32px 44px",
          opacity: interpolate(
            frame,
            [0, 0.5 * fps, durationInSeconds * fps - 12, durationInSeconds * fps],
            [0, 1, 1, 0],
            {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            },
          ),
          translate: interpolate(frame, [0, 0.6 * fps], ["0px 48px", "0px 0px"], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
            easing: Easing.bezier(0.16, 1, 0.3, 1),
          }),
          scale: interpolate(frame, [0, 0.6 * fps], [0.92, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
            easing: Easing.bezier(0.16, 1, 0.3, 1),
            output: "perceptual-scale",
          }),
        }}
      >
        {text}
      </Interactive.Div>
    </AbsoluteFill>
  );
};

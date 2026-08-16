import { Video } from "@remotion/media";
import { AbsoluteFill } from "remotion";
import { resolveSrc } from "../lib/resolve-src";

// Фоновое видео на весь кадр. objectFit: "cover" обрезает лишнее по краям
// и не растягивает картинку — горизонтальный исходник просто кадрируется
// по центру под вертикаль 1080x1920.
export const VideoLayer: React.FC<{ src: string }> = ({ src }) => {
  return (
    <AbsoluteFill name="Фон" style={{ backgroundColor: "#000000" }}>
      <Video
        name="Видео"
        src={resolveSrc(src)}
        objectFit="cover"
        style={{
          width: "100%",
          height: "100%",
        }}
      />
    </AbsoluteFill>
  );
};

import { staticFile } from "remotion";

const ABSOLUTE_URL = /^(https?:)?\/\//;

// Пропсы videoSrc и captionsSrc принимают путь внутри public/
// ("example/video.mp4"), но также переживут полный URL, если файл лежит
// в облаке. Отдельная функция, чтобы не дублировать эту проверку в слоях.
export const resolveSrc = (src: string): string => {
  if (ABSOLUTE_URL.test(src) || src.startsWith("data:") || src.startsWith("blob:")) {
    return src;
  }

  return staticFile(src);
};

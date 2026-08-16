import { ALL_FORMATS, Input, UrlSource } from "mediabunny";

// Читает длительность видео прямо из контейнера — без ffmpeg и без хардкода
// в композиции. Используется в calculateMetadata().
export const getVideoDuration = async (src: string): Promise<number> => {
  const input = new Input({
    formats: ALL_FORMATS,
    source: new UrlSource(src, {
      getRetryDelay: () => null,
    }),
  });

  return input.computeDuration();
};

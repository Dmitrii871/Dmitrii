import { Composition } from "remotion";
import { Reel, calculateReelMetadata, reelSchema } from "./Reel";

// Это тот самый «один файл»: чтобы собрать новый ролик, достаточно
// поменять значения в defaultProps. Компоненты трогать не нужно.
export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Reel"
      component={Reel}
      schema={reelSchema}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={300}
      calculateMetadata={calculateReelMetadata}
      defaultProps={{
        videoSrc: "example/video.webm",
        title: "Первые 3 секунды\nрешают всё",
        captionsSrc: "example/captions.srt",
        accentColor: "#FFD84D",
        ctaText: "Сохрани, чтобы не потерять",
        autoCutSilence: true,
        maxDurationSeconds: 180,
        shotTransition: "fade" as const,
        dynamicZoom: true,
        musicSrc: "example/music.mp3",
        musicVolume: 0.5,
      }}
    />
  );
};

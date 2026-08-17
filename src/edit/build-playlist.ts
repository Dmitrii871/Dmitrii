// Кусок конкретного клипа в сборке из нескольких файлов.
export type ClipShot = {
  src: string;
  fromMs: number;
  toMs: number;
};

export type Clip = {
  src: string;
  durationMs: number;
};

// Собирает очередь клипов под нужную длину.
//
// Если звуковая дорожка длиннее, чем все клипы вместе, клипы идут по кругу:
// лучше повторить кадр, чем оборвать озвучку на полуслове. Если наоборот —
// лишние клипы просто не попадают в ролик.
export const buildPlaylist = ({
  clips,
  maxClipMs,
  targetMs,
}: {
  clips: Clip[];
  maxClipMs: number;
  targetMs: number;
}): ClipShot[] => {
  const usable = clips.filter((clip) => clip.durationMs > 200);

  if (usable.length === 0) {
    return [];
  }

  const playlist: ClipShot[] = [];
  let filled = 0;
  let index = 0;

  // Ограничитель на случай очень коротких клипов и длинной озвучки.
  const maxShots = 400;

  while (filled < targetMs && playlist.length < maxShots) {
    const clip = usable[index % usable.length];
    const take = Math.min(maxClipMs, clip.durationMs, targetMs - filled);

    if (take < 200) {
      break;
    }

    playlist.push({ src: clip.src, fromMs: 0, toMs: take });
    filled += take;
    index++;
  }

  return playlist;
};

// Порядок клипов — по имени файла. Так его видно в Finder и можно
// задать нумерацией: 01-..., 02-... и так далее.
export const sortClipPaths = (paths: string[]): string[] =>
  [...paths].sort((a, b) => a.localeCompare(b, "ru", { numeric: true }));

const VIDEO_EXTENSIONS = [".mp4", ".mov", ".webm", ".m4v", ".mkv"];

export const isVideoPath = (path: string): boolean =>
  VIDEO_EXTENSIONS.some((extension) => path.toLowerCase().endsWith(extension));

import type { Shot } from "./build-shots";

// Готовый таймлайн: где каждый план стоит в смонтированном ролике.
// Один источник правды для длительности композиции, субтитров и музыки —
// иначе после нескольких склеек всё разъезжается.
export type Timeline = {
  shots: Shot[];
  fps: number;
  // Переходы съедают время: соседние планы накладываются друг на друга.
  transitionFrames: number;
  shotDurations: number[];
  shotStarts: number[];
  durationInFrames: number;
};

export const buildTimeline = ({
  shots,
  fps,
  transitionFrames,
}: {
  shots: Shot[];
  fps: number;
  transitionFrames: number;
}): Timeline => {
  const shotDurations = shots.map((shot) =>
    Math.max(2, Math.round(((shot.toMs - shot.fromMs) / 1000) * fps)),
  );

  // Переход не может быть длиннее половины самого короткого плана,
  // иначе Remotion справедливо ругается на невозможный таймлайн.
  const shortest = shotDurations.reduce((min, value) => Math.min(min, value), Infinity);
  const safeTransition = Math.max(
    0,
    Math.min(transitionFrames, Math.floor((shortest === Infinity ? 0 : shortest) / 2)),
  );

  const shotStarts: number[] = [];
  let cursor = 0;

  for (let i = 0; i < shotDurations.length; i++) {
    shotStarts.push(cursor);
    cursor += shotDurations[i] - (i < shotDurations.length - 1 ? safeTransition : 0);
  }

  return {
    shots,
    fps,
    transitionFrames: safeTransition,
    shotDurations,
    shotStarts,
    durationInFrames: Math.max(1, cursor),
  };
};

// Момент исходника → кадр смонтированного ролика.
// null, если это место вырезано.
export const originalMsToEditedFrame = (timeline: Timeline, ms: number): number | null => {
  for (let i = 0; i < timeline.shots.length; i++) {
    const shot = timeline.shots[i];

    if (ms < shot.fromMs || ms > shot.toMs) {
      continue;
    }

    return timeline.shotStarts[i] + ((ms - shot.fromMs) / 1000) * timeline.fps;
  }

  return null;
};

// Кадр смонтированного ролика → момент исходника.
// Нужно для приглушения музыки: уровень речи известен по исходнику.
export const editedFrameToOriginalMs = (timeline: Timeline, frame: number): number => {
  for (let i = timeline.shots.length - 1; i >= 0; i--) {
    if (frame < timeline.shotStarts[i]) {
      continue;
    }

    const offsetFrames = frame - timeline.shotStarts[i];

    return timeline.shots[i].fromMs + (offsetFrames / timeline.fps) * 1000;
  }

  return timeline.shots[0]?.fromMs ?? 0;
};

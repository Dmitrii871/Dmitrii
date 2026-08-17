import type { LoudnessEnvelope } from "./analyze-audio";

// Кусок исходника, который попадёт в готовый ролик.
export type Shot = {
  fromMs: number;
  toMs: number;
};

export type BuildShotsOptions = {
  envelope: LoudnessEnvelope;
  durationMs: number;
  // Ниже этой доли от самого громкого места считаем, что человек молчит.
  silenceThreshold: number;
  // Паузы короче этого не вырезаем — иначе речь рубится на середине фразы.
  minSilenceMs: number;
  // Запас до и после реплики, чтобы не срезать начало слова и вдох.
  paddingMs: number;
  // Слишком короткие обрывки выкидываем целиком.
  minShotMs: number;
  // Итоговая длина ролика.
  maxTotalMs: number;
};

const mergeOverlapping = (shots: Shot[]): Shot[] => {
  const merged: Shot[] = [];

  for (const shot of shots) {
    const previous = merged[merged.length - 1];

    if (previous && shot.fromMs <= previous.toMs) {
      previous.toMs = Math.max(previous.toMs, shot.toMs);
      continue;
    }

    merged.push({ ...shot });
  }

  return merged;
};

const capTotal = (shots: Shot[], maxTotalMs: number): Shot[] => {
  const capped: Shot[] = [];
  let used = 0;

  for (const shot of shots) {
    const length = shot.toMs - shot.fromMs;

    if (used + length <= maxTotalMs) {
      capped.push(shot);
      used += length;
      continue;
    }

    // Последний план обрезаем по остатку бюджета, если остаток осмысленный.
    const remaining = maxTotalMs - used;

    if (remaining > 500) {
      capped.push({ fromMs: shot.fromMs, toMs: shot.fromMs + remaining });
    }

    break;
  }

  return capped;
};

export const buildShots = ({
  envelope,
  durationMs,
  silenceThreshold,
  minSilenceMs,
  paddingMs,
  minShotMs,
  maxTotalMs,
}: BuildShotsOptions): Shot[] => {
  const wholeVideo: Shot[] = [{ fromMs: 0, toMs: durationMs }];

  if (envelope.values.length === 0) {
    // Дорожки нет или разобрать не удалось — берём ролик целиком.
    return capTotal(wholeVideo, maxTotalMs);
  }

  // 1. Собираем непрерывные участки, где громкость выше порога.
  const loud: Shot[] = [];

  for (let i = 0; i < envelope.values.length; i++) {
    if (envelope.values[i] < silenceThreshold) {
      continue;
    }

    const fromMs = i * envelope.windowMs;
    const toMs = fromMs + envelope.windowMs;
    const previous = loud[loud.length - 1];

    if (previous && previous.toMs === fromMs) {
      previous.toMs = toMs;
      continue;
    }

    loud.push({ fromMs, toMs });
  }

  if (loud.length === 0) {
    return capTotal(wholeVideo, maxTotalMs);
  }

  // 2. Короткие паузы внутри речи не вырезаем.
  const withoutMicroGaps: Shot[] = [];

  for (const shot of loud) {
    const previous = withoutMicroGaps[withoutMicroGaps.length - 1];

    if (previous && shot.fromMs - previous.toMs < minSilenceMs) {
      previous.toMs = shot.toMs;
      continue;
    }

    withoutMicroGaps.push({ ...shot });
  }

  // 3. Добавляем запас по краям и чистим результат.
  const padded = withoutMicroGaps.map((shot) => ({
    fromMs: Math.max(0, shot.fromMs - paddingMs),
    toMs: Math.min(durationMs, shot.toMs + paddingMs),
  }));

  const cleaned = mergeOverlapping(padded).filter((shot) => shot.toMs - shot.fromMs >= minShotMs);

  if (cleaned.length === 0) {
    return capTotal(wholeVideo, maxTotalMs);
  }

  return capTotal(cleaned, maxTotalMs);
};

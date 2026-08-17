import { ALL_FORMATS, AudioSampleSink, Input, UrlSource } from "mediabunny";

export type LoudnessEnvelope = {
  windowMs: number;
  // Громкость каждого окна, нормированная по самому громкому месту ролика.
  // 0 — тишина, 1 — пик. Нормировка нужна, потому что телефон пишет то
  // тихо, то громко, и абсолютный порог в децибелах не подошёл бы.
  values: number[];
};

// Разбор дорожки идёт в браузере и занимает секунды, а calculateMetadata
// в Studio перезапускается на каждую правку пропсов. Поэтому результат
// держим в памяти: один и тот же файл разбирается один раз.
const cache = new Map<string, Promise<LoudnessEnvelope>>();

const analyze = async (src: string, windowMs: number): Promise<LoudnessEnvelope> => {
  const input = new Input({
    formats: ALL_FORMATS,
    source: new UrlSource(src, { getRetryDelay: () => null }),
  });

  const track = await input.getPrimaryAudioTrack();

  if (!track) {
    return { windowMs, values: [] };
  }

  const sink = new AudioSampleSink(track);
  const sumOfSquares: number[] = [];
  const frameCounts: number[] = [];

  for await (const sample of sink.samples()) {
    const frames = sample.numberOfFrames;
    const channel = new Float32Array(frames);
    sample.copyTo(channel, { planeIndex: 0, format: "f32-planar" });

    const startMs = sample.timestamp * 1000;
    const msPerFrame = 1000 / sample.sampleRate;

    for (let i = 0; i < frames; i++) {
      const window = Math.floor((startMs + i * msPerFrame) / windowMs);

      if (window < 0) {
        continue;
      }

      sumOfSquares[window] = (sumOfSquares[window] ?? 0) + channel[i] * channel[i];
      frameCounts[window] = (frameCounts[window] ?? 0) + 1;
    }

    sample.close();
  }

  const rms: number[] = [];
  let peak = 1e-6;

  for (let i = 0; i < sumOfSquares.length; i++) {
    const value = Math.sqrt((sumOfSquares[i] ?? 0) / Math.max(1, frameCounts[i] ?? 1));
    rms[i] = value;

    if (value > peak) {
      peak = value;
    }
  }

  return {
    windowMs,
    // Округляем: значения уезжают в пропсы, а третьего знака хватает.
    values: rms.map((value) => Math.round((value / peak) * 1000) / 1000),
  };
};

export const analyzeLoudness = ({
  src,
  windowMs,
}: {
  src: string;
  windowMs: number;
}): Promise<LoudnessEnvelope> => {
  const key = `${src}|${windowMs}`;
  const cached = cache.get(key);

  if (cached) {
    return cached;
  }

  const promise = analyze(src, windowMs).catch((err) => {
    // Не срываем рендер из-за звука: без дорожки просто не будет
    // ни нарезки по паузам, ни приглушения музыки.
    cache.delete(key);
    throw err;
  });

  cache.set(key, promise);

  return promise;
};

// Громкость речи в конкретный момент исходника. Берём максимум по
// окрестности, иначе музыка дёргается на каждом слоге.
export const speechLevelAt = ({
  envelope,
  ms,
  smoothMs,
}: {
  envelope: LoudnessEnvelope;
  ms: number;
  smoothMs: number;
}): number => {
  if (envelope.values.length === 0) {
    return 0;
  }

  const radius = Math.max(0, Math.round(smoothMs / envelope.windowMs));
  const center = Math.floor(ms / envelope.windowMs);

  let level = 0;

  for (let i = center - radius; i <= center + radius; i++) {
    const value = envelope.values[i];

    if (value !== undefined && value > level) {
      level = value;
    }
  }

  return level;
};

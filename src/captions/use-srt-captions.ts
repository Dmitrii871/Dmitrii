import { useCallback, useEffect, useRef, useState } from "react";
import { useDelayRender } from "remotion";
import { resolveSrc } from "../lib/resolve-src";
import { parseSrtToPages } from "./srt-to-pages";
import type { CaptionPage } from "./srt-to-pages";

// Грузит srt из public/ и отдаёт готовые страницы субтитров.
// delayRender() держит кадр, пока файл не скачался, иначе при рендере
// первые кадры уедут без текста.
//
// Пустой captionsSrc — законное «субтитров пока нет»: ролик собирается
// без них. Если файл указан, но не найден, тоже не роняем рендер, а пишем
// предупреждение в консоль: чаще всего это значит, что srt ещё не сделан,
// а не что сломан проект.
export const useSrtCaptions = ({
  src,
  maxWordsPerPage,
}: {
  src: string;
  maxWordsPerPage: number;
}): CaptionPage[] | null => {
  const [pages, setPages] = useState<CaptionPage[] | null>(null);
  const { delayRender, continueRender, cancelRender } = useDelayRender();
  const [handle] = useState(() => delayRender(`Загрузка субтитров`));

  // Отпускать кадр можно только один раз, а src в Studio правится на ходу.
  const released = useRef(false);
  const release = useCallback(() => {
    if (released.current) {
      return;
    }

    released.current = true;
    continueRender(handle);
  }, [continueRender, handle]);

  const fetchCaptions = useCallback(async () => {
    if (src.trim() === "") {
      setPages([]);
      release();
      return;
    }

    try {
      const response = await fetch(resolveSrc(src));

      if (response.status === 404) {
        console.warn(
          `Файл субтитров не найден: ${src}. Ролик соберётся без субтитров. ` +
            `Проверьте путь относительно public/ или очистите captionsSrc.`,
        );
        setPages([]);
        release();
        return;
      }

      if (!response.ok) {
        throw new Error(`Не удалось загрузить субтитры (${src}): HTTP ${response.status}`);
      }

      const text = await response.text();
      setPages(parseSrtToPages({ input: text, maxWordsPerPage }));
      release();
    } catch (err) {
      cancelRender(err);
    }
  }, [src, maxWordsPerPage, release, cancelRender]);

  useEffect(() => {
    fetchCaptions();
  }, [fetchCaptions]);

  return pages;
};

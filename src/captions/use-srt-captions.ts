import { useCallback, useEffect, useState } from "react";
import { useDelayRender } from "remotion";
import { resolveSrc } from "../lib/resolve-src";
import { parseSrtToPages } from "./srt-to-pages";
import type { CaptionPage } from "./srt-to-pages";

// Грузит srt из public/ и отдаёт готовые страницы субтитров.
// delayRender() держит кадр, пока файл не скачался, иначе при рендере
// первые кадры уедут без текста.
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

  const fetchCaptions = useCallback(async () => {
    try {
      const response = await fetch(resolveSrc(src));

      if (!response.ok) {
        throw new Error(`Не удалось загрузить субтитры (${src}): HTTP ${response.status}`);
      }

      const text = await response.text();
      setPages(parseSrtToPages({ input: text, maxWordsPerPage }));
      continueRender(handle);
    } catch (err) {
      cancelRender(err);
    }
  }, [src, maxWordsPerPage, continueRender, cancelRender, handle]);

  useEffect(() => {
    fetchCaptions();
  }, [fetchCaptions]);

  return pages;
};

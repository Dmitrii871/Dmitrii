import { loadFont } from "@remotion/fonts";
import { staticFile } from "remotion";

// Montserrat с Google Fonts — плотная геометрическая гротеска, хорошо
// читается поверх видео и содержит полноценную кириллицу.
//
// Шрифт подключён файлом из public/fonts, а не запросом к fonts.gstatic.com.
// Так кириллица гарантированно на месте: рендер не зависит от сети, от
// прокси и от того, какие subsets успел отдать CDN. Именно на этом обычно
// и ломается кириллица — шрифт грузится одним «латинским» набором, а
// русский текст тихо уезжает в системный запасной шрифт.
//
// В public/fonts/Montserrat-Variable.ttf лежит вариативная версия
// (лицензия OFL, репозиторий google/fonts): один файл на все начертания
// от 100 до 900, латиница и кириллица внутри.
export const fontFamily = "Montserrat";

export const fontLoaded = loadFont({
  family: fontFamily,
  url: staticFile("fonts/Montserrat-Variable.ttf"),
  format: "truetype",
  // Диапазон, а не одно значение: файл вариативный, начертания
  // 700/800/900 берутся из него же.
  weight: "100 900",
  display: "block",
});

// Хочется вместо файла тянуть шрифт с Google Fonts по сети — замените
// весь блок выше на это (пакет @remotion/google-fonts уже установлен).
// Список subsets обязателен, без "cyrillic" русский текст не отрисуется:
//
// import { loadFont } from "@remotion/google-fonts/Montserrat";
//
// export const { fontFamily } = loadFont("normal", {
//   weights: ["700", "800", "900"],
//   subsets: ["cyrillic", "cyrillic-ext", "latin", "latin-ext"],
// });

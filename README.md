# Шаблон вертикальных рилсов на Remotion

Композиция `Reel` — 1080×1920, 30 fps. Все параметры ролика приходят
через props, поэтому новый ролик делается правкой одного файла:
`src/Root.tsx` (блок `defaultProps`) или отдельного json с пропсами.

Длительность считается автоматически из длины видео — хардкода таймингов нет.

## Быстрый старт

```bash
npm install
npm run dev
```

Откроется Remotion Studio с готовым примером из `public/example/`.

Рендер:

```bash
npx remotion render Reel out/reel.mp4 --props=examples/basic-reel/props.json
```

Подробности — куда класть видео, где брать `.srt`, какие флаги у рендера —
в [examples/README.md](examples/README.md).

## Props

| Параметр      | Тип             | Что делает                                              |
| ------------- | --------------- | ------------------------------------------------------- |
| `videoSrc`    | `string`        | Путь к видео внутри `public/` или полный URL            |
| `title`       | `string`        | Заголовок первых 2 секунд, `\n` — перенос строки        |
| `captionsSrc` | `string`        | Путь к `.srt` внутри `public/`                          |
| `accentColor` | `string` (цвет) | Активное слово субтитров и фон финальной плашки         |
| `ctaText`     | `string?`       | Текст финальной плашки; без него слой не рисуется       |

Схема описана через zod (`reelSchema` в `src/Reel.tsx`), так что в Studio
props правятся в боковой панели, а `accentColor` — пипеткой.

## Слои

| Файл                          | Что делает                                                                  |
| ----------------------------- | --------------------------------------------------------------------------- |
| `src/layers/VideoLayer.tsx`   | Видео на фон, `objectFit: "cover"` — кадрируется по центру без искажений     |
| `src/layers/TitleLayer.tsx`   | Заголовок 0–2 с: затемнённая подложка + размытие + тень, акцентная линия     |
| `src/layers/CaptionsLayer.tsx`| Субтитры из srt по 2–3 слова, активное слово — `accentColor`                 |
| `src/layers/CtaLayer.tsx`     | Плашка на последние 2 секунды, привязана к `durationInFrames`                |

Вспомогательное:

- `src/captions/srt-to-pages.ts` — разбор srt, раздача таймингов по словам,
  нарезка на страницы по 2–3 слова;
- `src/captions/use-srt-captions.ts` — загрузка srt с `delayRender()`;
- `src/lib/get-video-duration.ts` — длительность видео для `calculateMetadata`;
- `src/lib/font.ts` — шрифт;
- `src/lib/resolve-src.ts` — путь в `public/` или внешний URL.

## Шрифт и кириллица

Montserrat с Google Fonts, вариативный файл лежит в
`public/fonts/Montserrat-Variable.ttf` (лицензия OFL) — латиница и кириллица
в одном файле, все начертания от 100 до 900.

Файл, а не запрос к `fonts.gstatic.com`, специально: так кириллица не зависит
от сети во время рендера. Типовая поломка выглядит так — шрифт подключили
одной строкой без списка subsets, приехала только латиница, а русский текст
молча отрисовался системным шрифтом (в headless-браузере при рендере — часто
вообще квадратами).

Если нужен вариант с сетевой загрузкой — в `src/lib/font.ts` лежит
закомментированная замена на `@remotion/google-fonts` с обязательным
`subsets: ["cyrillic", "cyrillic-ext", "latin", "latin-ext"]`.

## Безопасные отступы под интерфейс инстаграма

- заголовок: `paddingTop: 300` (`TitleLayer.tsx`);
- субтитры: `paddingBottom: 430` (`CaptionsLayer.tsx`).

Это те два числа, которые стоит подвинуть, если интерфейс перекрывает текст.

## Проверки

```bash
npm run lint   # eslint + tsc
```

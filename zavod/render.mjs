#!/usr/bin/env node
// Рендер карусели в файлы: PNG 4:5 (Instagram/Facebook), PNG 9:16 и MP4-слайдшоу (TikTok/Reels), подпись.
//
//   node render.mjs --topic sleep --seed 2026-09-24 [--out out] [--no-video]
//
// Нужны: Playwright (npm i -g playwright) и ffmpeg с libx264 (путь можно задать в FFMPEG).

import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const ORIGIN = "https://zavod.local";
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".woff2": "font/woff2", ".webp": "image/webp" };

const args = Object.fromEntries(
  process.argv.slice(2).join(" ").split(/\s*--/).filter(Boolean).map((a) => {
    const [k, ...v] = a.split(" ");
    return [k, v.join(" ") || true];
  }),
);
const topic = args.topic || "sleep";
const seed = args.seed || new Date().toISOString().slice(0, 10);
const outDir = path.resolve(args.out || path.join(ROOT, "out"), `${topic}-${seed}`);
const ffmpeg = process.env.FFMPEG || "ffmpeg";

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch {
    const globalRoot = execFileSync("npm", ["root", "-g"]).toString().trim();
    return createRequire(import.meta.url)(path.join(globalRoot, "playwright"));
  }
}

async function renderFormat(browser, format) {
  const page = await browser.newPage({ viewport: { width: 1080, height: 1920 } });
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort(); // CDN для экспорта в браузере здесь не нужен
    const file = path.join(ROOT, decodeURIComponent(url.pathname));
    if (!file.startsWith(ROOT) || !fs.existsSync(file)) return route.fulfill({ status: 404 });
    route.fulfill({ contentType: TYPES[path.extname(file)] || "application/octet-stream", body: fs.readFileSync(file) });
  });
  await page.goto(`${ORIGIN}/index.html?topic=${topic}&format=${format}&seed=${encodeURIComponent(seed)}&render=1`);
  await page.waitForFunction(() => window.__ready, null, { timeout: 30000 });
  await page.evaluate(() => document.fonts.ready);
  const info = await page.evaluate(() => window.__carousel);
  const dir = path.join(outDir, format);
  fs.mkdirSync(dir, { recursive: true });
  const pages = await page.$$("#deck .page");
  const files = [];
  for (const [i, el] of pages.entries()) {
    const file = path.join(dir, `${String(i + 1).padStart(2, "0")}.png`);
    await el.screenshot({ path: file });
    files.push(file);
  }
  await page.close();
  return { info, files };
}

// Сколько секунд держать страницу: обложка короче, текст дольше.
function durationFor(type) {
  return { cover: 2.5, quote: 4, tip: 4.5, quiz: 4.5, answer: 4.5, cta: 5, final: 3 }[type] || 4;
}

function makeVideo(files, types, file) {
  const fade = 0.6;
  const inputs = files.flatMap((f, i) => ["-loop", "1", "-t", String(durationFor(types[i]) + fade), "-i", f]);
  const chain = [];
  let prev = "[0:v]";
  let offset = 0;
  for (let i = 1; i < files.length; i++) {
    offset += durationFor(types[i - 1]);
    const out = i === files.length - 1 ? "[v]" : `[x${i}]`;
    chain.push(`${prev}[${i}:v]xfade=transition=smoothleft:duration=${fade}:offset=${offset.toFixed(2)}${out}`);
    prev = out;
  }
  execFileSync(ffmpeg, [
    "-y", "-loglevel", "error", ...inputs,
    "-filter_complex", `${chain.join(";")};[v]fps=30,format=yuv420p[out]`,
    "-map", "[out]", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-movflags", "+faststart", file,
  ]);
}

const { chromium } = await loadPlaywright();
const browser = await chromium.launch();
try {
  const post = await renderFormat(browser, "post");
  const story = await renderFormat(browser, "story");
  fs.writeFileSync(path.join(outDir, "caption.txt"), post.info.caption + "\n");
  if (!args["no-video"]) makeVideo(story.files, story.info.types, path.join(outDir, "video.mp4"));
  console.log(`Готово: ${outDir} (${post.files.length} слайдов${args["no-video"] ? "" : " + video.mp4"})`);
} finally {
  await browser.close();
}

// Контент-завод: собирает карусель «страниц книги» из банка советов/цитат.
// Структура: 1 — обложка, каждый ctaEvery-й — продукт/ссылка, последний — финал.

const TOPICS = {
  sport: "Спорт",
  motivation: "Мотивация",
  psychology: "Психология",
  habits: "Привычки",
  sleep: "Сон",
};

const params = new URLSearchParams(location.search);
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let config;
let current = { slides: [], caption: "" };

// Детерминированный генератор: один и тот же seed даёт ту же карусель.
function rng(seed) {
  let h = 2166136261;
  for (const ch of String(seed)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return () => {
    h += 0x6d2b79f5;
    let t = h;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function shuffle(arr, rand) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function plan(topic, seed) {
  const rand = rng(`${topic.id}:${seed}`);
  const total = config.slidesPerCarousel;
  const every = config.ctaEvery;
  const items = shuffle(topic.items, rand);
  const products = shuffle(config.products, rand);
  const slides = [{ type: "cover" }];
  let tipNo = 0;
  let ctaNo = 0;
  for (let n = 2; n < total; n++) {
    if (n % every === 0) {
      slides.push({ type: "cta", product: products[ctaNo++ % products.length] });
    } else {
      const item = items[(n - 2 - ctaNo) % items.length];
      slides.push(item.kind === "quote" ? { type: "quote", item } : { type: "tip", item, no: ++tipNo });
    }
  }
  slides.push({ type: "final" });
  return slides;
}

function qrSvg(text) {
  if (!window.qrcode) return "";
  const qr = qrcode(0, "M");
  qr.addData(text);
  qr.make();
  return qr.createSvgTag({ cellSize: 8, margin: 0, scalable: true });
}

function pageHtml(slide, i, topic) {
  const { brand } = config;
  const head = `<div class="runhead"><span>${esc(brand.book)}</span><span>${esc(topic.chapter)}</span></div>`;
  const folio = `<div class="folio">— ${i + 1} —</div>`;
  let body;
  switch (slide.type) {
    case "cover":
      return `<div class="page cover"><div class="inner"><div class="frameline">
        <div class="chapter">${esc(topic.chapter)}</div>
        <h1>${esc(topic.cover)}</h1>
        <div class="ornament">❦</div>
        <div class="author">${esc(brand.author)}</div>
      </div></div><div class="swipe">листай →</div></div>`;
    case "tip":
      body = `<div class="tipno">Совет № ${slide.no}</div>
        <h2>${esc(slide.item.head)}</h2>
        <p class="text">${esc(slide.item.text)}</p>
        <div class="ornament">❧</div>`;
      break;
    case "quote":
      body = `<div class="mark">“</div>
        <blockquote>${esc(slide.item.text)}</blockquote>
        <div class="who">— ${esc(slide.item.author)}</div>`;
      break;
    case "cta": {
      const p = slide.product;
      body = `<div class="label">Закладка автора</div>
        <h2>${esc(p.title)}</h2>
        <p class="text">${esc(p.text)}</p>
        <div class="qr">${qrSvg(p.link)}</div>
        <div class="button">${esc(p.button)}</div>`;
      break;
    }
    case "final":
      body = `<h2>Сохрани эту страницу</h2>
        <div class="ornament">❦</div>
        <div class="actions">📌 Сохрани, чтобы вернуться<br>💬 Напиши, какой совет берёшь<br>➕ Подпишись: ${esc(brand.handle)}</div>`;
      break;
  }
  return `<div class="page ${slide.type}">${head}<div class="inner"><div class="body">${body}</div></div>${folio}</div>`;
}

function captionFor(topic, slides) {
  const tips = slides.filter((s) => s.type === "tip").map((s) => `• ${s.item.head}`);
  const cta = slides.find((s) => s.type === "cta");
  const tags = [...config.hashtags.common, ...(config.hashtags[topic.id] || [])].join(" ");
  return [
    `${topic.cover} 📖`,
    "",
    ...tips,
    "",
    cta ? `${cta.product.title} ${cta.product.button} 👉 ${cta.product.link}` : "",
    "",
    "Сохрани и отправь тому, кому это нужно.",
    "",
    tags,
  ].join("\n");
}

async function build() {
  const topicId = $("#topic").value;
  const format = config.formats[$("#format").value];
  const seed = $("#seed").value || new Date().toISOString().slice(0, 10);
  const topic = await (await fetch(`content/${topicId}.json`)).json();
  const slides = plan(topic, seed);
  const deck = $("#deck");
  deck.style.setProperty("--w", format.width);
  deck.style.setProperty("--h", format.height);
  deck.style.setProperty("--s", params.has("render") ? 1 : 0.28);
  deck.innerHTML = slides
    .map((s, i) => `<div class="frame"><span class="num">${i + 1}${s.type === "cta" ? " · продукт" : ""}</span>${pageHtml(s, i, topic)}</div>`)
    .join("");
  current = { slides, topic, format, seed, caption: captionFor(topic, slides) };
  $("#caption").value = current.caption;
  await document.fonts.ready;
  $("#status").textContent = `${slides.length} слайдов · ${format.width}×${format.height}`;
  window.__carousel = { topic: topic.id, seed, count: slides.length, caption: current.caption };
}

async function exportZip() {
  const btn = $("#export");
  btn.disabled = true;
  try {
    const zip = new JSZip();
    const pages = [...document.querySelectorAll("#deck .page")];
    for (const [i, page] of pages.entries()) {
      $("#status").textContent = `Рендер ${i + 1}/${pages.length}…`;
      const url = await htmlToImage.toPng(page, {
        width: current.format.width,
        height: current.format.height,
        style: { transform: "none" },
        pixelRatio: 1,
      });
      zip.file(`${String(i + 1).padStart(2, "0")}.png`, url.split(",")[1], { base64: true });
    }
    zip.file("caption.txt", $("#caption").value);
    const blob = await zip.generateAsync({ type: "blob" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${current.topic.id}-${current.seed}.zip`;
    a.click();
    $("#status").textContent = "Готово";
  } catch (e) {
    $("#status").textContent = `Ошибка: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

async function init() {
  config = await (await fetch("config.json")).json();
  $("#topic").innerHTML = Object.entries(TOPICS).map(([id, name]) => `<option value="${id}">${name}</option>`).join("");
  $("#format").innerHTML = Object.entries(config.formats).map(([id, f]) => `<option value="${id}">${f.label}</option>`).join("");
  if (params.get("topic")) $("#topic").value = params.get("topic");
  if (params.get("format")) $("#format").value = params.get("format");
  $("#seed").value = params.get("seed") || new Date().toISOString().slice(0, 10);
  if (params.has("render")) document.body.classList.add("render");
  for (const id of ["#topic", "#format", "#seed"]) $(id).addEventListener("change", build);
  $("#reroll").addEventListener("click", () => {
    $("#seed").value = Math.random().toString(36).slice(2, 8);
    build();
  });
  $("#copy").addEventListener("click", () => navigator.clipboard.writeText($("#caption").value));
  $("#export").addEventListener("click", exportZip);
  await build();
  window.__ready = true;
}

init();

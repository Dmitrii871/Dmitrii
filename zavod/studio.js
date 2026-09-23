// Контент-завод: собирает карусель «страниц книги» из банка советов/цитат/тестов.
// Структура: 1 — обложка, каждый ctaEvery-й — закладка с продуктом, последний — финал.
// Тест («quiz») занимает две страницы подряд: вопрос и ответ.

const TOPICS = {
  sleep: "Сон",
  psychology: "Психология",
  motivation: "Мотивация",
  habits: "Привычки",
  sport: "Спорт",
};

const params = new URLSearchParams(location.search);
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// **жирный** в текстах закладок
const rich = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
const LETTERS = ["А", "Б", "В", "Г"];
// Буквица только для текста, начинающегося с буквы («10 минут…» без неё).
const textP = (s) => `<p class="text${/^\p{L}/u.test(s) ? "" : " nodrop"}">${esc(s)}</p>`;

let config;
let catalog;
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

function ctaQueue(topic, rand) {
  const products = shuffle(catalog.items.filter((p) => p.topics.includes(topic.id)), rand)
    .map((p) => ({ ...p.slide, link: p.link, disclaimer: p.disclaimer, keyword: config.keywords[topic.id] }));
  const business = { ...config.business };
  return (i) => (products.length === 0 || (i > 0 && rand() < config.businessShare) ? business : products[i % products.length]);
}

function plan(topic, seed) {
  const rand = rng(`${topic.id}:${seed}`);
  const total = config.slidesPerCarousel;
  const isCta = (n) => n % config.ctaEvery === 0;
  const queue = shuffle(topic.items, rand);
  const nextCta = ctaQueue(topic, rand);
  const slides = [{ type: "cover" }];
  let tipNo = 0;
  let ctaNo = 0;
  let quizzes = 0;
  const take = (fits) => {
    const i = queue.findIndex(fits);
    return i < 0 ? null : queue.splice(i, 1)[0];
  };
  for (let n = 2; n < total; n++) {
    if (isCta(n)) {
      slides.push({ type: "cta", cta: nextCta(ctaNo++) });
      continue;
    }
    // Тест ставим, только если обе его страницы помещаются до закладки/финала.
    const roomForQuiz = n + 1 < total && !isCta(n + 1) && quizzes < 1;
    const item = take((it) => it.kind !== "quiz" || roomForQuiz) || { kind: "tip", head: "", text: "" };
    if (item.kind === "quiz") {
      quizzes++;
      slides.push({ type: "quiz", item }, { type: "answer", item });
      n++;
    } else if (item.kind === "quote") {
      slides.push({ type: "quote", item });
    } else {
      slides.push({ type: "tip", item, no: ++tipNo });
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
        ${textP(slide.item.text)}
        <div class="ornament">❧</div>`;
      break;
    case "quote":
      body = `<div class="mark">“</div>
        <blockquote>${esc(slide.item.text)}</blockquote>
        <div class="who">— ${esc(slide.item.author)}</div>`;
      break;
    case "quiz":
      body = `<div class="tipno">Проверь себя</div>
        <h2>${esc(slide.item.q)}</h2>
        <ol class="options">${slide.item.options.map((o, k) => `<li><span>${LETTERS[k]})</span> ${esc(o)}</li>`).join("")}</ol>
        <div class="hint">Запомни свой ответ — и переверни страницу →</div>`;
      break;
    case "answer": {
      const it = slide.item;
      body = `<div class="tipno">Ответ</div>
        <h2>${LETTERS[it.answer]}) ${esc(it.options[it.answer])}</h2>
        ${textP(it.explain)}
        <div class="ornament">❧</div>
        <div class="hint">Угадал? Напиши в комментариях ✍️</div>`;
      break;
    }
    case "cta": {
      const c = slide.cta;
      const keyword = c.keyword || config.keywords[topic.id];
      const qr = c.link
        ? `<div class="qr">${qrSvg(c.link)}</div>`
        : "";
      body = `<div class="label">Закладка автора</div>
        <h2>${esc(c.title)}</h2>
        <p class="text">${rich(c.text)}</p>
        <div class="cta-row${c.link ? "" : " solo"}">${qr}
          <div class="cta-side"><div class="button">Напиши «${esc(keyword)}»<br>в директ</div>
          ${c.link ? `<div class="region">${esc(config.regionHint)}</div>` : ""}</div>
        </div>
        ${c.note ? `<div class="note">${esc(c.note)}</div>` : ""}
        ${c.disclaimer ? `<div class="disclaimer">${esc(c.disclaimer)}</div>` : ""}`;
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
  const quiz = slides.find((s) => s.type === "quiz");
  const ctas = slides.filter((s) => s.type === "cta").map((s) => s.cta);
  const tags = [...config.hashtags.common, ...(config.hashtags[topic.id] || [])].join(" ");
  const keyword = config.keywords[topic.id];
  return [
    `${topic.cover} 📖`,
    "",
    ...tips,
    quiz ? `\n🧠 Внутри тест: ${quiz.item.q} Пиши свой вариант в комментариях до того, как посмотришь ответ!` : "",
    "",
    `💬 Напиши «${keyword}» в директ — расскажу подробнее и пришлю ссылку.`,
    ctas.some((c) => c.keyword === config.business.keyword) ? `💼 Интересно своё дело из дома? Напиши «${config.business.keyword}».` : "",
    "",
    "Сохрани и отправь тому, кому это нужно.",
    ctas.some((c) => c.disclaimer) ? "\nБАД. Не является лекарственным средством." : "",
    "",
    tags,
  ].filter((l, k, a) => !(l === "" && a[k - 1] === "")).join("\n");
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
  deck.dataset.format = $("#format").value;
  deck.innerHTML = slides
    .map((s, i) => `<div class="frame"><span class="num">${i + 1}${s.type === "cta" ? " · закладка" : ""}</span>${pageHtml(s, i, topic)}</div>`)
    .join("");
  current = { slides, topic, format, seed, caption: captionFor(topic, slides) };
  $("#caption").value = current.caption;
  await document.fonts.ready;
  $("#status").textContent = `${slides.length} слайдов · ${format.width}×${format.height}`;
  window.__carousel = { topic: topic.id, seed, count: slides.length, caption: current.caption, types: slides.map((s) => s.type) };
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
  [config, catalog] = await Promise.all(["config.json", "products.json"].map(async (f) => (await fetch(f)).json()));
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

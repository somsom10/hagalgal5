"use strict";

const $ = (sel) => document.querySelector(sel);

// ---------------------------------------------------------------- helpers
function timeOf(iso) {
  // "2026-07-02T19:15:00" -> "19:15"
  const m = /T(\d{2}:\d{2})/.exec(iso || "");
  return m ? m[1] : "";
}

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}

// ------------------------------------------------------------------- scan
const dateInput = $("#date");
const scanBtn = $("#scanBtn");
const scanHint = $("#scanHint");
const resultsPanel = $("#resultsPanel");
const resultsEl = $("#results");
const resultsTitle = $("#resultsTitle");

let staleTimer = null;

function setHint(msg, isError) {
  scanHint.innerHTML = msg;
  scanHint.classList.toggle("error", !!isError);
}

// While a cold scan crawls every cinema, the wait itself becomes part of the
// bit: rotating "status reports" from the hunt. Cached responses come back
// before the first joke fires, so warm scans never flash them.
const SCAN_JOKES = [
  "בודק מי קנה פופקורן זוגי…",
  "סורק את השורה האחורית. ברור שהם בשורה האחורית…",
  "מאתר זוגות שבטוחים שהם לבד…",
  "סופר כיסאות ריקים…",
  "מוודא שנשאר מקום לגלגל חמישי…",
  "מקשיב לרשרוש של שקית חטיפים באולם 6…",
  "מצליב תמונות לוויין של החניון…",
];
let jokeDelay = null;
let jokeTimer = null;
let lastJoke = -1;

function showScanJoke() {
  let i;
  do { i = Math.floor(Math.random() * SCAN_JOKES.length); } while (i === lastJoke);
  lastJoke = i;
  setHint(`<span class="spinner"></span>${SCAN_JOKES[i]}`);
}

function startScanJokes() {
  stopScanJokes();
  jokeDelay = setTimeout(() => {
    showScanJoke();
    jokeTimer = setInterval(showScanJoke, 3000);
  }, 900);
}

function stopScanJokes() {
  if (jokeDelay) { clearTimeout(jokeDelay); jokeDelay = null; }
  if (jokeTimer) { clearInterval(jokeTimer); jokeTimer = null; }
}

// The site keeps a private tally of how often you scan, and starts to worry.
function judgeRepeatScans() {
  let n = 0;
  try {
    const key = "tw-scans-" + israelDate(0);
    n = parseInt(localStorage.getItem(key) || "0", 10) + 1;
    localStorage.setItem(key, String(n));
  } catch (e) { /* private mode: no tally, no judgment */ }
  if (n >= 8) return "לכמה סרטים אתם מתכננים ללכת?";
  if (n >= 5) return "סריקה חמישית היום. אולי פשוט תזמינו חברים לסרט?";
  if (n >= 3) return "סריקה שלישית היום. הכל בסדר אצלכם?";
  return "";
}
let pendingJudgment = "";

async function runScan(manual) {
  const date = dateInput.value;
  if (!date) { setHint("בחרו תאריך.", true); return; }
  if (manual) pendingJudgment = judgeRepeatScans();

  scanBtn.disabled = true;
  setHint('<span class="spinner"></span>סורק את כל האולמות אחרי נשמות בודדות…');
  startScanJokes();
  if (staleTimer) { clearTimeout(staleTimer); staleTimer = null; }

  try {
    const url = `/api/scan?date=${encodeURIComponent(date)}`;
    const res = await fetch(url);
    if (res.status === 429) { setHint("רגע, יותר מדי בקשות. נסו שוב בעוד כמה שניות.", true); return; }
    const data = await res.json();

    if (data.error === "rate_limited") {
      setHint("פלאנט שמו לב אלינו 🫣 חוזרים לסרוק בעוד כמה דקות.", true);
      return;
    }
    if (data.error === "date_too_soon") {
      // Trust the server's clock over ours: snap to its earliest valid date
      // and retry once. (Second round can't loop: value === earliest then.)
      if (data.earliest && dateInput.value !== data.earliest) {
        dateInput.min = data.earliest;
        dateInput.value = data.earliest;
        runScan(false);
        return;
      }
      setHint("להיום כבר אי אפשר — ספונטניות זה לזוגות. נסו מחר.", true);
      return;
    }
    if (data.error) {
      setHint("לא הצלחנו למשוך נתונים מ־Planet כרגע. נסו שוב עוד מעט.", true);
      return;
    }

    renderResults(data);

    // Source fun facts from the films showing now — but only refresh the fact
    // when the set of films actually changes, so the stale-revalidate re-polls
    // below don't keep swapping the fact on their own.
    const films = [...new Set(data.opportunities.map((o) => o.film).filter(Boolean))];
    const filmsKey = films.slice().sort().join("|");
    if (films.length && filmsKey !== factFilmsKey) {
      factFilmsKey = filmsKey;
      factFilms = films;
      loadFact();
    }

    if (data.stale) {
      setHint("מציג תוצאות אחרונות ומרענן ברקע… 🔄");
      staleTimer = setTimeout(() => runScan(false), 4500);
    } else {
      setHint(pendingJudgment);
      pendingJudgment = "";
    }
  } catch (e) {
    setHint("משהו השתבש בדרך לאולם. נסו שוב.", true);
  } finally {
    stopScanJokes();
    scanBtn.disabled = false;
  }
}

function renderResults(data) {
  resultsPanel.hidden = false;
  resultsEl.innerHTML = "";
  resultsTitle.textContent = `האולמות הבודדים ביותר · ${data.date}`;

  if (!data.opportunities.length) {
    resultsEl.appendChild(el("p", "empty-state",
      "אין קורבנות מתאימים 🎭<br/>נסו תאריך אחר."));
    return;
  }

  data.opportunities.forEach((o, i) => resultsEl.appendChild(card(o, i + 1)));
}

function card(o, rank) {
  const c = el("div", "card");
  c.appendChild(el("div", "card__rank", String(rank)));
  c.appendChild(el("div", "card__film", escapeHtml(o.film)));
  c.appendChild(el("div", "card__meta",
    `${escapeHtml(o.cinema)} · ${escapeHtml(o.auditorium || "אולם")} · ${timeOf(o.starts_at)}`));

  const soldWord = o.seats_sold === 1 ? "צופה בודד" : `${o.seats_sold} צופים`;
  c.appendChild(el("div", "card__stat",
    `<b>${soldWord}</b> מתוך ${o.capacity} מושבים — יישארו לך <b>${o.seats_free_beside_you}</b> מושבים ריקים סביבם`));

  if (o.beside === "couple") {
    c.appendChild(el("div", "card__beside", "תשב ממש ליד <b>זוג 💑</b>"));
  }

  const pct = Math.round(o.emptiness * 100);
  const meterWrap = el("div");
  meterWrap.appendChild(el("div", "meter__label", `מדד בדידות · האולם ריק ב־${pct}%`));
  const meter = el("div", "meter");
  const fill = el("i");
  fill.style.width = pct + "%";
  meter.appendChild(fill);
  meterWrap.appendChild(meter);
  c.appendChild(meterWrap);

  const actions = el("div", "card__actions");
  const mapBtn = el("button", "btn", "מפת מושבים 🗺️");
  mapBtn.addEventListener("click", () => openSeatmap(o));
  actions.appendChild(mapBtn);
  if (o.booking_link) {
    const book = el("a", "btn btn--primary", "לאתר פלאנט 🎟️");
    book.href = o.booking_link;
    book.target = "_blank";
    book.rel = "noopener";
    guardBooking(book);
    actions.appendChild(book);
  }
  c.appendChild(actions);
  return c;
}

// ------------------------------------------------------- conscience check
// Every road to Planet passes through a short chain of confirmations.
// Regular dark patterns shame you into buying; these shame you into decency.
const confirmEl = $("#confirm");
const confirmText = $("#confirmText");
const confirmYes = $("#confirmYes");
const confirmNo = $("#confirmNo");
const CONFIRM_STEPS = [
  { text: "רגע — בטוחים שהזמנתם מספיק פופקורן לשלושתכם? 🍿", yes: "ברור", no: "אופס, לא" },
  { text: "הם נראים מאושרים.", yes: "בדיוק בגלל זה אני בא, נהיה יותר מאושרים שלושתנו", no: "צודקים, נוותר להם" },
  { text: "בסדר. שמרו על טווח לחישה נעים.", yes: "לאתר פלאנט 🎟️", no: "התחרטתי" },
];
let confirmStep = 0;
let confirmHref = "";

function guardBooking(a) {
  a.addEventListener("click", (e) => {
    e.preventDefault();
    openConfirm(a.href);
  });
}

function renderConfirmStep() {
  const s = CONFIRM_STEPS[confirmStep];
  confirmText.textContent = s.text;
  confirmYes.textContent = s.yes;
  confirmNo.textContent = s.no;
}

function openConfirm(href) {
  confirmHref = href;
  confirmStep = 0;
  confirmYes.hidden = false;
  confirmNo.hidden = false;
  renderConfirmStep();
  confirmEl.hidden = false;
  confirmNo.focus(); // the decent choice is the default focus, naturally
}

function closeConfirm() {
  confirmEl.hidden = true;
}

confirmYes.addEventListener("click", () => {
  if (confirmStep < CONFIRM_STEPS.length - 1) {
    confirmStep += 1;
    renderConfirmStep();
  } else {
    window.open(confirmHref, "_blank", "noopener");
    closeConfirm();
  }
});
confirmNo.addEventListener("click", () => {
  confirmText.textContent = "החלטה טובה. הם לעולם לא יידעו כמה קרוב זה היה.";
  confirmYes.hidden = true;
  confirmNo.hidden = true;
  setTimeout(closeConfirm, 1800);
});
confirmEl.querySelector("[data-cclose]").addEventListener("click", closeConfirm);

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// --------------------------------------------------------------- seatmap
const modal = $("#modal");
const seatmapEl = $("#seatmap");
const pickEl = $("#pick");
const bookLink = $("#bookLink");
const modalTitle = $("#modalTitle");

// While the dialog is open: focus lives inside it (and returns to the button
// that opened it on close), Tab cycles within it, and the page behind it
// doesn't scroll. Pure dialog mechanics — nothing to do with the seat data.
let lastFocused = null;

function closeModal() {
  if (modal.hidden) return;
  modal.hidden = true;
  document.body.classList.remove("modal-open");
  if (lastFocused && document.contains(lastFocused)) lastFocused.focus();
  lastFocused = null;
}
modal.querySelectorAll("[data-close]").forEach((n) => n.addEventListener("click", closeModal));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeConfirm(); closeModal(); }
});
guardBooking(bookLink); // the modal's Planet link passes the conscience check too
modal.addEventListener("keydown", (e) => {
  if (e.key !== "Tab") return;
  const items = [...modal.querySelectorAll("button, a[href]")]
    .filter((n) => !n.hidden && n.offsetParent !== null);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
  else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
});

async function openSeatmap(o) {
  modal.hidden = false;
  document.body.classList.add("modal-open");
  lastFocused = document.activeElement;
  modal.querySelector(".modal__close").focus();
  modalTitle.textContent = `${o.film} · ${o.cinema}`;
  seatmapEl.innerHTML = '<p class="empty-state"><span class="spinner"></span>טוען את מפת האולם…</p>';
  pickEl.innerHTML = "";
  if (o.booking_link) { bookLink.href = o.booking_link; bookLink.hidden = false; }
  else { bookLink.hidden = true; }

  try {
    const res = await fetch(`/api/seatmap?presentation=${encodeURIComponent(o.presentation_id)}&sold=${o.seats_sold}`);
    if (!res.ok) throw new Error("http " + res.status);
    const data = await res.json();
    renderSeatmap(data);
  } catch (e) {
    seatmapEl.innerHTML = '<p class="empty-state">לא הצלחנו לטעון את מפת האולם. נסו שוב.</p>';
  }
}

function renderSeatmap(data) {
  seatmapEl.innerHTML = "";
  seatmapEl.appendChild(el("div", "screen", "מסך"));

  const rowsWrap = el("div", "seat-rows");
  data.grid.forEach((row) => {
    const r = el("div", "seat-row");
    r.appendChild(el("span", "seat-row__name", escapeHtml(row.row_name)));
    // Each row spans only its own seats (with internal aisle gaps preserved)
    // and is centred, so shorter rows sit centred under the screen.
    const cells = el("div", "seat-cells");
    const byCol = new Map(row.cells.map((c) => [c.col, c]));
    const cols = row.cells.map((c) => c.col);
    const lo = Math.min(...cols), hi = Math.max(...cols);
    for (let col = lo; col <= hi; col++) {
      const c = byCol.get(col);
      if (!c) { cells.appendChild(el("span", "seat seat--gap")); continue; }
      const seat = el("span", "seat seat--" + c.state);
      seat.title = c.label + (c.state === "pick" ? " ← המושב שלך" : "");
      cells.appendChild(seat);
    }
    r.appendChild(cells);
    rowsWrap.appendChild(r);
  });
  seatmapEl.appendChild(rowsWrap);

  const legend = el("div", "legend",
    '<span><i style="background:var(--blood)"></i> תפוס</span>' +
    '<span><i style="background:rgba(255,255,255,0.14)"></i> ריק</span>' +
    '<span><i style="background:url(/static/wheel-white.svg) center/contain no-repeat"></i> המושב שלך</span>');
  seatmapEl.appendChild(legend);

  if (data.pick) {
    pickEl.innerHTML =
      `🪑 המטרה: <strong>${escapeHtml(data.pick.target)}</strong><br/>` +
      `👉 שב במושב <span class="seatlabel">${escapeHtml(data.pick.seat)}</span> — ממש לידם.`;
  } else {
    pickEl.innerHTML = "לא נמצא זוג מבודד עם מושב פנוי ממש לידו. האולם ריק מדי או חברותי מדי. 🤷";
  }
}

// -------------------------------------------------------------- fun fact
const factBtn = $("#factBtn");
const factText = $("#factText");
const factSource = $("#factSource");
let lastFact = "";
let factFilms = []; // films from the latest scan, to source IMDB trivia from
let factFilmsKey = ""; // set-of-films fingerprint, to avoid needless refreshes

async function loadFact() {
  factBtn.disabled = true;
  factText.innerHTML = '<span class="spinner"></span>מחפש עובדה…';
  const film = factFilms.length
    ? factFilms[Math.floor(Math.random() * factFilms.length)]
    : "";
  try {
    const q = new URLSearchParams({ exclude: lastFact });
    if (film) q.set("film", film);
    const res = await fetch(`/api/funfact?${q.toString()}`);
    const data = await res.json();
    lastFact = data.fact;
    factText.textContent = data.fact;
    factSource.textContent =
      data.source === "imdb" && data.film ? `🎬 ${data.film} · עובדה מ־IMDB` : "";
  } catch (e) {
    factText.textContent = "העובדה ברחה מהאולם. נסו שוב.";
    factSource.textContent = "";
  } finally {
    factBtn.disabled = false;
  }
}

// -------------------------------------------------------------------- init
// Always the *Israel* date (the cinemas' timezone), regardless of where the
// visitor is or their device clock — en-CA formats as YYYY-MM-DD.
function israelDate(offsetDays) {
  return new Date(Date.now() + offsetDays * 86400000)
    .toLocaleDateString("en-CA", { timeZone: "Asia/Jerusalem" });
}
// Scanning opens at tomorrow. No banner explains this; it's one of those
// things you don't notice at first glance.
dateInput.value = israelDate(1);
dateInput.min = israelDate(1);
scanBtn.addEventListener("click", () => runScan(true));
factBtn.addEventListener("click", loadFact);
loadFact(); // show a fact right away, not a "click the button" prompt

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
const currentTarget = "couple"; // the site targets couples only

function setHint(msg, isError) {
  scanHint.innerHTML = msg;
  scanHint.classList.toggle("error", !!isError);
}

async function runScan() {
  const date = dateInput.value;
  const target = currentTarget;
  if (!date) { setHint("בחרו תאריך.", true); return; }

  scanBtn.disabled = true;
  setHint('<span class="spinner"></span>סורק את כל האולמות אחרי נשמה בודדה…');
  if (staleTimer) { clearTimeout(staleTimer); staleTimer = null; }

  try {
    const url = `/api/scan?date=${encodeURIComponent(date)}&target=${target}`;
    const res = await fetch(url);
    if (res.status === 429) { setHint("רגע, יותר מדי בקשות. נסו שוב בעוד כמה שניות.", true); return; }
    const data = await res.json();

    if (data.error) {
      setHint("לא הצלחנו למשוך נתונים מ־Planet כרגע. נסו שוב עוד רגע.", true);
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
      staleTimer = setTimeout(runScan, 4500);
    } else {
      setHint(data.count ? "" : "");
    }
  } catch (e) {
    setHint("משהו השתבש בדרך לאולם. נסו שוב.", true);
  } finally {
    scanBtn.disabled = false;
  }
}

function renderResults(data) {
  resultsPanel.hidden = false;
  resultsEl.innerHTML = "";
  const targetLabel = { both: "זוגות ובודדים", couple: "זוגות", single: "בודדים" }[data.target] || "";
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

  if (o.beside) {
    const who = o.beside === "couple" ? "זוג 💑" : "בודד/ת 🧍";
    c.appendChild(el("div", "card__beside", `תשב ממש ליד <b>${who}</b>`));
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
    actions.appendChild(book);
  }
  c.appendChild(actions);
  return c;
}

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

function closeModal() { modal.hidden = true; }
modal.querySelectorAll("[data-close]").forEach((n) => n.addEventListener("click", closeModal));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

async function openSeatmap(o) {
  modal.hidden = false;
  modalTitle.textContent = `${o.film} · ${o.cinema}`;
  seatmapEl.innerHTML = '<p class="empty-state"><span class="spinner"></span>טוען את מפת האולם…</p>';
  pickEl.innerHTML = "";
  if (o.booking_link) { bookLink.href = o.booking_link; bookLink.hidden = false; }
  else { bookLink.hidden = true; }

  try {
    const res = await fetch(`/api/seatmap?presentation=${encodeURIComponent(o.presentation_id)}&sold=${o.seats_sold}&target=${currentTarget}`);
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
    pickEl.innerHTML = "לא נמצא בודד/זוג מבודד עם מושב פנוי ממש לידו. האולם ריק מדי או חברותי מדי. 🤷";
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
dateInput.value = new Date().toISOString().slice(0, 10);
dateInput.min = new Date().toISOString().slice(0, 10);
scanBtn.addEventListener("click", runScan);
factBtn.addEventListener("click", loadFact);
loadFact(); // show a fact right away, not a "click the button" prompt

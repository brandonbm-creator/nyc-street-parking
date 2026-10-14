// West Side Parking dashboard. Data: data.json (built by `python3 asp.py export`).
// All street-cleaning logic mirrors asp.py: a block face is
//   clear   - no cleaning overlaps [arrive, leave) that day
//   opens   - cleaning overlaps, but ends before you leave (park once it ends)
//   blocked - cleaning runs until/after you leave
const DAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const AREAS = [
  [61, 72, "Lincoln Sq", "61–72"],
  [73, 86, "UWS core", "73–86"],
  [87, 96, "UWS north", "87–96"],
  [97, 110, "Manhattan Valley", "97–110"],
  [111, 135, "Morningside / Harlem", "111–135"],
  [136, 185, "Hamilton Hts / Wash Hts", "136–185"],
];
const COLORS = { clear: "#16a34a", opens: "#f59e0b", blocked: "#dc2626" };
const PAGE = 40;

const $ = (s) => document.querySelector(s);
const state = {
  date: todayNY(),
  arrive: 450,
  leave: 720,
  max: 110,
  area: null,
  tab: "clear",
  shown: PAGE,
};
let DATA, map, markers = new Map(), rows = [];

// ---------------------------------------------------------------- time helpers
function todayNY() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
}
function addDays(iso, n) {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + n));
  return dt.toISOString().slice(0, 10);
}
function weekday(iso) { // Mon=0 .. Sun=6
  const [y, m, d] = iso.split("-").map(Number);
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
}
function fmtDate(iso, opts = { weekday: "long", month: "short", day: "numeric" }) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", { ...opts, timeZone: "UTC" });
}
function fmtTime(min) {
  let h = Math.floor(min / 60), m = min % 60;
  const ap = h < 12 || h === 24 ? "AM" : "PM";
  h = h % 12 || 12;
  return m ? `${h}:${String(m).padStart(2, "0")}${ap}` : `${h}${ap}`;
}
const toMin = (v) => { const [h, m] = v.split(":").map(Number); return h * 60 + m; };
const ordinal = (n) => n + (n % 100 >= 11 && n % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] || "th");

// ---------------------------------------------------------------- street-cleaning logic
function statusOf(b, iso) {
  if (DATA.holidays[iso]) return { kind: "clear" };
  const wd = weekday(iso);
  const today = b.s.filter(([days]) => days.includes(wd));
  const hits = today.filter(([, s, e]) => s < state.leave && e > state.arrive);
  if (!hits.length) return { kind: "clear", swept: today.some(([, , e]) => e <= state.arrive) };
  const free = Math.max(...hits.map(([, , e]) => e));
  return free < state.leave ? { kind: "opens", free } : { kind: "blocked", until: free };
}
function nextCleaning(b, iso, after) {
  for (let i = 0; i < 14; i++) {
    const d = addDays(iso, i);
    if (DATA.holidays[d]) continue;
    const wd = weekday(d);
    const starts = b.s.filter(([days, s]) => days.includes(wd) && (i || s >= after)).map(([, s]) => s);
    if (starts.length) {
      const label = i === 0 ? "today" : i === 1 ? "tomorrow" : DAY[wd];
      return `${label} ${fmtTime(Math.min(...starts))}`;
    }
  }
  return "—";
}
const schedText = (b) => b.s.map(([d, s, e]) => `${d.map((x) => DAY[x]).join("/")} ${fmtTime(s)}–${fmtTime(e)}`).join("; ");
const sideText = (s) => ({ N: "north", S: "south", E: "east", W: "west" }[s] || s) + " side";
const dirUrl = (b) => b.lat
  ? `https://www.google.com/maps/dir/?api=1&destination=${b.lat},${b.lon}`
  : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${b.on} & ${b.from}, Manhattan, NY`)}`;

// ---------------------------------------------------------------- render
function compute() {
  rows = DATA.blocks
    .filter((b) => b.n <= state.max)
    .map((b) => ({ b, st: statusOf(b, state.date) }));
}

function renderStatus() {
  const iso = state.date, hol = DATA.holidays[iso];
  const anyCleaning = rows.some((r) => r.st.kind !== "clear");
  const clear = rows.filter((r) => r.st.kind === "clear").length;
  const el = $("#status");
  const win = `${fmtTime(state.arrive)}–${fmtTime(state.leave)}`;
  if (hol) {
    el.className = "status off";
    el.innerHTML = `<div class="big">Suspended</div><div class="small">${fmtDate(iso)} · ${hol}. Park on either side; meters may still apply.</div>`;
  } else if (!anyCleaning) {
    el.className = "status off";
    el.innerHTML = `<div class="big">No cleaning</div><div class="small">${fmtDate(iso)} · every block is clear ${win}.</div>`;
  } else {
    el.className = "status on";
    el.innerHTML = `<div class="big">Cleaning in effect</div><div class="small">${fmtDate(iso)} · <b>${clear}</b> of ${rows.length} block faces clear ${win}</div>`;
  }
}

function renderAreas() {
  const html = AREAS.filter(([lo]) => lo <= state.max).map(([lo, hi, name, range], i) => {
    const r = rows.filter((x) => x.b.n >= lo && x.b.n <= hi);
    const c = { clear: 0, opens: 0, blocked: 0 };
    r.forEach((x) => c[x.st.kind]++);
    const pct = (k) => (r.length ? (100 * c[k]) / r.length : 0);
    return `<button class="area ${state.area === i ? "on" : ""}" data-area="${i}">
      <div class="name">${name} · ${range}</div>
      <div><span class="num">${c.clear}</span> <span class="of">clear of ${r.length}</span></div>
      <div class="bar"><span style="width:${pct("clear")}%;background:${COLORS.clear}"></span><span style="width:${pct("opens")}%;background:${COLORS.opens}"></span><span style="width:${pct("blocked")}%;background:${COLORS.blocked}"></span></div>
    </button>`;
  }).join("");
  $("#areas").innerHTML = html;
}

function visibleRows() {
  let r = rows.filter((x) => x.st.kind === state.tab);
  if (state.area !== null) {
    const [lo, hi] = AREAS[state.area];
    r = r.filter((x) => x.b.n >= lo && x.b.n <= hi);
  }
  if (state.tab === "opens") r.sort((a, b) => a.b.n - b.b.n || a.st.free - b.st.free);
  return r; // data.json is already sorted nearest-to-61st first
}

function badge(st) {
  if (st.kind === "clear") return `<span class="badge clear">${st.swept ? "swept earlier" : "clear"}</span>`;
  if (st.kind === "opens") return `<span class="badge opens">from ${fmtTime(st.free)}</span>`;
  return `<span class="badge blocked">until ${fmtTime(st.until)}</span>`;
}

function renderList() {
  const r = visibleRows();
  const list = $("#list");
  if (!r.length) {
    list.innerHTML = `<li class="empty">Nothing here for this window.</li>`;
    return;
  }
  list.innerHTML = r.slice(0, state.shown).map(({ b, st }, i) => `
    <li class="item" data-i="${DATA.blocks.indexOf(b)}">
      <div class="st">${b.n}</div>
      <div>
        <div class="blk">${b.on}</div>
        <div class="meta">${b.from} → ${b.to} · ${sideText(b.side)} · next ${nextCleaning(b, state.date, state.leave)}</div>
        <div style="margin-top:4px">${badge(st)}</div>
      </div>
      <a class="go" href="${dirUrl(b)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">Go ↗</a>
    </li>`).join("") +
    (r.length > state.shown ? `<li><button class="more" id="more">Show ${Math.min(PAGE, r.length - state.shown)} more of ${r.length - state.shown}</button></li>` : "");
}

function popupHtml(b, st) {
  const label = st.kind === "clear" ? "Clear for your window" : st.kind === "opens" ? `Opens at ${fmtTime(st.free)}` : `Cleaning until ${fmtTime(st.until)}`;
  return `<b>${b.on}</b><br>${b.from} → ${b.to} · ${sideText(b.side)}<br>
    <span style="color:${COLORS[st.kind]};font-weight:600">${label}</span><br>
    <span style="color:#64748b">${schedText(b)}</span><br>
    Next: ${nextCleaning(b, state.date, state.leave)}<br>
    <a href="${dirUrl(b)}" target="_blank" rel="noopener">Directions ↗</a>`;
}

function renderMap() {
  markers.forEach((m) => m.remove());
  markers.clear();
  rows.forEach(({ b, st }) => {
    if (b.lat == null) return;
    const m = L.circleMarker([b.lat, b.lon], {
      radius: st.kind === "clear" ? 6 : 5, weight: 1, color: "#fff",
      fillColor: COLORS[st.kind], fillOpacity: st.kind === "blocked" ? 0.55 : 0.9,
    }).bindPopup(() => popupHtml(b, st)).addTo(map);
    markers.set(DATA.blocks.indexOf(b), m);
  });
}

function renderFooter() {
  const next = Object.keys(DATA.holidays).sort().find((d) => d > state.date);
  const tomorrow = addDays(state.date, 1);
  const tHol = DATA.holidays[tomorrow];
  $("#upcoming").innerHTML =
    `<b>${fmtDate(tomorrow, { weekday: "long" })}:</b> ${tHol ? `suspended (${tHol})` : weekday(tomorrow) === 6 ? "no cleaning (Sunday)" : "cleaning in effect"}` +
    ` · <b>Next holiday:</b> ${next ? `${fmtDate(next, { weekday: "short", month: "short", day: "numeric" })} (${DATA.holidays[next]})` : "none on file"}`;
  $("#generated").textContent = `Sign data refreshed ${new Date(DATA.generated).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" })}`;
}

function render({ fit = false } = {}) {
  compute();
  renderStatus();
  renderAreas();
  renderList();
  renderMap();
  renderFooter();
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === state.tab));
  const dayIdx = state.date === todayNY() ? "0" : state.date === addDays(todayNY(), 1) ? "1" : null;
  document.querySelectorAll("#day-seg button").forEach((b) => b.classList.toggle("on", b.dataset.day === dayIdx));
  $("#date").value = state.date;
  if (fit) fitArea();
}

function fitArea() {
  const pts = rows.filter(({ b }) => b.lat != null && (state.area === null
    ? b.n <= Math.min(state.max, 110) : b.n >= AREAS[state.area][0] && b.n <= AREAS[state.area][1]))
    .map(({ b }) => [b.lat, b.lon]);
  if (pts.length) map.fitBounds(pts, { padding: [20, 20] });
}

// ---------------------------------------------------------------- wire up
function save() {
  localStorage.setItem("uws-parking", JSON.stringify({ arrive: state.arrive, leave: state.leave, max: state.max }));
}

async function init() {
  Object.assign(state, JSON.parse(localStorage.getItem("uws-parking") || "{}"));
  $("#arrive").value = `${String(Math.floor(state.arrive / 60)).padStart(2, "0")}:${String(state.arrive % 60).padStart(2, "0")}`;
  $("#leave").value = `${String(Math.floor(state.leave / 60)).padStart(2, "0")}:${String(state.leave % 60).padStart(2, "0")}`;
  $("#max").value = state.max;
  $("#max-label").textContent = ordinal(state.max);

  map = L.map("map", { zoomControl: false }).setView([40.785, -73.975], 14);
  L.control.zoom({ position: "topright" }).addTo(map);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);

  DATA = await (await fetch("data.json", { cache: "no-cache" })).json();
  render({ fit: true });

  $("#day-seg").addEventListener("click", (e) => {
    const d = e.target.dataset.day;
    if (d === undefined) return;
    state.date = addDays(todayNY(), Number(d));
    render();
  });
  $("#date").addEventListener("change", (e) => { if (e.target.value) { state.date = e.target.value; render(); } });
  $("#arrive").addEventListener("change", (e) => { state.arrive = toMin(e.target.value); save(); render(); });
  $("#leave").addEventListener("change", (e) => { state.leave = toMin(e.target.value); save(); render(); });
  $("#max").addEventListener("input", (e) => { $("#max-label").textContent = ordinal(+e.target.value); });
  $("#max").addEventListener("change", (e) => {
    state.max = +e.target.value;
    if (state.area !== null && AREAS[state.area][0] > state.max) state.area = null;
    save(); render({ fit: true });
  });
  $("#areas").addEventListener("click", (e) => {
    const btn = e.target.closest(".area");
    if (!btn) return;
    const i = Number(btn.dataset.area);
    state.area = state.area === i ? null : i;
    state.shown = PAGE;
    render({ fit: true });
  });
  $("#tabs").addEventListener("click", (e) => {
    if (!e.target.dataset.tab) return;
    state.tab = e.target.dataset.tab;
    state.shown = PAGE;
    render();
  });
  $("#list").addEventListener("click", (e) => {
    if (e.target.id === "more") { state.shown += PAGE; renderList(); return; }
    const li = e.target.closest(".item");
    const m = li && markers.get(Number(li.dataset.i));
    if (!m) return;
    map.flyTo(m.getLatLng(), 17, { duration: 0.6 });
    m.openPopup();
    if (innerWidth <= 820) window.scrollTo({ top: 0, behavior: "smooth" });
  });
  $("#locate").addEventListener("click", () => map.locate({ setView: true, maxZoom: 16 }));
  map.on("locationfound", (e) => L.circleMarker(e.latlng, { radius: 7, color: "#2563eb", fillOpacity: 1 }).addTo(map));
}

init();

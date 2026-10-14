// Uptown Parking dashboard. Data: data.json (built by `python3 asp.py export`).
// Street-cleaning logic mirrors asp.py status(): for a stay from `arrive` to `leave`, a block face is
//   wait    - WAIT & TAKE: a cleaning window's halfway mark is coming up within MAX_WAIT minutes. That's
//             when cars start moving back in on these competitive blocks, so be there by then; the spot is
//             yours until the next cleaning (often days).
//   clear   - no cleaning overlaps your stay
//   opens   - cleaning ends before you leave, but the wait is longer (or it hasn't started yet)
//   blocked - cleaning runs until/after you leave
// Views: "uws" (West Side blocks by street range) and "places" (blocks within a walk of a location or of you).
const DAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const AREAS = [
  [61, 72, "Lincoln Sq", "61–72"],
  [73, 86, "UWS core", "73–86"],
  [87, 96, "UWS north", "87–96"],
  [97, 110, "Manhattan Valley", "97–110"],
  [111, 135, "Morningside / Harlem", "111–135"],
  [136, 185, "Hamilton Hts / Wash Hts", "136–185"],
];
const KINDS = ["wait", "clear", "opens", "blocked"];
const COLORS = { wait: "#2563eb", clear: "#16a34a", opens: "#f59e0b", blocked: "#dc2626" };
const HINTS = {
  wait: "Cars start moving back in at the halfway mark of a cleaning window. Be there by the move-in time to claim the spot; it's yours until the next cleaning. (The posted rule technically runs to the end time.)",
  clear: "No street cleaning during your stay.",
  opens: "Cleaning ends before you leave, but it's a longer wait or hasn't started yet.",
  blocked: "Cleaning runs past when you'd leave. Avoid.",
};
const PAGE = 40;
const MAX_WAIT = 45; // longest worthwhile wait for a halfway mark (matches asp.py)
const ME = -1; // state.place value for "near my current location"

const $ = (s) => document.querySelector(s);
const state = {
  view: "uws",
  date: todayNY(),
  arrive: 450,
  leave: 720,
  now: false, // true while the times track the current clock
  max: 110,
  area: null,
  place: 0,
  here: null, // { lat, lon } from geolocation
  radius: 400,
  tab: "clear",
  shown: PAGE,
};
let DATA, map, ring, meMarker, markers = new Map(), pins = [], rows = [];

// ---------------------------------------------------------------- time + distance helpers
function todayNY() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
}
function nowMinutesNY() {
  const [h, m] = new Intl.DateTimeFormat("en-GB", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .format(new Date()).split(":").map(Number);
  return h * 60 + m;
}
function addDays(iso, n) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
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
const hhmm = (min) => `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;
const ordinal = (n) => n + (n % 100 >= 11 && n % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] || "th");

function meters(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180, dLat = (lat2 - lat1) * r, dLon = (lon2 - lon1) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}
// Straight-line distance inflated for the street grid, at ~80 m/min. Matches asp.py walk_min().
const walkMin = (m) => Math.max(1, Math.round((m * 1.25) / 80));
const mePlace = () => state.here && { name: "You", kind: "Your current location", type: "me", ...state.here };
const place = () => (state.place === ME ? mePlace() : DATA.places[state.place]);

// ---------------------------------------------------------------- street-cleaning logic
function statusOf(b, iso) {
  if (DATA.holidays[iso]) return { kind: "clear" };
  const wd = weekday(iso), { arrive, leave } = state;
  const today = b.s.filter(([days]) => days.includes(wd));
  const hits = today.filter(([, s, e]) => s < leave && e > arrive);
  if (!hits.length) return { kind: "clear", swept: today.some(([, , e]) => e <= arrive) };
  const free = Math.max(...hits.map(([, , e]) => e));
  if (free >= leave) return { kind: "blocked", until: free };
  for (const [, s, e] of hits) {
    const mid = Math.floor((s + e) / 2);
    if (e === free && mid - arrive >= 0 && mid - arrive <= MAX_WAIT) return { kind: "wait", at: mid, start: s, end: e, wait: mid - arrive };
  }
  // Already past the halfway mark: move-in has started, so it's a scramble until the posted end.
  const late = hits.find(([, s, e]) => s <= arrive && arrive < e && arrive > (s + e) / 2);
  return { kind: "opens", free, since: late && Math.floor((late[1] + late[2]) / 2) };
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

// Blocks within `radius` of p, nearest first, with status for the chosen date.
function nearPlace(p, radius) {
  return DATA.blocks
    .filter((b) => b.lat != null)
    .map((b) => ({ b, d: meters(p.lat, p.lon, b.lat, b.lon) }))
    .filter((x) => x.d <= radius)
    .sort((a, b) => a.d - b.d)
    .map((x) => ({ ...x, st: statusOf(x.b, state.date) }));
}

// ---------------------------------------------------------------- render
function compute() {
  if (state.view === "places") {
    const p = place();
    rows = p ? nearPlace(p, state.radius) : [];
  } else {
    rows = DATA.blocks
      .filter((b) => !b.near && b.n && b.n <= state.max)
      .map((b) => ({ b, st: statusOf(b, state.date) }));
  }
}

function counts(list) {
  const c = { wait: 0, clear: 0, opens: 0, blocked: 0 };
  list.forEach((x) => c[x.st.kind]++);
  return c;
}
function bar(c, total) {
  const pct = (k) => (total ? (100 * c[k]) / total : 0);
  return `<div class="bar">${KINDS.map((k) => `<span style="width:${pct(k)}%;background:${COLORS[k]}"></span>`).join("")}</div>`;
}

function renderStatus() {
  const iso = state.date, hol = DATA.holidays[iso], c = counts(rows);
  const win = `${state.now ? "now" : fmtTime(state.arrive)}–${fmtTime(state.leave)}`;
  const p = state.view === "places" && place();
  const scope = p ? ` within ~${walkMin(state.radius)} min of <b>${p.type === "me" ? "you" : p.name}</b>` : "";
  const el = $("#status");
  if (state.view === "places" && !p) {
    el.className = "status on";
    el.innerHTML = `<div class="big">Where are you?</div><div class="small">Tap “Park near me now” or pick a location below.</div>`;
  } else if (hol) {
    el.className = "status off";
    el.innerHTML = `<div class="big">Suspended</div><div class="small">${fmtDate(iso)} · ${hol}. Park on either side; meters may still apply.</div>`;
  } else if (!c.wait && !c.opens && !c.blocked) {
    el.className = "status off";
    el.innerHTML = `<div class="big">No cleaning</div><div class="small">${fmtDate(iso)} · every block is clear ${win}${scope}.</div>`;
  } else {
    const best = rows.filter((r) => r.st.kind === "wait").sort((a, b) => a.st.wait - b.st.wait)[0];
    el.className = "status on";
    el.innerHTML = `<div class="big">Cleaning in effect</div>
      <div class="small">${fmtDate(iso)} · <b>${c.clear}</b> of ${rows.length} block faces clear ${win}${scope}</div>
      ${c.wait ? `<div class="prime">⭐ ${c.wait} wait-&amp;-take spot${c.wait > 1 ? "s" : ""} · next move-in ${best.st.wait ? `${fmtTime(best.st.at)} (${best.st.wait} min)` : "now"}</div>` : ""}`;
  }
}

function placeTile(p, i) {
  if (!p) {
    return `<button class="area place me ${state.place === ME ? "on" : ""}" data-place="${ME}">
      <div class="name"><i class="tag me"></i>Near me</div><div class="kind">Tap to use your location</div></button>`;
  }
  const near = nearPlace(p, state.radius), c = counts(near);
  const best = near.find((x) => x.st.kind === "clear");
  return `<button class="area place ${p.type === "me" ? "me" : ""} ${state.place === i ? "on" : ""}" data-place="${i}">
    <div class="name"><i class="tag ${p.type}"></i>${p.type === "me" ? "Near me" : p.name}</div>
    <div class="kind">${p.kind}</div>
    <div><span class="num">${c.clear}</span> <span class="of">clear of ${near.length}</span></div>
    <div class="near">${c.wait ? `⭐ ${c.wait} wait &amp; take · ` : ""}${best ? `closest clear ${walkMin(best.d)} min` : "no clear block in range"}</div>
    ${bar(c, near.length)}
  </button>`;
}

function renderTiles() {
  if (state.view === "places") {
    $("#areas").innerHTML = placeTile(mePlace(), ME) + DATA.places.map((p, i) => placeTile(p, i)).join("");
    return;
  }
  $("#areas").innerHTML = AREAS.filter(([lo]) => lo <= state.max).map(([lo, hi, name, range], i) => {
    const r = rows.filter((x) => x.b.n >= lo && x.b.n <= hi), c = counts(r);
    return `<button class="area ${state.area === i ? "on" : ""}" data-area="${i}">
      <div class="name">${name} · ${range}</div>
      <div><span class="num">${c.clear}</span> <span class="of">clear of ${r.length}</span></div>
      ${c.wait ? `<div class="near">⭐ ${c.wait} wait &amp; take</div>` : ""}
      ${bar(c, r.length)}
    </button>`;
  }).join("");
}

function visibleRows() {
  let r = rows.filter((x) => x.st.kind === state.tab);
  if (state.view === "uws" && state.area !== null) {
    const [lo, hi] = AREAS[state.area];
    r = r.filter((x) => x.b.n >= lo && x.b.n <= hi);
  }
  const near = state.view === "places";
  if (state.tab === "wait") r.sort((a, b) => (a.st.wait + (near ? walkMin(a.d) : 0)) - (b.st.wait + (near ? walkMin(b.d) : 0)) || (a.b.n || 0) - (b.b.n || 0));
  else if (!near && state.tab === "opens") r.sort((a, b) => a.b.n - b.b.n || a.st.free - b.st.free);
  return r; // otherwise uws: nearest-to-61st first (data.json order); places: nearest first
}

function badge(b, st) {
  if (st.kind === "wait") return `<span class="badge wait">${st.wait ? `be there by ${fmtTime(st.at)} (${st.wait} min)` : "move in now"} → yours till ${nextCleaning(b, state.date, st.end)}</span>`;
  if (st.kind === "clear") return `<span class="badge clear">${st.swept ? "swept earlier" : "clear"}</span>`;
  if (st.kind === "opens") return `<span class="badge opens">${st.since ? `move-in since ${fmtTime(st.since)} · competitive` : `from ${fmtTime(st.free)}`}</span>`;
  return `<span class="badge blocked">until ${fmtTime(st.until)}</span>`;
}

function renderList() {
  const r = visibleRows();
  $("#tab-hint").textContent = HINTS[state.tab];
  const list = $("#list");
  if (!r.length) {
    list.innerHTML = `<li class="empty">${state.tab === "wait"
      ? `No cleaning window hits its halfway mark within ${MAX_WAIT} min of ${fmtTime(state.arrive)}. Try the Clear tab, or an earlier arrival.`
      : "Nothing here for this window."}</li>`;
    return;
  }
  list.innerHTML = r.slice(0, state.shown).map(({ b, st, d }) => `
    <li class="item" data-i="${DATA.blocks.indexOf(b)}">
      <div class="st">${d != null ? `${walkMin(d)}<div class="meta">min</div>` : b.n}</div>
      <div>
        <div class="blk">${b.on}</div>
        <div class="meta">${b.from} → ${b.to} · ${sideText(b.side)}${st.kind === "wait" ? "" : ` · next ${nextCleaning(b, state.date, state.leave)}`}</div>
        <div style="margin-top:4px">${badge(b, st)}</div>
      </div>
      <a class="go" href="${dirUrl(b)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">Go ↗</a>
    </li>`).join("") +
    (r.length > state.shown ? `<li><button class="more" id="more">Show ${Math.min(PAGE, r.length - state.shown)} more of ${r.length - state.shown}</button></li>` : "");
}

function popupHtml(b, st) {
  const label = {
    wait: `⭐ Move in at ${fmtTime(st.at)}${st.wait ? ` (${st.wait} min)` : " (now)"}, halfway through ${fmtTime(st.start)}–${fmtTime(st.end)} cleaning; then yours till ${nextCleaning(b, state.date, st.end)}`,
    clear: "Clear for your stay",
    opens: st.since ? `Move-in started ${fmtTime(st.since)}; competitive until ${fmtTime(st.free)}` : `Opens at ${fmtTime(st.free)}`,
    blocked: `Cleaning until ${fmtTime(st.until)}`,
  }[st.kind];
  return `<b>${b.on}</b><br>${b.from} → ${b.to} · ${sideText(b.side)}<br>
    <span style="color:${COLORS[st.kind]};font-weight:600">${label}</span><br>
    <span style="color:#64748b">${schedText(b)}</span><br>
    ${st.kind === "wait" ? "" : `Next: ${nextCleaning(b, state.date, state.leave)}<br>`}
    <a href="${dirUrl(b)}" target="_blank" rel="noopener">Directions ↗</a>`;
}

function renderMap() {
  markers.forEach((m) => m.remove());
  markers.clear();
  rows.forEach(({ b, st }) => {
    if (b.lat == null) return;
    const big = st.kind === "wait" || st.kind === "clear";
    const m = L.circleMarker([b.lat, b.lon], {
      radius: st.kind === "wait" ? 8 : big ? 6 : 5, weight: st.kind === "wait" ? 2 : 1, color: "#fff",
      fillColor: COLORS[st.kind], fillOpacity: st.kind === "blocked" ? 0.55 : 0.9,
    }).bindPopup(() => popupHtml(b, st)).addTo(map);
    if (st.kind === "wait") m.bringToFront();
    markers.set(DATA.blocks.indexOf(b), m);
  });
  if (ring) ring.remove();
  ring = null;
  const p = state.view === "places" && place();
  if (p) ring = L.circle([p.lat, p.lon], { radius: state.radius, color: "#7c3aed", weight: 2, dashArray: "6 6", fill: false }).addTo(map);
  pins.forEach((pin, i) => pin.setIcon(pinIcon(DATA.places[i], i)));
  if (meMarker) meMarker.setIcon(pinIcon(mePlace(), ME));
}

function pinIcon(p, i) {
  const on = state.view === "places" && state.place === i;
  const label = p.type === "me" ? "●" : i + 1;
  return L.divIcon({ className: "", html: `<div class="pin ${p.type} ${on ? "on" : ""}"><span>${label}</span></div>`, iconSize: [30, 30], iconAnchor: [15, 30] });
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
  document.body.className = `v-${state.view}`;
  renderStatus();
  renderTiles();
  renderList();
  renderMap();
  renderFooter();
  const sel = (q, attr, val) => document.querySelectorAll(q).forEach((b) => b.classList.toggle("on", b.dataset[attr] === val));
  sel("#tabs button", "tab", state.tab);
  sel("#view-seg button", "view", state.view);
  sel("#radius-seg button", "r", String(state.radius));
  const dayIdx = state.now ? "now" : state.date === todayNY() ? "0" : state.date === addDays(todayNY(), 1) ? "1" : null;
  sel("#day-seg button", "day", dayIdx);
  $("#date").value = state.date;
  $("#arrive").value = hhmm(state.arrive);
  $("#leave").value = hhmm(state.leave);
  if (fit) fitView();
}

function fitView() {
  if (ring) { map.fitBounds(ring.getBounds(), { padding: [20, 20] }); return; }
  if (state.view === "places") return;
  const pts = rows.filter(({ b }) => b.lat != null && (state.area === null
    ? b.n <= Math.min(state.max, 110) : b.n >= AREAS[state.area][0] && b.n <= AREAS[state.area][1]))
    .map(({ b }) => [b.lat, b.lon]);
  if (pts.length) map.fitBounds(pts, { padding: [20, 20] });
}

// ---------------------------------------------------------------- actions
function setNow() {
  state.now = true;
  state.date = todayNY();
  state.arrive = Math.floor(nowMinutesNY() / 5) * 5;
  if (state.leave <= state.arrive + 60) state.leave = Math.min(state.arrive + 180, 23 * 60 + 55);
}
// Best tab for the current results: wait & take if any, otherwise clear.
function bestTab() {
  compute();
  state.tab = rows.some((r) => r.st.kind === "wait") ? "wait" : "clear";
}

function locateMe(then) {
  if (!navigator.geolocation) { alert("Location isn't available in this browser."); return; }
  const btn = $("#near-me");
  btn.disabled = true;
  btn.textContent = "📍 Finding you…";
  navigator.geolocation.getCurrentPosition((pos) => {
    state.here = { lat: pos.coords.latitude, lon: pos.coords.longitude };
    if (meMarker) meMarker.remove();
    meMarker = L.marker([state.here.lat, state.here.lon], { icon: pinIcon(mePlace(), ME), zIndexOffset: 2000 })
      .bindTooltip("You", { direction: "top", offset: [0, -28] }).addTo(map);
    btn.disabled = false;
    btn.textContent = "📍 Park near me now";
    then();
  }, (err) => {
    btn.disabled = false;
    btn.textContent = "📍 Park near me now";
    alert(`Couldn't get your location (${err.message}). Pick one of our locations instead.`);
    Object.assign(state, { view: "places", place: Math.max(state.place, 0) });
    render({ fit: true });
  }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 });
}

function parkNearMeNow() {
  locateMe(() => {
    Object.assign(state, { view: "places", place: ME, shown: PAGE });
    setNow();
    bestTab();
    render({ fit: true });
  });
}

function selectPlace(i) {
  if (i === ME && !state.here) { parkNearMeNow(); return; }
  Object.assign(state, { view: "places", place: i, shown: PAGE });
  if (state.now) setNow();
  save();
  render({ fit: true });
}

// ---------------------------------------------------------------- wire up
function save() {
  const { arrive, leave, max, view, radius } = state;
  const p = state.place === ME ? 0 : state.place; // don't persist "me": location goes stale
  localStorage.setItem("uws-parking", JSON.stringify({ arrive, leave, max, view, place: p, radius }));
}

async function init() {
  Object.assign(state, JSON.parse(localStorage.getItem("uws-parking") || "{}"));
  $("#max").value = state.max;
  $("#max-label").textContent = ordinal(state.max);

  map = L.map("map", { zoomControl: false }).setView([40.785, -73.975], 14);
  L.control.zoom({ position: "topright" }).addTo(map);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);

  DATA = await (await fetch("data.json", { cache: "no-cache" })).json();

  // Deep links: ?place=4 (1-based) or ?place=me, ?now=1, ?view=uws, ?radius=800
  const q = new URLSearchParams(location.search);
  const wantMe = q.get("place") === "me";
  if (q.has("place") && !wantMe) Object.assign(state, { view: "places", place: Number(q.get("place")) - 1 });
  if (q.has("view")) state.view = q.get("view") === "places" ? "places" : "uws";
  if (q.has("radius")) state.radius = Number(q.get("radius")) || state.radius;
  if (!(state.place >= 0 && state.place < DATA.places.length)) state.place = 0;
  if (q.has("date")) state.date = q.get("date");
  if (q.has("arrive")) state.arrive = toMin(q.get("arrive"));
  if (q.has("leave")) state.leave = toMin(q.get("leave"));
  if (q.has("now")) setNow();
  if (q.has("now") || q.has("arrive")) bestTab();

  pins = DATA.places.map((p, i) => L.marker([p.lat, p.lon], { icon: pinIcon(p, i), zIndexOffset: 1000 })
    .bindTooltip(`<b>${p.name}</b><br>${p.kind}`, { direction: "top", offset: [0, -28] })
    .on("click", () => selectPlace(i))
    .addTo(map));
  render({ fit: true });
  if (wantMe) parkNearMeNow();

  $("#near-me").addEventListener("click", parkNearMeNow);
  $("#view-seg").addEventListener("click", (e) => {
    const v = e.target.dataset.view;
    if (!v || v === state.view) return;
    Object.assign(state, { view: v, shown: PAGE });
    save(); render({ fit: true });
  });
  $("#radius-seg").addEventListener("click", (e) => {
    const r = e.target.dataset.r;
    if (!r) return;
    Object.assign(state, { radius: Number(r), shown: PAGE });
    save(); render({ fit: true });
  });
  $("#day-seg").addEventListener("click", (e) => {
    const d = e.target.dataset.day;
    if (d === undefined) return;
    if (d === "now") { setNow(); bestTab(); }
    else { state.now = false; state.date = addDays(todayNY(), Number(d)); }
    render();
  });
  $("#date").addEventListener("change", (e) => { if (e.target.value) { state.now = false; state.date = e.target.value; render(); } });
  $("#arrive").addEventListener("change", (e) => { state.now = false; state.arrive = toMin(e.target.value); save(); render(); });
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
    if (btn.dataset.place !== undefined) { selectPlace(Number(btn.dataset.place)); return; }
    const i = Number(btn.dataset.area);
    state.area = state.area === i ? null : i;
    state.shown = PAGE;
    render({ fit: true });
  });
  $("#tabs").addEventListener("click", (e) => {
    const t = e.target.closest("button")?.dataset.tab;
    if (!t) return;
    state.tab = t;
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
  // Keep "Now" honest if the page stays open.
  setInterval(() => { if (state.now) { setNow(); render(); } }, 60000);
}

init();

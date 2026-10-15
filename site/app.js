// Uptown Parking dashboard. Data: data.json (built by `python3 asp.py export`).
// The street-cleaning logic lives in logic.js (pure, shared with the tests); this file is the UI.
// Views: "uws" (West Side blocks by street range) and "places" (blocks within a walk of a location or of you).
const LG = ParkingLogic;
const { DAY, fmtTime, addDays, weekday, isValidDate, parseTime, validateWindow, meters, walkMin, yearCovered } = LG;

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
  wait: "Suggested arrival is the halfway mark of the cleaning window, when cars typically start moving back in on these competitive blocks. The posted restriction still runs to the ASP end time (NYC311 KA-01011). Vacancy isn't measured.",
  clear: "No street cleaning during your stay. Meters, No Standing, hydrants and other signs still apply; vacancy isn't measured.",
  opens: "ASP ends before you leave, but the halfway mark is too far off or already passed (then it's a competitive move-in until the end time).",
  blocked: "ASP runs past when you'd leave. Avoid.",
};
const PAGE = 40;
const ME = -1;            // state.place value for "near my current location"
const RADII = [250, 400, 800];
const MAX_STREET = 185, MIN_MAX_STREET = 65;

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ordinal = (n) => n + (n % 100 >= 11 && n % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] || "th");
const hhmm = (min) => `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;

const state = {
  view: "uws",
  date: todayNY(),
  arrive: 450,
  leave: 720,
  now: false,       // true while the times track the current clock
  max: 110,
  area: null,
  place: 0,
  here: null,       // { lat, lon, accuracy } from geolocation
  geoError: null,
  radius: 400,
  tab: "clear",
  shown: PAGE,
  problem: null,    // message when the date/window can't be evaluated
  linkProblem: null, // a deep-link parameter that was ignored
};
let DATA, map, ring, accRing, meMarker, pins = [], rows = [];
const markers = new Map(); // block index -> { marker, st }

// ---------------------------------------------------------------- time helpers (New York clock)
function todayNY() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
}
function nowMinutesNY() {
  const [h, m] = new Intl.DateTimeFormat("en-GB", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .format(new Date()).split(":").map(Number);
  return h * 60 + m;
}
function fmtDate(iso, opts = { weekday: "long", month: "short", day: "numeric" }) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", { ...opts, timeZone: "UTC" });
}
function fmtStamp(iso) {
  const t = iso ? new Date(iso) : null;
  return t && !Number.isNaN(t.getTime()) ? t.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : "unknown";
}
function daysSince(iso) {
  const t = iso ? new Date(iso) : null;
  return t && !Number.isNaN(t.getTime()) ? Math.max(0, Math.floor((Date.now() - t.getTime()) / 86400000)) : null;
}

// ---------------------------------------------------------------- places and status
const mePlace = () => state.here && { name: "You", kind: "Your current location", type: "me", ...state.here };
const place = () => (state.place === ME ? mePlace() : DATA.places[state.place]);
const statusOf = (b) => LG.statusOf(b.s, state.date, state.arrive, state.leave, DATA.holidays, DATA.max_wait);
const nextLabel = (b, after) => LG.nextCleaningLabel(LG.nextCleaningAt(b.s, state.date, after, DATA.holidays), state.date);
const schedText = (b) => b.s.map(([d, s, e]) => `${d.map((x) => DAY[x]).join("/")} ${fmtTime(s)}–${fmtTime(e)}`).join("; ");
const sideText = (s) => ({ N: "north", S: "south", E: "east", W: "west" }[s] || s) + " side";
const dirUrl = (b) => b.lat != null
  ? `https://www.google.com/maps/dir/?api=1&destination=${b.lat},${b.lon}`
  : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${b.on} & ${b.from}, Manhattan, NY`)}`;

// Blocks within `radius` of p, nearest first, with status for the chosen stay.
function nearPlace(p, radius) {
  return DATA.blocks
    .filter((b) => b.lat != null)
    .map((b) => ({ b, d: meters(p.lat, p.lon, b.lat, b.lon) }))
    .filter((x) => x.d <= radius)
    .sort((a, b) => a.d - b.d)
    .map((x) => ({ ...x, st: statusOf(x.b) }));
}

function compute() {
  state.problem = !isValidDate(state.date) ? "Pick a valid date." : validateWindow(state.arrive, state.leave);
  if (state.problem) { rows = []; return; }
  if (state.view === "places") {
    const p = place();
    rows = p ? nearPlace(p, state.radius) : [];
  } else {
    rows = DATA.blocks
      .filter((b) => !b.near && b.n && b.n <= state.max)
      .map((b) => ({ b, st: statusOf(b) }));
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

// ---------------------------------------------------------------- render: status card and notices
function renderStatus() {
  const el = $("#status"), iso = state.date, c = counts(rows);
  const win = `${state.now ? "now" : fmtTime(state.arrive)}–${fmtTime(state.leave)}`;
  const p = state.view === "places" && place();
  const scope = p ? ` within ~${walkMin(state.radius)} min of <b>${p.type === "me" ? "you" : esc(p.name)}</b>` : ` from W 61st to ${ordinal(state.max)} St`;
  const set = (cls, big, small) => { el.className = `status ${cls}`; el.innerHTML = `<div class="big">${big}</div><div class="small">${small}</div>`; };

  if (state.problem) return set("err", "Check your times", esc(state.problem));
  if (state.view === "places" && !p) return set("on", "Where are you?", "Tap “Park near me now” or pick a location below.");
  if (!rows.length) return set("err", "No blocks in range", `No block faces with map coordinates${scope}. Try a larger radius or another location.`);
  const hol = DATA.holidays[iso];
  if (hol) return set("off", "ASP suspended", `${fmtDate(iso)} · ${esc(hol)}. Street cleaning rules don't apply; meters and other signs may.`);
  if (!c.wait && !c.opens && !c.blocked) return set("off", "No ASP", `${fmtDate(iso)} · none of the ${rows.length} block faces${scope} has street cleaning ${win}.`);
  const best = rows.filter((r) => r.st.kind === "wait").sort((a, b) => a.st.wait - b.st.wait)[0];
  el.className = "status on";
  el.innerHTML = `<div class="big">ASP in effect</div>
    <div class="small">${fmtDate(iso)} · <b>${c.clear}</b> of ${rows.length} block faces have no ASP ${win}${scope}</div>
    ${c.wait ? `<div class="prime">⭐ ${c.wait} wait-&amp;-take spot${c.wait > 1 ? "s" : ""} · suggested arrival ${fmtTime(best.st.at)}${best.st.wait ? ` (${best.st.wait} min)` : " (now)"} · ASP ends ${fmtTime(best.st.end)}</div>` : ""}`;
}

function renderNotices() {
  const notes = [];
  const age = daysSince(DATA.source_fetched_at);
  if (age === null) notes.push(["bad", "Sign data age is unknown; treat results with care."]);
  else if (age > DATA.stale_days) notes.push(["bad", `Sign data is ${age} days old; the daily refresh may be failing. Posted signs win.`]);
  if (isValidDate(state.date) && !yearCovered(DATA.holiday_years, state.date)) {
    notes.push(["bad", `Holiday calendar for ${state.date.slice(0, 4)} isn't on file: suspensions are unknown and ASP is assumed in effect. Add data/asp_suspensions_${state.date.slice(0, 4)}.csv from NYC DOT.`]);
  }
  const today = todayNY(), nextYear = Number(today.slice(0, 4)) + 1;
  if (today.slice(5, 7) === "12" && !DATA.holiday_years.includes(nextYear)) {
    notes.push(["warn", `The ${nextYear} holiday calendar isn't on file yet. Add it before Jan 1 (see the README).`]);
  }
  if (state.linkProblem) notes.push(["warn", esc(state.linkProblem)]);
  if (state.geoError) notes.push(["warn", esc(state.geoError)]);
  if (state.view === "places" && state.place === ME && state.here && state.here.accuracy > state.radius) {
    notes.push(["warn", `GPS accuracy is ±${Math.round(state.here.accuracy)} m, wider than your ${state.radius} m search. Results may be off; try a larger radius or wait for a better fix.`]);
  }
  $("#notice").innerHTML = notes.map(([cls, text]) => `<div class="n ${cls}">${text}</div>`).join("");
}

// ---------------------------------------------------------------- render: tiles, list, map
function placeTile(p, i) {
  if (!p) {
    return `<button class="area place me ${state.place === ME ? "on" : ""}" data-place="${ME}">
      <div class="name"><i class="tag me"></i>Near me</div><div class="kind">Tap to use your location</div></button>`;
  }
  const near = state.problem ? [] : nearPlace(p, state.radius), c = counts(near);
  const best = near.find((x) => x.st.kind === "clear");
  const acc = p.type === "me" && p.accuracy ? ` · GPS ±${Math.round(p.accuracy)} m` : "";
  return `<button class="area place ${p.type === "me" ? "me" : ""} ${state.place === i ? "on" : ""}" data-place="${i}">
    <div class="name"><i class="tag ${p.type}"></i>${p.type === "me" ? "Near me" : esc(p.name)}</div>
    <div class="kind">${esc(p.kind)}${acc}</div>
    <div><span class="num">${c.clear}</span> <span class="of">no ASP of ${near.length}</span></div>
    <div class="near">${c.wait ? `⭐ ${c.wait} wait &amp; take · ` : ""}${best ? `closest no-ASP ${walkMin(best.d)} min` : near.length ? "no no-ASP block in range" : "no blocks in range"}</div>
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
      <div><span class="num">${c.clear}</span> <span class="of">no ASP of ${r.length}</span></div>
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
  const cost = (x) => x.st.wait + (near ? walkMin(x.d) : 0);
  if (state.tab === "wait") r.sort((a, b) => cost(a) - cost(b) || (a.b.n || 0) - (b.b.n || 0));
  else if (!near && state.tab === "opens") r.sort((a, b) => a.b.n - b.b.n || a.st.free - b.st.free);
  return r; // otherwise uws: nearest-to-61st first (data.json order); places: nearest first
}

function badge(st) {
  if (st.kind === "wait") return `<span class="badge wait">⭐ arrive by ${fmtTime(st.at)}${st.wait ? ` (${st.wait} min)` : " (now)"} · ASP ends ${fmtTime(st.end)}</span>`;
  if (st.kind === "clear") return `<span class="badge clear">${st.swept ? "swept earlier · no ASP" : "no ASP"}</span>`;
  if (st.kind === "opens") return `<span class="badge opens">${st.since ? `move-in since ${fmtTime(st.since)} · competitive · ` : ""}ASP ends ${fmtTime(st.free)}</span>`;
  return `<span class="badge blocked">ASP until ${fmtTime(st.until)}</span>`;
}

function renderList() {
  const list = $("#list");
  $("#tab-hint").textContent = HINTS[state.tab];
  if (state.problem) { list.innerHTML = `<li class="empty">${esc(state.problem)}</li>`; return; }
  const r = visibleRows();
  if (!r.length) {
    list.innerHTML = `<li class="empty">${state.tab === "wait"
      ? `No cleaning window hits its halfway mark within ${DATA.max_wait} min of ${fmtTime(state.arrive)}. Try the No ASP tab, or an earlier arrival.`
      : "Nothing here for this stay."}</li>`;
    return;
  }
  list.innerHTML = r.slice(0, state.shown).map(({ b, st, d }) => `
    <li class="item" data-i="${b.i}">
      <div class="st">${d != null ? `${walkMin(d)}<div class="meta">min</div>` : b.n}</div>
      <div class="body">
        <div class="blk">${esc(b.on)}</div>
        <div class="meta">${esc(b.from)} → ${esc(b.to)} · ${sideText(b.side)} · next ASP ${nextLabel(b, st.kind === "wait" ? st.end : state.leave)}${b.lat == null ? " · no map pin" : ""}</div>
        <div class="tags">${badge(st)}</div>
      </div>
      <a class="go" href="${dirUrl(b)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">Go ↗</a>
    </li>`).join("") +
    (r.length > state.shown ? `<li><button class="more" id="more">Show ${Math.min(PAGE, r.length - state.shown)} more of ${r.length - state.shown}</button></li>` : "");
}

function popupHtml(b, st) {
  const label = {
    wait: `⭐ Suggested arrival ${fmtTime(st.at)}${st.wait ? ` (${st.wait} min)` : " (now)"}: halfway through the ${fmtTime(st.start)}–${fmtTime(st.end)} cleaning. ASP ends ${fmtTime(st.end)}.`,
    clear: "No ASP during your stay",
    opens: st.since ? `Move-in started ${fmtTime(st.since)}; competitive until ASP ends ${fmtTime(st.free)}` : `ASP ends ${fmtTime(st.free)}`,
    blocked: `ASP until ${fmtTime(st.until)}`,
  }[st.kind];
  return `<b>${esc(b.on)}</b><br>${esc(b.from)} → ${esc(b.to)} · ${sideText(b.side)}<br>
    <span style="color:${COLORS[st.kind]};font-weight:600">${label}</span><br>
    <span style="color:#64748b">${schedText(b)}</span><br>
    Next ASP: ${nextLabel(b, st.kind === "wait" ? st.end : state.leave)}<br>
    <a href="${dirUrl(b)}" target="_blank" rel="noopener">Directions ↗</a>`;
}

const markerStyle = (st) => ({
  radius: st.kind === "wait" ? 8 : st.kind === "clear" ? 6 : 5,
  weight: st.kind === "wait" ? 2 : 1, color: "#fff",
  fillColor: COLORS[st.kind], fillOpacity: st.kind === "blocked" ? 0.55 : 0.9,
});

// Markers are updated in place so an open popup survives the once-a-minute "Now" refresh.
function renderMap() {
  const wanted = new Map();
  rows.forEach(({ b, st }) => { if (b.lat != null) wanted.set(b.i, { b, st }); });
  for (const [i, m] of markers) if (!wanted.has(i)) { m.marker.remove(); markers.delete(i); }
  for (const [i, { b, st }] of wanted) {
    let m = markers.get(i);
    if (!m) {
      m = { marker: L.circleMarker([b.lat, b.lon], markerStyle(st)), st };
      m.marker.bindPopup(() => popupHtml(b, markers.get(i).st)).addTo(map);
      markers.set(i, m);
    } else {
      m.st = st;
      m.marker.setStyle(markerStyle(st));
      if (m.marker.isPopupOpen()) m.marker.setPopupContent(popupHtml(b, st));
    }
    if (st.kind === "wait") m.marker.bringToFront();
  }
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
  const tomorrow = isValidDate(state.date) ? addDays(state.date, 1) : null;
  const tHol = tomorrow && DATA.holidays[tomorrow];
  $("#upcoming").innerHTML = tomorrow
    ? `<b>${fmtDate(tomorrow, { weekday: "long" })}:</b> ${tHol ? `suspended (${esc(tHol)})` : weekday(tomorrow) === 6 ? "no ASP (Sunday)" : yearCovered(DATA.holiday_years, tomorrow) ? "ASP in effect" : "ASP assumed in effect (calendar not on file)"}` +
      ` · <b>Next holiday:</b> ${next ? `${fmtDate(next, { weekday: "short", month: "short", day: "numeric" })} (${esc(DATA.holidays[next])})` : "none on file"}`
    : "";
  const nc = DATA.stats && DATA.stats.faces_without_coords;
  $("#generated").textContent = `Sign data as of ${fmtStamp(DATA.source_fetched_at)} · site built ${fmtStamp(DATA.built_at)} · ${DATA.stats.faces} block faces` +
    (nc ? ` (${nc} without map coordinates appear in lists only)` : "");
}

function render({ fit = false } = {}) {
  compute();
  document.body.className = `v-${state.view}`;
  renderStatus();
  renderNotices();
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
  $("#date").value = isValidDate(state.date) ? state.date : "";
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
  // An explicit stay is kept; only a leave time that is now in the past gets a default.
  if (state.leave <= state.arrive) state.leave = Math.min(state.arrive + 180, 23 * 60 + 59);
}
// Best tab for the current results: wait & take if any, otherwise no ASP.
function bestTab() {
  compute();
  state.tab = rows.some((r) => r.st.kind === "wait") ? "wait" : "clear";
}

function locateMe(then) {
  const btn = $("#near-me");
  if (!navigator.geolocation) { state.geoError = "Location isn't available in this browser."; render(); return; }
  btn.disabled = true;
  btn.textContent = "📍 Finding you…";
  navigator.geolocation.getCurrentPosition((pos) => {
    state.here = { lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy };
    state.geoError = null;
    if (meMarker) meMarker.remove();
    if (accRing) accRing.remove();
    meMarker = L.marker([state.here.lat, state.here.lon], { icon: pinIcon(mePlace(), ME), zIndexOffset: 2000 })
      .bindTooltip("You", { direction: "top", offset: [0, -28] }).addTo(map);
    if (pos.coords.accuracy) {
      accRing = L.circle([state.here.lat, state.here.lon], { radius: pos.coords.accuracy, color: "#2563eb", weight: 1, fillOpacity: 0.08 }).addTo(map);
    }
    btn.disabled = false;
    btn.textContent = "📍 Park near me now";
    then();
  }, (err) => {
    btn.disabled = false;
    btn.textContent = "📍 Park near me now";
    state.geoError = `Couldn't get your location (${err.message}). Pick one of our locations instead.`;
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

// ---------------------------------------------------------------- persistence and deep links
function save() {
  const { arrive, leave, max, view, radius } = state;
  const p = state.place === ME ? 0 : state.place; // don't persist "me": location goes stale
  localStorage.setItem("uws-parking", JSON.stringify({ arrive, leave, max, view, place: p, radius }));
}

const inRange = (v, lo, hi) => Number.isInteger(v) && v >= lo && v <= hi;

function restore() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("uws-parking") || "{}") || {}; } catch (e) { saved = {}; }
  if (inRange(saved.arrive, 0, 1439)) state.arrive = saved.arrive;
  if (inRange(saved.leave, 0, 1439)) state.leave = saved.leave;
  if (inRange(saved.max, MIN_MAX_STREET, MAX_STREET)) state.max = saved.max;
  if (RADII.includes(saved.radius)) state.radius = saved.radius;
  if (saved.view === "places" || saved.view === "uws") state.view = saved.view;
  if (inRange(saved.place, 0, DATA.places.length - 1)) state.place = saved.place;
  if (validateWindow(state.arrive, state.leave)) { state.arrive = 450; state.leave = 720; }
}

// ?place=4 (1-based) or ?place=me · ?now=1 · ?view=uws|places · ?radius=250|400|800 · ?date=YYYY-MM-DD · ?arrive=9:00 · ?leave=12:00
function applyQuery(q) {
  const ignored = [];
  if (q.has("view")) state.view = q.get("view") === "places" ? "places" : "uws";
  const r = Number(q.get("radius"));
  if (q.has("radius")) { if (inRange(r, 100, 2000)) state.radius = r; else ignored.push("radius"); }
  const wantMe = q.get("place") === "me";
  const pi = Number(q.get("place")) - 1;
  if (q.has("place") && !wantMe) { if (inRange(pi, 0, DATA.places.length - 1)) Object.assign(state, { view: "places", place: pi }); else ignored.push("place"); }
  if (q.has("date")) { if (isValidDate(q.get("date"))) { state.date = q.get("date"); state.now = false; } else ignored.push("date"); }
  const a = parseTime(q.get("arrive") || ""), l = parseTime(q.get("leave") || "");
  if (q.has("arrive")) { if (a !== null) { state.arrive = a; state.now = false; } else ignored.push("arrive"); }
  if (q.has("leave")) { if (l !== null) state.leave = l; else ignored.push("leave"); }
  if (ignored.length) state.linkProblem = `Ignored invalid link parameter${ignored.length > 1 ? "s" : ""}: ${ignored.join(", ")}.`;
  if (q.has("now")) setNow();
  if (q.has("now") || (q.has("arrive") && a !== null)) bestTab();
  return wantMe;
}

// ---------------------------------------------------------------- init
function showDataProblem(msg) {
  document.body.className = "v-uws";
  $("#status").className = "status err";
  $("#status").innerHTML = `<div class="big">Data unavailable</div><div class="small">${esc(msg)} Reload to try again; if it persists, the daily build is failing (see the repo's Actions tab).</div>`;
  ["#areas", "#tabs", "#tab-hint", "#list", "#upcoming", "#generated"].forEach((s) => { $(s).innerHTML = ""; });
}

async function init() {
  map = L.map("map", { zoomControl: false }).setView([40.785, -73.975], 14);
  L.control.zoom({ position: "topright" }).addTo(map);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);

  try {
    const res = await fetch("data.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(`data.json: HTTP ${res.status}`);
    DATA = await res.json();
    if (!DATA || DATA.schema !== 1 || !Array.isArray(DATA.blocks) || !DATA.blocks.length || !Array.isArray(DATA.places)) {
      throw new Error("data.json is empty or has an unexpected format.");
    }
  } catch (e) {
    showDataProblem(e.message);
    return;
  }
  DATA.holidays = DATA.holidays || {};
  DATA.holiday_years = DATA.holiday_years || [];
  DATA.stats = DATA.stats || { faces: DATA.blocks.length, faces_without_coords: DATA.blocks.filter((b) => b.lat == null).length };
  DATA.max_wait = Number.isInteger(DATA.max_wait) ? DATA.max_wait : LG.MAX_WAIT;
  DATA.stale_days = Number.isInteger(DATA.stale_days) ? DATA.stale_days : 7;
  DATA.blocks.forEach((b, i) => { b.i = i; });

  restore();
  const wantMe = applyQuery(new URLSearchParams(location.search));
  $("#max").value = state.max;
  $("#max-label").textContent = ordinal(state.max);

  pins = DATA.places.map((p, i) => L.marker([p.lat, p.lon], { icon: pinIcon(p, i), zIndexOffset: 1000 })
    .bindTooltip(`<b>${esc(p.name)}</b><br>${esc(p.kind)}`, { direction: "top", offset: [0, -28] })
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
    const r = Number(e.target.dataset.r);
    if (!RADII.includes(r)) return;
    Object.assign(state, { radius: r, shown: PAGE });
    save(); render({ fit: true });
  });
  $("#day-seg").addEventListener("click", (e) => {
    const d = e.target.dataset.day;
    if (d === undefined) return;
    if (d === "now") { setNow(); bestTab(); }
    else { state.now = false; state.date = addDays(todayNY(), Number(d)); }
    render();
  });
  $("#date").addEventListener("change", (e) => {
    if (!isValidDate(e.target.value)) { e.target.value = isValidDate(state.date) ? state.date : ""; return; }
    state.now = false; state.date = e.target.value; render();
  });
  $("#arrive").addEventListener("change", (e) => {
    const v = parseTime(e.target.value);
    if (v === null) { e.target.value = hhmm(state.arrive); return; }
    state.now = false; state.arrive = v; save(); render();
  });
  $("#leave").addEventListener("change", (e) => {
    const v = parseTime(e.target.value);
    if (v === null) { e.target.value = hhmm(state.leave); return; }
    state.leave = v; save(); render();
  });
  $("#max").addEventListener("input", (e) => { $("#max-label").textContent = ordinal(+e.target.value); });
  $("#max").addEventListener("change", (e) => {
    state.max = Math.min(MAX_STREET, Math.max(MIN_MAX_STREET, +e.target.value || 110));
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
    if (!t || !KINDS.includes(t)) return;
    state.tab = t;
    state.shown = PAGE;
    render();
  });
  $("#list").addEventListener("click", (e) => {
    if (e.target.id === "more") { state.shown += PAGE; renderList(); return; }
    const li = e.target.closest(".item");
    const m = li && markers.get(Number(li.dataset.i));
    if (!m) return;
    map.flyTo(m.marker.getLatLng(), 17, { duration: 0.6 });
    m.marker.openPopup();
    if (innerWidth <= 820) window.scrollTo({ top: 0, behavior: "smooth" });
  });
  // Keep "Now" honest if the page stays open; markers update in place so an open popup stays open.
  setInterval(() => { if (state.now) { setNow(); render(); } }, 60000);
}

init();

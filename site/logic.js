/* Uptown Parking - pure street-cleaning logic shared by the dashboard (app.js) and the tests.
 *
 * It mirrors asp.py; both implementations are checked against tests/fixtures/cases.json.
 * No DOM, no globals: every function takes what it needs as arguments.
 * Loaded as a classic <script> in the browser (window.ParkingLogic) and require()'d in Node.
 *
 * A schedule is [days, start, end]: days are Mon=0..Sun=6, start/end are minutes since midnight.
 * Segments with end <= start (overnight signs) are not modeled and are ignored.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ParkingLogic = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const MAX_WAIT = 45; // longest worthwhile wait for a cleaning window's halfway mark (minutes)
  const MINUTES_PER_DAY = 1440;
  const DAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  // ---------------------------------------------------------------- dates and times
  function isValidDate(iso) {
    if (typeof iso !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return false;
    const [y, m, d] = iso.split("-").map(Number);
    const t = new Date(Date.UTC(y, m - 1, d));
    return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d;
  }

  // Mon=0 .. Sun=6, or null for an invalid date.
  function weekday(iso) {
    if (!isValidDate(iso)) return null;
    const [y, m, d] = iso.split("-").map(Number);
    return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
  }

  function addDays(iso, n) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
  }

  // "7:45", "07:45", "7:45AM", "8 am", "13:00" -> minutes since midnight, or null if not a time.
  function parseTime(s) {
    if (typeof s !== "string") return null;
    const m = /^\s*(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?\s*$/i.exec(s);
    if (!m) return null;
    let h = Number(m[1]);
    const min = Number(m[2] || 0);
    const ap = m[3] ? m[3].toUpperCase() : null;
    if (min > 59) return null;
    if (ap) {
      if (h < 1 || h > 12) return null;
      h = ap === "AM" ? (h === 12 ? 0 : h) : (h === 12 ? 12 : h + 12);
    } else if (h > 23) {
      return null;
    }
    return h * 60 + min;
  }

  function fmtTime(min) {
    let h = Math.floor(min / 60);
    const m = min % 60;
    const ap = h < 12 ? "AM" : "PM";
    h = h % 12 || 12;
    return m ? `${h}:${String(m).padStart(2, "0")}${ap}` : `${h}${ap}`;
  }

  // null when the stay is usable, otherwise a message for the user.
  function validateWindow(arrive, leave) {
    if (!Number.isInteger(arrive) || !Number.isInteger(leave)) return "Arrive and leave times are required.";
    if (arrive < 0 || arrive >= MINUTES_PER_DAY || leave < 0 || leave >= MINUTES_PER_DAY) return "Times must be within one day.";
    if (leave <= arrive) return "Leave time must be after arrival.";
    return null;
  }

  // ---------------------------------------------------------------- street-cleaning status
  const has = (obj, key) => !!obj && Object.prototype.hasOwnProperty.call(obj, key);
  const validSeg = (seg) => Array.isArray(seg) && Array.isArray(seg[0]) && Number.isInteger(seg[1]) && Number.isInteger(seg[2]) && seg[2] > seg[1];

  /* Classify one block face for a stay from `arrive` until `leave` on `iso`:
   *   { kind: "clear",   swept }            no ASP overlaps the stay (swept: a window ended before arrival)
   *   { kind: "wait",    at, start, end, wait }
   *                                         WAIT & TAKE: a window's halfway mark is 0..maxWait minutes after
   *                                         arrival. Cars start moving back in then on competitive blocks, so
   *                                         `at` is the suggested arrival. The posted restriction still runs to
   *                                         `end`; the spot is then free of ASP until the next window.
   *   { kind: "opens",   free, since }      ASP ends before you leave, but the halfway mark is too far away or
   *                                         already passed (`since`: move-in started then; competitive)
   *   { kind: "blocked", until }            ASP runs until or past when you leave
   * Throws on an invalid date or window; callers validate first and show the message.
   */
  function statusOf(scheds, iso, arrive, leave, holidays, maxWait) {
    if (maxWait === undefined) maxWait = MAX_WAIT;
    const err = validateWindow(arrive, leave);
    if (err) throw new Error(err);
    const wd = weekday(iso);
    if (wd === null) throw new Error("Invalid date.");
    if (has(holidays, iso)) return { kind: "clear", swept: false };
    const today = scheds.filter((seg) => validSeg(seg) && seg[0].includes(wd));
    const hits = today.filter(([, s, e]) => s < leave && e > arrive);
    if (!hits.length) return { kind: "clear", swept: today.some(([, , e]) => e <= arrive) };
    const free = Math.max(...hits.map(([, , e]) => e));
    if (free >= leave) return { kind: "blocked", until: free };
    for (const [, s, e] of hits) {
      const mid = Math.floor((s + e) / 2);
      if (e === free && mid - arrive >= 0 && mid - arrive <= maxWait) {
        return { kind: "wait", at: mid, start: s, end: e, wait: mid - arrive };
      }
    }
    const late = hits.find(([, s, e]) => s <= arrive && arrive < e && arrive > Math.floor((s + e) / 2));
    return { kind: "opens", free, since: late ? Math.floor((late[1] + late[2]) / 2) : null };
  }

  // Next ASP window at or after `after` minutes today, else on a later day within `horizon` days
  // (holidays skipped). Returns { offset, start } or null.
  function nextCleaningAt(scheds, iso, after, holidays, horizon) {
    if (horizon === undefined) horizon = 14;
    if (!isValidDate(iso)) return null;
    for (let i = 0; i < horizon; i++) {
      const d = addDays(iso, i);
      if (has(holidays, d)) continue;
      const wd = weekday(d);
      const starts = scheds
        .filter((seg) => validSeg(seg) && seg[0].includes(wd) && (i > 0 || seg[1] >= after))
        .map((seg) => seg[1]);
      if (starts.length) return { offset: i, start: Math.min(...starts) };
    }
    return null;
  }

  function nextCleaningLabel(nc, iso) {
    if (!nc) return "none in 2 weeks";
    const when = nc.offset === 0 ? "today" : nc.offset === 1 ? "tomorrow" : DAY[weekday(addDays(iso, nc.offset))];
    return `${when} ${fmtTime(nc.start)}`;
  }

  // ---------------------------------------------------------------- distance
  function meters(lat1, lon1, lat2, lon2) {
    const r = Math.PI / 180, dLat = (lat2 - lat1) * r, dLon = (lon2 - lon1) * r;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
    return 2 * 6371000 * Math.asin(Math.sqrt(h));
  }

  // Straight-line distance inflated for the street grid, at ~80 m/min. Matches asp.py walk_min().
  const walkMin = (m) => Math.max(1, Math.round((m * 1.25) / 80));

  // Is the holiday-suspension calendar for this date's year on file?
  function yearCovered(holidayYears, iso) {
    return Array.isArray(holidayYears) && isValidDate(iso) && holidayYears.includes(Number(iso.slice(0, 4)));
  }

  return {
    MAX_WAIT, DAY, isValidDate, weekday, addDays, parseTime, fmtTime, validateWindow,
    statusOf, nextCleaningAt, nextCleaningLabel, meters, walkMin, yearCovered,
  };
});

// Tests for site/logic.js. Run from the repo root: node --test tests/
// The fixtures are shared with tests/test_asp.py so the browser logic and asp.py cannot drift apart.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const L = require("../site/logic.js");
const FIX = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "cases.json"), "utf8"));

test("parseTime fixtures", () => {
  for (const c of FIX.time_parse) assert.equal(L.parseTime(c.in), c.out, JSON.stringify(c.in));
  assert.equal(L.parseTime(null), null);
  assert.equal(L.parseTime(undefined), null);
});

test("fmtTime fixtures", () => {
  for (const c of FIX.time_format) assert.equal(L.fmtTime(c.in), c.out, String(c.in));
});

test("date fixtures", () => {
  for (const c of FIX.date) {
    assert.equal(L.isValidDate(c.in), c.valid, c.in);
    assert.equal(L.weekday(c.in), c.valid ? c.weekday : null, c.in);
  }
});

test("window fixtures", () => {
  for (const c of FIX.window) assert.equal(L.validateWindow(c.arrive, c.leave) === null, c.ok, JSON.stringify(c));
});

test("statusOf fixtures", () => {
  for (const c of FIX.status) {
    if (c.expect.error) {
      assert.throws(() => L.statusOf(c.scheds, c.date, c.arrive, c.leave, c.holidays, FIX.max_wait), c.name);
      continue;
    }
    const st = L.statusOf(c.scheds, c.date, c.arrive, c.leave, c.holidays, FIX.max_wait);
    assert.deepEqual(st, c.expect, c.name);
  }
  assert.equal(L.MAX_WAIT, FIX.max_wait);
});

test("nextCleaningAt fixtures", () => {
  for (const c of FIX.next_cleaning) {
    assert.deepEqual(L.nextCleaningAt(c.scheds, c.date, c.after, c.holidays), c.expect, c.name);
  }
  assert.equal(L.nextCleaningAt([[[3], 540, 630]], "2026-02-30", 0, {}), null, "invalid date");
});

test("nextCleaningLabel", () => {
  assert.equal(L.nextCleaningLabel({ offset: 0, start: 540 }, "2026-10-15"), "today 9AM");
  assert.equal(L.nextCleaningLabel({ offset: 1, start: 480 }, "2026-10-15"), "tomorrow 8AM");
  assert.equal(L.nextCleaningLabel({ offset: 4, start: 540 }, "2026-10-15"), "Mon 9AM");
  assert.equal(L.nextCleaningLabel(null, "2026-10-15"), "none in 2 weeks");
});

test("addDays crosses month and year boundaries", () => {
  assert.equal(L.addDays("2026-12-31", 1), "2027-01-01");
  assert.equal(L.addDays("2026-03-01", -1), "2026-02-28");
  assert.equal(L.addDays("2024-02-28", 1), "2024-02-29");
});

test("meters and walkMin", () => {
  assert.equal(L.meters(40, -74, 40, -74), 0);
  const d = L.meters(40.8101, -73.9538, 40.8191, -73.9538);
  assert.ok(Math.abs(d - 1000) < 15, String(d));
  assert.equal(L.walkMin(400), 6);
  assert.equal(L.walkMin(0), 1);
});

test("yearCovered", () => {
  assert.equal(L.yearCovered([2026], "2026-12-31"), true);
  assert.equal(L.yearCovered([2026], "2027-01-01"), false);
  assert.equal(L.yearCovered([2026], "bad"), false);
  assert.equal(L.yearCovered(null, "2026-01-01"), false);
});

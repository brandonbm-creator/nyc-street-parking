"""Tests for asp.py. Run from the repo root: python3 -m unittest discover -s tests -v

The behavior fixtures in tests/fixtures/cases.json are shared with tests/logic.test.js (site/logic.js),
so the Python and JavaScript street-cleaning logic are checked against the same expectations.
No test touches the network.
"""
import argparse
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import asp  # noqa: E402

FIX = json.loads((ROOT / "tests" / "fixtures" / "cases.json").read_text(encoding="utf-8"))
NOW = dt.datetime(2026, 10, 15, 9, 0, tzinfo=asp.NYC)


def scheds_of(case):
    return [(set(d), s, e) for d, s, e in case["scheds"]]


# ---------------------------------------------------------------- shared fixtures

class SharedFixtureTests(unittest.TestCase):
    def test_time_parse(self):
        for c in FIX["time_parse"]:
            with self.subTest(c["in"]):
                self.assertEqual(asp.parse_time(c["in"]), c["out"])

    def test_time_format(self):
        for c in FIX["time_format"]:
            with self.subTest(c["in"]):
                self.assertEqual(asp.fmt_time(c["in"]), c["out"])

    def test_dates(self):
        for c in FIX["date"]:
            with self.subTest(c["in"]):
                self.assertEqual(asp.is_valid_date(c["in"]), c["valid"])
                if c["valid"]:
                    self.assertEqual(dt.date.fromisoformat(c["in"]).weekday(), c["weekday"])

    def test_windows(self):
        for c in FIX["window"]:
            with self.subTest(c):
                self.assertEqual(asp.validate_window(c["arrive"], c["leave"]) is None, c["ok"])

    def test_status(self):
        for c in FIX["status"]:
            with self.subTest(c["name"]):
                if c["expect"].get("error"):
                    with self.assertRaises(ValueError):
                        if not asp.is_valid_date(c["date"]):
                            raise ValueError("Invalid date.")
                        asp.status(scheds_of(c), dt.date.fromisoformat(c["date"]), c["arrive"], c["leave"],
                                   c["holidays"], FIX["max_wait"])
                    continue
                st = asp.status(scheds_of(c), dt.date.fromisoformat(c["date"]), c["arrive"], c["leave"],
                                c["holidays"], FIX["max_wait"])
                self.assertEqual(st, c["expect"])

    def test_next_cleaning(self):
        for c in FIX["next_cleaning"]:
            with self.subTest(c["name"]):
                nc = asp.next_cleaning_at(scheds_of(c), dt.date.fromisoformat(c["date"]), c["after"], c["holidays"])
                exp = c["expect"]
                self.assertEqual(nc, (exp["offset"], exp["start"]) if exp else None)

    def test_next_cleaning_labels(self):
        thu = dt.date(2026, 10, 15)
        mon_thu = [({0, 3}, 540, 630)]
        self.assertEqual(asp.next_cleaning(mon_thu, thu, 500, {}), "today 9AM")
        self.assertEqual(asp.next_cleaning(mon_thu, thu, 720, {}), "Mon 9AM")
        self.assertEqual(asp.next_cleaning([({3, 4}, 480, 510)], thu, 720, {}), "tomorrow 8AM")
        self.assertEqual(asp.next_cleaning([], thu, 0, {}), "none in 2 weeks")

    def test_max_wait_matches_fixture(self):
        self.assertEqual(asp.MAX_WAIT, FIX["max_wait"])


# ---------------------------------------------------------------- sign parsing (Python only)

class ParseScheduleTests(unittest.TestCase):
    CASES = [
        ("NO PARKING (SANITATION BROOM SYMBOL) TUESDAY FRIDAY 11AM-12:30PM <-> (SUPERSEDES SP-360C)", ({1, 4}, 660, 750)),
        ("NO PARKING (SANITATION BROOM SYMBOL) MONDAY THURSDAY 9AM-10:30AM <->", ({0, 3}, 540, 630)),
        ("NO PARKING (SANITATION BROOM SYMBOL) 7:30AM-8AM EXCEPT SUNDAY <->", ({0, 1, 2, 3, 4, 5}, 450, 480)),
        ("NO PARKING (SANITATION BROOM SYMBOL) TUESDAY FRIDAY 10A M-11:30AM --> (SUPERSEDES SP-847CA)", ({1, 4}, 600, 690)),
        ("NO PARKING (SANITATION BROOM SYMBOL) MOON & STARS (SYMBOLS) TUESDAY THURSDAY SATURDAY MIDNIGHT-3AM <->", ({1, 3, 5}, 0, 180)),
        ("NO PARKING (SANITATION BROOM SYMBOL) 11AM TO 12:30PM TUES & FRI <----> (SUPERSEDED BY PS-55B)", ({1, 4}, 660, 750)),
        ("NO PARKING (SANITATION BROOM SYMBOL) MONDAY TUESDAY THURSDAY FRIDAY 8:30AM-9AM <->", ({0, 1, 3, 4}, 510, 540)),
        ("NO PARKING (SANITATION BROOM SYMBOL) WEDNESDAY 11:30AM-1PM <->", ({2}, 690, 780)),
        ("NO PARKING (SANITATION BROOM SYMBOL) MON-FRI 8AM-9AM", ({0, 1, 2, 3, 4}, 480, 540)),
        ("NO PARKING (SANITATION BROOM SYMBOL) 11-12:30PM MONDAY", ({0}, 660, 750)),
        ("NO PARKING (SANITATION BROOM SYMBOL) 8AM-9AM", (set(range(7)), 480, 540)),
        ("NO PARKING (SANITATION BROOM SYMBOL) <-> (SUPERSEDES SP-864C)", None),
        ("NO PARKING (SANITATION BROOM SYMBOL) MONDAY 11PM-2AM", None),  # overnight: not modeled, rejected
        ("", None),
    ]

    def test_cases(self):
        for text, expect in self.CASES:
            with self.subTest(text):
                self.assertEqual(asp.parse_schedule(text), expect)


class NamesTests(unittest.TestCase):
    def test_street_number(self):
        self.assertEqual(asp.street_number("WEST 72 STREET"), ("WEST", 72))
        self.assertEqual(asp.street_number("W 146 ST"), ("WEST", 146))
        self.assertEqual(asp.street_number("EAST 85 STREET"), ("EAST", 85))
        self.assertEqual(asp.street_number("BROADWAY"), (None, None))
        self.assertEqual(asp.street_number("WEST END AVENUE"), (None, None))

    def test_locate(self):
        self.assertEqual(asp.locate("BROADWAY", "WEST 72 STREET", "WEST 73 STREET"), (72, "WEST"))
        self.assertEqual(asp.locate("WEST 61 STREET", "AMSTERDAM AVENUE", "DEAD END"), (61, "WEST"))
        self.assertEqual(asp.locate("3 AVENUE", "EAST 86 STREET", "EAST 85 STREET"), (85, "EAST"))
        self.assertEqual(asp.locate("BROADWAY", "LA SALLE STREET", "TIEMANN PLACE"), (None, None))

    def test_pretty(self):
        self.assertEqual(asp.pretty("WEST 72 STREET"), "W 72nd St")
        self.assertEqual(asp.pretty("WEST 103 STREET"), "W 103rd St")
        self.assertEqual(asp.pretty("WEST 111 STREET"), "W 111th St")
        self.assertEqual(asp.pretty("WEST END AVENUE"), "West End Ave")
        self.assertEqual(asp.pretty("SAINT NICHOLAS AVENUE"), "St Nicholas Ave")
        self.assertEqual(asp.pretty("ST NICHOLAS AVENUE"), "St Nicholas Ave")


class GeoTests(unittest.TestCase):
    def test_state_plane_to_latlon(self):
        lat, lon = asp.state_plane_to_latlon(996877, 222815)  # 3 Ave at E 85th-86th St
        self.assertAlmostEqual(lat, 40.7782, places=3)
        self.assertAlmostEqual(lon, -73.9544, places=3)
        lat, lon = asp.state_plane_to_latlon(992017, 212576)  # 2 Ave at E 42nd-43rd St
        self.assertAlmostEqual(lat, 40.7501, places=3)
        self.assertAlmostEqual(lon, -73.9720, places=3)

    def test_meters_and_walk_minutes(self):
        self.assertAlmostEqual(asp.meters((40.0, -74.0), (40.0, -74.0)), 0)
        self.assertAlmostEqual(asp.meters((40.8101, -73.9538), (40.8191, -73.9538)), 1000, delta=15)
        self.assertEqual(asp.walk_min(400), 6)
        self.assertEqual(asp.walk_min(0), 1)


# ---------------------------------------------------------------- CLI window

class ResolveWindowTests(unittest.TestCase):
    def test_explicit_short_stay_is_kept(self):
        self.assertEqual(asp.resolve_window("9:00", "9:10", 0), (540, 550))

    def test_explicit_invalid_stay_is_rejected(self):
        with self.assertRaises(ValueError):
            asp.resolve_window("12:00", "9:00", 0)
        with self.assertRaises(ValueError):
            asp.resolve_window("9:00", "9:00", 0)

    def test_missing_leave_defaults_to_noon_or_three_hours(self):
        self.assertEqual(asp.resolve_window("7:30", None, 0), (450, 720))
        self.assertEqual(asp.resolve_window("now", None, 15 * 60), (900, 1080))
        self.assertEqual(asp.resolve_window("now", None, 22 * 60), (1320, 1439))
        with self.assertRaises(ValueError):
            asp.resolve_window("23:59", None, 0)

    def test_now_uses_the_clock(self):
        self.assertEqual(asp.resolve_window("now", "10:00", 9 * 60), (540, 600))

    def test_bad_time_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            asp.resolve_window("25:00", None, 0)


# ---------------------------------------------------------------- pipeline (temp dir, mocked network)

def sign(i, desc="NO PARKING (SANITATION BROOM SYMBOL) MONDAY THURSDAY 9AM-10:30AM <->"):
    """A fake API row for a distinct West Side block face (streets 61..185, both sides)."""
    street = 61 + (i // 2) % 125
    return {
        "order_number": f"P-{i:07d}", "record_type": "Current", "borough": "Manhattan",
        "on_street": f"WEST {street} STREET", "from_street": "BROADWAY", "to_street": "AMSTERDAM AVENUE",
        "side_of_street": "N" if i % 2 == 0 else "S", "sign_description": desc,
        "sign_x_coord": str(990000 + i), "sign_y_coord": str(225000 + i),
    }


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "data").mkdir()
        (d / "site").mkdir()
        self.patches = [
            mock.patch.object(asp, "DATA", d / "data"),
            mock.patch.object(asp, "CLEANING_CSV", d / "data" / "street_cleaning.csv"),
            mock.patch.object(asp, "SNAPSHOT_JSON", d / "data" / "snapshot.json"),
            mock.patch.object(asp, "LOCATIONS_CSV", d / "data" / "locations.csv"),
            mock.patch.object(asp, "SITE_DATA", d / "site" / "data.json"),
        ]
        for p in self.patches:
            p.start()
        (d / "data" / "locations.csv").write_text(
            "name,kind,type,address,lat,lon\nTest HQ,Headquarters,nonprofit,addr,40.8101,-73.9538\n", encoding="utf-8")
        (d / "data" / "asp_suspensions_2026.csv").write_text(
            "date,holiday\n2026-11-26,Thanksgiving Day\n2026-12-25,Christmas Day\n", encoding="utf-8")
        self.d = d

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def fetch_args(self, **kw):
        base = dict(min_street=61, max_street=185, min_schedules=5, force=False)
        base.update(kw)
        return argparse.Namespace(**base)

    def good_fetch(self, n=40):
        with mock.patch.object(asp, "fetch_rows", return_value=[sign(i) for i in range(n)]):
            asp.fetch(self.fetch_args())

    def snapshot_bytes(self):
        return asp.CLEANING_CSV.read_bytes(), asp.SNAPSHOT_JSON.read_bytes()

    def test_fetch_writes_validated_snapshot_and_meta(self):
        self.good_fetch(40)
        rows = asp.CLEANING_CSV.read_text(encoding="utf-8").splitlines()
        self.assertEqual(rows[0].split(","), asp.CSV_HEADER)
        self.assertEqual(len(rows), 41)
        meta = json.loads(asp.SNAPSHOT_JSON.read_text(encoding="utf-8"))
        self.assertEqual(meta["schema"], asp.SCHEMA)
        self.assertEqual(meta["schedules"], 40)
        self.assertEqual(meta["faces"], 40)
        self.assertEqual(meta["signs"], 40)
        self.assertIn("source_fetched_at", meta)
        self.assertEqual([p.name for p in self.d.glob("**/*.tmp")], [])

    def test_fetch_rejects_empty_response_and_keeps_snapshot(self):
        self.good_fetch(40)
        before = self.snapshot_bytes()
        with mock.patch.object(asp, "fetch_rows", return_value=[]):
            with self.assertRaises(SystemExit):
                asp.fetch(self.fetch_args())
        self.assertEqual(self.snapshot_bytes(), before)

    def test_fetch_rejects_schema_change(self):
        self.good_fetch(40)
        before = self.snapshot_bytes()
        rows = [{k: v for k, v in sign(i).items() if k != "sign_description"} for i in range(40)]
        with mock.patch.object(asp, "fetch_rows", return_value=rows):
            with self.assertRaises(SystemExit):
                asp.fetch(self.fetch_args())
        self.assertEqual(self.snapshot_bytes(), before)

    def test_fetch_rejects_snapshot_below_floor(self):
        with mock.patch.object(asp, "fetch_rows", return_value=[sign(i) for i in range(3)]):
            with self.assertRaises(SystemExit):
                asp.fetch(self.fetch_args(min_schedules=5))
        self.assertFalse(asp.CLEANING_CSV.exists())

    def test_fetch_rejects_big_shrink_unless_forced(self):
        self.good_fetch(40)
        before = self.snapshot_bytes()
        with mock.patch.object(asp, "fetch_rows", return_value=[sign(i) for i in range(20)]):
            with self.assertRaises(SystemExit):
                asp.fetch(self.fetch_args())
            self.assertEqual(self.snapshot_bytes(), before)
            asp.fetch(self.fetch_args(force=True))
        self.assertEqual(json.loads(asp.SNAPSHOT_JSON.read_text())["schedules"], 20)

    def test_fetch_keeps_snapshot_when_network_fails(self):
        self.good_fetch(40)
        before = self.snapshot_bytes()
        with mock.patch.object(asp, "fetch_rows", side_effect=OSError("network down")):
            with self.assertRaises(SystemExit):
                asp.fetch(self.fetch_args())
        self.assertEqual(self.snapshot_bytes(), before)

    def test_build_schedules_counts_and_scopes(self):
        rows = [sign(i) for i in range(10)] + [sign(10, "NO PARKING (SANITATION BROOM SYMBOL) <->")]
        rows.append({**sign(11), "on_street": "EAST 85 STREET", "from_street": "3 AVENUE", "to_street": "2 AVENUE"})
        csv_rows, meta = asp.build_schedules(rows, 61, 185, asp.load_locations())
        self.assertEqual(meta["skipped_signs"], 1)
        self.assertEqual(meta["schedules"], 10)  # the East Side row is out of scope and far from Test HQ
        self.assertTrue(all(r[-1] == "west" for r in csv_rows))

    def test_export_builds_site_data(self):
        self.good_fetch(40)
        asp.export(argparse.Namespace(min_schedules=5, force=False))
        out = json.loads(asp.SITE_DATA.read_text(encoding="utf-8"))
        meta = json.loads(asp.SNAPSHOT_JSON.read_text(encoding="utf-8"))
        self.assertEqual(out["schema"], asp.SCHEMA)
        self.assertEqual(out["source_fetched_at"], meta["source_fetched_at"])
        self.assertIn("built_at", out)
        self.assertEqual(out["holiday_years"], [2026])
        self.assertEqual(out["holidays"]["2026-11-26"], "Thanksgiving Day")
        self.assertEqual(out["max_wait"], asp.MAX_WAIT)
        self.assertEqual(out["stats"], {"schedules": 40, "faces": 40, "faces_without_coords": 0})
        self.assertEqual(len(out["places"]), 1)
        b = out["blocks"][0]
        self.assertEqual(set(b), {"n", "on", "from", "to", "side", "lat", "lon", "s"})
        self.assertEqual(b["s"], [[[0, 3], 540, 630]])

    def test_export_rejects_empty_snapshot(self):
        asp.CLEANING_CSV.write_text(",".join(asp.CSV_HEADER) + "\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            asp.export(argparse.Namespace(min_schedules=5, force=False))
        self.assertFalse(asp.SITE_DATA.exists())

    def test_export_rejects_missing_snapshot_and_missing_calendar(self):
        with self.assertRaises(SystemExit):
            asp.export(argparse.Namespace(min_schedules=5, force=False))
        self.good_fetch(40)
        (self.d / "data" / "asp_suspensions_2026.csv").unlink()
        with self.assertRaises(SystemExit):
            asp.export(argparse.Namespace(min_schedules=5, force=False))

    def test_load_suspensions_rejects_bad_dates(self):
        (self.d / "data" / "asp_suspensions_2027.csv").write_text("date,holiday\n2027-02-30,Nope\n", encoding="utf-8")
        with self.assertRaises(asp.DataError):
            asp.load_suspensions()

    def test_suspension_years_and_coverage(self):
        self.assertEqual(asp.suspension_years(), [2026])
        self.assertTrue(asp.year_covered([2026], dt.date(2026, 12, 31)))
        self.assertFalse(asp.year_covered([2026], dt.date(2027, 1, 1)))

    def test_data_age_and_coverage_warnings(self):
        self.good_fetch(40)
        meta = json.loads(asp.SNAPSHOT_JSON.read_text(encoding="utf-8"))
        fetched = dt.datetime.fromisoformat(meta["source_fetched_at"])
        self.assertEqual(asp.data_age_days(meta, fetched + dt.timedelta(days=9, hours=1)), 9)
        self.assertIsNone(asp.data_age_days({}, fetched))
        fresh = asp.coverage_warnings(dt.date(2026, 10, 15), fetched)
        self.assertEqual(fresh, [])
        stale = asp.coverage_warnings(dt.date(2027, 1, 5), fetched + dt.timedelta(days=30))
        self.assertEqual(len(stale), 2)
        self.assertIn("2027", stale[0])
        self.assertIn("30 days old", stale[1])

    def test_load_blocks_near_and_classify(self):
        self.good_fetch(40)
        coords = asp.load_coords()
        here = next(iter(coords.values()))
        near = asp.load_blocks(0, 0, near=(*here, 50))
        self.assertGreaterEqual(len(near), 1)
        by = asp.classify(near, dt.date(2026, 10, 15), 540, 720, {})  # Thu, arrive at 9:00 during 9-10:30
        self.assertEqual(len(by["wait"]), len(near))
        self.assertEqual(by["wait"][0][2], {"kind": "wait", "at": 585, "start": 540, "end": 630, "wait": 45})


# ---------------------------------------------------------------- committed data sanity

class CommittedDataTests(unittest.TestCase):
    def test_holiday_calendar_is_well_formed(self):
        years = asp.suspension_years()
        self.assertIn(2026, years)
        holidays = asp.load_suspensions()
        dates = sorted(d for d in holidays if d.startswith("2026-"))
        self.assertEqual(len(dates), 42)
        self.assertTrue(all(holidays[d] for d in dates))
        for p in asp.DATA.glob("asp_suspensions_*.csv"):
            year = int(p.stem.rsplit("_", 1)[1])
            with p.open(encoding="utf-8") as f:
                rows = list(__import__("csv").DictReader(f))
            self.assertTrue(all(r["date"].startswith(f"{year}-") for r in rows), p.name)

    def test_locations_are_in_upper_manhattan(self):
        for p in asp.load_locations():
            self.assertTrue(40.79 < p["lat"] < 40.87 and -73.97 < p["lon"] < -73.92, p["name"])
            self.assertIn(p["type"], {"subway", "nonprofit", "development", "site"})


if __name__ == "__main__":
    unittest.main()

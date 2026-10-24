#!/usr/bin/env python3
"""Uptown Manhattan street-cleaning (Alternate Side Parking) finder, digest, and site-data builder.

  python3 asp.py fetch                          # refresh data/street_cleaning.csv + data/snapshot.json (validated, atomic)
  python3 asp.py export                         # build site/data.json for the dashboard (validated, atomic)
  python3 asp.py digest [--date 2026-10-15]     # markdown digest for a morning (7:30-12:00 by default)
  python3 asp.py find --arrive 7:45 --leave 9   # blocks for a stay; an explicit stay is kept exactly as given
  python3 asp.py find --near 3920 --arrive now  # wait-&-take + no-ASP blocks near a location (or 'lat,lon')

Stdlib only. Source: NYC DOT "Parking Regulation Locations and Signs" (nfid-uabd).
The street-cleaning logic mirrors site/logic.js; tests/fixtures/cases.json keeps the two in step.
"""
import argparse
import csv
import datetime as dt
import io
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CLEANING_CSV = DATA / "street_cleaning.csv"
SNAPSHOT_JSON = DATA / "snapshot.json"      # provenance of the committed snapshot (written only by a good fetch)
LOCATIONS_CSV = DATA / "locations.csv"
SITE_DATA = ROOT / "site" / "data.json"     # built for the Pages artifact; not committed

SCHEMA = 1
API = "https://data.cityofnewyork.us/resource/nfid-uabd.json"
API_PAGE = 50000
REQUIRED_FIELDS = ("on_street", "from_street", "to_street", "side_of_street", "sign_description")
MIN_SCHEDULES = 1500   # a snapshot with fewer schedules than this is rejected outright
MIN_RATIO = 0.85       # a new snapshot must keep at least this share of the previous one (unless --force)
STALE_DAYS = 7         # warn when the sign data is older than this
NEAR_METERS = 800      # default coverage around a location (data/locations.csv `radius` overrides it)
MAX_NEAR_METERS = 3000 # largest per-location coverage radius accepted
WALK_METERS = 400      # "nearby" in the digest: about a 6-minute walk
MAX_WAIT = 45          # wait & take: longest worthwhile wait for a cleaning window's halfway mark
HORIZON_DAYS = 14      # how far ahead next_cleaning looks
NYC = ZoneInfo("America/New_York")
DOT_ASP_URL = "https://www.nyc.gov/html/dot/html/motorist/alternate-side-parking.shtml"
NYC311_ASP_URL = "https://portal.311.nyc.gov/article/?kanumber=KA-01011"

# 60th St is inside the congestion toll zone ("south of and including 60th Street"),
# so the first toll-free street is 61st.
MIN_STREET, MAX_STREET, FOCUS_MAX = 61, 185, 110
DEFAULT_ARRIVE, DEFAULT_LEAVE = "7:30", "12:00"
AREAS = [(61, 72, "Lincoln Sq (61–72)"), (73, 86, "UWS core (73–86)"),
         (87, 96, "UWS north (87–96)"), (97, 110, "Manhattan Valley (97–110)")]

DAY_NAMES = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
DAY_ALIASES = {
    "MONDAY": 0, "MON": 0, "TUESDAY": 1, "TUES": 1, "TUE": 1, "WEDNESDAY": 2, "WED": 2,
    "THURSDAY": 3, "THURS": 3, "THUR": 3, "THU": 3, "FRIDAY": 4, "FRI": 4,
    "SATURDAY": 5, "SAT": 5, "SUNDAY": 6, "SUN": 6,
}
DAY_RE = re.compile(r"\b(" + "|".join(sorted(DAY_ALIASES, key=len, reverse=True)) + r")\b")
RANGE_RE = re.compile(DAY_RE.pattern + r"\s*(?:-|THRU|TO)\s*" + DAY_RE.pattern)
TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?\s*(?:-|TO)\s*(\d{1,2})(?::(\d{2}))?\s*(AM|PM)")
STREET_RE = re.compile(r"^(EAST|WEST|E|W)\s+(\d+)\s+(?:STREET|ST)\b")
ABBREV = {"STREET": "St", "AVENUE": "Ave", "BOULEVARD": "Blvd", "PLACE": "Pl",
          "DRIVE": "Dr", "PARKWAY": "Pkwy", "TERRACE": "Ter", "SAINT": "St"}
SIGNS_NOTE = ("Covers street-cleaning (ASP) signs only: meters, No Standing, hydrants, bus stops and "
              "construction permits are not included, and vacancy is not measured. Always read the posted signs.")
LEGAL_NOTE = ("The posted ASP restriction runs to its end time even after the sweeper passes (NYC311 KA-01011); "
              "the suggested arrival is when cars typically start moving back in, not when the rule ends.")


class DataError(Exception):
    """Fetched or stored data is unusable; the previous snapshot must be left alone."""


# ---------------------------------------------------------------- names and numbers

def norm(name):
    return " ".join((name or "").upper().split())


def ordinal(n):
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def pretty(name):
    """'WEST 72 STREET' -> 'W 72nd St', 'SAINT NICHOLAS AVENUE' -> 'St Nicholas Ave'."""
    words = norm(name).split()
    out = []
    for i, w in enumerate(words):
        if w.isdigit():
            out.append(ordinal(int(w)))
        elif i == 0 and w in ("WEST", "EAST") and len(words) > 1 and words[1].isdigit():
            out.append(w[0])
        else:
            out.append(ABBREV.get(w, w.title()))
    return " ".join(out)


def street_number(name):
    """('WEST', 72) for 'WEST 72 STREET', else (None, None)."""
    m = STREET_RE.match(norm(name))
    if not m:
        return None, None
    return ("EAST" if m.group(1).startswith("E") else "WEST"), int(m.group(2))


def locate(on, frm, to):
    """Return (street_no, town) for a block face. Avenue blocks use the lower cross street."""
    town, num = street_number(on)
    if num is not None:
        return num, town
    cross = [street_number(s) for s in (frm, to)]
    cross = [c for c in cross if c[1] is not None]
    if not cross:
        return None, None
    return min(c[1] for c in cross), cross[0][0]


# ---------------------------------------------------------------- dates and times

def to_minutes(h, m, ampm):
    h, m = int(h), int(m or 0)
    if ampm == "AM":
        h = 0 if h == 12 else h
    elif ampm == "PM":
        h = 12 if h == 12 else h + 12
    return h * 60 + m


def parse_time(s):
    """'7:45', '07:45', '7:45AM', '8 am', '13:00' -> minutes since midnight, or None if not a time."""
    if not isinstance(s, str):
        return None
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?\s*", s.upper())
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if mi > 59:
        return None
    if ap:
        if not 1 <= h <= 12:
            return None
    elif h > 23:
        return None
    return to_minutes(h, mi, ap)


def parse_hhmm(s):
    t = parse_time(s)
    if t is None:
        raise argparse.ArgumentTypeError(f"not a time: {s!r} (try 7:45, 7:45AM or 13:00)")
    return t


def is_valid_date(iso):
    if not isinstance(iso, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso):
        return False
    try:
        dt.date.fromisoformat(iso)
    except ValueError:
        return False
    return True


def parse_date(iso):
    if not is_valid_date(iso):
        raise argparse.ArgumentTypeError(f"not a date: {iso!r} (use YYYY-MM-DD)")
    return dt.date.fromisoformat(iso)


def validate_window(arrive, leave):
    """None when the stay is usable, otherwise a message for the user."""
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (arrive, leave)):
        return "Arrive and leave times are required."
    if not (0 <= arrive < 1440 and 0 <= leave < 1440):
        return "Times must be within one day."
    if leave <= arrive:
        return "Leave time must be after arrival."
    return None


def fmt_time(mins):
    h, m = divmod(mins, 60)
    suffix = "AM" if h < 12 else "PM"
    h = h % 12 or 12
    return f"{h}:{m:02d}{suffix}" if m else f"{h}{suffix}"


def fmt_days(days):
    return "/".join(DAY_NAMES[d][:3].title() for d in sorted(days))


def parse_schedule(desc):
    """Return (days:set[int], start_min, end_min) from a sign description, or None if unusable.

    Overnight windows (end <= start) are not modeled and are rejected here so they never reach the snapshot.
    """
    text = norm(desc)
    text = re.sub(r"\(SUPERSEDES[^)]*\)", "", text)
    text = re.sub(r"(\d)([AP])\s+M\b", r"\1\2M", text)
    text = text.replace("MIDNIGHT", "12AM").replace("NOON", "12PM")

    t = TIME_RE.search(text)
    if not t:
        return None
    h1, m1, ap1, h2, m2, ap2 = t.groups()
    end = to_minutes(h2, m2, ap2)
    start = to_minutes(h1, m1, ap1 or ap2)
    if not ap1 and start > end:  # e.g. "11-12:30PM" means 11AM
        start = to_minutes(h1, m1, "AM")
    if end <= start:
        return None

    if "EXCEPT SUNDAY" in text:
        days = set(range(6))
    elif r := RANGE_RE.search(text):
        a, b = DAY_ALIASES[r.group(1)], DAY_ALIASES[r.group(2)]
        days = set(range(a, b + 1))
    else:
        days = {DAY_ALIASES[d] for d in DAY_RE.findall(text)}
    if not days:
        days = set(range(7))  # no day listed = every day
    return days, start, end


# ---------------------------------------------------------------- geometry

def meters(a, b):
    """Haversine distance in meters between two (lat, lon) points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def walk_min(m):
    """Rough walking minutes, inflating straight-line distance for the street grid."""
    return max(1, round(m * 1.25 / 80))


def valid_latlon(lat, lon):
    """Finite and inside the New York City box; anything else is treated as 'no coordinates'."""
    return (all(isinstance(v, float) and math.isfinite(v) for v in (lat, lon))
            and 40.4 < lat < 41.0 and -74.3 < lon < -73.6)


def valid_state_plane(x, y):
    """Finite and inside the EPSG:2263 range NYC signs use (feet)."""
    return (all(isinstance(v, float) and math.isfinite(v) for v in (x, y))
            and 900000 < x < 1100000 and 100000 < y < 300000)


def state_plane_to_latlon(x_ft, y_ft):
    """NAD83 / New York Long Island (EPSG:2263, US ft) -> (lat, lon). Lambert Conformal Conic inverse."""
    a, f = 6378137.0, 1 / 298.257222101
    e = math.sqrt(2 * f - f * f)
    lat1, lat2, lat0, lon0 = (math.radians(v) for v in (41.0333333333, 40.6666666667, 40.1666666667, -74.0))
    m = lambda p: math.cos(p) / math.sqrt(1 - (e * math.sin(p)) ** 2)
    t = lambda p: math.tan(math.pi / 4 - p / 2) / ((1 - e * math.sin(p)) / (1 + e * math.sin(p))) ** (e / 2)
    n = (math.log(m(lat1)) - math.log(m(lat2))) / (math.log(t(lat1)) - math.log(t(lat2)))
    F = m(lat1) / (n * t(lat1) ** n)
    rho0 = a * F * t(lat0) ** n
    ft = 1200 / 3937
    dx, dy = x_ft * ft - 300000.0, rho0 - y_ft * ft
    tt = (math.hypot(dx, dy) / (a * F)) ** (1 / n)
    lon = math.atan2(dx, dy) / n + lon0
    lat = math.pi / 2 - 2 * math.atan(tt)
    for _ in range(6):
        lat = math.pi / 2 - 2 * math.atan(tt * ((1 - e * math.sin(lat)) / (1 + e * math.sin(lat))) ** (e / 2))
    return round(math.degrees(lat), 6), round(math.degrees(lon), 6)


# ---------------------------------------------------------------- files

def rel(path):
    """Path relative to the repo for messages; absolute when it lives elsewhere (tests use temp dirs)."""
    try:
        return Path(path).relative_to(ROOT)
    except ValueError:
        return Path(path)


def write_atomic(path, text):
    """Write the whole file or nothing: a crash mid-write never leaves a truncated snapshot."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_locations():
    """data/locations.csv rows with float lat/lon and an int `radius` (meters of coverage around the place).

    `radius` is optional per row (blank = NEAR_METERS); a non-integer or out-of-range value is a DataError
    so a typo can't silently shrink or blow up the fetch.
    """
    out = []
    with LOCATIONS_CSV.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            raw = (r.get("radius") or "").strip()
            try:
                radius = int(raw) if raw else NEAR_METERS
                lat, lon = float(r["lat"]), float(r["lon"])
            except (KeyError, TypeError, ValueError):
                raise DataError(f"{rel(LOCATIONS_CSV)}: bad lat/lon/radius for {r.get('name')!r}")
            if not valid_latlon(lat, lon):
                raise DataError(f"{rel(LOCATIONS_CSV)}: {r.get('name')!r} is not inside New York City")
            if not 100 <= radius <= MAX_NEAR_METERS:
                raise DataError(f"{rel(LOCATIONS_CSV)}: radius for {r.get('name')!r} must be 100-{MAX_NEAR_METERS} m")
            out.append({**r, "lat": lat, "lon": lon, "radius": radius})
    return out


def read_calendars():
    """[(year, {iso date: holiday})] from every data/asp_suspensions_<year>.csv.

    A calendar must have at least one row, every date must be valid and in the file's year, and every
    holiday must be named; otherwise DataError, so a half-made file can never count as coverage.
    """
    out = []
    for p in sorted(DATA.glob("asp_suspensions_*.csv")):
        m = re.fullmatch(r"asp_suspensions_(\d{4})\.csv", p.name)
        if not m:
            raise DataError(f"{p.name}: name must be asp_suspensions_<year>.csv")
        year, days = int(m.group(1)), {}
        with p.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                d, name = (r.get("date") or "").strip(), (r.get("holiday") or "").strip()
                if not is_valid_date(d) or not d.startswith(f"{year}-"):
                    raise DataError(f"{p.name}: bad or out-of-year date {d!r}")
                if not name:
                    raise DataError(f"{p.name}: {d} has no holiday name")
                days[d] = name
        if not days:
            raise DataError(f"{p.name}: calendar is empty")
        out.append((year, days))
    return out


def load_suspensions():
    """{iso date: holiday name} across every calendar on file."""
    return {d: n for _, days in read_calendars() for d, n in days.items()}


def suspension_years():
    """Years with a complete, validated holiday calendar on file."""
    return sorted(y for y, _ in read_calendars())


def year_covered(years, date):
    return date.year in years


def load_snapshot_meta():
    if not SNAPSHOT_JSON.exists():
        return None
    try:
        return json.loads(SNAPSHOT_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def data_age_days(meta, now):
    """Days since the sign data was fetched, or None when unknown."""
    if not meta or not meta.get("source_fetched_at"):
        return None
    try:
        fetched = dt.datetime.fromisoformat(meta["source_fetched_at"])
    except ValueError:
        return None
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=NYC)
    return max(0, (now - fetched).days)


# ---------------------------------------------------------------- fetch

def fetch_rows(api=API, page=API_PAGE):
    where = ("borough='Manhattan' AND record_type='Current' "
             "AND upper(sign_description) like '%SANITATION BROOM%'")
    rows, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"$where": where, "$limit": page, "$offset": offset,
                                    "$order": "order_number"})
        with urllib.request.urlopen(f"{api}?{q}", timeout=120) as r:
            batch = json.load(r)
        if not isinstance(batch, list):
            raise DataError(f"API returned {type(batch).__name__}, not a list of rows")
        rows += batch
        print(f"  fetched {len(rows)} signs", file=sys.stderr)
        if len(batch) < page:
            break
        offset += page
    return rows


def validate_api_rows(rows):
    if not isinstance(rows, list) or not rows:
        raise DataError("API returned no sign rows")
    if not isinstance(rows[0], dict):
        raise DataError("API rows are not objects")
    missing = [f for f in REQUIRED_FIELDS if f not in rows[0]]
    if missing:
        raise DataError("API rows are missing expected fields: " + ", ".join(missing))


CSV_HEADER = ["street_no", "on_street", "from_street", "to_street", "side",
              "days", "start", "end", "sign_count", "order_numbers", "lat", "lon", "scope", "unknown"]


def build_schedules(rows, min_street, max_street, places):
    """Group sign rows into one schedule per (block face, days, start, end).

    Returns (csv_rows, meta). Kept faces are West Side blocks in [min_street, max_street] (scope=west)
    plus anything within a location's coverage radius (scope=near; `radius` in data/locations.csv,
    NEAR_METERS by default, larger for a whole zone such as Battery Park City around Stuyvesant HS).
    """
    groups, skipped = defaultdict(lambda: {"signs": 0, "orders": set()}), 0
    coords = defaultdict(list)  # block face -> [(x, y), ...] for a map pin
    in_west = {}                # block face -> inside the West Side street range?
    unknown = defaultdict(set)  # block face -> order numbers of sanitation signs we could not read
    for r in rows:
        on, frm, to = norm(r.get("on_street")), norm(r.get("from_street")), norm(r.get("to_street"))
        num, town = locate(on, frm, to)
        face = (num or 0, on, frm, to, r.get("side_of_street", ""))
        in_west[face] = town == "WEST" and min_street <= (num or 0) <= max_street
        try:
            x, y = float(r["sign_x_coord"]), float(r["sign_y_coord"])
            if valid_state_plane(x, y):
                coords[face].append((x, y))
        except (KeyError, TypeError, ValueError):
            pass
        sched = parse_schedule(r.get("sign_description", ""))
        if not sched:
            # An ASP sign we can't model (no time, overnight window): the face is 'unknown', never 'no ASP'.
            skipped += 1
            unknown[face].add(r.get("order_number", ""))
            continue
        days, start, end = sched
        key = (*face, fmt_days(days), fmt_time(start), fmt_time(end))
        groups[key]["signs"] += 1
        groups[key]["orders"].add(r.get("order_number", ""))

    latlon = {}
    for face, pts in coords.items():
        lat, lon = state_plane_to_latlon(sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts))
        if valid_latlon(lat, lon):
            latlon[face] = (lat, lon)

    def scope(face):
        if in_west[face]:
            return "west"
        ll = latlon.get(face)
        if ll and any(meters(ll, (p["lat"], p["lon"])) <= p.get("radius", NEAR_METERS) for p in places):
            return "near"
        return None

    out, faces, no_coords = [], set(), set()
    for key in sorted(groups):
        face = key[:5]
        sc = scope(face)
        if not sc:
            continue
        g = groups[key]
        lat, lon = latlon.get(face, ("", ""))
        out.append([face[0] or "", *key[1:], g["signs"], " ".join(sorted(g["orders"])), lat, lon, sc,
                    1 if face in unknown else 0])
        faces.add(face)
        if lat == "":
            no_coords.add(face)
    # Faces whose only sanitation signs were unreadable get a schedule-less row so they still show as unknown.
    for face in sorted(unknown):
        if face in faces or not scope(face):
            continue
        lat, lon = latlon.get(face, ("", ""))
        out.append([face[0] or "", *face[1:], "", "", "", len(unknown[face]), " ".join(sorted(unknown[face])),
                    lat, lon, scope(face), 1])
        faces.add(face)
        if lat == "":
            no_coords.add(face)
    meta = {"signs": len(rows), "schedules": sum(1 for r in out if r[5]), "faces": len(faces),
            "faces_without_coords": len(no_coords), "faces_unknown": sum(1 for f in faces if f in unknown),
            "skipped_signs": skipped}
    return out, meta


def check_counts(n, previous, min_schedules=MIN_SCHEDULES, min_ratio=MIN_RATIO, force=False):
    """Refuse a snapshot that is too small on its own, or shrank too much versus the previous one."""
    if n < min_schedules:
        raise DataError(f"only {n} schedules were built; the floor is {min_schedules}")
    if previous and not force and n < min_ratio * previous:
        raise DataError(f"{n} schedules is under {min_ratio:.0%} of the previous snapshot ({previous}); "
                        "re-run with --force to accept it")


def write_snapshot(csv_rows, meta, now=None):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_HEADER)
    w.writerows(csv_rows)
    write_atomic(CLEANING_CSV, buf.getvalue())
    info = {"schema": SCHEMA, "source": "NYC Open Data nfid-uabd", "api": API,
            "source_fetched_at": (now or dt.datetime.now(NYC)).isoformat(timespec="minutes"), **meta}
    write_atomic(SNAPSHOT_JSON, json.dumps(info, indent=2) + "\n")
    return info


def fetch(args):
    try:
        rows = fetch_rows()
        validate_api_rows(rows)
        csv_rows, meta = build_schedules(rows, args.min_street, args.max_street, load_locations())
        prev = load_snapshot_meta()
        check_counts(meta["schedules"], prev.get("schedules") if prev else None,
                     args.min_schedules, MIN_RATIO, args.force)
    except (DataError, OSError, ValueError) as e:
        sys.exit(f"fetch failed; the committed snapshot was left unchanged: {e}")
    info = write_snapshot(csv_rows, meta)
    print(f"Wrote {info['schedules']} schedules for {info['faces']} block faces to {rel(CLEANING_CSV)} "
          f"({info['skipped_signs']} unparseable signs skipped, {info['faces_without_coords']} faces without coordinates)")


# ---------------------------------------------------------------- export

def csv_latlon(r):
    """(lat, lon) from a snapshot row, or (None, None) when absent or not a usable NYC coordinate."""
    try:
        lat, lon = float(r["lat"]), float(r["lon"])
    except (KeyError, TypeError, ValueError):
        return None, None
    return (lat, lon) if valid_latlon(lat, lon) else (None, None)


def read_faces():
    """site/data.json block entries from the snapshot. Returns (faces list, schedule count)."""
    if not CLEANING_CSV.exists():
        raise DataError(f"{rel(CLEANING_CSV)} is missing; run: python3 asp.py fetch")
    faces, n = {}, 0
    with CLEANING_CSV.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != CSV_HEADER:
            raise DataError("snapshot CSV has an unexpected header")
        for r in reader:
            key = (int(r["street_no"] or 0), r["on_street"], r["from_street"], r["to_street"], r["side"])
            lat, lon = csv_latlon(r)
            face = faces.setdefault(key, {
                "n": key[0] or None, "on": pretty(key[1]), "from": pretty(key[2]), "to": pretty(key[3]),
                "side": key[4], "lat": lat, "lon": lon, "s": []})
            if r["scope"] == "near":
                face["near"] = 1  # only shown in location views, not the West Side area views
            if r["unknown"] == "1":
                face["u"] = 1     # an ASP sign on this face could not be read: never report it as 'no ASP'
            if not r["days"]:
                if r["unknown"] != "1":
                    raise DataError(f"snapshot row without a schedule on {key[1]}")
                continue
            days = sorted(DAY_ALIASES[d.upper()] for d in r["days"].split("/"))
            start, end = parse_hhmm(r["start"]), parse_hhmm(r["end"])
            if end <= start:
                raise DataError(f"snapshot has an overnight window on {key[1]} ({r['start']}-{r['end']})")
            face["s"].append([days, start, end])
            n += 1
    for b in faces.values():
        if not b["s"] and not b.get("u"):
            raise DataError(f"snapshot face without schedules: {b['on']}")
    return sorted(faces.values(), key=lambda b: (b["n"] or 9999, b["on"], b["side"])), n


def build_site_data(now=None):
    faces, n = read_faces()
    meta = load_snapshot_meta() or {}
    now = now or dt.datetime.now(NYC)
    return {
        "schema": SCHEMA,
        "built_at": now.isoformat(timespec="minutes"),
        "source": meta.get("source", "NYC Open Data nfid-uabd"),
        "source_fetched_at": meta.get("source_fetched_at"),
        "stale_days": STALE_DAYS,
        "max_wait": MAX_WAIT,
        "holiday_years": suspension_years(),
        "holidays": load_suspensions(),
        "places": [{k: p[k] for k in ("name", "kind", "type", "address", "lat", "lon", "radius")} for p in load_locations()],
        "stats": {"schedules": n, "faces": len(faces),
                  "faces_without_coords": sum(1 for b in faces if b["lat"] is None),
                  "faces_unknown": sum(1 for b in faces if b.get("u"))},
        "blocks": faces,
    }, n


def export(args):
    try:
        out, n = build_site_data()
        if n < args.min_schedules and not args.force:
            raise DataError(f"snapshot has only {n} schedules (floor {args.min_schedules}); re-run with --force to accept")
        if not out["holiday_years"]:
            raise DataError("no data/asp_suspensions_<year>.csv on file")
        write_atomic(SITE_DATA, json.dumps(out, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    except (DataError, OSError, ValueError) as e:
        sys.exit(f"export failed: {e}")
    print(f"Wrote {out['stats']['faces']} block faces ({n} schedules) to {rel(SITE_DATA)}; "
          f"holiday calendar years: {out['holiday_years']}; sign data as of {out['source_fetched_at'] or 'unknown'}")


# ---------------------------------------------------------------- analysis

def load_blocks(min_street, max_street, near=None):
    """{(street_no, on, from, to, side): [(days, start, end), ...]}

    Default: West Side blocks in [min_street, max_street].
    near=(lat, lon, radius_m): every block face (any scope) within the radius instead.
    Returns (blocks, unknown): `unknown` is the set of keys with an unreadable ASP sign.
    """
    if not CLEANING_CSV.exists():
        raise DataError("No data yet - run: python3 asp.py fetch")
    blocks, unknown = {}, set()
    with CLEANING_CSV.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != CSV_HEADER:
            raise DataError("snapshot CSV has an unexpected header; run: python3 asp.py fetch")
        for r in reader:
            num = int(r["street_no"] or 0)
            lat, lon = csv_latlon(r)
            if near:
                if lat is None or meters((lat, lon), near[:2]) > near[2]:
                    continue
            elif r["scope"] != "west" or not (min_street <= num <= max_street):
                continue
            key = (num, r["on_street"], r["from_street"], r["to_street"], r["side"])
            blocks.setdefault(key, [])
            if r["unknown"] == "1":
                unknown.add(key)
            if r["days"]:
                days = {DAY_ALIASES[d.upper()] for d in r["days"].split("/")}
                blocks[key].append((days, parse_hhmm(r["start"]), parse_hhmm(r["end"])))
    return blocks, unknown


def load_coords():
    out = {}
    with CLEANING_CSV.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lat, lon = csv_latlon(r)
            if lat is not None:
                out[(int(r["street_no"] or 0), r["on_street"], r["from_street"], r["to_street"], r["side"])] = (lat, lon)
    return out


def status(scheds, date, arrive, leave, holidays, max_wait=MAX_WAIT):
    """Classify one block face for a stay from `arrive` until `leave` on `date` (mirrors logic.js statusOf):

      {"kind": "clear",   "swept": bool}          no ASP overlaps the stay (swept: a window ended before arrival)
      {"kind": "wait",    "at", "start", "end", "wait"}
                                                   WAIT & TAKE: a window's halfway mark is 0..max_wait minutes
                                                   after arrival. Cars start moving back in then on competitive
                                                   blocks, so `at` is the suggested arrival. The posted restriction
                                                   still runs to `end`; the spot is then free of ASP until the next window.
      {"kind": "opens",   "free", "since"}         ASP ends before you leave, but the halfway mark is too far away or
                                                   already passed (`since`: move-in started then; competitive)
      {"kind": "blocked", "until"}                 ASP runs until or past when you leave
    Raises ValueError on an invalid window.
    """
    err = validate_window(arrive, leave)
    if err:
        raise ValueError(err)
    if date.isoformat() in holidays:
        return {"kind": "clear", "swept": False}
    today = [(s, e) for days, s, e in scheds if e > s and date.weekday() in days]
    hits = [(s, e) for s, e in today if s < leave and e > arrive]
    if not hits:
        return {"kind": "clear", "swept": any(e <= arrive for _, e in today)}
    free = max(e for _, e in hits)
    if free >= leave:
        return {"kind": "blocked", "until": free}
    for s, e in hits:
        mid = (s + e) // 2
        if e == free and 0 <= mid - arrive <= max_wait:
            return {"kind": "wait", "at": mid, "start": s, "end": e, "wait": mid - arrive}
    late = next(((s, e) for s, e in hits if s <= arrive < e and arrive > (s + e) // 2), None)
    return {"kind": "opens", "free": free, "since": (late[0] + late[1]) // 2 if late else None}


def next_cleaning_at(scheds, date, after_min, holidays, horizon=HORIZON_DAYS):
    """Next ASP window at or after `after_min` today, else within `horizon` days (holidays skipped).

    Returns (offset_days, start_min) or None.
    """
    for offset in range(horizon):
        d = date + dt.timedelta(days=offset)
        if d.isoformat() in holidays:
            continue
        starts = [s for days, s, e in scheds if e > s and d.weekday() in days and (offset or s >= after_min)]
        if starts:
            return offset, min(starts)
    return None


def next_cleaning(scheds, date, after_min, holidays):
    nc = next_cleaning_at(scheds, date, after_min, holidays)
    if not nc:
        return "none in 2 weeks"
    offset, start = nc
    when = "today" if offset == 0 else ("tomorrow" if offset == 1 else (date + dt.timedelta(days=offset)).strftime("%a"))
    return f"{when} {fmt_time(start)}"


def block_label(on, frm, to):
    return f"{pretty(on)} ({pretty(frm)} → {pretty(to)})"


def map_url(on, frm, to):
    cross = to if "DEAD END" in frm or "RAMP" in frm else frm
    q = f"{pretty(on)} & {pretty(cross)}, Manhattan, NY"
    return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote_plus(q)


def classify(blocks, date, arrive, leave, holidays, unknown=frozenset()):
    """-> {"clear", "wait", "opens", "blocked", "unknown": [...]} of (key, scheds, status).

    Faces in `unknown` (an ASP sign we could not read) are reported as unknown, never as no ASP.
    """
    out = {"clear": [], "wait": [], "opens": [], "blocked": [], "unknown": []}
    for key, scheds in blocks.items():
        st = {"kind": "unknown"} if key in unknown else status(scheds, date, arrive, leave, holidays)
        out[st["kind"]].append((key, scheds, st))
    out["clear"].sort(key=lambda x: (x[0][0], not x[2]["swept"]))
    out["wait"].sort(key=lambda x: (x[2]["wait"], x[0][0]))
    out["opens"].sort(key=lambda x: (x[2]["free"], x[0][0]))
    out["blocked"].sort(key=lambda x: x[0][0])
    out["unknown"].sort(key=lambda x: x[0][0])
    return out


def resolve_window(arrive_arg, leave_arg, now_min):
    """CLI stay window. An explicit --leave is kept exactly as given (even a 10-minute stay);
    a missing one defaults to noon, or three hours after arrival when noon is already past."""
    arrive = now_min if arrive_arg.strip().lower() == "now" else parse_hhmm(arrive_arg)
    if leave_arg is None:
        default = parse_hhmm(DEFAULT_LEAVE)
        leave = default if default > arrive else min(arrive + 180, 1439)
    else:
        leave = parse_hhmm(leave_arg)
    err = validate_window(arrive, leave)
    if err:
        raise ValueError(err)
    return arrive, leave


def coverage_warnings(date, now, holidays_years=None):
    """Lines to show when the app cannot vouch for the date: holiday calendar missing, sign data stale."""
    years = suspension_years() if holidays_years is None else holidays_years
    out = []
    if not year_covered(years, date):
        out.append(f"Holiday calendar for {date.year} is not on file, so suspensions are unknown; "
                   f"ASP is assumed in effect. Add data/asp_suspensions_{date.year}.csv from NYC DOT.")
    age = data_age_days(load_snapshot_meta(), now)
    if age is None:
        out.append("Sign data age is unknown (no data/snapshot.json).")
    elif age > STALE_DAYS:
        out.append(f"Sign data is {age} days old; the daily refresh may be failing.")
    return out


# ---------------------------------------------------------------- digest

def digest(args):
    now = dt.datetime.now(NYC)
    date = args.date or now.date()
    arrive, leave = parse_hhmm(args.arrive), parse_hhmm(args.leave)
    err = validate_window(arrive, leave)
    if err:
        sys.exit(err)
    holidays = load_suspensions()
    blocks, unknown = load_blocks(args.min_street, args.max_street)
    if not blocks:
        sys.exit(f"No block faces in range W {args.min_street}-{args.max_street} St.")
    by = classify(blocks, date, arrive, leave, holidays, unknown)
    clear, wait, opens, blocked, unk = by["clear"], by["wait"], by["opens"], by["blocked"], by["unknown"]
    window = f"{fmt_time(arrive)}–{fmt_time(leave)}"
    suspended = date.isoformat() in holidays

    out = [f"# 🚗 Uptown parking · {date:%a %b} {date.day}", ""]
    if suspended:
        out.append(f"**ASP today: SUSPENDED ({holidays[date.isoformat()]})** — "
                   "street cleaning rules don't apply. Meters and other signs may still apply.")
    elif not (wait or opens or blocked or unk):
        out.append(f"**ASP today: none scheduled** — none of the {len(blocks)} block faces has street cleaning {window}.")
    else:
        out.append(f"**ASP today: IN EFFECT** · {len(clear)} of {len(blocks)} block faces have no ASP "
                   f"{window} · W {ordinal(args.min_street)}–{ordinal(args.max_street)} St"
                   + (f" · {len(unk)} with an unreadable ASP sign (check posted signs)" if unk else ""))
    out += ["", f"> Toll-free from 61st St up (60th is in the toll zone). {SIGNS_NOTE}", ""]
    for line in coverage_warnings(date, now):
        out.append(f"> ⚠️ {line}")
    out.append("")

    if wait:
        out += [f"## ⭐ Wait & take — suggested arrival at the halfway mark (≤ {MAX_WAIT} min from {fmt_time(arrive)})", "",
                f"> {LEGAL_NOTE}", "",
                "| Suggested arrival | ASP ends | St | Block | Side | Next ASP |", "|---|---|---|---|---|---|"]
        for (num, on, frm, to, side), scheds, st in wait[:args.top]:
            out.append(f"| {fmt_time(st['at'])} ({st['wait']} min) | {fmt_time(st['end'])} | {num} | "
                       f"[{block_label(on, frm, to)}]({map_url(on, frm, to)}) | {side} | "
                       f"{next_cleaning(scheds, date, st['end'], holidays)} |")
        out.append("")

    if wait or opens or blocked or unk:
        out += [f"## ✅ No ASP {window}, closest to 61st", "",
                "| St | Block | Side | Next ASP |", "|---|---|---|---|"]
        for (num, on, frm, to, side), scheds, st in clear[:args.top]:
            note = " · swept earlier" if st["swept"] else ""
            out.append(f"| {num} | [{block_label(on, frm, to)}]({map_url(on, frm, to)}) | {side} | "
                       f"{next_cleaning(scheds, date, leave, holidays)}{note} |")

        if opens:
            out += ["", "## ⏱ ASP ends before you leave", "",
                    "| ASP ends | St | Block | Side |", "|---|---|---|---|"]
            nearest = sorted(opens, key=lambda x: x[0][0])[:args.top]
            for (num, on, frm, to, side), _, st in sorted(nearest, key=lambda x: (x[2]["free"], x[0][0])):
                out.append(f"| {fmt_time(st['free'])} | {num} | [{block_label(on, frm, to)}]"
                           f"({map_url(on, frm, to)}) | {side} |")

        out += ["", "## 🗺 By area", "",
                f"| Area | No ASP {window} | Wait & take | ASP ends later | ASP during window |", "|---|---|---|---|---|"]
        for lo, hi, name in AREAS:
            if hi < args.min_street or lo > args.max_street:
                continue
            count = lambda rows: sum(lo <= k[0] <= hi for k, _, _ in rows)
            out.append(f"| {name} | {count(clear)} | {count(wait)} | {count(opens)} | {count(blocked)} |")

    if not suspended and date.weekday() != 6:
        coords = load_coords()
        out += ["", f"## 📍 Near our locations (within ~{walk_min(WALK_METERS)} min walk)", "",
                f"| Location | No ASP {window} | Wait & take | ASP ends later | Nearest no-ASP block |", "|---|---|---|---|---|"]
        for p in load_locations():
            here = (p["lat"], p["lon"])
            near_blocks, near_unknown = load_blocks(0, 0, near=(*here, WALK_METERS))
            near = classify(near_blocks, date, arrive, leave, holidays, near_unknown)
            best = min(near["clear"], key=lambda x: meters(coords[x[0]], here), default=None)
            if best:
                (_, on, frm, to, _side) = best[0]
                nearest = (f"[{block_label(on, frm, to)}]({map_url(on, frm, to)}) · "
                           f"{walk_min(meters(coords[best[0]], here))} min")
            else:
                nearest = "none within range"
            out.append(f"| **{p['name']}** <sub>{p['kind']}</sub> | {len(near['clear'])} | {len(near['wait'])} | "
                       f"{len(near['opens'])} | {nearest} |")

    tomorrow = date + dt.timedelta(days=1)
    t = classify(blocks, tomorrow, arrive, leave, holidays, unknown)
    if tomorrow.isoformat() in holidays:
        t_line = f"SUSPENDED ({holidays[tomorrow.isoformat()]})"
    elif not (t["wait"] or t["opens"] or t["blocked"] or t["unknown"]):
        t_line = "no street cleaning"
    else:
        t_line = f"in effect — {len(t['clear'])} of {len(blocks)} block faces have no ASP {window}"
    upcoming = sorted((d, h) for d, h in holidays.items() if d > date.isoformat())
    nxt = (f"{dt.date.fromisoformat(upcoming[0][0]):%a %b %-d} ({upcoming[0][1]})"
           if upcoming else "none on file — add next year's calendar to data/")
    meta = load_snapshot_meta() or {}
    out += ["", "## 📅 Coming up", "",
            f"- **Tomorrow ({tomorrow:%a}):** {t_line}",
            f"- **Next holiday suspension:** {nxt}",
            f"- **Snow/emergency suspensions:** not predictable — check [NYC DOT]({DOT_ASP_URL}) or 311", "",
            f"<sub>Sign data as of {meta.get('source_fetched_at') or 'unknown'} · generated {now:%a %b %-d %-I:%M %p} ET · "
            f"source: NYC Open Data parking signs</sub>"]

    text = "\n".join(out) + "\n"
    if args.out:
        write_atomic(args.out, text)
        print(f"Wrote {args.out}")
    else:
        print(text)


# ---------------------------------------------------------------- find

def find(args):
    now = dt.datetime.now(NYC)
    date = args.date or now.date()
    try:
        arrive, leave = resolve_window(args.arrive, args.leave, now.hour * 60 + now.minute)
    except (ValueError, argparse.ArgumentTypeError) as e:
        sys.exit(str(e))
    holidays = load_suspensions()
    where, here = f"W {args.min_street}-{args.max_street} St", None
    if args.near:
        if re.fullmatch(r"\s*-?\d+\.\d+\s*,\s*-?\d+\.\d+\s*", args.near):
            here = tuple(float(v) for v in args.near.split(","))
            label = f"{here[0]:.5f},{here[1]:.5f}"
        else:
            q = args.near.lower()
            matches = [p for p in load_locations() if q in f"{p['name']} {p['kind']} {p['address']}".lower()]
            if not matches:
                sys.exit(f"No location matches {args.near!r}. See data/locations.csv, or pass lat,lon.")
            here, label = (matches[0]["lat"], matches[0]["lon"]), matches[0]["name"]
        if args.radius <= 0:
            sys.exit("--radius must be a positive number of meters")
        blocks, unknown = load_blocks(0, 0, near=(*here, args.radius))
        where = f"within {args.radius} m of {label}"
    else:
        blocks, unknown = load_blocks(args.min_street, args.max_street)
    print(f"{date:%A %b %d, %Y}  |  park {fmt_time(arrive)}-{fmt_time(leave)}  |  {where}")
    for line in coverage_warnings(date, now):
        print(f"WARNING: {line}")
    if date.isoformat() in holidays:
        print(f"ASP SUSPENDED today ({holidays[date.isoformat()]}) - street cleaning rules don't apply.")
        return
    if not blocks:
        print("No block faces in range.")
        return

    by = classify(blocks, date, arrive, leave, holidays, unknown)
    clear, wait, opens, blocked, unk = by["clear"], by["wait"], by["opens"], by["blocked"], by["unknown"]
    coords = load_coords()
    dist = lambda key: meters(coords[key], here) if here and key in coords else float("inf")
    if here:
        clear.sort(key=lambda x: dist(x[0]))
        wait.sort(key=lambda x: x[2]["wait"] + walk_min(dist(x[0])))  # wait + walk, shortest first
    first_col = lambda key: f"{walk_min(dist(key))}m" if here else key[0]
    print(f"{len(wait)} wait & take, {len(clear)} no ASP, {len(opens)} ASP ends later, "
          f"{len(blocked)} ASP during your stay" + (f", {len(unk)} with an unreadable ASP sign (avoid)" if unk else "") + ".\n")

    if wait:
        print("WAIT & TAKE - suggested arrival is the halfway mark of the cleaning window, when cars start moving back in.")
        print(f"  {LEGAL_NOTE}")
        print(f"{'Walk' if here else 'St':>4}  {'Block':<52} {'Side':<4} {'Arrive by':<10} {'Wait':<6} {'ASP ends':<9} Next ASP")
        for key, scheds, st in wait[:args.limit]:
            num, on, frm, to, side = key
            print(f"{first_col(key):>4}  {block_label(on, frm, to)[:52]:<52} {side:<4} {fmt_time(st['at']):<10} "
                  f"{st['wait']:>3} m  {fmt_time(st['end']):<9} {next_cleaning(scheds, date, st['end'], holidays)}")
        print()

    print("NO ASP for your whole stay")
    print(f"{'Walk' if here else 'St':>4}  {'Block':<52} {'Side':<4} {'Next ASP':<18} Note")
    for key, scheds, st in clear[:args.limit]:
        num, on, frm, to, side = key
        print(f"{first_col(key):>4}  {block_label(on, frm, to)[:52]:<52} {side:<4} "
              f"{next_cleaning(scheds, date, leave, holidays):<18} {'swept earlier' if st['swept'] else ''}")
    if len(clear) > args.limit:
        print(f"\n... {len(clear) - args.limit} more (use --limit)")
    print(f"\n{SIGNS_NOTE}")


# ---------------------------------------------------------------- CLI

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download, validate, and atomically replace data/street_cleaning.csv")
    f.add_argument("--min-street", type=int, default=MIN_STREET)
    f.add_argument("--max-street", type=int, default=MAX_STREET)
    f.add_argument("--min-schedules", type=int, default=MIN_SCHEDULES, help=f"reject smaller snapshots (default {MIN_SCHEDULES})")
    f.add_argument("--force", action="store_true", help="accept a snapshot that shrank more than the allowed share")
    f.set_defaults(func=fetch)

    e = sub.add_parser("export", help="validate the snapshot and write site/data.json for the dashboard")
    e.add_argument("--min-schedules", type=int, default=MIN_SCHEDULES)
    e.add_argument("--force", action="store_true")
    e.set_defaults(func=export)

    for name, func, help_ in [("digest", digest, "markdown digest for a morning"),
                              ("find", find, "blocks for a stay (wait & take, no ASP)")]:
        q = sub.add_parser(name, help=help_)
        q.add_argument("--date", type=parse_date, help="YYYY-MM-DD (default: today, New York time)")
        q.add_argument("--min-street", type=int, default=MIN_STREET)
        q.add_argument("--max-street", type=int, default=FOCUS_MAX)
        q.set_defaults(func=func)
        if name == "digest":
            q.add_argument("--arrive", default=DEFAULT_ARRIVE, help=f"e.g. 7:45 or 7:45AM (default {DEFAULT_ARRIVE})")
            q.add_argument("--leave", default=DEFAULT_LEAVE, help=f"e.g. 12:00 (default {DEFAULT_LEAVE})")
            q.add_argument("--top", type=int, default=12, help="rows per table (default 12)")
            q.add_argument("--out", help="write markdown to this file instead of stdout")
        else:
            q.add_argument("--arrive", default=DEFAULT_ARRIVE, help=f"e.g. 7:45, 7:45AM, or 'now' (default {DEFAULT_ARRIVE})")
            q.add_argument("--leave", default=None,
                           help="e.g. 9:00; kept exactly as given. Default: noon, or arrival + 3h after noon")
            q.add_argument("--limit", type=int, default=40)
            q.add_argument("--near", help="a location (name/address/kind, e.g. 'hancock', '3920') or 'lat,lon'")
            q.add_argument("--radius", type=int, default=WALK_METERS, help=f"meters (default {WALK_METERS})")

    args = p.parse_args(argv)
    try:
        args.func(args)
    except DataError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()

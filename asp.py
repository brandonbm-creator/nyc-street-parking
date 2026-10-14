#!/usr/bin/env python3
"""West Side Manhattan street-cleaning (Alternate Side Parking) finder and daily digest.

  python3 asp.py fetch                          # refresh data/street_cleaning.csv (West Side, 61st-185th)
  python3 asp.py digest [--date 2026-10-15]     # today's digest (markdown); --out TODAY.md to save
  python3 asp.py find --arrive 7:45 --leave 12  # full list of blocks clear for your window
  python3 asp.py export                         # write site/data.json for the web dashboard

Stdlib only. Source: NYC DOT "Parking Regulation Locations and Signs" (nfid-uabd).
"""
import argparse
import csv
import datetime as dt
import json
import math
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
API = "https://data.cityofnewyork.us/resource/nfid-uabd.json"
NYC = ZoneInfo("America/New_York")
DOT_ASP_URL = "https://www.nyc.gov/html/dot/html/motorist/alternate-side-parking.shtml"

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
          "DRIVE": "Dr", "PARKWAY": "Pkwy", "TERRACE": "Ter"}


def norm(name):
    return " ".join((name or "").upper().split())


def ordinal(n):
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def pretty(name):
    """'WEST 72 STREET' -> 'W 72nd St', 'WEST END AVENUE' -> 'West End Ave'."""
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


def to_minutes(h, m, ampm):
    h, m = int(h), int(m or 0)
    if ampm == "AM":
        h = 0 if h == 12 else h
    elif ampm == "PM":
        h = 12 if h == 12 else h + 12
    return h * 60 + m


def parse_hhmm(s):
    """'7:45', '7:45AM', '8AM', '13:00' -> minutes since midnight."""
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?", s.strip().upper())
    if not m:
        raise argparse.ArgumentTypeError(f"bad time: {s!r}")
    return to_minutes(*m.groups())


def parse_schedule(desc):
    """Return (days:set[int], start_min, end_min) or None if unparseable."""
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


def fmt_time(mins):
    h, m = divmod(mins, 60)
    suffix = "AM" if h < 12 else "PM"
    h = h % 12 or 12
    return f"{h}:{m:02d}{suffix}" if m else f"{h}{suffix}"


def fmt_days(days):
    return "/".join(DAY_NAMES[d][:3].title() for d in sorted(days))


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


# ---------------------------------------------------------------- fetch

def fetch(args):
    where = ("borough='Manhattan' AND record_type='Current' "
             "AND upper(sign_description) like '%SANITATION BROOM%'")
    rows, offset, page = [], 0, 50000
    while True:
        q = urllib.parse.urlencode({"$where": where, "$limit": page, "$offset": offset,
                                    "$order": "order_number"})
        with urllib.request.urlopen(f"{API}?{q}", timeout=120) as r:
            batch = json.load(r)
        rows += batch
        print(f"  fetched {len(rows)} signs", file=sys.stderr)
        if len(batch) < page:
            break
        offset += page

    groups, skipped = defaultdict(lambda: {"signs": 0, "orders": set()}), 0
    coords = defaultdict(list)  # block face -> [(x, y), ...] for a map pin
    for r in rows:
        on, frm, to = norm(r.get("on_street")), norm(r.get("from_street")), norm(r.get("to_street"))
        num, town = locate(on, frm, to)
        if town != "WEST" or not (args.min_street <= num <= args.max_street):
            continue
        sched = parse_schedule(r.get("sign_description", ""))
        if not sched:
            skipped += 1
            continue
        days, start, end = sched
        face = (num, on, frm, to, r.get("side_of_street", ""))
        key = (*face, fmt_days(days), fmt_time(start), fmt_time(end))
        groups[key]["signs"] += 1
        groups[key]["orders"].add(r.get("order_number", ""))
        try:
            coords[face].append((float(r["sign_x_coord"]), float(r["sign_y_coord"])))
        except (KeyError, ValueError):
            pass

    latlon = {face: state_plane_to_latlon(sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts))
              for face, pts in coords.items() if pts}
    DATA.mkdir(exist_ok=True)
    with CLEANING_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["street_no", "on_street", "from_street", "to_street", "side",
                    "days", "start", "end", "sign_count", "order_numbers", "lat", "lon"])
        for key in sorted(groups):
            g = groups[key]
            lat, lon = latlon.get(key[:5], ("", ""))
            w.writerow([*key, g["signs"], " ".join(sorted(g["orders"])), lat, lon])
    print(f"Wrote {len(groups)} West Side block-face schedules to {CLEANING_CSV.relative_to(ROOT)}"
          f" ({skipped} unparseable signs skipped)")


def export(args):
    """Write site/data.json: one entry per block face with pretty names, pin, and schedules."""
    faces = {}
    with CLEANING_CSV.open() as f:
        for r in csv.DictReader(f):
            key = (int(r["street_no"]), r["on_street"], r["from_street"], r["to_street"], r["side"])
            face = faces.setdefault(key, {
                "n": key[0], "on": pretty(key[1]), "from": pretty(key[2]), "to": pretty(key[3]),
                "side": key[4], "lat": float(r["lat"]) if r["lat"] else None,
                "lon": float(r["lon"]) if r["lon"] else None, "s": []})
            days = sorted(DAY_ALIASES[d.upper()] for d in r["days"].split("/"))
            face["s"].append([days, parse_hhmm(r["start"]), parse_hhmm(r["end"])])
    out = {
        "generated": dt.datetime.now(NYC).isoformat(timespec="minutes"),
        "holidays": load_suspensions(),
        "blocks": sorted(faces.values(), key=lambda b: (b["n"], b["on"], b["side"])),
    }
    path = ROOT / "site" / "data.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"Wrote {len(faces)} block faces to {path.relative_to(ROOT)}")


# ---------------------------------------------------------------- analysis

def load_suspensions():
    out = {}
    for p in DATA.glob("asp_suspensions_*.csv"):
        with p.open() as f:
            out.update({r["date"]: r["holiday"] for r in csv.DictReader(f)})
    return out


def load_blocks(min_street, max_street):
    """{(street_no, on, from, to, side): [(days, start, end), ...]}"""
    if not CLEANING_CSV.exists():
        sys.exit("No data yet - run: python3 asp.py fetch")
    blocks = defaultdict(list)
    with CLEANING_CSV.open() as f:
        for r in csv.DictReader(f):
            num = int(r["street_no"])
            if not (min_street <= num <= max_street):
                continue
            days = {DAY_ALIASES[d.upper()] for d in r["days"].split("/")}
            key = (num, r["on_street"], r["from_street"], r["to_street"], r["side"])
            blocks[key].append((days, parse_hhmm(r["start"]), parse_hhmm(r["end"])))
    return blocks


def status(scheds, date, arrive, leave, holidays):
    """('clear', swept_earlier) | ('opens', minute_free) | ('blocked', None) for one block face."""
    if date.isoformat() in holidays:
        return "clear", False
    today = [(s, e) for days, s, e in scheds if date.weekday() in days]
    hits = [(s, e) for s, e in today if s < leave and e > arrive]
    if not hits:
        return "clear", any(e <= arrive for _, e in today)
    free_from = max(e for _, e in hits)
    return ("opens", free_from) if free_from < leave else ("blocked", None)


def next_cleaning(scheds, date, after_min, holidays):
    for offset in range(14):
        d = date + dt.timedelta(days=offset)
        if d.isoformat() in holidays:
            continue
        times = sorted(s for days, s, _ in scheds if d.weekday() in days
                       and (offset or s >= after_min))
        if times:
            label = "today" if offset == 0 else ("tomorrow" if offset == 1 else d.strftime("%a"))
            return f"{label} {fmt_time(times[0])}"
    return "-"


def block_label(on, frm, to):
    return f"{pretty(on)} ({pretty(frm)} → {pretty(to)})"


def map_url(on, frm, to):
    cross = to if "DEAD END" in frm or "RAMP" in frm else frm
    q = f"{pretty(on)} & {pretty(cross)}, Manhattan, NY"
    return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote_plus(q)


def classify(blocks, date, arrive, leave, holidays):
    clear, opens, blocked = [], [], []
    for key, scheds in blocks.items():
        kind, info = status(scheds, date, arrive, leave, holidays)
        {"clear": clear, "opens": opens, "blocked": blocked}[kind].append((key, scheds, info))
    clear.sort(key=lambda x: (x[0][0], not x[2]))
    opens.sort(key=lambda x: (x[2], x[0][0]))
    return clear, opens, blocked


# ---------------------------------------------------------------- digest

def digest(args):
    now = dt.datetime.now(NYC)
    date = dt.date.fromisoformat(args.date) if args.date else now.date()
    arrive, leave = parse_hhmm(args.arrive), parse_hhmm(args.leave)
    holidays = load_suspensions()
    blocks = load_blocks(args.min_street, args.max_street)
    clear, opens, blocked = classify(blocks, date, arrive, leave, holidays)
    window = f"{fmt_time(arrive)}–{fmt_time(leave)}"

    out = [f"# 🚗 West Side parking · {date:%a %b} {date.day}", ""]
    if date.isoformat() in holidays:
        out.append(f"**Street cleaning today: SUSPENDED ({holidays[date.isoformat()]})** — "
                   "park on any side. Meters and other signs may still apply.")
    elif not blocked and not opens:
        out.append("**Street cleaning today: none scheduled** — every block is clear.")
    else:
        out.append(f"**Street cleaning today: IN EFFECT** · {len(clear)} of {len(blocks)} block faces "
                   f"clear for {window} · W {ordinal(args.min_street)}–{ordinal(args.max_street)} St")
    out += ["", "> Toll-free from 61st St up (60th is in the toll zone). This covers street cleaning only — "
            "always read the posted signs for meters, no standing, and hydrants.", ""]

    if blocked or opens:
        out += [f"## ✅ Best bets — clear {window}, closest to 61st", "",
                "| St | Block | Side | Next cleaning |", "|---|---|---|---|"]
        for (num, on, frm, to, side), scheds, swept in clear[:args.top]:
            note = " · swept earlier" if swept else ""
            out.append(f"| {num} | [{block_label(on, frm, to)}]({map_url(on, frm, to)}) | {side} | "
                       f"{next_cleaning(scheds, date, leave, holidays)}{note} |")

        if opens:
            out += ["", "## ⏱ Opens up later this morning (nearest first within each time)", "",
                    "| Free from | St | Block | Side |", "|---|---|---|---|"]
            nearest = sorted(opens, key=lambda x: x[0][0])[:args.top]
            for (num, on, frm, to, side), _, free in sorted(nearest, key=lambda x: (x[2], x[0][0])):
                out.append(f"| {fmt_time(free)} | {num} | [{block_label(on, frm, to)}]"
                           f"({map_url(on, frm, to)}) | {side} |")

        out += ["", "## 🗺 By area", "",
                f"| Area | Clear {window} | Opens later | Cleaning during window |", "|---|---|---|---|"]
        for lo, hi, name in AREAS:
            if hi < args.min_street or lo > args.max_street:
                continue
            count = lambda rows: sum(lo <= k[0] <= hi for k, _, _ in rows)
            out.append(f"| {name} | {count(clear)} | {count(opens)} | {count(blocked)} |")

    tomorrow = date + dt.timedelta(days=1)
    t_clear, t_opens, t_blocked = classify(blocks, tomorrow, arrive, leave, holidays)
    if tomorrow.isoformat() in holidays:
        t_line = f"SUSPENDED ({holidays[tomorrow.isoformat()]})"
    elif not t_blocked and not t_opens:
        t_line = "no street cleaning"
    else:
        t_line = f"in effect — {len(t_clear)} of {len(blocks)} block faces clear {window}"
    upcoming = sorted((d, h) for d, h in holidays.items() if d > date.isoformat())
    nxt = (f"{dt.date.fromisoformat(upcoming[0][0]):%a %b %-d} ({upcoming[0][1]})"
           if upcoming else "none on file — add next year's calendar to data/")
    out += ["", "## 📅 Coming up", "",
            f"- **Tomorrow ({tomorrow:%a}):** {t_line}",
            f"- **Next holiday suspension:** {nxt}",
            f"- **Snow/emergency suspensions:** not predictable — check [NYC DOT]({DOT_ASP_URL}) or 311", "",
            f"<sub>Generated {now:%a %b %-d %-I:%M %p} ET · data: NYC Open Data parking signs</sub>"]

    text = "\n".join(out) + "\n"
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out}")
    else:
        print(text)


# ---------------------------------------------------------------- find

def find(args):
    date = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(NYC).date()
    arrive, leave = parse_hhmm(args.arrive), parse_hhmm(args.leave)
    holidays = load_suspensions()
    blocks = load_blocks(args.min_street, args.max_street)
    print(f"{date:%A %b %d, %Y}  |  park {fmt_time(arrive)}-{fmt_time(leave)}  |  "
          f"W {args.min_street}-{args.max_street} St")
    if date.isoformat() in holidays:
        print(f"ASP SUSPENDED today ({holidays[date.isoformat()]}) - street cleaning rules don't apply.")
        return

    clear, opens, blocked = classify(blocks, date, arrive, leave, holidays)
    print(f"{len(clear)} clear, {len(opens)} open up later, {len(blocked)} have cleaning during your window.\n")
    print(f"{'St':>4}  {'Block':<56} {'Side':<4} {'Next cleaning':<16} Note")
    for (num, on, frm, to, side), scheds, swept in clear[:args.limit]:
        print(f"{num:>4}  {block_label(on, frm, to)[:56]:<56} {side:<4} "
              f"{next_cleaning(scheds, date, leave, holidays):<16} {'swept earlier' if swept else ''}")
    if len(clear) > args.limit:
        print(f"\n... {len(clear) - args.limit} more (use --limit)")
    print("\nAlways read the posted signs: this only covers street cleaning, not meters,"
          " no-standing zones, hydrants, or emergency suspensions.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download and rebuild data/street_cleaning.csv")
    f.add_argument("--min-street", type=int, default=MIN_STREET)
    f.add_argument("--max-street", type=int, default=MAX_STREET)
    f.set_defaults(func=fetch)

    sub.add_parser("export", help="write site/data.json for the web dashboard").set_defaults(func=export)

    for name, func, help_ in [("digest", digest, "daily markdown digest"),
                              ("find", find, "full list of blocks clear for your window")]:
        q = sub.add_parser(name, help=help_)
        q.add_argument("--date", help="YYYY-MM-DD (default: today, New York time)")
        q.add_argument("--arrive", default=DEFAULT_ARRIVE, help=f"e.g. 7:45 or 7:45AM (default {DEFAULT_ARRIVE})")
        q.add_argument("--leave", default=DEFAULT_LEAVE, help=f"e.g. 12:00 (default {DEFAULT_LEAVE})")
        q.add_argument("--min-street", type=int, default=MIN_STREET)
        q.add_argument("--max-street", type=int, default=FOCUS_MAX)
        q.set_defaults(func=func)
        if name == "digest":
            q.add_argument("--top", type=int, default=12, help="rows per table (default 12)")
            q.add_argument("--out", help="write markdown to this file instead of stdout")
        else:
            q.add_argument("--limit", type=int, default=40)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

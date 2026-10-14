#!/usr/bin/env python3
"""Street-cleaning (Alternate Side Parking) finder for upper Manhattan.

  python3 asp.py fetch                      # refresh data/street_cleaning.csv from NYC Open Data
  python3 asp.py find --arrive 7:45 --leave 12:00 [--date 2026-10-15] [--town west]

Stdlib only. Source: NYC DOT "Parking Regulation Locations and Signs" (nfid-uabd).
"""
import argparse
import csv
import datetime as dt
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CLEANING_CSV = DATA / "street_cleaning.csv"
API = "https://data.cityofnewyork.us/resource/nfid-uabd.json"

# 60th St is inside the congestion toll zone ("south of and including 60th Street"),
# so the first toll-free street is 61st.
MIN_STREET, MAX_STREET, FOCUS_MAX = 61, 185, 110

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


def norm(name):
    return " ".join((name or "").upper().split())


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


def parse_hhmm(s):
    """'7:45', '7:45AM', '8AM', '13:00' -> minutes since midnight."""
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?", s.strip().upper())
    if not m:
        raise argparse.ArgumentTypeError(f"bad time: {s!r}")
    return to_minutes(*m.groups())


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
    for r in rows:
        on, frm, to = norm(r.get("on_street")), norm(r.get("from_street")), norm(r.get("to_street"))
        num, town = locate(on, frm, to)
        if num is None or not (args.min_street <= num <= args.max_street):
            continue
        sched = parse_schedule(r.get("sign_description", ""))
        if not sched:
            skipped += 1
            continue
        days, start, end = sched
        key = (num, town, on, frm, to, r.get("side_of_street", ""), fmt_days(days),
               fmt_time(start), fmt_time(end))
        groups[key]["signs"] += 1
        groups[key]["orders"].add(r.get("order_number", ""))

    DATA.mkdir(exist_ok=True)
    with CLEANING_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["street_no", "town", "on_street", "from_street", "to_street", "side",
                    "days", "start", "end", "sign_count", "order_numbers"])
        for key in sorted(groups):
            g = groups[key]
            w.writerow([*key, g["signs"], " ".join(sorted(g["orders"]))])
    print(f"Wrote {len(groups)} block-face schedules to {CLEANING_CSV.relative_to(ROOT)}"
          f" ({skipped} unparseable signs skipped)")


# ---------------------------------------------------------------- find

def load_suspensions():
    out = {}
    for p in DATA.glob("asp_suspensions_*.csv"):
        with p.open() as f:
            out.update({r["date"]: r["holiday"] for r in csv.DictReader(f)})
    return out


def load_blocks():
    if not CLEANING_CSV.exists():
        sys.exit("No data yet - run: python3 asp.py fetch")
    blocks = defaultdict(list)
    with CLEANING_CSV.open() as f:
        for r in csv.DictReader(f):
            days = {DAY_ALIASES[d.upper()] for d in r["days"].split("/")}
            start, end = parse_hhmm(r["start"]), parse_hhmm(r["end"])
            key = (int(r["street_no"]), r["town"], r["on_street"], r["from_street"],
                   r["to_street"], r["side"])
            blocks[key].append((days, start, end))
    return blocks


def next_cleaning(scheds, date, after_min):
    for offset in range(8):
        d = date + dt.timedelta(days=offset)
        times = sorted(s for days, s, _ in scheds if d.weekday() in days
                       and (offset or s >= after_min))
        if times:
            label = "today" if offset == 0 else d.strftime("%a")
            return f"{label} {fmt_time(times[0])}"
    return "-"


def find(args):
    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    arrive, leave = parse_hhmm(args.arrive), parse_hhmm(args.leave)
    holiday = load_suspensions().get(date.isoformat())
    print(f"{date:%A %b %d, %Y}  |  park {fmt_time(arrive)}-{fmt_time(leave)}  |  "
          f"streets {args.min_street}-{args.max_street}  |  town: {args.town}")
    if holiday:
        print(f"ASP SUSPENDED today ({holiday}) - street cleaning rules don't apply."
              " Other signs (meters, no standing) may still apply.")
        return

    ok, conflicts = [], 0
    for key, scheds in load_blocks().items():
        num, town = key[0], key[1]
        if not (args.min_street <= num <= args.max_street):
            continue
        if args.town != "both" and town != args.town.upper():
            continue
        today = [(s, e) for days, s, e in scheds if date.weekday() in days]
        if any(s < leave and e > arrive for s, e in today):
            conflicts += 1
            continue
        swept = any(e <= arrive for _, e in today)
        ok.append((num, key, scheds, swept))

    ok.sort(key=lambda x: (x[0], not x[3]))
    print(f"{len(ok)} block faces are clear of street cleaning for your window "
          f"({conflicts} have cleaning during it).\n")
    print(f"{'St':>4}  {'Block':<58} {'Side':<4} {'Next cleaning':<14} Note")
    for num, (_, _, on, frm, to, side), scheds, swept in ok[:args.limit]:
        block = f"{on.title()} ({frm.title()} -> {to.title()})"
        note = "swept earlier today" if swept else ""
        print(f"{num:>4}  {block[:58]:<58} {side:<4} {next_cleaning(scheds, date, leave):<14} {note}")
    if len(ok) > args.limit:
        print(f"\n... {len(ok) - args.limit} more (use --limit)")
    print("\nAlways read the posted signs: this only covers street cleaning, not meters,"
          " no-standing zones, hydrants, or emergency suspensions (check @NYCASP / 311).")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download and rebuild data/street_cleaning.csv")
    f.add_argument("--min-street", type=int, default=MIN_STREET)
    f.add_argument("--max-street", type=int, default=MAX_STREET)
    f.set_defaults(func=fetch)

    q = sub.add_parser("find", help="list block faces with no cleaning during your stay")
    q.add_argument("--date", help="YYYY-MM-DD (default: today)")
    q.add_argument("--arrive", default="8:00", help="e.g. 7:45 or 7:45AM (default 8:00)")
    q.add_argument("--leave", default="12:00", help="e.g. 12:00 (default 12:00)")
    q.add_argument("--min-street", type=int, default=MIN_STREET)
    q.add_argument("--max-street", type=int, default=FOCUS_MAX)
    q.add_argument("--town", choices=["east", "west", "both"], default="both")
    q.add_argument("--limit", type=int, default=40)
    q.set_defaults(func=find)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

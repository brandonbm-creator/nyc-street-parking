# Manhattan Street Parking · v1.1

### 👉 Live dashboard: https://brandonbm-creator.github.io/nyc-street-parking/

A map-first, phone-first dashboard of **street cleaning (Alternate Side Parking, "ASP")** for the Upper West Side
from 61st St up, for our Harlem / Washington Heights locations, and for the **Stuyvesant HS zone** downtown
(Battery Park City from Pier 40 south to the Battery). It answers one question fast: *where can I leave the car
without getting swept?*

The congestion toll zone covers Manhattan "south of *and including* 60th Street," so 61st is the first toll-free street.
The Stuyvesant zone is inside the toll zone: the toll is charged once per day on entry, so use that zone when the trip
downtown is happening anyway.

## What you get

- **📍 Park near me now** — uses your phone's location and the current time, shows the best options around you.
- **⭐ Wait & take** — blocks where a cleaning window's **halfway mark** is coming up within 45 minutes. On these
  competitive blocks cars start moving back in at the halfway mark, so the app shows a *suggested arrival*
  (the halfway mark) **and** the *posted ASP end time*. The restriction legally runs to the end time even after the
  sweeper passes ([NYC311 KA-01011](https://portal.311.nyc.gov/article/?kanumber=KA-01011)); the suggested arrival is
  when move-in typically starts, not when the rule ends. Vacancy is not measured.
- **No ASP** — blocks with no street cleaning during your stay, nearest to 61st (or nearest to you) first.
- **Ends later / Avoid** — ASP that ends before you leave (competitive once past the halfway mark) and ASP that
  runs past when you leave.
- **Map** — every block face colored by status; tap a dot for the schedule and directions. Popups stay open
  through the once-a-minute "Now" refresh.
- **Our locations** — a tile per store and site with a walking ring (~4, ~6, or ~12 min) and blocks sorted by walk time.
  A location can carry its own coverage radius (`radius` in `data/locations.csv`); the picker then adds a **Whole zone**
  option. The Stuyvesant HS zone is a 1.5 km circle on 345 Chambers St that takes in Pier 40 (1.28 km north) and
  the Battery (1.45 km south): about 265 block faces across Battery Park City, Tribeca, Hudson Square and the Civic Center.
- **Honest states** — the card distinguishes *ASP in effect*, *No ASP*, *ASP suspended (holiday)*, *No blocks in
  range*, *Check your times* (invalid stay) and *Data unavailable*. Banners warn when the sign data is stale, when the
  holiday calendar for the chosen year isn't on file, and when GPS accuracy is wider than the search radius.

**Rules of use:** this covers street-cleaning signs only. Meters, No Standing, hydrants, bus stops and construction
permits aren't included, snow/emergency suspensions aren't predictable, and vacancy isn't measured.
**Always read the posted signs.**

## Our locations

| # | Location | What | Direct link |
|---|---|---|---|
| 1 | Subway · 3559 Broadway (at W 146th St; 600 W 146th St is the same building) | Subway restaurant #40614 | [open](https://brandonbm-creator.github.io/nyc-street-parking/?place=1) |
| 2 | Subway · 3920 Broadway | Subway restaurant #24100 | [open](https://brandonbm-creator.github.io/nyc-street-parking/?place=2) |
| 3 | Subway · 1502 St Nicholas Ave | Subway restaurant #46882 | [open](https://brandonbm-creator.github.io/nyc-street-parking/?place=3) |
| 4 | National Action Network · 6 Hancock Pl | NAN headquarters | [open](https://brandonbm-creator.github.io/nyc-street-parking/?place=4) |
| 5 | ALIA · 121 Lenox Ave (aka 101 W 116th St) | Nonprofit development | [open](https://brandonbm-creator.github.io/nyc-street-parking/?place=5) |
| 6 | 145 · W 145th St & Lenox Ave | Upcoming development | [open](https://brandonbm-creator.github.io/nyc-street-parking/?place=6) |
| 7 | Stuyvesant HS · 345 Chambers St | Battery Park City zone, Pier 40 to the Battery (inside the toll zone) | [open](https://brandonbm-creator.github.io/nyc-street-parking/?place=7&radius=1500) |
| 📍 | Wherever you are | Phone location + current time | [open](https://brandonbm-creator.github.io/nyc-street-parking/?place=me&now=1) |

Save `?place=me&now=1` to your phone's home screen (Safari: Share → **Add to Home Screen**) for a one-tap
"find me parking". Deep links: `?place=1..7|me`, `?now=1`, `?view=uws|places`, `?radius=250|400|800` (or a zone's
own radius such as `1500`; a radius past the location's coverage is clamped to it), `?date=YYYY-MM-DD`, `?arrive=9:00`,
`?leave=12:00` (invalid values are ignored and the app says so).

## How it works

```
NYC Open Data sign API ─► asp.py fetch ─► data/street_cleaning.csv + data/snapshot.json   (validated, atomic, committed by CI)
                                                       │
                              asp.py export ─► site/data.json   (validated; built into the Pages artifact, not committed)
                                                       │
                                     site/ (static HTML/CSS/JS, no build) ─► GitHub Pages
```

- **`asp.py`** (stdlib only): `fetch` downloads every "sanitation broom" sign in Manhattan, groups them into one
  schedule per block face, keeps the West Side 61st–185th plus anything within each location's coverage radius
  (800 m by default, 1,500 m for the Stuyvesant zone), converts the
  sign coordinates (EPSG:2263) to lat/lon, and **refuses** the result if the API schema changed, fewer than
  1,500 schedules came back, or the snapshot shrank by more than 15% (`--force` overrides). Writes are atomic, so a
  crash never leaves a truncated file. `export` validates the snapshot again and writes `site/data.json` with
  `source_fetched_at` (when the signs were fetched) separate from `built_at` (when the site was built).
- **`site/logic.js`** is the pure street-cleaning logic (status, next cleaning, time/date validation). It mirrors
  `asp.py`; **`tests/fixtures/cases.json`** is run through both, so the browser and the CLI can't drift apart.
- **`.github/workflows/deploy.yml`** runs Monday–Saturday at 10:15 UTC (6:15 AM EDT, 5:15 AM EST) and on pushes
  to `main`: tests first, then a validated refresh (a failed refresh keeps the committed snapshot and the site
  shows its age), then export, then deploy. A failed test, export, or validation stops the deploy, and the build
  deploys exactly the revision it tested (the snapshot commit is never rebased onto newer code). The workflow
  re-enables its own schedule each run because GitHub disables cron on public repos after 60 days without
  activity. **`ci.yml`** runs the same tests and a trial export on every pull request and non-`main` push.
- A block face with an ASP sign the parser can't model (no times, or an overnight window) is kept as
  **unknown** (gray, under *Avoid*) rather than silently dropped, so it can never show as "No ASP".

## Local use (Python 3.10+, Node 20+ for the JS tests, no packages)

```sh
python3 asp.py export                          # build site/data.json from the committed snapshot
python3 -m http.server -d site 8000            # dashboard at http://localhost:8000
python3 asp.py find --near 3920 --arrive now   # wait-&-take + no-ASP blocks near a location (or 'lat,lon')
python3 asp.py find --arrive 9:00 --leave 9:10 # an explicit stay is kept exactly as given, even 10 minutes
python3 asp.py digest --date 2026-10-16        # markdown digest for a morning (7:30–12:00 by default)
python3 asp.py fetch                           # refresh the snapshot by hand (don't commit it; CI owns it)

python3 -m unittest discover -s tests -v       # Python tests (43)
node --test tests/                             # JavaScript tests (10), same fixtures
```

## Files

| File | What |
|---|---|
| `site/index.html`, `site/app.js`, `site/style.css` | The dashboard UI (no build step) |
| `site/logic.js` | Pure street-cleaning logic shared with the tests |
| `site/data.json` | Generated by `asp.py export`; git-ignored |
| `site/vendor/leaflet/` | Leaflet 1.9.4, vendored so the map doesn't depend on a CDN (BSD-2, see LICENSE) |
| `asp.py` | `fetch`, `export`, `digest`, `find` |
| `data/street_cleaning.csv`, `data/snapshot.json` | The validated sign snapshot and its provenance (committed by CI) |
| `data/locations.csv` | Our stores and sites: `name,kind,type,address,lat,lon,radius`; `type` sets the pin color (subway, nonprofit, development, site, school); `radius` (m, optional, default 800, max 3,000) is how far the sign data is kept around the place |
| `data/asp_suspensions_<year>.csv` | Holiday suspensions per year, verified against the NYC DOT table |
| `tests/` | `test_asp.py`, `logic.test.js`, `fixtures/cases.json` |
| `.github/workflows/deploy.yml`, `ci.yml` | Daily refresh + deploy (main only); tests on every pull request |
| `data/my_spots.csv` | Your own parking log; git-ignored |

## Operations for the year

**Holiday calendar (do this each December).** NYC DOT publishes next year's suspensions at
https://www.nyc.gov/html/dot/html/motorist/alternate-side-parking.shtml. Create `data/asp_suspensions_2027.csv`
with the header `date,holiday` and one row per date (`2027-01-01,New Year's Day`), copied from the official table,
never guessed. Push it; the tests check the dates and the deploy picks it up. Until it exists the site shows a
banner on any 2027 date ("suspensions unknown, ASP assumed in effect") and, from December 1, a reminder.
The 2026 file was checked date-by-date against the DOT table (42 dates).

**Sign data.** The daily refresh commits a new snapshot only when it passes validation. If the data is more than
7 days old the site says so; look at the repo's **Actions** tab for the failing step. If the city legitimately
removed many signs, run `python3 asp.py fetch --force` locally (`--force` skips the shrink-versus-previous check;
the 1,500-schedule floor still applies unless you lower `--min-schedules`), check the diff, and commit it.

**Schedule.** If the daily run stops appearing in Actions, GitHub has disabled the schedule: open **Actions →
Refresh data and deploy dashboard**, press **Enable workflow**, then **Run workflow**
([GitHub docs](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)).
Each successful run re-enables the schedule itself.

**Locations.** Edit `data/locations.csv` (lat/lon from
[NYC GeoSearch](https://geosearch.planninglabs.nyc/v2/search?text=6%20Hancock%20Place%20Manhattan)) and push. Leave
`radius` blank for a normal location; set it (100–3,000 m) for a place that should cover a whole zone, as Stuyvesant does.
The next deploy fetches the new area; until then its tile says "no blocks in range".

**Tuning.** The wait & take threshold is `MAX_WAIT` in `asp.py`, `site/logic.js`, and `tests/fixtures/cases.json`
(`max_wait`); the tests fail if they disagree.

**Don't commit generated files.** `site/data.json` is built in CI; `data/street_cleaning.csv` and
`data/snapshot.json` are committed by CI. Committing them locally invites merge races.

## Next iteration (v1.1 ideas)

Meter and No Standing layers from the same sign dataset; a vacancy signal (crowd or camera); offline support
(service worker); push notifications at the suggested arrival time; a 2028 calendar check that opens an issue.

## Sources

[Parking Regulation Locations and Signs](https://data.cityofnewyork.us/Transportation/Parking-Regulation-Locations-and-Signs/nfid-uabd)
(NYC Open Data) · [NYC DOT ASP suspensions](https://www.nyc.gov/html/dot/html/motorist/alternate-side-parking.shtml) ·
[NYC311 KA-01011](https://portal.311.nyc.gov/article/?kanumber=KA-01011) · map tiles © [OpenStreetMap](https://www.openstreetmap.org/copyright) · [Leaflet](https://leafletjs.com)

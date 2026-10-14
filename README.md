# West Side Manhattan Street Parking

### 👉 Live dashboard: https://brandonbm-creator.github.io/nyc-street-parking/

A map-first dashboard of **Upper West Side street cleaning** (Alternate Side Parking), starting at 61st St.
The congestion toll zone covers Manhattan "south of *and including* 60th Street," so 61st is the first toll-free street.

- **Big status card:** cleaning in effect, suspended for a holiday, or no cleaning
- **Map:** every block face, colored green (clear for your window), amber (opens later), or red (cleaning)
- **Area tiles:** Lincoln Sq, UWS core, UWS north, and Manhattan Valley; tap one to zoom and filter
- **Lists:** *Clear all morning* (nearest to 61st first), *Opens later*, and *Avoid*, each with a one-tap **Go ↗** for directions
- **Controls:** Today, Tomorrow, or any date; arrive and leave times (default 7:30 AM–noon, remembered on your device); northern limit up to 185th

On a phone, open the link in Safari, tap Share, then **Add to Home Screen** to get an app icon.

## How it works

```
NYC Open Data sign API ──► asp.py fetch ──► data/street_cleaning.csv
                                              │
                           asp.py export ──► site/data.json ──► site/ (static HTML/JS/CSS, no build)
                           asp.py digest ──► TODAY.md (plain-text version)
```

A GitHub Action (`.github/workflows/digest.yml`) runs Monday to Saturday at about 6:15 AM ET. It refreshes the data,
commits it, and deploys `site/` to GitHub Pages. It also redeploys whenever `site/`, `asp.py`, or the holiday files change.

All the street-cleaning logic runs in the browser (`site/app.js`), so the site can be edited with any tool or AI
assistant: change the HTML, CSS, or JS, push, and it deploys.

## Local use (Python 3.10+, no dependencies)

```sh
python3 -m http.server -d site 8000          # dashboard at http://localhost:8000
python3 asp.py digest                         # text digest in the terminal
python3 asp.py find --max-street 185          # full list, including Harlem / Washington Heights
python3 asp.py fetch && python3 asp.py export # refresh data by hand
```

## Files

| File | What |
|---|---|
| `site/` | The dashboard: `index.html`, `app.js`, `style.css`, `data.json` (generated) |
| `asp.py` | `fetch`, `export`, `digest`, and `find` commands |
| `data/street_cleaning.csv` | West Side street-cleaning schedules per block face, 61st–185th St, with lat/lon |
| `data/asp_suspensions_2026.csv` | 2026 holiday suspensions ([NYC DOT](https://www.nyc.gov/html/dot/html/motorist/alternate-side-parking.shtml)). **Add a 2027 file before January.** |
| `TODAY.md` | Plain-text digest, rebuilt daily |
| `data/my_spots.csv` | Your own parking log; git-ignored so it stays only on your computer |

## Caveats

- This only covers street-cleaning signs. Meters, No Standing zones, hydrants, bus stops, and construction
  permits aren't included, so **always read the posted signs.**
- Snow and emergency suspensions aren't known in advance. Check NYC DOT or 311.
- Source data: [Parking Regulation Locations and Signs](https://data.cityofnewyork.us/Transportation/Parking-Regulation-Locations-and-Signs/nfid-uabd).

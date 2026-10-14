# West Side Manhattan Street Parking: Daily Street-Cleaning Digest

### 👉 [Today's digest (TODAY.md)](TODAY.md)

Every morning (Mon–Sat, ~6:15 AM ET) a GitHub Action builds a digest of **Upper West Side**
street-cleaning (Alternate Side Parking) and posts it as a GitHub issue, which sends you an
email or a GitHub mobile app notification. The digest includes:

- **Street cleaning status** for the day: in effect, suspended for a holiday, or not scheduled
- **Best bets:** block faces with no cleaning from 7:30 AM to noon, nearest to 61st St first, with Google Maps links
- **Opens up later:** blocks you can park on once that morning's cleaning ends
- **By area:** Lincoln Sq (61–72), UWS core (73–86), UWS north (87–96), Manhattan Valley (97–110)
- **Coming up:** tomorrow's status and the next holiday suspension

Why it starts at 61st St: the congestion toll zone covers Manhattan "south of *and including* 60th Street."

## Run it locally (Python 3.10+, no dependencies)

```sh
python3 asp.py digest                                  # today's digest
python3 asp.py digest --date 2026-10-16 --arrive 8:15 --leave 11
python3 asp.py find --limit 100                        # full list of clear blocks
python3 asp.py find --max-street 185                   # include Harlem / Washington Heights / Inwood
python3 asp.py fetch                                   # refresh sign data (the Action does this daily)
```

To get a one-word terminal shortcut, add this line to `~/.zshrc`:

```sh
alias parking='python3 ~/projects/nyc-street-parking/asp.py digest'
```

## Files

| File | What |
|---|---|
| `asp.py` | `fetch`, `digest`, and `find` commands |
| `TODAY.md` | Latest digest (rebuilt daily by the Action) |
| `.github/workflows/digest.yml` | Daily schedule; you can also start it by hand from the repo's **Actions** tab |
| `data/street_cleaning.csv` | West Side street-cleaning schedules per block face, 61st–185th St |
| `data/asp_suspensions_2026.csv` | 2026 holiday suspensions ([NYC DOT](https://www.nyc.gov/html/dot/html/motorist/alternate-side-parking.shtml)). **Add a 2027 file before January.** |
| `data/my_spots.csv` | Your own log: where you parked and how it went |

## Caveats

- This only covers street-cleaning signs. Meters, No Standing zones, hydrants, bus stops, and
  construction permits aren't included, so **always read the posted signs.**
- Snow and emergency suspensions aren't known in advance. Check NYC DOT or 311.
- Source data: [Parking Regulation Locations and Signs](https://data.cityofnewyork.us/Transportation/Parking-Regulation-Locations-and-Signs/nfid-uabd).

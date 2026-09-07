# maps-list-to-itinerary

Turn a **public Google Maps list** into a day-by-day itinerary: places grouped
by area, ordered to minimise back-tracking, with arrival times, walk/transit
legs, per-place photo + category + suggested dwell time, "open in Maps" links,
and a GPX export.

```
list URL ──▶ extract ──▶ enrich (opt.) ──▶ filter ──▶ plan ──▶ itinerary.html / .md / .json / .gpx
```

## The interactive page (`itinerary.html`)

A self-contained page (no server, no build) that recomputes the schedule in the
browser as you change things. All edits are saved to that browser's
`localStorage` — "Ripristina tutto" clears them.

- **Two switches**: show/hide the list's generic areas (cities, neighborhoods),
  and show/hide permanently-closed / limited-access places. Off by default.
- **Per stop**: disable it (the legs after it re-close automatically), or type
  the **actual arrival time** to re-anchor everything that follows, or
  "Riottimizza seguito" to re-order the rest of the day from that point.
- **Add a stop from a Google Maps link** — paste a full URL (`/maps/place/…`
  or one with `@lat,lng`); choose a day, or let it drop into whichever day the
  route detour is smallest.
- **Per day**: editable start time, optional end time (warns if the plan runs
  past it); if a day ends well before its end time, a panel offers nearby
  stops from later days to pull in. "Riottimizza giorno" re-orders it.
- **Global**: default start + default end time for every day.
- **Left rail**: sticky day index; click to jump, current day highlights.
- **Multiple itineraries**: the switcher at the top holds more than one trip.
  "＋ Nuovo itinerario" takes a title + a list of Google Maps place links
  (one per line) and builds a fresh plan client-side — those places have no
  photo/category (the browser can't query Google) but you get an ordered,
  editable multi-day itinerary. Each trip keeps its own edits.

Travel times on this page are straight-line estimates; the `.md` / `.json` use
the real matrix (Routes API) when a key is set.

## Why it's built in stages

Google has **no public API for saved/shared lists**, so pulling the places out
is the fragile part. Everything downstream works off plain `Place` objects, so
you can always skip scraping and feed a hand-made JSON file instead.

## Install

```bash
pip install -e .
# only if you want to scrape a live URL:
pip install -e ".[browser]"
python -m playwright install chromium
```

## Use

```bash
# from a live list (needs the browser extra)
maps-itinerary "https://maps.app.goo.gl/xJpi4UPLgSBQSfrw9"

# from a page you saved yourself after scrolling the whole list
maps-itinerary --from-html saved-list.html

# from curated data (always works, no deps beyond requests)
maps-itinerary --from-json examples/japan-2028.places.json --no-enrich
```

Output lands in `./out/` (`itinerary.html`, `itinerary.md`, `itinerary.json`,
`itinerary.gpx`). Restrict with `--format html` or `--format md,json`.

## Filtering (what actually gets planned)

| Rule | Default | Flag |
|---|---|---|
| Only **specific places** — cities, neighborhoods, prefectures are dropped | always on | `--keep "Ginza"` to force one back in |
| **Permanently-closed** places | dropped | `--include-closed` to keep them (flagged ⚠️ in the output) |
| Entries with no coordinates | dropped (listed in the "ignored" section) | — |
| Rating floor | off | `--min-rating 4.0` |
| Force-drop a place | — | `--drop "FamilyMart Ebisubashi"` |

Everything dropped is listed at the bottom of `itinerary.md` so nothing
disappears silently.

## Planning options

| Flag | Meaning |
|---|---|
| `--days N` | force a day count (default: auto from visit time + place count) |
| `--max-per-day N` | cap stops per day (default 6) |
| `--pace relaxed\|normal\|packed` | scales visit durations and day length |
| `--travel-mode walking\|transit\|driving` | default `transit` |
| `--start "LAT,LNG"` | anchor for day 1 (e.g. your hotel) — also sets which city you start in |
| `--day-start HH:MM` | default `09:30` |

The planner first splits places into **regions** (things >40 km apart are
treated as different bases — Tokyo vs Kyoto vs a Mt. Fuji day trip), shares the
day budget between regions by size, then clusters each region into days and
orders every day with nearest-neighbour + 2-opt.

## Enrichment (optional)

Set `GOOGLE_MAPS_API_KEY` (Places API **New** + Routes API enabled) to add:

- real addresses, categories, opening hours, editorial blurbs
- `businessStatus` — a reliable "permanently closed" signal
- **real travel times** from the Routes API instead of straight-line estimates

Results are cached under `.cache/` so re-runs cost nothing. Without a key the
tool still produces a full itinerary using category-based default visit times
and great-circle distances.

Get a key: <https://console.cloud.google.com/> → enable *Places API (New)* and
*Routes API* → Credentials → API key. Both have a monthly free allowance.

## The `Place` JSON shape (for `--from-json` / `--save-places`)

```json
{
  "name": "Nishiki Market",
  "lat": 35.0050, "lng": 135.7649,
  "category": "Mercato",
  "rating": 4.3, "reviews": 53794,
  "permanently_closed": false,
  "note": "optional free text"
}
```

`examples/japan-2028.places.json` is the "Japan 2028" list (49 places), scraped
live — real coordinates and feature ids. A top-level `{"title": ..., "places":
[...]}` wrapper is optional; a bare array works too.

## Limitations

- Scraping breaks when Google changes its markup; `--from-html` / `--from-json`
  are the stable fallbacks.
- Opening hours are **not** used to reshuffle the plan yet — they're shown so
  you can adjust manually.
- Region ordering is nearest-neighbour from `--start` (or the southernmost
  region); reverse it with `--start`.

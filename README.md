# maps-list-to-itinerary

Turn a **public Google Maps list** into a day-by-day itinerary: places grouped
by area, ordered to minimise back-tracking, with arrival times, walking/transit
legs, "open in Maps" links, and a GPX export.

```
list URL ──▶ extract ──▶ enrich (opt.) ──▶ filter ──▶ plan ──▶ itinerary.md / .json / .gpx
```

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

Output lands in `./out/` (`itinerary.md`, `itinerary.json`, `itinerary.gpx`).

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

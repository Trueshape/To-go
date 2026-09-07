# Example: "Japan 2028"

`japan-2028.places.json` — the 49 places from the public list
<https://maps.app.goo.gl/xJpi4UPLgSBQSfrw9>, scraped live (real coordinates and
feature ids; `reviews` and opening hours only get filled in when you run with a
`GOOGLE_MAPS_API_KEY`).

`japan-2028.output/` — what the tool produces from it:

```bash
maps-itinerary --from-json examples/japan-2028.places.json --no-enrich --out examples/japan-2028.output
```

Result: 9 days (Osaka → Nara → Kyoto → a Mt. Fuji day trip → 5 in Tokyo).
7 generic areas (Ginza, Shimokitazawa, Enoshima, …) and 1 permanently-closed
place (Unicorn Gundam) are dropped and listed at the bottom of the itinerary.
Add `--include-closed` to keep the latter, `--keep "Ginza"` to force one back.

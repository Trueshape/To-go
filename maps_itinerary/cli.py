"""Command-line entry point: URL (or saved data) -> itinerary files."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .enrich import Enricher
from .extract import (
    extract_from_browser,
    extract_from_html,
    load_from_json,
    resolve_list_url,
)
from .filters import FilterConfig, apply_filters
from .plan import PlanConfig, build_itinerary
from .render import write_all


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="maps-itinerary",
        description="Turn a public Google Maps list into a day-by-day itinerary.",
    )
    ap.add_argument("url", nargs="?", help="public Google Maps list URL (maps.app.goo.gl/...)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    src = ap.add_argument_group("input")
    src.add_argument("--from-html", metavar="FILE", help="parse a saved, fully-scrolled list page instead of scraping")
    src.add_argument("--from-json", metavar="FILE", help="load places from a JSON file (skips extraction)")
    src.add_argument("--headful", action="store_true", help="show the browser window while scraping")
    src.add_argument("--save-places", metavar="FILE", help="also write the extracted places to this JSON file")

    flt = ap.add_argument_group("filtering")
    g = flt.add_mutually_exclusive_group()
    g.add_argument("--include-closed", dest="include_closed", action="store_true",
                   help="keep places marked 'permanently closed'")
    g.add_argument("--exclude-closed", dest="include_closed", action="store_false",
                   help="drop permanently-closed places (default)")
    ap.set_defaults(include_closed=False)
    flt.add_argument("--min-rating", type=float, metavar="X", help="drop places rated below X")
    flt.add_argument("--keep", action="append", default=[], metavar="NAME",
                     help="force-keep this place even if it looks generic (repeatable)")
    flt.add_argument("--drop", action="append", default=[], metavar="NAME",
                     help="force-drop this place (repeatable)")

    enr = ap.add_argument_group("enrichment (needs GOOGLE_MAPS_API_KEY)")
    enr.add_argument("--no-enrich", action="store_true", help="skip all Places/Routes API calls")
    enr.add_argument("--lang", default="it", help="language for categories / hours (default: it)")
    enr.add_argument("--cache-dir", default=".cache")

    pl = ap.add_argument_group("planning")
    pl.add_argument("--days", type=int, help="target number of days (default: auto)")
    pl.add_argument("--max-per-day", type=int, default=6)
    pl.add_argument("--pace", choices=["relaxed", "normal", "packed"], default="normal")
    pl.add_argument("--travel-mode", choices=["walking", "transit", "driving"], default="transit")
    pl.add_argument("--start", metavar="LAT,LNG", help="anchor point for day 1 (e.g. your hotel)")
    pl.add_argument("--day-start", default="09:30")

    out = ap.add_argument_group("output")
    out.add_argument("--out", default="out", help="output directory (default: ./out)")
    out.add_argument("--format", default="md,json,gpx", help="comma list of: md,json,gpx")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not (args.url or args.from_html or args.from_json):
        build_parser().error("give a list URL, or --from-html FILE, or --from-json FILE")

    # ---- 1. extract ---------------------------------------------------- #
    source_url = args.url or (args.from_html or args.from_json)
    if args.from_json:
        print(f"Loading places from {args.from_json}")
        places = load_from_json(args.from_json)
    elif args.from_html:
        print(f"Parsing saved page {args.from_html}")
        places = extract_from_html(args.from_html)
    else:
        real = resolve_list_url(args.url)
        print(f"Scraping list: {real}")
        places = extract_from_browser(real, headful=args.headful)
        source_url = real

    print(f"  extracted {len(places)} entries")
    if not places:
        print("Nothing extracted. Try --headful, or save the page and use --from-html.")
        return 2

    if args.save_places:
        import json

        with open(args.save_places, "w", encoding="utf-8") as fh:
            json.dump([p.to_dict() for p in places], fh, ensure_ascii=False, indent=2)
        print(f"  wrote {args.save_places}")

    # ---- 2. enrich --------------------------------------------------- #
    enricher = Enricher(
        api_key=None if args.no_enrich else os.environ.get("GOOGLE_MAPS_API_KEY"),
        cache_dir=args.cache_dir,
        lang=args.lang,
    )
    if enricher.enabled:
        print("Enriching via Places API ...")
        enricher.enrich_all(places)
    else:
        print("  (no enrichment — set GOOGLE_MAPS_API_KEY for hours/addresses/real travel times)")

    # ---- 3. filter -------------------------------------------------- #
    fcfg = FilterConfig(
        include_closed=args.include_closed,
        min_rating=args.min_rating,
        keep=tuple(args.keep),
        drop=tuple(args.drop),
    )
    fr = apply_filters(places, fcfg)
    print(
        f"  keep {len(fr.kept)} · generic {len(fr.generic)} · "
        f"closed {len(fr.closed)} · no-coords {len(fr.no_coords)} · "
        f"below-rating {len(fr.below_rating)}"
    )
    if not fr.kept:
        print("No specific places left after filtering.")
        return 2

    # ---- 4. plan --------------------------------------------------- #
    start = None
    if args.start:
        lat, lng = (float(x) for x in args.start.split(","))
        start = (lat, lng)
    pcfg = PlanConfig(
        days=args.days,
        max_per_day=args.max_per_day,
        pace=args.pace,
        travel_mode=args.travel_mode,
        start=start,
        day_start=args.day_start,
        api_key=None if args.no_enrich else os.environ.get("GOOGLE_MAPS_API_KEY"),
    )
    title = _guess_title(source_url)
    it = build_itinerary(
        fr.kept, title=title, source_url=source_url, cfg=pcfg, enriched=enricher.enabled
    )
    it.skipped_generic = [p.name for p in fr.generic]
    it.skipped_closed = [p.name for p in fr.closed]
    it.skipped_no_coords = [p.name for p in fr.no_coords]

    # ---- 5. render ----------------------------------------------- #
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    written = write_all(it, args.out, formats)
    print("Wrote:")
    for w in written:
        print(f"  {w}")
    return 0


def _guess_title(source: str) -> str:
    from urllib.parse import unquote

    if source and source.endswith(".json"):
        return "Itinerario"
    return "Itinerario dalla lista Google Maps"


if __name__ == "__main__":
    sys.exit(main())

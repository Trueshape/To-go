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
    pl.add_argument("--day-end", default="20:00", help="target end of a day (default 20:00)")

    ui = ap.add_argument_group("local web page")
    ui.add_argument("--serve", action="store_true",
                     help="open a local page to paste the list link, instead of the CLI")
    ui.add_argument("--port", type=int, default=8765, help="port for --serve (default 8765)")

    out = ap.add_argument_group("output")
    out.add_argument("--out", default="out", help="output directory (default: ./out)")
    out.add_argument("--format", default="html,md,json,gpx", help="comma list of: html,md,json,gpx")
    return ap


def main(argv: list[str] | None = None) -> int:
    interactive_launch = argv is None and len(sys.argv) == 1
    args = build_parser().parse_args(argv)

    # double-clicked, or run with no arguments at all: open the local page
    # instead of demanding a URL on the command line
    if args.serve or (interactive_launch and not (args.url or args.from_html or args.from_json)):
        from . import webapp

        webapp.run_server(args.port)
        return 0

    return _run(args)


def _run(args: argparse.Namespace) -> int:
    if not (args.url or args.from_html or args.from_json):
        if sys.stdin.isatty():
            args.url = _ask_for_url()
        else:
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
        day_end=args.day_end,
        api_key=None if args.no_enrich else os.environ.get("GOOGLE_MAPS_API_KEY"),
    )
    from . import extract as _extract

    title = _extract.LAST_LIST_TITLE or _guess_title(source_url)
    it = build_itinerary(
        fr.kept, title=title, source_url=source_url, cfg=pcfg, enriched=enricher.enabled
    )
    it.skipped_generic = [p.name for p in fr.generic]
    it.skipped_closed = [p.name for p in fr.closed]
    it.skipped_no_coords = [p.name for p in fr.no_coords]

    # ---- 5. render ----------------------------------------------- #
    formats = [f.strip() for f in args.format.split(",") if f.strip()]

    written: list[str] = []
    static = [f for f in formats if f != "html"]
    if static:
        written += write_all(it, args.out, static)

    if "html" in formats:
        # the interactive page also holds the generic + closed places (toggled
        # off by default) so they can be switched back on in the browser
        for p in fr.generic:
            p.disabled_reason = "generic"
        for p in fr.closed:
            p.disabled_reason = "closed"
        extra = [p for p in (fr.generic + fr.closed) if p.has_coords]
        html_it = build_itinerary(
            fr.kept + extra,
            title=title,
            source_url=source_url,
            cfg=pcfg,
            enriched=enricher.enabled,
            anchor_places=fr.kept,
        )
        html_it.skipped_no_coords = it.skipped_no_coords
        written += write_all(html_it, args.out, ["html"])
    print("Wrote:")
    for w in written:
        print(f"  {w}")
    return 0


def _ask_for_url() -> str:
    print("maps-itinerary - da una lista pubblica di Google Maps a un itinerario\n")
    while True:
        try:
            url = input("Incolla qui il link della lista Google Maps: ").strip()
        except EOFError:
            raise SystemExit(1)
        if url:
            return url
        print("(vuoto - incolla un link tipo https://maps.app.goo.gl/... o Ctrl+C per uscire)")


def _guess_title(source: str) -> str:
    from urllib.parse import unquote

    if source and source.endswith(".json"):
        return "Itinerario"
    return "Itinerario dalla lista Google Maps"


if __name__ == "__main__":
    sys.exit(main())

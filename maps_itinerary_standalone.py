#!/usr/bin/env python3
"""
maps-list-to-itinerary — single-file build.

Turn a public Google Maps list into a day-by-day itinerary (Markdown, JSON,
GPX, and a self-contained interactive HTML page).

This file is the whole program, assembled from the maps_itinerary/ package
so it can be copied and run on its own. The package is still the source of
truth - regenerate this file after changing it (see bundle.py in the repo).

Install:
    pip install requests
    pip install playwright && playwright install chromium   # only to scrape a live URL

Use:
    python maps_itinerary_standalone.py "https://maps.app.goo.gl/..."
    python maps_itinerary_standalone.py --from-json places.json --no-enrich
    python maps_itinerary_standalone.py --help
"""

from __future__ import annotations

__version__ = "0.1.0"

# ============================================================================
# model.py — Core data types shared across the pipeline.
# ============================================================================
import json
from dataclasses import dataclass, field, asdict
from typing import Optional


# Default time spent at a place, by coarse kind, in minutes.
# Used when the Places API can't give us anything better.
DEFAULT_VISIT_MINUTES = {
    "museum": 90,
    "gallery": 75,
    "park": 60,
    "garden": 60,
    "market": 60,
    "food_hall": 60,
    "mall": 90,
    "shop": 45,
    "shopping_street": 60,
    "shrine": 45,
    "temple": 45,
    "church": 30,
    "castle": 90,
    "landmark": 30,
    "viewpoint": 30,
    "photo_spot": 20,
    "restaurant": 75,
    "cafe": 40,
    "bar": 60,
    "nightlife_alley": 75,
    "neighborhood": 90,
    "station": 15,
    "convenience": 10,   # konbini / minimarket - a quick stop, not a visit
    "hotel": 0,
    "default": 45,
}

# Maps a raw Google category string (any language) onto one of the kinds above.
# Keys are lowercase substrings matched against the scraped/enriched category.
CATEGORY_HINTS = [
    ("museo", "museum"), ("museum", "museum"),
    ("galleria d'arte", "gallery"), ("art gallery", "gallery"),
    ("parco", "park"), ("park", "park"), ("giardino", "garden"), ("garden", "garden"),
    ("mercato", "market"), ("market", "market"),
    ("centro commerciale", "mall"), ("shopping mall", "mall"), ("shopping center", "mall"),
    ("negozio", "shop"), ("store", "shop"), ("shop", "shop"),
    ("via dello shopping", "shopping_street"), ("shopping street", "shopping_street"),
    ("tempio shintoista", "shrine"), ("shinto shrine", "shrine"), ("shrine", "shrine"),
    ("tempio", "temple"), ("temple", "temple"), ("buddhist", "temple"),
    ("chiesa", "church"), ("church", "church"),
    ("castello", "castle"), ("castle", "castle"),
    ("monumento", "landmark"), ("monument", "landmark"), ("landmark", "landmark"),
    ("memorial", "landmark"), ("memoriale", "landmark"),
    ("flea market", "market"), ("mercatino", "market"),
    ("business park", "neighborhood"), ("centro direzionale", "neighborhood"),
    ("punto d'interesse", "landmark"), ("point of interest", "landmark"),
    ("punto panoramico", "viewpoint"), ("observation deck", "viewpoint"),
    ("scenic", "viewpoint"), ("lookout", "viewpoint"),
    ("attrazione turistica", "photo_spot"), ("tourist attraction", "photo_spot"),
    ("ristorante", "restaurant"), ("restaurant", "restaurant"),
    ("tonkatsu", "restaurant"), ("ramen", "restaurant"), ("sushi", "restaurant"),
    ("caffè", "cafe"), ("caffe", "cafe"), ("coffee", "cafe"), ("cafe", "cafe"),
    ("bar", "bar"), ("pub", "bar"),
    ("fiume", "viewpoint"), ("river", "viewpoint"),
    ("stazione", "station"), ("station", "station"),
    ("hotel", "hotel"),
    ("minimarket", "convenience"), ("konbini", "convenience"),
    ("convenience store", "convenience"),
]

# Category strings that mean "this is an administrative area, not a place".
GENERIC_CATEGORY_MARKERS = (
    "città", "city", "quartiere", "neighborhood", "prefettura", "prefecture",
    "regione", "region", "località", "locality", "villaggio", "village",
)

# "Permanently closed" as Google renders it, per language.
CLOSED_MARKERS = (
    "chiuso definitivamente", "permanently closed", "definitivamente fechado",
    "définitivement fermé", "dauerhaft geschlossen",
)


@dataclass
class Place:
    """One entry from the list, progressively enriched."""

    name: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    category: str = ""            # raw Google category / status line
    rating: Optional[float] = None
    reviews: Optional[int] = None
    address: str = ""
    place_id: str = ""            # ChIJ... (from Places API)
    ftid: str = ""               # 0x..:0x.. feature id (from the list href)
    permanently_closed: bool = False
    opening_hours: list[str] = field(default_factory=list)  # human-readable, per weekday
    editorial: str = ""
    note: str = ""               # the list author's own note, if any
    photo: str = ""              # hotlinkable photo URL (googleusercontent / Places API)
    disabled_reason: str = ""    # "generic" | "closed" | "" - off by default in the UI
    source_href: str = ""

    # --- derived ---------------------------------------------------------
    @property
    def kind(self) -> str:
        cat = (self.category or "").lower()
        # longer, more specific needles win ("business park" before "park")
        for needle, kind in sorted(CATEGORY_HINTS, key=lambda h: -len(h[0])):
            if needle in cat:
                return kind
        return "default"

    @property
    def is_generic_area(self) -> bool:
        """True when the entry is a city/neighborhood rather than a specific POI.

        Maps shows administrative areas with no rating and no real category
        (just an address line like "Kitazawa, Setagaya City, Tokyo").
        """
        cat = (self.category or "").strip().lower()
        if any(m in cat for m in GENERIC_CATEGORY_MARKERS):
            return True
        if self.rating is not None:
            return False
        # no rating: it's a real POI only if it carries a recognisable category
        return self.kind == "default"

    @property
    def has_coords(self) -> bool:
        return self.lat is not None and self.lng is not None

    def visit_minutes(self, pace: float = 1.0) -> int:
        base = DEFAULT_VISIT_MINUTES.get(self.kind, DEFAULT_VISIT_MINUTES["default"])
        return int(round(base * pace))

    def maps_url(self) -> str:
        if self.place_id:
            q = self.lat is not None and f"{self.lat},{self.lng}" or self.name
            return (
                "https://www.google.com/maps/search/?api=1"
                f"&query={_enc(q)}&query_place_id={self.place_id}"
            )
        if self.has_coords:
            return f"https://www.google.com/maps/search/?api=1&query={self.lat},{self.lng}"
        return f"https://www.google.com/maps/search/?api=1&query={_enc(self.name)}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Place":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Leg:
    """Travel between two consecutive stops."""

    from_place: str
    to_place: str
    minutes: int
    distance_km: float
    mode: str
    directions_url: str


@dataclass
class Stop:
    place: Place
    arrive: str          # "HH:MM"
    depart: str          # "HH:MM"


@dataclass
class Day:
    index: int
    label: str
    stops: list[Stop] = field(default_factory=list)
    legs: list[Leg] = field(default_factory=list)


@dataclass
class Itinerary:
    title: str
    source_url: str
    days: list[Day] = field(default_factory=list)
    skipped_generic: list[str] = field(default_factory=list)
    skipped_closed: list[str] = field(default_factory=list)
    skipped_no_coords: list[str] = field(default_factory=list)
    enriched: bool = False
    travel_mode: str = "transit"
    pace_factor: float = 1.0
    day_start: str = "09:30"
    day_end: str = "20:00"
    lunch_at: str = "13:00"
    lunch_minutes: int = 60
    walk_threshold_km: float = 1.1

    def to_json(self) -> str:
        def enc(o):
            if isinstance(o, Place):
                return o.to_dict()
            if hasattr(o, "__dict__"):
                return o.__dict__
            raise TypeError(type(o))

        return json.dumps(self, default=enc, ensure_ascii=False, indent=2)


def _enc(s: str) -> str:
    from urllib.parse import quote

    return quote(str(s))

# ============================================================================
# geo.py — Distances and travel times between places.
# ============================================================================
import math
import os
from dataclasses import dataclass

import requests


# rough door-to-door speeds incl. stops/walking, km/h
_FALLBACK_SPEED_KMH = {"walking": 4.5, "transit": 16.0, "driving": 22.0}
_ROUTE_MATRIX = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
_MODE_MAP = {"walking": "WALK", "transit": "TRANSIT", "driving": "DRIVE"}


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@dataclass
class Matrix:
    minutes: list[list[float]]
    km: list[list[float]]
    mode: str
    real: bool  # True when it came from the Routes API

    def time(self, i: int, j: int) -> float:
        return self.minutes[i][j]

    def dist(self, i: int, j: int) -> float:
        return self.km[i][j]


def build_matrix(places: list[Place], mode: str, api_key: str | None = None) -> Matrix:
    api_key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY")
    n = len(places)
    if api_key and n <= 25:  # matrix API caps elements; keep it to day-sized sets
        try:
            return _routes_matrix(places, mode, api_key)
        except requests.RequestException as e:
            print(f"  ! Routes API matrix failed ({e}); using straight-line estimate")
    return _fallback_matrix(places, mode)


def _fallback_matrix(places: list[Place], mode: str) -> Matrix:
    speed = _FALLBACK_SPEED_KMH.get(mode, 16.0)
    n = len(places)
    km = [[0.0] * n for _ in range(n)]
    mins = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            d = haversine_km(
                (places[i].lat, places[i].lng), (places[j].lat, places[j].lng)
            )
            # detour factor: real routes are longer than the crow flies
            d_route = d * (1.3 if mode == "walking" else 1.4)
            km[i][j] = round(d_route, 2)
            mins[i][j] = round(d_route / speed * 60 + (4 if mode == "transit" else 0), 1)
    return Matrix(mins, km, mode, real=False)


def _routes_matrix(places: list[Place], mode: str, api_key: str) -> Matrix:
    pts = [
        {
            "waypoint": {
                "location": {"latLng": {"latitude": p.lat, "longitude": p.lng}}
            }
        }
        for p in places
    ]
    body = {
        "origins": pts,
        "destinations": pts,
        "travelMode": _MODE_MAP.get(mode, "TRANSIT"),
    }
    if mode == "driving":
        body["routingPreference"] = "TRAFFIC_AWARE"
    r = requests.post(
        _ROUTE_MATRIX,
        headers={
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "originIndex,destinationIndex,duration,distanceMeters,condition",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=60,
    )
    r.raise_for_status()
    n = len(places)
    km = [[0.0] * n for _ in range(n)]
    mins = [[0.0] * n for _ in range(n)]
    for cell in r.json():
        i, j = cell.get("originIndex", 0), cell.get("destinationIndex", 0)
        if i == j:
            continue
        secs = float(str(cell.get("duration", "0s")).rstrip("s") or 0)
        mins[i][j] = round(secs / 60, 1)
        km[i][j] = round(cell.get("distanceMeters", 0) / 1000, 2)
    return Matrix(mins, km, mode, real=True)

# ============================================================================
# filters.py — Decide which extracted entries actually make it into the plan.
# ============================================================================
from dataclasses import dataclass



@dataclass
class FilterConfig:
    include_closed: bool = False
    min_rating: float | None = None
    keep: tuple[str, ...] = ()      # names to force-keep even if generic
    drop: tuple[str, ...] = ()      # names to force-drop


@dataclass
class FilterResult:
    kept: list[Place]
    generic: list[Place]
    closed: list[Place]
    no_coords: list[Place]
    below_rating: list[Place]


def apply_filters(places: list[Place], cfg: FilterConfig) -> FilterResult:
    kept, generic, closed, no_coords, below = [], [], [], [], []
    keep_l = {s.lower() for s in cfg.keep}
    drop_l = {s.lower() for s in cfg.drop}

    for p in places:
        name_l = p.name.lower()
        if name_l in drop_l:
            continue

        forced = name_l in keep_l

        # 1. only specific places — drop cities / neighborhoods / prefectures
        if p.is_generic_area and not forced:
            generic.append(p)
            continue

        # 2. permanently closed — user's choice
        if p.permanently_closed and not cfg.include_closed and not forced:
            closed.append(p)
            continue

        # 3. need a location to plan around it
        if not p.has_coords:
            no_coords.append(p)
            continue

        # 4. optional rating floor
        if (
            cfg.min_rating is not None
            and p.rating is not None
            and p.rating < cfg.min_rating
            and not forced
        ):
            below.append(p)
            continue

        kept.append(p)

    return FilterResult(kept, generic, closed, no_coords, below)

# ============================================================================
# enrich.py — Fill in opening hours / address / a cleaner category via the Places API.
# ============================================================================
import json
import os
import pathlib
import time

import requests


_TEXT_SEARCH = "https://places.googleapis.com/v1/places:searchText"
_DETAILS = "https://places.googleapis.com/v1/places/{place_id}"


class Enricher:
    def __init__(self, api_key: str | None = None, cache_dir: str = ".cache", lang: str = "en"):
        self.api_key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY")
        self.lang = lang
        self.cache = pathlib.Path(cache_dir) / "places"
        self.cache.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    # ------------------------------------------------------------------ #
    def enrich_all(self, places: list[Place]) -> None:
        if not self.enabled:
            return
        for p in places:
            try:
                self._enrich_one(p)
            except requests.RequestException as e:  # keep going on transient errors
                print(f"  ! enrich failed for {p.name}: {e}")
            time.sleep(0.05)

    # ------------------------------------------------------------------ #
    def _enrich_one(self, p: Place) -> None:
        key = p.ftid or p.place_id or f"{p.name}|{p.lat},{p.lng}"
        cached = self._cache_get(key)
        if cached is None:
            place_id = p.place_id or self._resolve_place_id(p)
            cached = self._details(place_id) if place_id else {}
            self._cache_put(key, cached)
        _apply_details(p, cached)

    def _resolve_place_id(self, p: Place) -> str:
        body = {"textQuery": p.name, "languageCode": self.lang, "maxResultCount": 1}
        if p.has_coords:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": p.lat, "longitude": p.lng},
                    "radius": 400.0,
                }
            }
        r = requests.post(
            _TEXT_SEARCH,
            headers={
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": "places.id,places.location",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        places = r.json().get("places", [])
        return places[0]["id"] if places else ""

    def _details(self, place_id: str) -> dict:
        fields = ",".join(
            [
                "id",
                "displayName",
                "formattedAddress",
                "location",
                "rating",
                "userRatingCount",
                "primaryTypeDisplayName",
                "types",
                "businessStatus",
                "regularOpeningHours.weekdayDescriptions",
                "editorialSummary",
            ]
        )
        r = requests.get(
            _DETAILS.format(place_id=place_id),
            headers={
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": fields,
                "Accept-Language": self.lang,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ #
    def _cache_path(self, key: str) -> pathlib.Path:
        safe = "".join(c if c.isalnum() else "_" for c in key)[:120]
        return self.cache / f"{safe}.json"

    def _cache_get(self, key: str):
        p = self._cache_path(key)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    def _cache_put(self, key: str, value: dict) -> None:
        self._cache_path(key).write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )


def _apply_details(p: Place, d: dict) -> None:
    if not d:
        return
    p.place_id = d.get("id", p.place_id)
    loc = d.get("location") or {}
    if loc.get("latitude") is not None:
        p.lat, p.lng = loc["latitude"], loc["longitude"]
    p.address = d.get("formattedAddress", p.address)
    if d.get("rating") is not None:
        p.rating = d["rating"]
    if d.get("userRatingCount") is not None:
        p.reviews = d["userRatingCount"]
    cat = (d.get("primaryTypeDisplayName") or {}).get("text")
    if cat:
        p.category = cat
    if d.get("businessStatus") == "CLOSED_PERMANENTLY":
        p.permanently_closed = True
    hours = (d.get("regularOpeningHours") or {}).get("weekdayDescriptions") or []
    if hours:
        p.opening_hours = hours
    summary = (d.get("editorialSummary") or {}).get("text")
    if summary:
        p.editorial = summary

# ============================================================================
# extract.py — Get the raw list of places out of a public Google Maps list.
# ============================================================================
import html as _html
import json
import re
import time
from typing import Iterable

import requests


CONSENT_COOKIE = "CONSENT=YES+cb.20211029-17-p0.en+F+cb; SOCS=CAI"

# Set by extract_from_browser so the CLI can title the itinerary after the list.
LAST_LIST_TITLE = ""
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_COORDS_3D4D = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
_COORDS_AT = re.compile(r"[@/](-?\d+\.\d{3,}),(-?\d+\.\d{3,})")
_FTID = re.compile(r"!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)")
_RATING = re.compile(r"^\s*([0-5][.,]\d)\s*\(\s*([\d.,]+)\s*\)\s*$")


# --------------------------------------------------------------------------- #
# public entry points
# --------------------------------------------------------------------------- #
def resolve_list_url(url: str) -> str:
    """Follow a maps.app.goo.gl short link to the real Google Maps URL.

    Unwraps the consent.google.com interstitial and appends ``ucbcb=1`` which,
    together with the SOCS cookie, tells Google to skip the cookie banner.
    """
    from urllib.parse import parse_qs, urlparse, urlsplit, urlunsplit

    r = requests.get(
        url,
        headers={"User-Agent": UA, "Cookie": CONSENT_COOKIE},
        allow_redirects=True,
        timeout=30,
    )
    final = r.url
    host = urlparse(final).netloc
    if "consent.google" in host:
        cont = parse_qs(urlparse(final).query).get("continue", [])
        if cont:
            final = cont[0]

    parts = urlsplit(final)
    if "ucbcb=1" not in parts.query:
        q = (parts.query + "&" if parts.query else "") + "ucbcb=1"
        final = urlunsplit((parts.scheme, parts.netloc, parts.path, q, parts.fragment))
    return final


_ITEM_SEL = "button.SMP2wb"
_HEAD_SEL = ".fontHeadlineSmall"
_STEALTH = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"


def extract_from_browser(
    url: str, *, headful: bool = False, max_scrolls: int = 60, resolve_coords: bool = True
) -> list[Place]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover - env dependent
        raise SystemExit(
            "Scraping a live URL needs Playwright:\n"
            "    pip install 'maps-list-to-itinerary[browser]'\n"
            "    playwright install chromium\n"
            "Or extract by hand and pass --from-html / --from-json."
        ) from e

    # Force English UI + the cookie-banner bypass; the list panel only renders
    # reliably from the full ...!3m1!4b1!4m3... form of the URL.
    if "hl=" not in url:
        url += ("&" if "?" in url else "?") + "hl=en"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headful,
            args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
        )
        ctx = browser.new_context(
            user_agent=UA, locale="en-US", viewport={"width": 1360, "height": 950}
        )
        ctx.add_cookies(_consent_cookies())
        page = ctx.new_page()
        page.add_init_script(_STEALTH)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        _clear_consent(page)
        page.wait_for_selector(_ITEM_SEL, timeout=30000)

        global LAST_LIST_TITLE
        LAST_LIST_TITLE = re.sub(r"\s*-\s*Google Maps\s*$", "", page.title() or "").strip()

        _scroll_list(page, max_scrolls)
        raw = page.evaluate(_JS_LIST)
        places = [_raw_to_place(r) for r in raw]
        places = _dedupe([p for p in places if p.name])
        print(f"  found {len(places)} places in the list")

        if resolve_coords:
            _resolve_coords(page, places)

        browser.close()

    return places


def _scroll_list(page, max_scrolls: int) -> None:
    handle = page.evaluate_handle(
        """() => {
          let e = document.querySelector('""" + _ITEM_SEL + """');
          while (e) { if (e.scrollHeight > e.clientHeight + 50) return e; e = e.parentElement; }
          return document.scrollingElement;
        }"""
    )
    last, stable = -1, 0
    for _ in range(max_scrolls):
        n = page.eval_on_selector_all(_ITEM_SEL, "e => e.length")
        page.evaluate("el => el.scrollBy(0, el.clientHeight)", handle)
        page.wait_for_timeout(550)
        stable = stable + 1 if n == last else 0
        last = n
        if stable >= 4:
            break


_JS_PANE = r"""
() => {
  const q = s => document.querySelector(s);
  const addr = q('button[data-item-id="address"]');
  const cat = q('button[jsaction*="category"]');
  const h1 = q('[role="main"] h1') || [...document.querySelectorAll('h1')].pop();
  // the hero photo sits inside the button that opens the photo gallery
  let photo = '';
  const hero = q('button[jsaction*="heroHeaderImage"] img, button[aria-label^="Photo"] img, [data-photo-index="0"] img');
  const cand = hero ? [hero] : [...document.querySelectorAll('[role="main"] img, .RZ66Rb img, .ZKCDEc img')];
  for (const im of cand) {
    const s = im.src || '';
    const m = s.match(/=w(\d+)-h(\d+)/);
    if (s.includes('googleusercontent') && m && +m[1] >= 90 && !s.includes('=w36') && !s.includes('/a-/')) {
      photo = s; break;
    }
  }
  return {
    title: h1 ? h1.textContent.trim() : '',
    address: addr ? (addr.getAttribute('aria-label') || '').replace(/^Address:\s*/, '').trim() : '',
    category: cat ? cat.textContent.trim() : '',
    photo,
  };
}
"""


def _resolve_coords(page, places: list[Place]) -> None:
    """Click each list item; read coords/ftid off the URL and photo/address/
    category off the place pane. All best-effort - failures are skipped."""
    total = len(places)
    last_photo = ""
    for i, place in enumerate(places):
        try:
            items = page.query_selector_all(_ITEM_SEL)
            if i >= len(items):
                _scroll_list(page, 20)
                items = page.query_selector_all(_ITEM_SEL)
            if i >= len(items):
                continue
            before = page.url
            items[i].scroll_into_view_if_needed(timeout=4000)
            items[i].click(timeout=5000)
            # wait for the URL to change to a *different* place page
            try:
                page.wait_for_function(
                    "u => location.href !== u && /\\/maps\\/place\\//.test(location.href)",
                    arg=before,
                    timeout=12000,
                )
            except Exception:
                page.wait_for_url(re.compile(r"/maps/place/"), timeout=8000)
            _fill_from_url(place, page.url)
            # wait for the pane to belong to THIS place: address button present and
            # the title contains a chunk of the name
            key = (place.name.split()[0][:6] if place.name.split() else "").lower()
            try:
                page.wait_for_selector('button[data-item-id="address"]', timeout=8000)
            except Exception:
                pass
            pane = {}
            for _ in range(8):
                try:
                    pane = page.evaluate(_JS_PANE)
                except Exception:
                    pane = {}
                title_ok = not key or key in (pane.get("title") or "").lower()
                fresh_photo = pane.get("photo") and pane["photo"] != last_photo
                if title_ok and (fresh_photo or not pane.get("photo")):
                    break
                page.wait_for_timeout(500)
            if pane.get("photo"):
                last_photo = pane["photo"]
                place.photo = re.sub(r"=w\d+-h\d+[^&]*", "=w1000-h640-k-no", pane["photo"])
            if pane.get("address"):
                place.address = pane["address"]
            if pane.get("category") and " " in pane["category"]:
                place.category = pane["category"]
        except Exception as e:  # noqa: BLE001 - keep going, coords are best-effort
            print(f"  ! no coords for {place.name}: {str(e).splitlines()[0]}")
        finally:
            try:
                page.go_back(wait_until="domcontentloaded", timeout=12000)
                page.wait_for_selector(_ITEM_SEL, timeout=10000)
                page.wait_for_timeout(400)
            except Exception:
                pass
        if (i + 1) % 10 == 0:
            print(f"  ... {i + 1}/{total}")


def _fill_from_url(place: Place, url: str) -> None:
    place.source_href = url
    mc = _COORDS_3D4D.search(url) or _COORDS_AT.search(url)
    if mc:
        place.lat, place.lng = float(mc.group(1)), float(mc.group(2))
    mf = _FTID.search(url)
    if mf:
        place.ftid = mf.group(1)


def extract_from_html(path: str) -> list[Place]:
    """Best-effort parse of a saved, fully-scrolled list page."""
    text = open(path, encoding="utf-8", errors="replace").read()
    # Anchor + its aria-label is the most stable signal in saved HTML.
    out: list[Place] = []
    for m in re.finditer(r'<a\b[^>]*href="([^"]*\/maps\/place\/[^"]*)"[^>]*>', text):
        href = _html.unescape(m.group(1))
        chunk = text[m.start(): m.start() + 1200]
        label = re.search(r'aria-label="([^"]+)"', text[max(0, m.start() - 300): m.end()])
        name = _html.unescape(label.group(1)) if label else _name_from_href(href)
        p = _new_place(name=name, href=href)
        # rating / category live in nearby fontBodyMedium spans
        for sub in re.findall(r">([^<>{}]{2,60})<", chunk):
            sub = _html.unescape(sub).strip()
            _absorb_line(p, sub)
        out.append(p)
    return _dedupe([p for p in out if p.name])


def load_from_json(path: str) -> list[Place]:
    global LAST_LIST_TITLE
    data = json.load(open(path, encoding="utf-8"))
    if isinstance(data, dict):
        LAST_LIST_TITLE = data.get("title", "") or LAST_LIST_TITLE
        data = data.get("places", [])
    return [Place.from_dict(d) for d in data]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
_JS_LIST = r"""
() => {
  const out = [];
  document.querySelectorAll('button.SMP2wb').forEach(b => {
    const head = b.querySelector('.fontHeadlineSmall');
    if (!head) return;
    const name = head.textContent.trim();
    const star = b.querySelector('[role="img"][aria-label*="star"], [role="img"][aria-label*="stelle"]');
    const lines = Array.from(b.querySelectorAll('div,span'))
      .map(el => (el.childElementCount === 0 ? el.textContent.trim() : ''))
      .filter(Boolean);
    out.push({
      name,
      href: '',
      aria: star ? star.getAttribute('aria-label') : '',
      lines: Array.from(new Set(lines)).slice(0, 14),
    });
  });
  return out;
}
"""


def _consent_cookies():
    return [
        {"name": "CONSENT", "value": "YES+cb", "domain": ".google.com", "path": "/"},
        {"name": "SOCS", "value": "CAISNQgQEitib3VuZGVkX2ZsaWdodHM", "domain": ".google.com", "path": "/"},
        {"name": "SOCS", "value": "CAISNQgQEitib3VuZGVkX2ZsaWdodHM", "domain": ".consent.google.com", "path": "/"},
    ]


_CONSENT_BUTTON_RE = "Reject all|Rifiuta tutto|Alle ablehnen|Tout refuser|Accept all|Accetta tutto"


def _clear_consent(page) -> None:
    """Get past consent.google.com by rejecting, then wait for the real page."""
    import re as _re

    for _ in range(4):
        if "consent.google" not in page.url:
            return
        clicked = False
        try:
            btn = page.get_by_role("button", name=_re.compile(_CONSENT_BUTTON_RE, _re.I))
            if btn.count():
                btn.first.click(timeout=5000)
                clicked = True
        except Exception:
            pass
        if not clicked:
            for sel in (
                'form[action*="consent"] button[type="submit"]',
                'form[action*="save"] button',
                'button[aria-label*="Reject"]',
                'button[aria-label*="Rifiuta"]',
            ):
                try:
                    page.click(sel, timeout=3000)
                    clicked = True
                    break
                except Exception:
                    continue
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(1500)


def _find_scroll_panel(page):
    for sel in ('div[role="main"]', 'div[role="region"]', 'div.m6QErb'):
        try:
            el = page.query_selector(sel)
            if el:
                return el
        except Exception:
            pass
    return None


def _raw_to_place(r: dict) -> Place:
    p = _new_place(name=(r.get("name") or "").strip(), href=r.get("href", ""))
    for line in r.get("lines", []):
        _absorb_line(p, line)
    if p.rating is None:
        m = re.search(r"([0-5][.,]\d)\s*(?:stars?|stelle)", r.get("aria", ""))
        if m:
            p.rating = float(m.group(1).replace(",", "."))
    return p


def _new_place(*, name: str, href: str) -> Place:
    p = Place(name=name, source_href=href)
    mc = _COORDS_3D4D.search(href) or _COORDS_AT.search(href)
    if mc:
        p.lat, p.lng = float(mc.group(1)), float(mc.group(2))
    mf = _FTID.search(href)
    if mf:
        p.ftid = mf.group(1)
    return p


_ADDRESS_TAIL = re.compile(
    r",\s*(?:tokyo|osaka|kyoto|nara|japan|giappone|kanagawa|[A-Z][a-z]+ (?:City|Ward|Prefecture))"
    r"|(?:City|Ward|Prefecture|Kanagawa|Setagaya)\b",
    re.I,
)


def _absorb_line(p: Place, line: str) -> None:
    line = (line or "").strip()
    low = line.lower()
    if not line or line == p.name:
        return
    if any(m in low for m in CLOSED_MARKERS):
        p.permanently_closed = True
        return
    # bare rating ("4.2") or rating with review count ("4.4(12,904)")
    if re.match(r"^[0-5][.,]\d$", line):
        p.rating = p.rating or float(line.replace(",", "."))
        return
    mr = _RATING.match(line)
    if mr:
        p.rating = float(mr.group(1).replace(",", "."))
        try:
            p.reviews = int(re.sub(r"[.,]", "", mr.group(2)))
        except ValueError:
            pass
        return
    if any(tok in low for tok in ("giappone", "japan", "·", "salva ", "share", "condividi")):
        return
    if re.search(r"\d", line) or _ADDRESS_TAIL.search(line) or "," in line:
        return
    known_single = {
        "memorial", "market", "river", "park", "garden", "mall", "museum", "shrine",
        "temple", "castle", "landmark", "monument", "neighborhood", "hotel", "cafe",
        "restaurant", "bar", "izakaya", "station",
    }
    words = line.split()
    prose = {"di", "e", "con", "del", "della", "the", "of", "per", "in", "a", "da", "un"}
    is_prose = len(words) > 3 or any(w.lower() in prose for w in words)
    looks_like_category = not is_prose and (" " in line or line.lower() in known_single)
    if not p.category and looks_like_category and 2 <= len(line) <= 40 and line[0].isupper():
        p.category = line
    elif len(line) > 12 and not p.note:
        p.note = line  # the list author's own annotation, e.g. "Equivalente di Akihabara"


def _name_from_href(href: str) -> str:
    m = re.search(r"/maps/place/([^/@]+)", href)
    return _html.unescape(m.group(1).replace("+", " ")) if m else ""


def _dedupe(places: Iterable[Place]) -> list[Place]:
    out: list[Place] = []
    keys: set = set()
    for p in places:
        k = p.ftid or p.source_href or p.name.lower()
        if k in keys:
            continue
        keys.add(k)
        out.append(p)
    return out

# ============================================================================
# plan.py — Group places into days and order each day sensibly.
# ============================================================================
import math
import random
import re
from dataclasses import dataclass


_DIR_URL = (
    "https://www.google.com/maps/dir/?api=1"
    "&origin={o}&destination={d}&travelmode={m}"
)


@dataclass
class PlanConfig:
    days: int | None = None
    max_per_day: int = 6
    pace: str = "normal"           # relaxed | normal | packed
    travel_mode: str = "transit"
    start: tuple[float, float] | None = None
    day_start: str = "09:30"
    day_end: str = "20:00"
    lunch_at: str = "13:00"
    lunch_minutes: int = 60
    region_gap_km: float = 40.0    # places farther apart than this = different bases
    merge_gap_km: float = 75.0     # adjacent days closer than this may share one day
    api_key: str | None = None
    seed: int = 7


_PACE_FACTOR = {"relaxed": 1.25, "normal": 1.0, "packed": 0.8}


def _day_budget(cfg: "PlanConfig") -> float:
    """Wall-clock minutes available for touring on a normal day."""
    span = _to_min(cfg.day_end) - _to_min(cfg.day_start) - cfg.lunch_minutes
    return max(180.0, span)


def build_itinerary(
    places: list[Place],
    *,
    title: str,
    source_url: str,
    cfg: PlanConfig,
    enriched: bool,
    anchor_places: list[Place] | None = None,
) -> Itinerary:
    """Plan a day-by-day itinerary.

    ``anchor_places`` (a subset of ``places``) drives the day count and region
    split; every other place is dropped into the nearest resulting day. Used so
    that off-by-default entries (generic areas, closed spots) don't inflate the
    trip - they just ride along in whatever day is closest.
    """
    random.seed(cfg.seed)
    pace = _PACE_FACTOR.get(cfg.pace, 1.0)

    anchors = anchor_places or places
    extras = [p for p in places if p not in anchors]

    n_days = cfg.days or _auto_days(anchors, cfg, pace)
    n_days = max(1, min(n_days, len(anchors)))

    # 1. split into geographic regions (different cities / day-trip bases)
    regions = _regions(anchors, cfg.region_gap_km)
    # never fewer days than regions, and respect the per-day cap
    n_days = max(n_days, len(regions), math.ceil(len(anchors) / cfg.max_per_day))

    # 2. share the day budget out between regions, by size
    clusters: list[list[Place]] = []
    for region in _order_clusters(regions, cfg.start):
        share = max(1, round(n_days * len(region) / len(anchors)))
        share = max(share, math.ceil(len(region) / cfg.max_per_day))
        sub = _cluster(region, min(share, len(region)), cfg)
        sub = _rebalance(sub, cfg.max_per_day)
        clusters.extend(_order_clusters(sub, cfg.start))

    # 2b. merge adjacent days that would each finish well short of a full day
    if cfg.days is None:
        clusters = _merge_thin_days(clusters, cfg, pace)

    # 3. drop the extras into the nearest day
    for p in extras:
        near = min(clusters, key=lambda c: haversine_km((p.lat, p.lng), _centroid(c)))
        near.append(p)

    it = Itinerary(
        title=title,
        source_url=source_url,
        enriched=enriched,
        travel_mode=cfg.travel_mode,
        pace_factor=pace,
        day_start=cfg.day_start,
        day_end=cfg.day_end,
        lunch_at=cfg.lunch_at,
        lunch_minutes=cfg.lunch_minutes,
    )

    cursor = cfg.start
    for di, group in enumerate(clusters, start=1):
        ordered, matrix = _order_within_day(group, cfg, entry=cursor)
        day = _schedule_day(di, ordered, matrix, cfg, pace)
        it.days.append(day)
        if ordered:
            last = ordered[-1]
            cursor = (last.lat, last.lng)
    return it


# --------------------------------------------------------------------------- #
# day count + clustering
# --------------------------------------------------------------------------- #
def _auto_days(places: list[Place], cfg: PlanConfig, pace: float) -> int:
    budget = _day_budget(cfg)
    total = sum(p.visit_minutes(pace) for p in places)
    total += 35 * max(0, len(places) - 1)  # rough inter-stop travel allowance
    by_time = math.ceil(total / budget)
    by_count = math.ceil(len(places) / cfg.max_per_day)
    return max(1, by_time, by_count)


def _regions(places: list[Place], gap_km: float) -> list[list[Place]]:
    """Single-linkage grouping: places within gap_km hop into the same region."""
    n = len(places)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        for j in range(i + 1, n):
            d = haversine_km(
                (places[i].lat, places[i].lng), (places[j].lat, places[j].lng)
            )
            if d <= gap_km:
                parent[find(i)] = find(j)

    buckets: dict[int, list[Place]] = {}
    for idx, p in enumerate(places):
        buckets.setdefault(find(idx), []).append(p)
    return list(buckets.values())


def _xy(p: Place | tuple[float, float]) -> tuple[float, float]:
    lat, lng = (p if isinstance(p, tuple) else (p.lat, p.lng))
    return lat, lng * math.cos(math.radians(lat))


def _cluster(places: list[Place], k: int, cfg: PlanConfig) -> list[list[Place]]:
    if k <= 1:
        return [list(places)]
    pts = [_xy(p) for p in places]
    # k-means++ seeding
    centers = [random.choice(pts)]
    while len(centers) < k:
        d2 = [min(_sq(pt, c) for c in centers) for pt in pts]
        tot = sum(d2) or 1.0
        r = random.random() * tot
        acc = 0.0
        for pt, w in zip(pts, d2):
            acc += w
            if acc >= r:
                centers.append(pt)
                break
    for _ in range(60):
        groups: list[list[int]] = [[] for _ in range(k)]
        for idx, pt in enumerate(pts):
            j = min(range(k), key=lambda c: _sq(pt, centers[c]))
            groups[j].append(idx)
        new_centers = []
        for g, c in zip(groups, centers):
            if not g:
                new_centers.append(c)
                continue
            new_centers.append(
                (sum(pts[i][0] for i in g) / len(g), sum(pts[i][1] for i in g) / len(g))
            )
        if new_centers == centers:
            break
        centers = new_centers
    return [[places[i] for i in g] for g in groups if g]


def _day_minutes(group: list[Place], pace: float) -> float:
    """Rough active minutes for a day: visits + a hop between each stop."""
    if not group:
        return 0.0
    visit = sum(p.visit_minutes(pace) for p in group)
    hop = 0.0
    for a, b in zip(group, group[1:]):
        d = haversine_km((a.lat, a.lng), (b.lat, b.lng)) * 1.4
        hop += d / 16.0 * 60 + 4
    return visit + hop


def _merge_thin_days(
    clusters: list[list[Place]], cfg: PlanConfig, pace: float
) -> list[list[Place]]:
    """Combine two consecutive days - even in different cities - whenever the
    transfer between them is short enough that seeing both in one day still fits
    the day window. Stops a trip from being a string of half-empty days.
    """
    budget = _day_budget(cfg)
    out = [list(c) for c in clusters]
    changed = True
    while changed and len(out) > 1:
        changed = False
        best = None
        for i in range(len(out) - 1):
            a, b = out[i], out[i + 1]
            transfer_km = haversine_km(_centroid(a), _centroid(b))
            if transfer_km > cfg.merge_gap_km:
                continue
            transfer_min = transfer_km * 1.4 / 16.0 * 60
            combined = _day_minutes(a, pace) + _day_minutes(b, pace) + transfer_min
            if combined <= budget * 1.02 and (best is None or combined < best[1]):
                best = (i, combined)
        if best is not None:
            i = best[0]
            out[i] = out[i] + out[i + 1]
            del out[i + 1]
            changed = True
    return out


def _rebalance(clusters: list[list[Place]], max_per_day: int) -> list[list[Place]]:
    clusters = [list(c) for c in clusters]
    changed = True
    guard = 0
    while changed and guard < 200:
        changed = False
        guard += 1
        clusters.sort(key=len, reverse=True)
        for ci, c in enumerate(clusters):
            while len(c) > max_per_day:
                targets = [
                    (cj, d)
                    for cj, d in enumerate(clusters)
                    if cj != ci and len(d) < max_per_day
                ]
                if not targets:
                    clusters.append([])
                    targets = [(len(clusters) - 1, clusters[-1])]
                # move the member that fits some other day best (least disruptive),
                # not just the one farthest from this day's centre
                victim, cj, dst = min(
                    (
                        (p, cj, d)
                        for p in c
                        for cj, d in targets
                    ),
                    key=lambda t: haversine_km(
                        (t[0].lat, t[0].lng), _centroid(t[2]) or (t[0].lat, t[0].lng)
                    ),
                )
                c.remove(victim)
                dst.append(victim)
                changed = True
    clusters = _polish([c for c in clusters if c], max_per_day)
    return clusters


def _polish(clusters: list[list[Place]], max_per_day: int) -> list[list[Place]]:
    """k-means-style clean-up: send each place to the nearest day that has room."""
    for _ in range(20):
        centers = [_centroid(c) for c in clusters]
        moved = False
        for ci, c in enumerate(clusters):
            if len(c) <= 1:
                continue
            for p in list(c):
                here = haversine_km((p.lat, p.lng), centers[ci])
                best = min(
                    (
                        (cj, haversine_km((p.lat, p.lng), centers[cj]))
                        for cj in range(len(clusters))
                        if cj != ci and len(clusters[cj]) < max_per_day
                    ),
                    key=lambda t: t[1],
                    default=None,
                )
                if best and best[1] + 0.3 < here:
                    c.remove(p)
                    clusters[best[0]].append(p)
                    moved = True
        if not moved:
            break
    return [c for c in clusters if c]


def _order_clusters(
    clusters: list[list[Place]], start: tuple[float, float] | None
) -> list[list[Place]]:
    if len(clusters) <= 1:
        return clusters
    remaining = list(clusters)
    ordered = []
    cur = start or _centroid(min(remaining, key=lambda c: _centroid(c)[0]))
    while remaining:
        nxt = min(remaining, key=lambda c: haversine_km(cur, _centroid(c)))
        ordered.append(nxt)
        remaining.remove(nxt)
        cur = _centroid(nxt)
    return ordered


# --------------------------------------------------------------------------- #
# ordering inside a day
# --------------------------------------------------------------------------- #
def _order_within_day(
    group: list[Place], cfg: PlanConfig, entry: tuple[float, float] | None
) -> tuple[list[Place], Matrix]:
    if len(group) <= 1:
        return group, build_matrix(group, cfg.travel_mode, cfg.api_key) if group else Matrix([], [], cfg.travel_mode, False)

    matrix = build_matrix(group, cfg.travel_mode, cfg.api_key)
    n = len(group)

    # nearest-neighbour seed from the entry point
    start_i = 0
    if entry:
        start_i = min(range(n), key=lambda i: haversine_km(entry, (group[i].lat, group[i].lng)))
    unvisited = set(range(n))
    order = [start_i]
    unvisited.discard(start_i)
    while unvisited:
        last = order[-1]
        nxt = min(unvisited, key=lambda j: matrix.time(last, j))
        order.append(nxt)
        unvisited.discard(nxt)

    order = _two_opt(order, matrix)
    return [group[i] for i in order], _reindex_matrix(matrix, order)


def _two_opt(order: list[int], matrix: Matrix) -> list[int]:
    def cost(o):
        return sum(matrix.time(o[i], o[i + 1]) for i in range(len(o) - 1))

    best = order[:]
    best_c = cost(best)
    improved = True
    while improved:
        improved = False
        for i in range(1, len(best) - 1):
            for j in range(i + 1, len(best)):
                cand = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                c = cost(cand)
                if c + 1e-6 < best_c:
                    best, best_c = cand, c
                    improved = True
    return best


def _reindex_matrix(matrix: Matrix, order: list[int]) -> Matrix:
    m = [[matrix.minutes[a][b] for b in order] for a in order]
    k = [[matrix.km[a][b] for b in order] for a in order]
    return Matrix(m, k, matrix.mode, matrix.real)


# --------------------------------------------------------------------------- #
# scheduling
# --------------------------------------------------------------------------- #
def _schedule_day(
    index: int, ordered: list[Place], matrix: Matrix, cfg: PlanConfig, pace: float
) -> Day:
    label = _day_label(ordered)
    day = Day(index=index, label=label)
    if not ordered:
        return day

    t = _to_min(cfg.day_start)
    lunch_t = _to_min(cfg.lunch_at)
    lunch_done = False

    for i, p in enumerate(ordered):
        if i > 0:
            leg_min = matrix.time(i - 1, i)
            leg_km = matrix.dist(i - 1, i)
            # short hops are always faster on foot than waiting for a train
            mode = "walking" if (leg_km <= 1.1 and cfg.travel_mode != "driving") else cfg.travel_mode
            if mode == "walking" and not matrix.real:
                leg_min = round(leg_km * 1.3 / 4.5 * 60 + 2, 1)
            day.legs.append(
                Leg(
                    from_place=ordered[i - 1].name,
                    to_place=p.name,
                    minutes=int(round(leg_min)),
                    distance_km=round(leg_km, 1),
                    mode=mode,
                    directions_url=_DIR_URL.format(
                        o=f"{ordered[i-1].lat},{ordered[i-1].lng}",
                        d=f"{p.lat},{p.lng}",
                        m=mode,
                    ),
                )
            )
            t += leg_min

        if not lunch_done and t >= lunch_t:
            t += cfg.lunch_minutes
            lunch_done = True
            p.note = (p.note + " " if p.note else "") + "(dopo pausa pranzo)"

        arrive = _to_hhmm(t)
        t += p.visit_minutes(pace)
        day.stops.append(Stop(place=p, arrive=arrive, depart=_to_hhmm(t)))

    return day


# rough centres of the areas this kind of list tends to touch; used only for
# human-readable day labels when there is no address from the Places API.
_KNOWN_AREAS = [
    ("Tokyo", 35.68, 139.75), ("Yokohama", 35.44, 139.64),
    ("Kamakura / Enoshima", 35.31, 139.49), ("Fuji / Fujiyoshida", 35.49, 138.80),
    ("Nikko", 36.75, 139.60), ("Hakone", 35.23, 139.02),
    ("Kyoto", 35.01, 135.76), ("Osaka", 34.69, 135.50), ("Nara", 34.685, 135.83),
    ("Kobe", 34.69, 135.20), ("Hiroshima", 34.39, 132.45), ("Kanazawa", 36.56, 136.65),
    ("Sapporo", 43.06, 141.35), ("Fukuoka", 33.59, 130.40),
]


def _day_label(places: list[Place]) -> str:
    if not places:
        return "—"
    # if an address names a known area, trust that
    blob = " ".join(p.address for p in places).lower()
    for name, _la, _lo in _KNOWN_AREAS:
        if name.split(" / ")[0].lower() in blob:
            return name
    # otherwise label by the nearest known area to the day's centre
    cen = _centroid(places)
    name, dist = min(
        ((n, haversine_km(cen, (la, lo))) for n, la, lo in _KNOWN_AREAS),
        key=lambda t: t[1],
    )
    base = name if dist < 70 else f"{cen[0]:.2f}, {cen[1]:.2f}"
    return base + _ward_suffix(places)


def _ward_suffix(places: list[Place]) -> str:
    wards: list[str] = []
    for p in places:
        m = re.search(r"([A-Za-zĀ-ヿ]+(?:-ku| City| Ward))", p.address)
        if m:
            wards.append(m.group(1).replace(" City", "").replace(" Ward", "").replace("-ku", ""))
    if not wards:
        return ""
    top = max(set(wards), key=wards.count)
    return f" · {top}" if wards.count(top) >= 2 else ""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _sq(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _centroid(places):
    if not places:
        return (0.0, 0.0)
    return (
        sum(p.lat for p in places) / len(places),
        sum(p.lng for p in places) / len(places),
    )


def _to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _to_hhmm(mins: float) -> str:
    mins = int(round(mins))
    return f"{mins // 60 % 24:02d}:{mins % 60:02d}"

# ============================================================================
# render.py — Write the itinerary out as Markdown, JSON and GPX.
# ============================================================================
import html
import json
import pathlib
import re



def write_all(it: Itinerary, out_dir: str, formats: list[str]) -> list[str]:
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    if "md" in formats:
        p = out / "itinerary.md"
        p.write_text(to_markdown(it), encoding="utf-8")
        written.append(str(p))
    if "json" in formats:
        p = out / "itinerary.json"
        p.write_text(it.to_json(), encoding="utf-8")
        written.append(str(p))
    if "gpx" in formats:
        p = out / "itinerary.gpx"
        p.write_text(to_gpx(it), encoding="utf-8")
        written.append(str(p))
    if "html" in formats:
        p = out / "itinerary.html"
        p.write_text(to_html(it), encoding="utf-8")
        written.append(str(p))
    return written


_KIND_IT = {
    "museum": "Museo", "gallery": "Galleria d'arte", "park": "Parco", "garden": "Giardino",
    "market": "Mercato", "mall": "Centro commerciale", "shop": "Negozio",
    "shopping_street": "Via dello shopping", "shrine": "Santuario shintoista",
    "temple": "Tempio", "church": "Chiesa", "castle": "Castello", "landmark": "Monumento",
    "viewpoint": "Punto panoramico", "photo_spot": "Attrazione", "restaurant": "Ristorante",
    "cafe": "Caffè", "bar": "Bar", "nightlife_alley": "Vicolo notturno",
    "neighborhood": "Quartiere", "station": "Stazione", "convenience": "Konbini",
    "default": "Luogo",
}
_KIND_EMOJI = {
    "park": "\U0001f333", "garden": "\U0001f331", "shrine": "⛩️", "temple": "\U0001f6d0",
    "market": "\U0001f35c", "mall": "\U0001f6cd️", "shop": "\U0001f6cd️", "museum": "\U0001f3db️",
    "viewpoint": "\U0001f304", "photo_spot": "\U0001f4f7", "restaurant": "\U0001f371",
    "cafe": "☕", "bar": "\U0001f378", "castle": "\U0001f3f0", "station": "\U0001f689",
    "neighborhood": "\U0001f3d9️", "landmark": "\U0001f5fc", "convenience": "\U0001f3ea",
    "default": "\U0001f4cd",
}

_DWELL = {
    "museum": 90, "gallery": 75, "park": 60, "garden": 60, "market": 60, "mall": 90,
    "shop": 45, "shopping_street": 60, "shrine": 45, "temple": 45, "church": 30,
    "castle": 90, "landmark": 30, "viewpoint": 30, "photo_spot": 20, "restaurant": 75,
    "cafe": 40, "bar": 60, "nightlife_alley": 75, "neighborhood": 90, "station": 15,
    "convenience": 10, "default": 45,
}


_KNOWN_AREAS_JS = [
    ["Tokyo", 35.68, 139.75], ["Yokohama", 35.44, 139.64],
    ["Kamakura / Enoshima", 35.31, 139.49], ["Fuji / Fujiyoshida", 35.49, 138.80],
    ["Nikko", 36.75, 139.60], ["Hakone", 35.23, 139.02],
    ["Kyoto", 35.01, 135.76], ["Osaka", 34.69, 135.50], ["Nara", 34.685, 135.83],
    ["Kobe", 34.69, 135.20], ["Hiroshima", 34.39, 132.45], ["Kanazawa", 36.56, 136.65],
    ["Sapporo", 43.06, 141.35], ["Fukuoka", 33.59, 130.40],
]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "x"


def to_html(it: Itinerary) -> str:
    """Self-contained interactive planner: toggles for generic / closed places,
    per-stop enable-disable and actual-arrival time, add-a-Maps-link, editable
    start / end times - the schedule recomputes client-side on every change."""
    data = {
        "title": it.title,
        "sourceUrl": it.source_url,
        "enriched": it.enriched,
        "mode": it.travel_mode,
        "stateKey": "mli:" + __import__("hashlib").md5(
            (it.source_url or it.title).encode("utf-8")
        ).hexdigest()[:12],
        "cfg": {
            "dayStart": it.day_start,
            "dayEnd": it.day_end,
            "lunchAt": it.lunch_at,
            "lunchMin": it.lunch_minutes,
            "pace": it.pace_factor,
            "walkKm": it.walk_threshold_km,
            "speeds": {"walking": 4.5, "transit": 16.0, "driving": 22.0},
            "dwell": _DWELL,
        },
        "kindLabels": _KIND_IT,
        "kindEmoji": _KIND_EMOJI,
        "knownAreas": _KNOWN_AREAS_JS,
        "skippedNoCoords": it.skipped_no_coords,
        "days": [],
    }
    for day in it.days:
        stops = []
        for i, stop in enumerate(day.stops):
            p = stop.place
            reason = p.disabled_reason or ("closed" if p.permanently_closed else "")
            stops.append(
                {
                    "id": f"{day.index}-{i}-{_slug(p.name)}",
                    "name": p.name,
                    "kind": p.kind,
                    "category": p.category,
                    "rating": p.rating,
                    "reviews": p.reviews,
                    "photo": p.photo,
                    "address": p.address,
                    "note": p.note,
                    "editorial": p.editorial,
                    "hours": p.opening_hours,
                    "lat": p.lat,
                    "lng": p.lng,
                    "placeId": p.place_id,
                    "reason": reason,
                }
            )
        data["days"].append({"index": day.index, "label": day.label, "stops": stops})

    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (
        "<!doctype html><html lang=\"it\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(it.title)}</title><style>{_APP_CSS}</style></head><body>"
        "<div id=\"app\"></div>"
        f"<script id=\"ITIN\" type=\"application/json\">{payload}</script>"
        f"<script>{_APP_JS}</script></body></html>"
    )


_APP_CSS = r"""
:root{--bg:#f6f5f2;--card:#fff;--ink:#1c1b18;--mut:#6b675f;--line:#e7e3db;--accent:#b4472e;--ok:#2e7d5b;--rail:210px}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent)}
button{font:inherit;cursor:pointer}
#app{display:flex;align-items:flex-start}
.rail{flex:0 0 var(--rail);position:sticky;top:0;height:100vh;overflow-y:auto;border-right:1px solid var(--line);background:var(--card)}
.railinner{padding:16px 10px;display:flex;flex-direction:column;gap:3px}
.raillabel{font-weight:700;font-size:13px;padding:2px 8px 8px;color:var(--mut)}
.raillink{display:flex;flex-direction:column;gap:1px;text-decoration:none;color:var(--ink);padding:7px 9px;border-radius:8px;line-height:1.2}
.raillink b{font-size:13px}
.raillink span{font-size:11px;color:var(--mut);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.raillink:hover{background:var(--bg)}
.raillink.here{background:var(--accent);color:#fff}
.raillink.here span{color:#fde7e1}
.content{flex:1;min-width:0}
section.day{scroll-margin-top:8px}
@media(max-width:720px){
  #app{display:block}
  .rail{position:sticky;top:0;height:auto;width:100%;flex-basis:auto;border-right:0;border-bottom:1px solid var(--line);z-index:20}
  .railinner{flex-direction:row;flex-wrap:nowrap;overflow-x:auto;gap:6px;padding:8px 12px}
  .raillabel{display:none}
  .raillink{flex-direction:row;gap:5px;align-items:baseline;border:1px solid var(--line);white-space:nowrap}
  .raillink span{max-width:90px}
}
.itinbar{max-width:820px;margin:0 auto;padding:14px 20px 0;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.itinbar select{font:inherit;padding:4px 6px;border:1px solid var(--line);border-radius:7px;max-width:60vw}
.itinbar .newitin{flex-basis:100%;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin-top:6px}
.itinbar .newitin input,.itinbar .newitin textarea{width:100%;font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:7px;margin:5px 0}
.itinbar .newitin .r{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.itinbar .newitin .go{background:var(--accent);color:#fff;border:0;border-radius:7px;padding:6px 12px}
header{padding:16px 20px 10px;max-width:820px;margin:0 auto}
header h1{margin:0 0 4px;font-size:26px}
.sub{margin:0 0 12px;color:var(--mut);font-size:14px}
.panel{max-width:820px;margin:0 auto;padding:0 20px}
.controls{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px;display:flex;flex-wrap:wrap;gap:14px 22px;align-items:center}
.controls label{display:flex;gap:7px;align-items:center;font-size:13px}
.controls input[type=time]{font:inherit;padding:3px 5px;border:1px solid var(--line);border-radius:6px}
.sw{position:relative;width:38px;height:22px;flex:0 0 auto}
.sw input{opacity:0;width:100%;height:100%;margin:0;cursor:pointer}
.sw span{position:absolute;inset:0;background:#cfc9be;border-radius:999px;transition:.15s}
.sw span:before{content:"";position:absolute;width:16px;height:16px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.15s}
.sw input:checked+span{background:var(--accent)}
.sw input:checked+span:before{transform:translateX(16px)}
nav{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0 0}
nav a{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:5px 11px;font-size:13px;text-decoration:none;color:var(--ink)}
main{max-width:820px;margin:0 auto;padding:6px 20px 70px}
.day{margin-top:22px}
.day-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;position:sticky;top:0;background:var(--bg);padding:10px 0;border-bottom:2px solid var(--ink);z-index:5}
.day-head h2{margin:0;font-size:19px}
.day-head .place{font-weight:600}
.day-head .tot{color:var(--mut);font-size:12px}
.day-head .spacer{flex:1}
.day-head input[type=time]{font:inherit;padding:2px 4px;border:1px solid var(--line);border-radius:6px}
.day-head button.mini,.stop button.mini{font-size:12px;border:1px solid var(--line);background:var(--card);border-radius:7px;padding:3px 8px}
.warn{color:#9a2f19;font-size:12px;font-weight:600}
.stop{display:flex;gap:14px;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;margin:12px 0;position:relative}
.stop.off{opacity:.62;filter:grayscale(.7)}
.stop.off .photo{filter:grayscale(1)}
.offtag{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.photo{flex:0 0 148px;background:#ece8e1}
.photo img{width:100%;height:100%;object-fit:cover;display:block;min-height:148px}
.noimg{width:100%;height:100%;min-height:148px;display:grid;place-items:center;font-size:38px}
.pbody{padding:11px 13px;min-width:0;flex:1}
.time{font-variant-numeric:tabular-nums;font-weight:700;color:var(--accent)}
.time span{margin:0 4px;color:var(--mut);font-weight:400}
.pbody h3{margin:2px 0 6px;font-size:16px;line-height:1.25}
.pbody h3 a{text-decoration:none}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:5px}
.chip{background:#efece5;border-radius:6px;padding:2px 8px;font-size:12px;color:var(--mut)}
.chip.star{background:#fbf0d9;color:#8a6d1d}
.chip.closed{background:#f6dcd6;color:#9a2f19}
.chip.generic{background:#e4e9f0;color:#3f5b7d}
.dwell{margin:5px 0;display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:13px}
.dwell .step{width:22px;height:22px;line-height:1;border:1px solid var(--line);background:var(--card);border-radius:6px;padding:0}
.dwell .step:disabled{opacity:.4;cursor:default}
.addr,.note{margin:3px 0;color:var(--mut);font-size:13px}
.hours{margin-top:5px;font-size:13px}.hours ul{margin:5px 0 0;padding-left:18px;color:var(--mut)}
.stoprow{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px}
.stoprow input[type=time],.stoprow select{font:inherit;padding:2px 4px;border:1px solid var(--line);border-radius:6px}
.usernote{display:block;font-size:12px;color:var(--mut);margin-top:8px}
.usernote input{display:block;width:100%;font:inherit;color:var(--ink);padding:5px 7px;border:1px solid var(--line);border-radius:7px;margin-top:3px;background:#fffdf7}
.hiddenrow,.fillpanel{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:8px 0}
.fillpanel{background:#eef3ec;border:1px solid #d3e0cf;border-radius:12px;padding:9px 11px}
.fillpanel .hint{flex-basis:100%}
.pill{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:12px;color:var(--ink)}
.pill b{color:var(--accent)}
.leg{display:flex;align-items:center;gap:9px;margin:1px 0 1px 18px;color:var(--mut);font-size:13px}
.leg .ico{font-size:16px}
.leg a{margin-left:auto;text-decoration:none;white-space:nowrap}
.addbox{background:var(--card);border:1px dashed var(--line);border-radius:12px;padding:10px 12px;margin:10px 0}
.addbox input[type=text]{width:100%;font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:7px;margin:6px 0}
.addbox .r{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.addbox select{font:inherit;padding:4px 6px;border:1px solid var(--line);border-radius:7px}
.addbox button.go{background:var(--accent);color:#fff;border:0;border-radius:7px;padding:6px 12px}
.offlist{margin:6px 0}
.offlist summary{cursor:pointer;color:var(--mut);font-size:13px;padding:4px 0}
.dropped{margin-top:30px;padding-top:12px;border-top:1px solid var(--line);color:var(--mut);font-size:13px}
.hint{color:var(--mut);font-size:12px}
footer{max-width:820px;margin:0 auto;padding:20px;color:var(--mut);font-size:12px}
@media(max-width:560px){.stop{flex-direction:column}.photo{flex-basis:170px}.photo img,.noimg{min-height:170px}}
"""

_APP_JS = r"""
"use strict";
const SEED = JSON.parse(document.getElementById("ITIN").textContent);
const STORE_KEY = "mli:store:v2";

/* ---- multi-itinerary store: many lists, each with its own edit state ---- */
function loadStore(){
  try{
    const j = JSON.parse(localStorage.getItem(STORE_KEY));
    if(j && j.itins && j.itins.length) return j;
  }catch(e){}
  return {current:"default", itins:[{id:"default", data:SEED, state:null}]};
}
function saveStore(){ try{ localStorage.setItem(STORE_KEY, JSON.stringify(STORE)); }catch(e){} }
let STORE = loadStore();
// keep the built-in itinerary's data in sync with this file
const def = STORE.itins.find(i=>i.id==="default");
if(def) def.data = SEED; else STORE.itins.unshift({id:"default", data:SEED, state:null});

let D, CFG, SPEED, S;
function freshState(){
  return {showGeneric:false, showClosed:false, globalStart:CFG.dayStart, globalEnd:CFG.dayEnd||"20:00",
          perStop:{}, dayCfg:{}, added:[], order:{}};
}
function selectItin(id){
  const it = STORE.itins.find(i=>i.id===id) || STORE.itins[0];
  STORE.current = it.id;
  D = it.data; CFG = D.cfg; SPEED = CFG.speeds;
  S = it.state ? Object.assign(freshState(), it.state) : freshState();
  it.state = S;                       // bind so save() persists into the slot
  rebuildHome();
}
function save(){ saveStore(); }
function ps(id){ return S.perStop[id] || (S.perStop[id] = {}); }

/* ---------- geometry ---------- */
function hav(a,b){
  const R=6371, r=Math.PI/180;
  const dla=(b.lat-a.lat)*r, dlo=(b.lng-a.lng)*r;
  const s=Math.sin(dla/2)**2 + Math.cos(a.lat*r)*Math.cos(b.lat*r)*Math.sin(dlo/2)**2;
  return 2*R*Math.asin(Math.sqrt(s));
}
function legInfo(a,b){
  const km0 = hav(a,b);
  const walk = km0 <= CFG.walkKm && D.mode !== "driving";
  const mode = walk ? "walking" : D.mode;
  const detour = mode==="walking" ? 1.3 : 1.4;
  const km = km0*detour;
  let min = km / SPEED[mode] * 60 + (mode==="transit"?4:0);
  return {km:Math.round(km*10)/10, min:Math.round(min), mode};
}
function dwell(kind){ return Math.round((CFG.dwell[kind]||CFG.dwell.default) * CFG.pace); }
const toMin = t => { const [h,m]=t.split(":").map(Number); return h*60+m; };
const hhmm = m => { m=Math.round(m); return String(Math.floor(m/60)%24).padStart(2,"0")+":"+String(((m%60)+60)%60).padStart(2,"0"); };
const minLabel = m => { m=Math.round(m); const h=Math.floor(m/60), x=m%60; return h&&x?`${h}h ${x}min`:h?`${h}h`:`${x} min`; };
const MODE = {walking:["A piedi","🚶"],transit:["Mezzi","🚆"],driving:["Auto","🚗"]};

/* ---------- per-day model ---------- */
let HOME = {};                                      // stop id -> its original day
function rebuildHome(){ HOME = {}; D.days.forEach(d => d.stops.forEach(s => { HOME[s.id] = d.index; })); }
const dayOf = (id, fallback) => {
  const ov = (S.perStop[id] || {}).day;
  return ov != null ? ov : (HOME[id] != null ? HOME[id] : fallback);
};
function baseStops(day){
  let list = [];
  D.days.forEach(d => d.stops.forEach(s => { if(dayOf(s.id) === day.index) list.push(s); }));
  S.added.forEach(a => { if(dayOf(a.id, a.day) === day.index) list.push({...a, added:true, reason:""}); });
  const custom = S.order[day.index];
  if(custom){
    const byId = {}; list.forEach(s => byId[s.id]=s);
    const seen = new Set();
    const ordered = custom.map(id=>byId[id]).filter(s=>s && !seen.has(s.id) && seen.add(s.id));
    list.forEach(s=>{ if(!seen.has(s.id)) ordered.push(s); });
    list = ordered;
  }
  return list;
}
function reorder(dayIndex, id, dir){
  const day = D.days.find(d=>d.index===dayIndex);
  const ids = baseStops(day).map(s=>s.id);
  const i = ids.indexOf(id), j = i + dir;
  if(i<0 || j<0 || j>=ids.length) return;
  [ids[i], ids[j]] = [ids[j], ids[i]];
  S.order[dayIndex] = ids;
}
function moveToDay(id, toDay){
  ps(id).day = toDay;
  for(const k in S.order) S.order[k] = S.order[k].filter(x=>x!==id);
}
function enabled(s){
  const st = S.perStop[s.id] || {};
  if(st.off) return false;
  if(st.on) return true;
  if(s.reason==="generic") return S.showGeneric;
  if(s.reason==="closed")  return S.showClosed;
  return true;
}
function nnOrder(items, from){
  const left = items.slice(); const out=[];
  let cur = from || left[0];
  if(!from && left.length){ out.push(left.shift()); cur = out[0]; }
  while(left.length){
    let bi=0, bd=Infinity;
    left.forEach((s,i)=>{ const d=hav(cur,s); if(d<bd){bd=d;bi=i;} });
    cur = left.splice(bi,1)[0]; out.push(cur);
  }
  return out;
}
function schedule(day){
  const all = baseStops(day);
  let on = all.filter(enabled);
  const dc = S.dayCfg[day.index] || {};
  const start = toMin(dc.start || S.globalStart);
  const endStr = dc.end || S.globalEnd;
  const endLimit = endStr ? toMin(endStr) : null;

  // optional re-optimise of the tail after a stop with a recorded arrival
  const anchorId = Object.keys(S.perStop).find(id => (S.perStop[id]||{}).reoptFrom && on.some(s=>s.id===id));
  if(anchorId){
    const idx = on.findIndex(s=>s.id===anchorId);
    if(idx>=0 && idx < on.length-1){
      const head = on.slice(0, idx+1);
      const tail = nnOrder(on.slice(idx+1), on[idx]);
      on = head.concat(tail);
    }
  }

  // render a stop when it's active, or when it was switched off by hand (those
  // stay visible, greyed); generic / closed entries left at their default-off
  // stay hidden until their master switch is on
  const shownAll = all.filter(s => enabled(s) || (S.perStop[s.id]||{}).off);
  let k = 0;
  const seq = shownAll.map(s => enabled(s) ? on[k++] : s);

  const rows = []; let t = start, lunched = false, prev = null;
  seq.forEach(s=>{
    const st = S.perStop[s.id] || {};
    if(!enabled(s)){ rows.push({type:"stopoff", stop:s}); return; }
    if(prev){
      const lg = legInfo(prev, s);
      rows.push({type:"leg", ...lg, from:prev, to:s});
      t += lg.min;
    }
    if(st.arrive) t = toMin(st.arrive);            // real arrival re-anchors everything after
    if(!lunched && prev && t >= toMin(CFG.lunchAt)){ t += CFG.lunchMin; lunched = true; rows.push({type:"lunch"}); }
    const dw = st.dwell != null ? st.dwell : dwell(s.kind);
    rows.push({type:"stop", stop:s, arrive:t, depart:t+dw, dwell:dw, real:!!st.arrive, custom:st.dwell!=null});
    t += dw;
    prev = s;
  });
  return {rows, end:t, endLimit, endStr, count:on.length,
          hidden: all.filter(s => !enabled(s) && !(S.perStop[s.id]||{}).off),
          travel: rows.filter(r=>r.type==="leg").reduce((a,r)=>a+r.min,0),
          visit: rows.filter(r=>r.type==="stop").reduce((a,r)=>a+r.dwell,0)};
}

/* ---------- Maps URL parsing ---------- */
function parseMapsUrl(url){
  let m = url.match(/!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)/) || url.match(/[@/](-?\d+\.\d+),(-?\d+\.\d+)/);
  if(!m) return null;
  let name = "Tappa aggiunta";
  const nm = url.match(/\/maps\/place\/([^/@?]+)/);
  if(nm){ try{ name = decodeURIComponent(nm[1].replace(/\+/g," ")); }catch(e){ name = nm[1]; } }
  return {lat:parseFloat(m[1]), lng:parseFloat(m[2]), name, url};
}
function bestDayFor(pt){
  let best=null, bestCost=Infinity;
  D.days.forEach(day=>{
    const on = baseStops(day).filter(enabled);
    if(!on.length) return;
    for(let i=0;i<=on.length;i++){
      const prev = on[i-1], next = on[i];
      let c = 0;
      if(prev) c += hav(prev,pt);
      if(next) c += hav(pt,next);
      if(prev && next) c -= hav(prev,next);
      if(!prev || !next) c += 3;               // small penalty for going on an end
      if(c < bestCost){ bestCost=c; best={day:day.index, pos:i}; }
    }
  });
  return best;
}

/* ---------- rendering ---------- */
const dirUrl = (a,b,mode) =>
  `https://www.google.com/maps/dir/?api=1&origin=${a.lat},${a.lng}&destination=${b.lat},${b.lng}&travelmode=${mode}`;
const mapsUrl = s => s.placeId
  ? `https://www.google.com/maps/search/?api=1&query=${s.lat},${s.lng}&query_place_id=${s.placeId}`
  : (s.url || `https://www.google.com/maps/search/?api=1&query=${s.lat},${s.lng}`);
const esc = s => (s==null?"":String(s)).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

function stopCard(r){
  const s = r.stop, st = S.perStop[s.id] || {};
  const off = r.type==="stopoff" || !enabled(s);
  const dw = st.dwell != null ? st.dwell : dwell(s.kind);
  const kindLabel = D.kindLabels[s.kind] || s.category || "Luogo";
  const chips = [`<span class="chip">${esc(kindLabel)}</span>`];
  if(s.rating) chips.push(`<span class="chip star">★ ${String(s.rating).replace(".",",")}${s.reviews?` (${s.reviews})`:""}</span>`);
  if(s.reason==="closed") chips.push(`<span class="chip closed">Chiuso / apertura limitata</span>`);
  if(s.reason==="generic") chips.push(`<span class="chip generic">Area generica</span>`);
  if(s.added) chips.push(`<span class="chip">Aggiunto</span>`);
  const img = s.photo
    ? `<img loading="lazy" src="${esc(s.photo)}" alt="">`
    : `<div class="noimg">${D.kindEmoji[s.kind]||"📍"}</div>`;
  let extra = "";
  if(s.address) extra += `<p class="addr">${esc(s.address)}</p>`;
  if(s.note) extra += `<p class="note">${esc(s.note)}</p>`;
  if(s.editorial) extra += `<p class="note">${esc(s.editorial)}</p>`;
  if(s.hours && s.hours.length) extra += `<details class="hours"><summary>Orari</summary><ul>${s.hours.map(h=>`<li>${esc(h)}</li>`).join("")}</ul></details>`;

  return `<article class="stop ${off?"off":""}" id="stop-${esc(s.id)}">
    <div class="photo">${img}</div>
    <div class="pbody">
      <div class="time">${off?'<span class="offtag">tappa disattivata</span>':hhmm(r.arrive)+'<span>&ndash;</span>'+hhmm(r.depart)}${r.real?' <span class="warn">arrivo reale</span>':""}</div>
      <h3><a href="${esc(mapsUrl(s))}" target="_blank" rel="noopener">${esc(s.name)}</a></h3>
      <div class="chips">${chips.join("")}</div>
      <div class="dwell">
        <span>Sosta:</span>
        <button class="step" data-act="dwell-" data-id="${esc(s.id)}" ${off?"disabled":""}>&minus;</button>
        <b>${minLabel(dw)}</b>${st.dwell!=null?' <span class="hint">(modificata)</span>':''}
        <button class="step" data-act="dwell+" data-id="${esc(s.id)}" ${off?"disabled":""}>+</button>
        ${st.dwell!=null?`<button class="mini" data-act="dwell0" data-id="${esc(s.id)}">reimposta</button>`:""}
      </div>
      ${extra}
      <label class="usernote">La tua nota
        <input type="text" data-act="note" data-id="${esc(s.id)}" value="${esc(st.note||"")}" placeholder="promemoria per questa tappa…">
      </label>
      <div class="stoprow">
        <button class="mini" data-act="up" data-id="${esc(s.id)}" data-day="${dayOf(s.id, D.days[0].index)}">↑</button>
        <button class="mini" data-act="down" data-id="${esc(s.id)}" data-day="${dayOf(s.id, D.days[0].index)}">↓</button>
        <label>giorno
          <select data-act="moveday" data-id="${esc(s.id)}">
            ${D.days.map(d=>`<option value="${d.index}" ${d.index===dayOf(s.id,D.days[0].index)?"selected":""}>G${d.index} — ${esc(d.label)}</option>`).join("")}
          </select>
        </label>
        <button class="mini" data-act="toggle" data-id="${esc(s.id)}">${off?"Riattiva tappa":"Disattiva tappa"}</button>
        <label>arrivo effettivo
          <input type="time" data-act="arrive" data-id="${esc(s.id)}" value="${st.arrive||""}">
        </label>
        ${st.arrive?`<button class="mini" data-act="arrive0" data-id="${esc(s.id)}">annulla</button>`:""}
        <button class="mini" data-act="reopt" data-id="${esc(s.id)}">Riottimizza seguito</button>
        ${s.added?`<button class="mini" data-act="remove" data-id="${esc(s.id)}">Rimuovi</button>`:""}
      </div>
    </div>
  </article>`;
}

function legRow(r){
  const [label,icon] = MODE[r.mode] || ["","→"];
  return `<div class="leg"><span class="ico">${icon}</span>
    <span>${label} &middot; <b>${r.min} min</b> &middot; ${String(r.km).replace(".",",")} km</span>
    <a href="${dirUrl(r.from,r.to,r.mode)}" target="_blank" rel="noopener">naviga ↗</a></div>`;
}

function daySection(day){
  const sc = schedule(day);
  const dc = S.dayCfg[day.index] || {};
  const over = sc.endLimit && sc.end > sc.endLimit;
  const body = sc.rows.map(r =>
      r.type==="leg" ? legRow(r)
    : r.type==="lunch" ? `<div class="leg"><span class="ico">🍱</span><span>Pausa pranzo &middot; ${CFG.lunchMin} min</span></div>`
    : stopCard(r)     // "stop" and "stopoff"
  ).join("");
  const slack = sc.endLimit ? sc.endLimit - sc.end : 0;
  return `<section class="day" id="g${day.index}">
    <div class="day-head">
      <h2>Giorno ${day.index}</h2><span class="place">${esc(day.label)}</span>
      <span class="spacer"></span>
      <label class="hint">inizio <input type="time" data-act="daystart" data-day="${day.index}" value="${dc.start||S.globalStart}"></label>
      <label class="hint">fine <input type="time" data-act="dayend" data-day="${day.index}" value="${dc.end||""}" placeholder="${S.globalEnd||"--:--"}"></label>
      <button class="mini" data-act="reoptday" data-day="${day.index}">Riottimizza giorno</button>
    </div>
    <p class="day-head tot">${sc.count} tappe &middot; visite ~${minLabel(sc.visit)} &middot; spostamenti ~${minLabel(sc.travel)} &middot;
       fine prevista <b>${hhmm(sc.end)}</b> ${over?`<span class="warn">oltre le ${sc.endStr}</span>`:""}</p>
    ${body}
    ${sc.hidden.length ? `<div class="hiddenrow"><span class="hint">Nascoste in questo giorno:</span>
      ${sc.hidden.map(s=>`<button class="pill" data-act="show" data-id="${esc(s.id)}">${esc(s.name)} <b>+</b></button>`).join("")}</div>` : ""}
    ${slack > 40 ? fillPanel(day, sc, slack) : ""}
    ${addBox(day.index)}
  </section>`;
}

function fillPanel(day, sc, slack){
  const anchor = sc.rows.filter(r=>r.type==="stop").map(r=>r.stop).pop()
               || {lat:day.stops[0]?.lat, lng:day.stops[0]?.lng};
  // enabled stops from later days, nearest first, with the time each would add
  const cands = [];
  D.days.filter(d=>d.index>day.index).forEach(d=>{
    baseStops(d).filter(enabled).forEach(s=>{
      const lg = legInfo(anchor, s);
      cands.push({s, add: lg.min + (S.perStop[s.id]?.dwell ?? dwell(s.kind)), km: lg.km, from:d.index});
    });
  });
  cands.sort((a,b)=>a.km-b.km);
  // a day with lots of free time can justify a hop to another city
  const maxKm = slack > 240 ? 90 : slack > 150 ? 55 : slack > 80 ? 30 : 16;
  const fit = cands.filter(c=>c.km <= maxKm && c.add <= slack + 25).slice(0,8);
  if(!fit.length) return "";
  return `<div class="fillpanel">
    <span class="hint">La giornata finisce alle <b>${hhmm(sc.end)}</b>, ${minLabel(slack)} liberi fino alle ${sc.endStr}.
      Sposta qui una tappa dai giorni successivi:</span>
    ${fit.map(c=>`<button class="pill" data-act="pullin" data-id="${esc(c.s.id)}" data-day="${day.index}">
      ${esc(c.s.name)} <span class="hint">(da G${c.from}, +${minLabel(c.add)})</span> <b>+</b></button>`).join("")}
  </div>`;
}

function addBox(dayIndex){
  return `<div class="addbox">
    <b class="hint">Aggiungi una tappa da un link di Google Maps</b>
    <input type="text" data-add="url-${dayIndex}" placeholder="Incolla qui il link (URL completo con /maps/place/... o con @lat,lng)">
    <div class="r">
      <select data-add="day-${dayIndex}">
        <option value="0">Posizionamento automatico</option>
        ${D.days.map(d=>`<option value="${d.index}" ${d.index===dayIndex?"selected":""}>Giorno ${d.index} — ${esc(d.label)}</option>`).join("")}
      </select>
      <button class="go" data-act="add" data-day="${dayIndex}">Aggiungi</button>
    </div>
    <div class="hint" data-add="msg-${dayIndex}"></div>
  </div>`;
}

function itinBar(){
  return `<div class="itinbar">
    <label>Itinerario
      <select data-act="pickitin">
        ${STORE.itins.map(i=>`<option value="${esc(i.id)}" ${i.id===STORE.current?"selected":""}>${esc(i.data.title)}</option>`).join("")}
      </select>
    </label>
    <button class="mini" data-act="newitin">＋ Nuovo itinerario</button>
    ${STORE.current!=="default" ? `<button class="mini" data-act="delitin">Elimina questo</button>` : ""}
    <div class="newitin" hidden>
      <input type="text" data-new="title" placeholder="Titolo (es. Portogallo 2027)">
      <textarea data-new="urls" rows="5" placeholder="Incolla i link di Google Maps dei posti, uno per riga (URL completi con /maps/place/... o con @lat,lng)"></textarea>
      <div class="r"><button class="go" data-act="createitin">Crea itinerario</button>
        <span class="hint" data-new="msg"></span></div>
      <p class="hint">I posti aggiunti così non hanno foto/categoria (il browser non può interrogare Google): avrai comunque un piano ordinato e modificabile.</p>
    </div>
  </div>`;
}

function render(){
  const n = D.days.reduce((a,d)=>a+schedule(d).count,0);
  document.getElementById("app").innerHTML = `
  <aside class="rail">
    <div class="railinner">
      <span class="raillabel">${esc(D.title)}</span>
      ${D.days.map(d=>`<a class="raillink" href="#g${d.index}" data-day="${d.index}">
        <b>G${d.index}</b><span>${esc(d.label)}</span></a>`).join("")}
    </div>
  </aside>
  <div class="content">
  ${itinBar()}
  <header>
    <h1>${esc(D.title)}</h1>
    <p class="sub">${D.days.length} giorni &middot; ${n} tappe attive &middot; spostamenti stimati in linea d'aria${D.enriched?"":" &middot; orari non verificati"}</p>
  </header>
  <div class="panel"><div class="controls">
    <label><span class="sw"><input type="checkbox" data-act="showGeneric" ${S.showGeneric?"checked":""}><span></span></span> Mostra aree generiche</label>
    <label><span class="sw"><input type="checkbox" data-act="showClosed" ${S.showClosed?"checked":""}><span></span></span> Mostra luoghi chiusi / ad accesso limitato</label>
    <label>Partenza predefinita <input type="time" data-act="globalStart" value="${S.globalStart}"></label>
    <label>Fine predefinita <input type="time" data-act="globalEnd" value="${S.globalEnd}"></label>
    <button class="mini" data-act="reset">Ripristina tutto</button>
  </div></div>
  <main>${D.days.map(daySection).join("")}
    ${D.skippedNoCoords && D.skippedNoCoords.length ? `<div class="dropped"><b>Senza coordinate (non pianificabili):</b> ${D.skippedNoCoords.map(esc).join(", ")}</div>`:""}
  </main>
  <footer>Generato da <code>maps-list-to-itinerary</code>
    ${D.sourceUrl?` &middot; <a href="${esc(D.sourceUrl)}" target="_blank" rel="noopener">lista di origine</a>`:""}
    &middot; le modifiche restano su questo browser</footer>
  </div>`;
  spyDays();
}

let _spyObs = null;
function spyDays(){
  if(_spyObs) _spyObs.disconnect();
  const links = {};
  document.querySelectorAll(".raillink").forEach(a=>{ links[a.dataset.day] = a; });
  _spyObs = new IntersectionObserver(entries=>{
    entries.forEach(en=>{
      if(en.isIntersecting){
        Object.values(links).forEach(a=>a.classList.remove("here"));
        const idx = en.target.id.slice(1);
        if(links[idx]) links[idx].classList.add("here");
      }
    });
  }, {rootMargin:"-10% 0px -80% 0px"});
  document.querySelectorAll("section.day").forEach(s=>_spyObs.observe(s));
}

/* ---------- build an itinerary from pasted Maps links (client side) ---------- */
function regionsOf(pts, gapKm){
  const par = pts.map((_,i)=>i);
  const find = a => { while(par[a]!==a){ par[a]=par[par[a]]; a=par[a]; } return a; };
  for(let i=0;i<pts.length;i++) for(let j=i+1;j<pts.length;j++)
    if(hav(pts[i],pts[j])<=gapKm) par[find(i)]=find(j);
  const b={}; pts.forEach((p,i)=>{ (b[find(i)]=b[find(i)]||[]).push(p); });
  return Object.values(b);
}
function kSplit(list, k){
  if(k<=1 || list.length<=k) return k<=1 ? [list] : list.map(p=>[p]);
  const cx = p => p.lng*Math.cos(p.lat*Math.PI/180);
  let cen = list.slice(0,k).map(p=>({x:cx(p),y:p.lat}));
  let groups;
  for(let it=0;it<40;it++){
    groups = cen.map(()=>[]);
    list.forEach(p=>{
      let bi=0,bd=1e9; cen.forEach((c,i)=>{ const d=(cx(p)-c.x)**2+(p.lat-c.y)**2; if(d<bd){bd=d;bi=i;} });
      groups[bi].push(p);
    });
    const nc = groups.map((g,i)=> g.length ? {x:g.reduce((a,p)=>a+cx(p),0)/g.length, y:g.reduce((a,p)=>a+p.lat,0)/g.length} : cen[i]);
    if(nc.every((c,i)=>c.x===cen[i].x && c.y===cen[i].y)) break;
    cen = nc;
  }
  return groups.filter(g=>g.length);
}
function areaLabel(pts){
  const c = {lat:pts.reduce((a,p)=>a+p.lat,0)/pts.length, lng:pts.reduce((a,p)=>a+p.lng,0)/pts.length};
  let best="", bd=1e9;
  (SEED.knownAreas||[]).forEach(([n,la,lo])=>{ const d=hav(c,{lat:la,lng:lo}); if(d<bd){bd=d;best=n;} });
  return bd<70 ? best : `${c.lat.toFixed(2)}, ${c.lng.toFixed(2)}`;
}
function buildItin(title, pts){
  const days = [];
  const regions = regionsOf(pts, 40);
  regions.sort((a,b)=>{
    const ca={lat:a.reduce((s,p)=>s+p.lat,0)/a.length}, cb={lat:b.reduce((s,p)=>s+p.lat,0)/b.length};
    return ca.lat - cb.lat;
  });
  regions.forEach(r=>{
    kSplit(r, Math.max(1, Math.ceil(r.length/6))).forEach(chunk=>{
      const ordered = nnOrder(chunk);
      const di = days.length+1;
      days.push({index:di, label:areaLabel(ordered), stops: ordered.map((p,i)=>({
        id:`c${di}-${i}-${(p.name||"x").toLowerCase().replace(/[^a-z0-9]+/g,"-").slice(0,28)}`,
        name:p.name||"Tappa", kind:"default", category:"", rating:null, reviews:null,
        photo:"", address:"", note:"", editorial:"", hours:[],
        lat:p.lat, lng:p.lng, placeId:"", reason:"", url:p.url||""
      }))});
    });
  });
  return {title:title||"Nuovo itinerario", sourceUrl:"", enriched:false, mode:SEED.mode,
          cfg:SEED.cfg, kindLabels:SEED.kindLabels, kindEmoji:SEED.kindEmoji,
          knownAreas:SEED.knownAreas, skippedNoCoords:[], days};
}

/* ---------- events ---------- */
document.addEventListener("change", e=>{
  const el = e.target, act = el.dataset.act;
  if(act==="showGeneric"){ S.showGeneric = el.checked; }
  else if(act==="showClosed"){ S.showClosed = el.checked; }
  else if(act==="globalStart"){ S.globalStart = el.value || CFG.dayStart; }
  else if(act==="globalEnd"){ S.globalEnd = el.value; }
  else if(act==="daystart"){ (S.dayCfg[el.dataset.day] ||= {}).start = el.value; }
  else if(act==="dayend"){ (S.dayCfg[el.dataset.day] ||= {}).end = el.value; }
  else if(act==="arrive"){ ps(el.dataset.id).arrive = el.value; }
  else if(act==="note"){ const v=el.value.trim(); if(v) ps(el.dataset.id).note=v; else delete ps(el.dataset.id).note; }
  else if(act==="moveday"){ moveToDay(el.dataset.id, Number(el.value)); clearReopt(); }
  else if(act==="pickitin"){ selectItin(el.value); render(); return; }
  else return;
  save(); render();
});

document.addEventListener("click", e=>{
  const rl = e.target.closest(".raillink");
  if(rl){
    e.preventDefault();
    const t = document.getElementById("g"+rl.dataset.day);
    if(t) t.scrollIntoView({behavior:"smooth", block:"start"});
    document.querySelectorAll(".raillink").forEach(a=>a.classList.remove("here"));
    rl.classList.add("here");
    return;
  }
  const b = e.target.closest("button"); if(!b) return;
  const act = b.dataset.act;
  if(act==="toggle"){
    const st = ps(b.dataset.id);
    const s = allStops().find(x=>x.id===b.dataset.id);
    const onByDefault = !(s.reason==="generic"||s.reason==="closed");
    if(st.off){ st.off=false; } else if(st.on){ st.on=false; }
    else if(onByDefault){ st.off=true; } else { st.on=true; }
  }
  else if(act==="dwell-" || act==="dwell+"){
    const st = ps(b.dataset.id);
    const s = allStops().find(x=>x.id===b.dataset.id);
    const cur = st.dwell != null ? st.dwell : dwell(s.kind);
    st.dwell = Math.max(5, cur + (act==="dwell+" ? 15 : -15));
  }
  else if(act==="dwell0"){ delete ps(b.dataset.id).dwell; }
  else if(act==="arrive0"){ delete ps(b.dataset.id).arrive; }
  else if(act==="up"){ reorder(Number(b.dataset.day), b.dataset.id, -1); }
  else if(act==="down"){ reorder(Number(b.dataset.day), b.dataset.id, +1); }
  else if(act==="show"){ ps(b.dataset.id).on = true; delete ps(b.dataset.id).off; }
  else if(act==="pullin"){
    const toDay = Number(b.dataset.day);
    const cur = baseStops(D.days.find(d=>d.index===toDay)).map(s=>s.id);
    moveToDay(b.dataset.id, toDay);
    S.order[toDay] = cur.concat([b.dataset.id]);
    clearReopt();
  }
  else if(act==="reopt"){ clearReopt(); ps(b.dataset.id).reoptFrom = true; }
  else if(act==="reoptday"){
    const day = D.days.find(d=>d.index==b.dataset.day);
    const on = baseStops(day).filter(enabled);
    S.order[day.index] = nnOrder(on).map(s=>s.id);
    clearReopt();
  }
  else if(act==="remove"){
    S.added = S.added.filter(a=>a.id!==b.dataset.id);
    for(const k in S.order) S.order[k] = S.order[k].filter(id=>id!==b.dataset.id);
  }
  else if(act==="reset"){
    if(confirm("Ripristinare questo itinerario alle impostazioni originali?")){
      const it = STORE.itins.find(i=>i.id===STORE.current);
      it.state = null; selectItin(STORE.current);
    }
  }
  else if(act==="newitin"){ const nb=b.closest(".itinbar").querySelector(".newitin"); nb.hidden=!nb.hidden; return; }
  else if(act==="createitin"){
    const box = b.closest(".newitin");
    const title = box.querySelector('[data-new="title"]').value.trim();
    const lines = box.querySelector('[data-new="urls"]').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
    const msg = box.querySelector('[data-new="msg"]');
    const pts = lines.map(parseMapsUrl).filter(Boolean);
    if(pts.length < 2){ msg.textContent = `Servono almeno 2 link validi (trovati ${pts.length}). Usa URL completi con @lat,lng.`; return; }
    const id = "it-" + Date.now().toString(36);
    STORE.itins.push({id, data:buildItin(title, pts), state:null});
    saveStore(); selectItin(id); render(); return;
  }
  else if(act==="delitin"){
    if(STORE.current==="default") return;
    if(!confirm("Eliminare questo itinerario?")) return;
    STORE.itins = STORE.itins.filter(i=>i.id!==STORE.current);
    saveStore(); selectItin("default"); render(); return;
  }
  else if(act==="add"){
    const di = b.dataset.day;
    const url = (document.querySelector(`[data-add="url-${di}"]`)||{}).value || "";
    const sel = (document.querySelector(`[data-add="day-${di}"]`)||{}).value || "0";
    const msg = document.querySelector(`[data-add="msg-${di}"]`);
    const pt = parseMapsUrl(url.trim());
    if(!pt){ if(msg) msg.textContent = "Link non riconosciuto. Serve un URL completo con @lat,lng oppure !3d..!4d.. (apri il link corto in Maps e copia quello lungo)."; return; }
    const id = "add-" + Math.random().toString(36).slice(2,9);
    let day = Number(sel), pos = null;
    if(day===0){ const best = bestDayFor(pt); day = best?best.day:D.days[0].index; pos = best?best.pos:null; }
    S.added.push({id, name:pt.name, lat:pt.lat, lng:pt.lng, kind:"default", url:pt.url, day});
    if(pos!=null){
      const target = D.days.find(d=>d.index===day);
      const on = baseStops(target).filter(enabled).map(s=>s.id);
      on.splice(pos, 0, id);
      // keep disabled/other stops after, in their prior relative order
      const rest = baseStops(target).map(s=>s.id).filter(x=>!on.includes(x));
      S.order[day] = on.concat(rest);
    }
    clearReopt();
  }
  else return;
  save(); render();
});

function allStops(){ return D.days.flatMap(d=>baseStops(d)); }
function clearReopt(){ for(const k in S.perStop) delete S.perStop[k].reoptFrom; }

selectItin(STORE.current);
render();
"""


def to_markdown(it: Itinerary) -> str:
    L: list[str] = []
    L.append(f"# {it.title}")
    L.append("")
    L.append(f"Fonte: {it.source_url}")
    mode_it = {"transit": "mezzi pubblici", "walking": "a piedi", "driving": "auto"}
    L.append(
        f"{len(it.days)} giorni · spostamenti stimati **{mode_it.get(it.travel_mode, it.travel_mode)}**"
        + ("" if it.enriched else " · _orari di apertura non verificati (nessuna API key)_")
    )
    L.append("")

    for day in it.days:
        L.append(f"## Giorno {day.index} — {day.label}")
        L.append("")
        leg_by_to = {leg.to_place: leg for leg in day.legs}
        for i, stop in enumerate(day.stops):
            p = stop.place
            if i > 0 and p.name in leg_by_to:
                leg = leg_by_to[p.name]
                L.append(
                    f"   ↓ _{leg.minutes} min ({leg.distance_km} km)_ · "
                    f"[naviga]({leg.directions_url})"
                )
            head = f"**{stop.arrive}–{stop.depart}  {p.name}**"
            bits = []
            if p.category:
                bits.append(p.category)
            if p.rating:
                bits.append(f"★ {p.rating}" + (f" ({p.reviews})" if p.reviews else ""))
            if p.permanently_closed:
                bits.append("⚠️ CHIUSO DEFINITIVAMENTE")
            L.append(f"- {head}")
            if bits:
                L.append(f"  - {' · '.join(bits)}")
            if p.address:
                L.append(f"  - {p.address}")
            if p.opening_hours:
                L.append(f"  - Orari: {'; '.join(p.opening_hours)}")
            if p.editorial:
                L.append(f"  - {p.editorial}")
            if p.note:
                L.append(f"  - _{p.note.strip()}_")
            L.append(f"  - [apri in Maps]({p.maps_url()})")
        L.append("")

    _skip_block(L, "Ignorati — aree generiche (città/quartieri)", it.skipped_generic)
    _skip_block(L, "Ignorati — chiusi definitivamente", it.skipped_closed)
    _skip_block(L, "Ignorati — nessuna coordinata trovata", it.skipped_no_coords)

    L.append("")
    L.append("---")
    L.append("_Generato con maps-list-to-itinerary._")
    return "\n".join(L)


def _skip_block(L: list[str], title: str, names: list[str]) -> None:
    if not names:
        return
    L.append(f"### {title}")
    for n in names:
        L.append(f"- {n}")
    L.append("")


def to_gpx(it: Itinerary) -> str:
    L = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="maps-list-to-itinerary" '
        'xmlns="http://www.topografix.com/GPX/1/1">',
        f"  <metadata><name>{html.escape(it.title)}</name></metadata>",
    ]
    for day in it.days:
        for stop in day.stops:
            p = stop.place
            if not p.has_coords:
                continue
            L.append(f'  <wpt lat="{p.lat}" lon="{p.lng}">')
            L.append(f"    <name>{html.escape(p.name)}</name>")
            desc = f"Giorno {day.index} {stop.arrive}-{stop.depart}. {html.escape(p.category)}"
            L.append(f"    <desc>{desc}</desc>")
            L.append("  </wpt>")
    for day in it.days:
        pts = [s.place for s in day.stops if s.place.has_coords]
        if len(pts) < 2:
            continue
        L.append(f"  <trk><name>Giorno {day.index} — {html.escape(day.label)}</name><trkseg>")
        for p in pts:
            L.append(f'    <trkpt lat="{p.lat}" lon="{p.lng}"><name>{html.escape(p.name)}</name></trkpt>')
        L.append("  </trkseg></trk>")
    L.append("</gpx>")
    return "\n".join(L)

# ============================================================================
# cli.py — Command-line entry point: URL (or saved data) -> itinerary files.
# ============================================================================
import argparse
import os
import sys



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

    out = ap.add_argument_group("output")
    out.add_argument("--out", default="out", help="output directory (default: ./out)")
    out.add_argument("--format", default="html,md,json,gpx", help="comma list of: html,md,json,gpx")
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
        day_end=args.day_end,
        api_key=None if args.no_enrich else os.environ.get("GOOGLE_MAPS_API_KEY"),
    )
    title = LAST_LIST_TITLE or _guess_title(source_url)
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


def _guess_title(source: str) -> str:
    from urllib.parse import unquote

    if source and source.endswith(".json"):
        return "Itinerario"
    return "Itinerario dalla lista Google Maps"


if __name__ == "__main__":
    sys.exit(main())


# ============================================================================

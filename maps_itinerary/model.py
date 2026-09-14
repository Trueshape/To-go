"""Core data types shared across the pipeline."""

from __future__ import annotations

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


# --------------------------------------------------------------------------- #
# filtering - decide which extracted entries actually make it into the plan
# --------------------------------------------------------------------------- #
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

"""Fill in opening hours / address / a cleaner category via the Places API.

Optional. Without GOOGLE_MAPS_API_KEY the pipeline just uses what was scraped
plus category-based default visit durations.

Uses the Places API (New): Text Search to resolve a place_id, then Place Details.
Results are cached on disk so re-runs are free.
"""

from __future__ import annotations

import json
import os
import pathlib
import time

import requests

from .model import Place, CLOSED_MARKERS

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

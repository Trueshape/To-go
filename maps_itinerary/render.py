"""Write the itinerary out as Markdown, JSON and GPX."""

from __future__ import annotations

import html
import pathlib

from .model import Itinerary


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
    return written


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

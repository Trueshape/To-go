"""Decide which extracted entries actually make it into the plan."""

from __future__ import annotations

from dataclasses import dataclass

from .model import Place


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

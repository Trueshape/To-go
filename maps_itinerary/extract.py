"""Get the raw list of places out of a public Google Maps list.

Two paths:
  * scrape a live share URL with a headless browser  (needs the `browser` extra)
  * parse a page you saved yourself after scrolling it  (--from-html, no deps)

Google has no public API for saved/shared lists, so this is the fragile part of
the pipeline by design. Everything downstream works off the Place objects this
returns, so a hand-written JSON file (--from-json) is always a valid substitute.
"""

from __future__ import annotations

import html as _html
import json
import re
import time
from typing import Iterable

import requests

from .model import Place, CLOSED_MARKERS

CONSENT_COOKIE = "CONSENT=YES+cb.20211029-17-p0.en+F+cb; SOCS=CAI"
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
    """Follow a maps.app.goo.gl short link to the real URL."""
    r = requests.get(
        url,
        headers={"User-Agent": UA, "Cookie": CONSENT_COOKIE},
        allow_redirects=True,
        timeout=30,
    )
    return r.url


def extract_from_browser(url: str, *, headful: bool = False, max_scrolls: int = 120) -> list[Place]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover - env dependent
        raise SystemExit(
            "Scraping a live URL needs Playwright:\n"
            "    pip install 'maps-list-to-itinerary[browser]'\n"
            "    playwright install chromium\n"
            "Or extract by hand and pass --from-html / --from-json."
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headful)
        ctx = browser.new_context(user_agent=UA, locale="en-US")
        ctx.add_cookies(_consent_cookies())
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        _dismiss_consent(page)
        page.wait_for_timeout(2500)

        panel = _find_scroll_panel(page)
        seen = 0
        stable = 0
        for _ in range(max_scrolls):
            raw = page.evaluate(_JS_EXTRACT)
            if len(raw) == seen:
                stable += 1
                if stable >= 4:
                    break
            else:
                stable = 0
            seen = len(raw)
            if panel:
                page.evaluate("el => el.scrollBy(0, el.clientHeight * 0.9)", panel)
            else:
                page.mouse.wheel(0, 1200)
            page.wait_for_timeout(700)

        raw = page.evaluate(_JS_EXTRACT)
        title = page.evaluate(
            "() => (document.querySelector('h1')||{}).textContent || document.title"
        )
        browser.close()

    places = [_raw_to_place(r) for r in raw]
    return _dedupe([p for p in places if p.name])


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
    data = json.load(open(path, encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("places", [])
    return [Place.from_dict(d) for d in data]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
_JS_EXTRACT = r"""
() => {
  const seen = new Map();
  document.querySelectorAll('a[href*="/maps/place/"]').forEach(a => {
    const href = a.href;
    if (seen.has(href)) return;
    // climb to the card container that also holds the rating/category text
    let box = a;
    for (let i = 0; i < 6 && box.parentElement; i++) {
      box = box.parentElement;
      if (box.querySelector('[role="img"][aria-label*="star"], [aria-label*="stelle"]')) break;
    }
    const lines = Array.from(box.querySelectorAll('div,span'))
      .map(el => (el.childElementCount === 0 ? el.textContent.trim() : ''))
      .filter(Boolean);
    seen.set(href, {
      name: (a.getAttribute('aria-label') || a.textContent || '').trim(),
      href,
      lines: Array.from(new Set(lines)).slice(0, 12),
    });
  });
  return Array.from(seen.values());
}
"""


def _consent_cookies():
    return [
        {"name": "CONSENT", "value": "YES+cb", "domain": ".google.com", "path": "/"},
        {"name": "SOCS", "value": "CAI", "domain": ".google.com", "path": "/"},
    ]


def _dismiss_consent(page) -> None:
    for sel in (
        'button[aria-label*="Reject all"]',
        'button[aria-label*="Rifiuta tutto"]',
        'form[action*="consent"] button',
        'button:has-text("Reject all")',
        'button:has-text("Rifiuta tutto")',
    ):
        try:
            el = page.query_selector(sel)
            if el:
                el.click()
                page.wait_for_timeout(1500)
                return
        except Exception:
            pass


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


def _absorb_line(p: Place, line: str) -> None:
    low = line.lower()
    if not line or line == p.name:
        return
    if any(m in low for m in CLOSED_MARKERS):
        p.permanently_closed = True
        if not p.category:
            p.category = line
        return
    mr = _RATING.match(line)
    if mr:
        p.rating = float(mr.group(1).replace(",", "."))
        try:
            p.reviews = int(re.sub(r"[.,]", "", mr.group(2)))
        except ValueError:
            pass
        return
    # a short, non-numeric line right after the name is the category
    if not p.category and 2 <= len(line) <= 48 and not re.search(r"\d{3,}", line):
        if not any(tok in low for tok in ("giappone", "japan", "·", "salva", "condividi")):
            p.category = line


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

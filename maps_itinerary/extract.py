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
import os
import pathlib
import re
import time
from typing import Iterable

import requests

from .model import Place, CLOSED_MARKERS

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


# --------------------------------------------------------------------------- #
# enrichment - fill in opening hours / address / a cleaner category via the
# Places API. Optional: without GOOGLE_MAPS_API_KEY the pipeline just uses
# what was scraped plus category-based default visit durations.
# --------------------------------------------------------------------------- #
_TEXT_SEARCH = "https://places.googleapis.com/v1/places:searchText"
_DETAILS = "https://places.googleapis.com/v1/places/{place_id}"


class Enricher:
    """Text Search to resolve a place_id, then Place Details. Cached on disk."""

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

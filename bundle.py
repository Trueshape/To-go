import re

SRC = r"D:\AI\Projects\To go\maps_itinerary"
OUT = r"D:\AI\Projects\To go\maps_itinerary_standalone.py"

ORDER = ["model.py", "geo.py", "filters.py", "enrich.py", "extract.py", "plan.py", "render.py", "cli.py"]

def strip(text: str, fname: str) -> str:
    # drop the module docstring (first triple-quoted string) - keep as a comment header instead
    doc_m = re.match(r'\s*"""(.*?)"""\s*\n', text, re.S)
    doc = doc_m.group(1).strip() if doc_m else ""
    if doc_m:
        text = text[doc_m.end():]
    # drop local imports: single-line "from .xxx import ..." and multi-line "from .xxx import (\n ... \n)"
    text = re.sub(r'^from \. import[^\n]*\n', '', text, flags=re.M)
    text = re.sub(r'^from \.\w+ import \([^)]*\)\n', '', text, flags=re.M)
    text = re.sub(r'^from \.\w+ import[^\n]*\n', '', text, flags=re.M)
    # drop "from __future__ import annotations" (re-added once at top of bundle)
    text = re.sub(r'^from __future__ import annotations\n', '', text, flags=re.M)
    # cli.py special case: it aliases the whole extract module just to reach one global
    text = text.replace("    from . import extract as _extract\n\n    title = _extract.LAST_LIST_TITLE or _guess_title(source_url)",
                         "    title = LAST_LIST_TITLE or _guess_title(source_url)")
    doc_first_line = doc.splitlines()[0].strip() if doc else ""
    header = f"# {'='*76}\n# {fname}" + (f" \u2014 {doc_first_line}" if doc_first_line else "") + f"\n# {'='*76}\n"
    return header + text.strip("\n") + "\n"

parts = [
    '#!/usr/bin/env python3\n"""\nmaps-list-to-itinerary \u2014 single-file build.\n\n'
    'Turn a public Google Maps list into a day-by-day itinerary (Markdown, JSON,\n'
    'GPX, and a self-contained interactive HTML page).\n\n'
    "This file is the whole program, assembled from the maps_itinerary/ package\n"
    "so it can be copied and run on its own. The package is still the source of\n"
    "truth - regenerate this file after changing it (see bundle.py in the repo).\n\n"
    "Install:\n"
    "    pip install requests\n"
    "    pip install playwright && playwright install chromium   # only to scrape a live URL\n\n"
    "Use:\n"
    "    python maps_itinerary_standalone.py \"https://maps.app.goo.gl/...\"\n"
    "    python maps_itinerary_standalone.py --from-json places.json --no-enrich\n"
    '    python maps_itinerary_standalone.py --help\n"""\n\n'
    "from __future__ import annotations\n\n"
    '__version__ = "0.1.0"\n'
]

for fname in ORDER:
    with open(f"{SRC}\\{fname}", encoding="utf-8") as fh:
        text = fh.read()
    parts.append(strip(text, fname))

parts.append(
    '\n# ' + '='*76 + '\nif __name__ == "__main__":\n    sys.exit(main())\n'
)

out = "\n\n".join(p.rstrip("\n") for p in parts) + "\n"
# cli.py already ends with its own `if __name__` guard - drop the duplicate we appended
# only if cli.py's guard is still present (it is, we only stripped imports/docstring)
if out.count('if __name__ == "__main__":') > 1:
    # remove the LAST occurrence (the one we appended) since cli.py's own is identical
    idx = out.rfind('if __name__ == "__main__":')
    out = out[:idx].rstrip("\n") + "\n"

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(out)

print("wrote", OUT, len(out), "bytes")

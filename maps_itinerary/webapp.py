"""A tiny local web page: paste the Google Maps list link there, not in the terminal.

No framework - just the stdlib http.server. Meant for the "I double-clicked the
program" case: it opens a form in the default browser, runs the scrape + plan
in a background thread, and swaps the page for the finished interactive
itinerary when it's done.
"""

from __future__ import annotations

import http.server
import re
import threading
import urllib.parse
import uuid
import webbrowser

from . import extract as _extract
from .extract import Enricher, extract_from_browser, resolve_list_url
from .model import FilterConfig, apply_filters
from .plan import PlanConfig, build_itinerary
from .render import to_html

_JOBS: dict[str, dict] = {}


def _job_worker(job_id: str, url: str, opts: dict) -> None:
    job = _JOBS[job_id]
    try:
        job["msg"] = "Apro il link..."
        real = resolve_list_url(url)
        job["msg"] = "Leggo la lista (Chromium headless apre e clicca ogni luogo - qualche minuto)..."
        places = extract_from_browser(real, headful=False)
        if not places:
            raise RuntimeError(
                "Nessun luogo estratto. Il link potrebbe non essere una lista pubblica, "
                "o Google ha cambiato pagina - riprova con --headful da riga di comando."
            )

        job["msg"] = f"{len(places)} luoghi trovati. Filtro e pianifico..."
        api_key = None  # keep the web UI dependency-free: no Places/Routes API calls
        enricher = Enricher(api_key=api_key, cache_dir=".cache")
        fr = apply_filters(places, FilterConfig(include_closed=opts["include_closed"]))
        if not fr.kept:
            raise RuntimeError("Nessun luogo specifico rimasto dopo i filtri (tutto era generico o chiuso).")

        for p in fr.generic:
            p.disabled_reason = "generic"
        for p in fr.closed:
            p.disabled_reason = "closed"
        extra = [p for p in (fr.generic + fr.closed) if p.has_coords]

        pcfg = PlanConfig(
            pace=opts["pace"], travel_mode=opts["travel_mode"],
            day_start=opts["day_start"], day_end=opts["day_end"],
        )
        title = _extract.LAST_LIST_TITLE or "Itinerario dalla lista Google Maps"
        it = build_itinerary(
            fr.kept + extra, title=title, source_url=real, cfg=pcfg,
            enriched=enricher.enabled, anchor_places=fr.kept,
        )
        it.skipped_no_coords = [p.name for p in fr.no_coords]
        job.update(status="done", html=to_html(it))
    except BaseException as e:  # noqa: BLE001 - report to the page, don't just die silently
        job.update(status="error", msg=f"{type(e).__name__}: {e}")


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def _send(self, body: str | bytes, status: int = 200) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            self._send(_FORM_PAGE)
        elif path.startswith("/status/"):
            job = _JOBS.get(path.rsplit("/", 1)[-1])
            if not job:
                self._send(_page("Job non trovato.", refresh=False), 404)
            elif job["status"] == "running":
                self._send(_page(job.get("msg", "In corso..."), refresh=True))
            elif job["status"] == "error":
                self._send(_page(f"Errore: {job.get('msg')}", refresh=False, ok=False))
            else:
                self._send(job["html"])
        else:
            self._send(_page("Pagina non trovata.", refresh=False), 404)

    def do_POST(self) -> None:  # noqa: N802
        if urllib.parse.urlsplit(self.path).path != "/generate":
            self._send(_page("Pagina non trovata.", refresh=False), 404)
            return
        length = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        url = (form.get("url") or [""])[0].strip()
        if not url:
            self._send(_FORM_PAGE.replace(
                "<!--ERR-->", '<p class="err">Incolla un link prima di continuare.</p>'
            ))
            return
        opts = {
            "pace": (form.get("pace") or ["normal"])[0],
            "travel_mode": (form.get("travel_mode") or ["transit"])[0],
            "day_start": (form.get("day_start") or ["09:30"])[0],
            "day_end": (form.get("day_end") or ["20:00"])[0],
            "include_closed": "include_closed" in form,
        }
        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {"status": "running", "msg": "Avvio..."}
        threading.Thread(target=_job_worker, args=(job_id, url, opts), daemon=True).start()
        self.send_response(303)
        self.send_header("Location", f"/status/{job_id}")
        self.end_headers()


_FORM_PAGE = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>maps-itinerary</title>
<style>
body{font:16px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  max-width:640px;margin:60px auto;padding:0 20px;background:#f6f5f2;color:#1c1b18}
h1{font-size:22px}
input[type=text]{width:100%;padding:11px;font:inherit;box-sizing:border-box;margin:12px 0;
  border:1px solid #d8d3c8;border-radius:8px}
.row{display:flex;gap:20px;flex-wrap:wrap;align-items:flex-end;margin:10px 0 20px;font-size:14px;color:#555}
.field{display:flex;flex-direction:column;gap:6px}
.field label{white-space:nowrap}
.field select,.field .pair input[type=text]{
  height:38px;padding:0 8px;font:inherit;margin:0;width:auto;
  border:1px solid #d8d3c8;border-radius:6px;box-sizing:border-box}
.field .pair{display:flex;gap:6px}
.field .pair input[type=text]{width:66px;text-align:center}
.field.checkbox{flex-direction:row;align-items:center;height:38px}
.field.checkbox label{display:flex;align-items:center;gap:6px;color:#1c1b18;white-space:nowrap}
button{padding:11px 22px;font:inherit;background:#b4472e;color:#fff;border:0;border-radius:8px;cursor:pointer}
.hint{color:#888;font-size:13px}
.err{color:#9a2f19;font-weight:600}
</style></head><body>
<h1>Da una lista pubblica di Google Maps a un itinerario</h1>
<!--ERR-->
<form method="POST" action="/generate">
  <input type="text" name="url" placeholder="Incolla qui il link della lista Google Maps..." required autofocus>
  <div class="row">
    <div class="field">
      <label for="day_start">Inizio/Fine giornata</label>
      <div class="pair">
        <input type="text" id="day_start" name="day_start" value="09:30">
        <input type="text" name="day_end" value="20:00">
      </div>
    </div>
    <div class="field">
      <label for="pace">Ritmo</label>
      <select id="pace" name="pace"><option value="normal" selected>normale</option>
        <option value="relaxed">tranquillo</option><option value="packed">intenso</option></select>
    </div>
    <div class="field">
      <label for="travel_mode">Come ti muovi</label>
      <select id="travel_mode" name="travel_mode"><option value="transit" selected>mezzi pubblici</option>
        <option value="walking">a piedi</option><option value="driving">auto</option></select>
    </div>
    <div class="field checkbox">
      <label><input type="checkbox" name="include_closed"> includi i luoghi chiusi</label>
    </div>
  </div>
  <button type="submit">Genera itinerario</button>
  <p class="hint">La prima volta ci vogliono 3-4 minuti: apre un browser invisibile e clicca ogni luogo della lista per leggerne foto e coordinate.</p>
</form>
</body></html>"""


def _page(message: str, *, refresh: bool, ok: bool = True) -> str:
    meta = '<meta http-equiv="refresh" content="2">' if refresh else ""
    color = "#1c1b18" if ok else "#9a2f19"
    spinner = (
        '<div class="spin"></div><style>.spin{width:26px;height:26px;border:3px solid #e7e3db;'
        "border-top-color:#b4472e;border-radius:50%;margin:18px 0;"
        "animation:sp 0.8s linear infinite}@keyframes sp{to{transform:rotate(360deg)}}</style>"
        if refresh else ""
    )
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">{meta}
<title>maps-itinerary</title>
<style>body{{font:16px -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  max-width:560px;margin:100px auto;padding:0 20px;color:{color};background:#f6f5f2}}
a{{color:#b4472e}}</style></head><body>
<p>{_esc(message)}</p>{spinner}
<p><a href="/">&larr; torna al modulo</a></p>
</body></html>"""


def _esc(s: str) -> str:
    return re.sub(r"[&<>]", lambda m: {"&": "&amp;", "<": "&lt;", ">": "&gt;"}[m.group()], s)


def run_server(port: int = 8765) -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Apro {url} nel browser - lascia questa finestra aperta (Ctrl+C per chiudere).")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nChiuso.")

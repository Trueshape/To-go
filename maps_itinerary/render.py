"""Write the itinerary out as Markdown, JSON and GPX."""

from __future__ import annotations

import html
import json
import pathlib
import re

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

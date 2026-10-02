"""Аутлаєри тижня: довгі відео, які набрали в рази більше за норму свого каналу.

Аутлаєр = тема або формат, які глядач хоче більше, ніж звичайно. Найсильніший сигнал — маленький канал
(до 100K підписників), чиє відео набрало ≥3× від кількості підписників: тема спрацює і на новому каналі.

- Пошук YouTube через внутрішній API (youtubei/v1/search) — без ключа; дає «N days ago» і id каналу.
- Норма каналу через yt-dlp: медіана ~30 останніх відео без 5 найсвіжіших (вони ще не набрали перегляди).
- Новинні й ТВ-канали з потоком відео (медіана < 0.2% від підписників при 1M+) — норма не визначена.
"""
import html
import logging
import re
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote

import requests

log = logging.getLogger(__name__)

WEEK_BY_VIEWS = "CAISBAgDEAE%3D"
WEEK_BY_RELEVANCE = "EgQIAxAB"
MONTH_BY_VIEWS = "CAMSBAgEEAE%3D"
QUERIES = ["investigation", "exposed", "what happened", "the truth about", "downfall", "scandal",
           "is worse than you thought", "life is falling apart", "explained", "scam", "fraud", "lawsuit", "cover up",
           "whistleblower", "leaked", "what really happened", "collapse", "the dark side of", "documentary",
           "the rise and fall", "dark truth", "i investigated", "the real story", "conspiracy", "trial", "verdict",
           "bodycam", "cult", "disaster", "plane crash", "hack", "billionaire", "influencer", "youtuber", "drama",
           "controversy", "situation"]
JUNK = re.compile(r"episode|\bep\.? ?\d|full movie|free movie|eng sub|\bsub\)|disguised|homeless|romance|love story|"
                  r"drama review|minecraft|roblox|fortnite|marries|reacts? to|reaction|livestream|highlights|"
                  r"billionaire .*(wife|heir|ceo)|full review|【|#", re.I)
NEWS_RE = re.compile(r"\bnews\b|news\d+|times now|gb ?news|republic (world|tv|bharat)|\babp\b|tv9|dd news|\bndtv|mathrubhumi|asianet|manorama|\bnews ?nation|\bwion|i24|kan news|jerusalem post|times of israel|ynet|\bchannel ?\d+\b|\bnbc\b|\bcbs\b|\babc\d*\b|\bfox \d+|^fox news|\bcnn\b|\bbbc\b|\bsky (news|sports)|\bdw (news|documentary)|al jazeera|\bwion\b|court ?tv|court trials tv|law ?& ?crime|reuters|associated press|"
 r"^the guardian|the independent|telegraph|hindustan times|economic times|new york post|\bforbes\b|bloomberg|cnbc|msnbc|^ms now$|newsmax|gb news|talktv|talksport|itv (news|sport)|"
 r"\bpbs\b|frontline|60 minutes|^today$|usa today|india today|business today|inside edition|tmz|e! news|entertainment tonight|access hollywood|^the (sun|mirror)$|mirror now|"
 r"daily mail|yahoo|abs-cbn|^gma |\bnhk\b|\bndtv\b|aaj tak|\bzee\b|wsj|wall street journal|new york times|washington post|vice news|business insider|euronews|france 24|cgtn|"
 r"\btrt\b|11alive|dayton 24/7|east idaho news|senate of|c-span|parliament|scripps|straight arrow|the athletic|\bespn\b|\bdazn\b|livenow|cbs mornings|king 5|"
 r"click on detroit|hum tv|har pal geo|ary digital|green tv entertainment|jamuna tv|sakshi tv|untv|taiwanplus|\bcbc\b|9 news|12 news|kare 11|abc7|kstp|koco|kgw",re.I)
CALL_RE = re.compile(r"\b[KW][A-Z]{2,3}(-TV)?\b")   # американські місцеві станції (KARE, WLWT, WXMI)
NOT_NEWS_RE = re.compile(r"kay rated|kaye|wild nature|some more news|my views on news|true crime news|down the rabbit hole|mo news|crime wire",re.I)
def is_news(name):
    name=name or ""
    if NOT_NEWS_RE.search(name): return False
    return bool(NEWS_RE.search(name) or CALL_RE.search(name))


CTX = {"client": {"clientName": "WEB", "clientVersion": "2.20250925.01.00", "hl": "en", "gl": "US"}}


def _num(s: str) -> int:
    m = re.search(r"([\d.]+)\s*([KMB]?)", (s or "").replace(",", ""))
    return int(float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]) if m else 0


def _dur(s: str) -> int:
    t = 0
    for x in (s or "0").split(":"):
        t = t * 60 + int(x)
    return t


def _walk(o):
    if isinstance(o, dict):
        if "videoRenderer" in o:
            yield o["videoRenderer"]
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def published(ago: str, today: date | None = None) -> str:
    """'3 days ago' / 'Streamed 5 hours ago' -> ISO-дата (±1 день)."""
    today = today or date.today()
    m = re.search(r"(\d+)\s+(second|minute|hour|day|week|month|year)", ago or "")
    if not m:
        return ""
    n, unit = int(m.group(1)), m.group(2)
    days = {"second": 0, "minute": 0, "hour": 0, "day": n, "week": 7 * n, "month": 30 * n, "year": 365 * n}[unit]
    return (today - timedelta(days=days)).isoformat()


def search(query: str, sp: str = WEEK_BY_VIEWS, session: requests.Session | None = None) -> list[dict]:
    s = session or requests
    j = s.post("https://www.youtube.com/youtubei/v1/search?prettyPrint=false",
               json={"context": CTX, "query": query, "params": unquote(sp)}, timeout=25).json()
    out = []
    for v in _walk(j):
        runs = v.get("ownerText", {}).get("runs", [])
        vc = v.get("viewCountText", {})
        out.append({
            "id": v["videoId"],
            "title": "".join(r.get("text", "") for r in v.get("title", {}).get("runs", [])),
            "channel": "".join(r.get("text", "") for r in runs),
            "cid": next((r["navigationEndpoint"].get("browseEndpoint", {}).get("browseId")
                         for r in runs if r.get("navigationEndpoint")), None),
            "views": _num(vc.get("simpleText") or "".join(r.get("text", "") for r in vc.get("runs", []))),
            "dur": _dur(v.get("lengthText", {}).get("simpleText")),
            "ago": v.get("publishedTimeText", {}).get("simpleText", ""),
            "url": f"https://www.youtube.com/watch?v={v['videoId']}",
        })
    for v in out:
        v["published"] = published(v["ago"])
    return out


def channel_stats(cid: str, n: int = 35) -> dict:
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True, "playlistend": n, "no_warnings": True}) as y:
        d = y.extract_info(f"https://www.youtube.com/channel/{cid}/videos", download=False)
    vs = [e for e in d.get("entries") or [] if e.get("view_count")]
    return {"name": d.get("channel"), "subs": d.get("channel_follower_count") or 0,
            "recent": [(e["id"], e["view_count"]) for e in vs]}


def outlier_score(v: dict, ch: dict, skip_newest: int = 5) -> dict | None:
    if not ch or ch.get("err"):
        return None
    others = [x for i, x in ch["recent"][skip_newest:] if i != v["id"]]
    med = statistics.median(others) if len(others) >= 8 else 0
    if ch["subs"] >= 1_000_000 and med < ch["subs"] * 0.002:   # новинний/ТВ-потік — норма не визначена
        med = 0
    return {"x": round(v["views"] / med, 1) if med else None, "median": int(med), "subs": ch["subs"],
            "vs_subs": round(v["views"] / ch["subs"], 2) if ch["subs"] else None}


def is_relevant(v: dict) -> bool:
    t = v["title"]
    return not JUNK.search(t) and sum(ord(c) < 128 for c in t) / max(1, len(t)) > 0.85


def find_outliers(queries: list[str] = QUERIES, min_minutes: int = 8, min_x: float = 3.0, min_views: int = 50_000,
                  workers: int = 6) -> dict:
    session = requests.Session()
    jobs = [(q, sp) for q in queries for sp in (WEEK_BY_VIEWS, WEEK_BY_RELEVANCE)]

    def run(job):
        try:
            return search(job[0], job[1], session)
        except Exception as exc:
            log.warning("search %s: %s", job[0], exc)
            return []

    with ThreadPoolExecutor(workers) as ex:
        found = {v["id"]: v for r in ex.map(run, jobs) for v in r
                 if v["dur"] >= min_minutes * 60 and re.search(r"hour|day", v["ago"]) and is_relevant(v)}
    cids = sorted({v["cid"] for v in found.values() if v["cid"]})
    log.info("%d відео, %d каналів — рахую норму каналів", len(found), len(cids))

    def stats(cid):
        try:
            return cid, channel_stats(cid)
        except Exception as exc:
            return cid, {"err": str(exc)[:100]}

    with ThreadPoolExecutor(8) as ex:
        channels = dict(ex.map(stats, cids))
    for v in found.values():
        v["o"] = outlier_score(v, channels.get(v["cid"]))
        v["news"] = is_news(v["channel"])
    vids = [v for v in found.values() if v["o"]]
    x = lambda v: v["o"]["x"] or 0
    top = sorted([v for v in vids if not v["news"] and x(v) >= min_x and v["views"] >= min_views], key=lambda v: -x(v))
    news = sorted([v for v in vids if v["news"] and x(v) >= min_x and v["views"] >= min_views], key=lambda v: -x(v))
    small = sorted([v for v in vids if not v["news"] and 0 < v["o"]["subs"] < 100_000 and v["views"] >= 3 * v["o"]["subs"]
                    and v["views"] >= 30_000], key=lambda v: -v["views"] / v["o"]["subs"])
    return {"total": len(found), "outliers": top, "news": news, "small": small}


def _fmt(n: int) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else (f"{n / 1e3:.0f}K" if n >= 1e3 else str(n))


def _card(v: dict) -> str:
    e = html.escape
    o = v["o"]
    xb = f'<b class="x">×{o["x"]:g} від норми</b> · ' if o["x"] else ""
    return (f'<a class="vid" href="{v["url"]}" target="_blank" rel="noopener"><div class="thumb">'
            f'<img loading="lazy" src="https://i.ytimg.com/vi/{v["id"]}/mqdefault.jpg" alt="">'
            f'<span class="dur">{v["dur"] // 60} хв</span></div><div class="vt">{e(v["title"])}</div>'
            f'<div class="vm"><b>{_fmt(v["views"])}</b> · {e(v["channel"])}</div>'
            f'<div class="vm">📅 {v["published"][8:10]}.{v["published"][5:7]} · {e(v["ago"])}</div>'
            f'<div class="vm">{xb}{_fmt(o["subs"])} підп.</div></a>')


def write_outliers_report(res: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"outliers_{date.today().isoformat()}.html"
    page = f"""<!doctype html><html lang="uk"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Аутлаєри тижня</title><style>
:root{{--bg:#f6f5f2;--card:#fff;--ink:#1b1b1f;--mute:#6a6a73;--line:#e3e1dc;--hot:#c2410c;--link:#1d4ed8}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#121214;--card:#1c1c20;--ink:#ececf0;--mute:#9a9aa5;--line:#2c2c33;--hot:#fb923c;--link:#93b4ff}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}}.wrap{{max-width:1180px;margin:0 auto;padding:20px 16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}}.vid{{display:block;text-decoration:none;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
.thumb{{position:relative;aspect-ratio:16/9;background:#000}}.thumb img{{width:100%;height:100%;object-fit:cover}}.dur{{position:absolute;right:6px;bottom:6px;background:rgba(0,0,0,.8);color:#fff;font-size:11px;padding:1px 5px;border-radius:4px}}
.vt{{font-size:13px;font-weight:600;padding:8px 8px 2px}}.vm{{font-size:12px;color:var(--mute);padding:0 8px 4px}}.x{{color:var(--hot)}}.mute{{color:var(--mute)}}
</style></head><body><div class="wrap"><h1>Аутлаєри тижня — {date.today():%d.%m.%Y}</h1>
<p class="mute">{res["total"]} довгих відео за 7 днів. ×N = перегляди ÷ медіана ~30 останніх відео каналу (без 5 найсвіжіших).</p>
<h2>Найбільші аутлаєри — автори</h2><div class="grid">{"".join(_card(v) for v in res["outliers"][:48])}</div>
<h2>Малі канали (до 100K), де відео набрало ≥3× від підписників</h2><div class="grid">{"".join(_card(v) for v in res["small"][:24])}</div>
<h2>Новинні й ТВ-канали — окремо (сигнал теми, не формату)</h2><div class="grid">{"".join(_card(v) for v in res.get("news", [])[:24])}</div>
</div></body></html>"""
    path.write_text(page, encoding="utf-8")
    return path

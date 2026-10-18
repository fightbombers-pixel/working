"""Мисливець за аутлаєрами для Lume (людина проти гіганта / афери / пограбування / лазівки).

Логіка — з LUME_CONTEXT_PACK (02_OUTLIER_LOGIC.md):
- пул пошукових запитів: галузі × механіки + формули людини і розслідувань; пошук «цей місяць / цей тиждень»
  за переглядами;
- кожен канал із результатів: останні 40 відео (≥60 с) → медіана → хіти серед останніх 12;
- ХІТ: ≥10 хв, ≥250k переглядів, ≥5× медіани; канал ≥10 відео і медіана ≥8 000 (нижче — AI-фабрики);
- ПРОРИВ: підписників ≤250k, перегляди ≥10× підписників (або ≥5× при 1M+), ≥8 хв, у назві історія/механіка;
- бал = min(×, 60) · √перегляди, ×4 якщо назва в ніші Lume;
- дедуп: проти вже знайдених аутлаєрів (03_ALL_OUTLIERS.csv) і виданих кейсів Lume (06_LUME_CASE_INDEX.md);
- казино позначається «вичерпано» (на каналі вже 19 відео про казино).

Запуск: python -m niche_finder lume [--pack ШЛЯХ_ДО_LUME_CONTEXT_PACK] [--queries N]
"""
import csv
import html
import logging
import math
import random
import re
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from .outliers import JUNK, MONTH_BY_VIEWS, WEEK_BY_VIEWS, is_news, published, search

log = logging.getLogger(__name__)

INDUSTRIES = ["airline", "bank", "casino", "insurance", "health insurance", "lottery", "credit card", "crypto", "real estate",
              "car dealership", "fast food", "supermarket", "pharmaceutical", "hospital", "university", "school district",
              "phone company", "tech company", "amazon", "apple", "google", "walmart", "uber", "airbnb", "ticketmaster",
              "IRS", "FBI", "police", "government", "military", "museum", "art", "diamond", "gold", "stock market",
              "hedge fund", "sports betting", "video game", "toy company", "soda company", "cereal", "theme park",
              "cruise line", "hotel", "car rental", "parking", "toll road", "utility company", "patent", "lawyer"]
MECHANICS = ["loophole", "scam", "heist", "lawsuit", "exploit", "legally cheated", "outsmarted", "fraud", "stole millions",
             "beat the system"]
PERSON = ["the man who", "the woman who", "the kid who", "and got away with it", "legally", "outsmarted",
          "beat the system", "stole $", "million dollar mistake", "the untold story of", "inside the",
          "the greatest heist", "con artist", "the biggest scam", "cheated", "fooled", "conned", "smuggled",
          "counterfeit", "forger", "spy who", "insider trading", "ponzi scheme", "I investigated", "exposing"]
NICHE = re.compile(r"scam|fraud|heist|stole|steal|\bcon\b|conned|con artist|loophole|legally|lawsuit|sued|court|casino|"
                   r"bank|airline|corporat|company|billion|million|\$\d|cheat|hack|outsmart|trick|exploit|scheme|ponzi|"
                   r"fake|forg|counterfeit|smuggl|\bspy\b|fbi|irs|\btax|insurance|lottery|patent|monopoly|beat the|"
                   r"got away|robbery|robbed|thief|thieves|swindl|embezzl|bribe|cartel|mafia", re.I)
STORY = re.compile(r"\bwho\b|how |why |the man|the woman|the kid|story|inside|heist|stole|scam|cheat|legally|"
                   r"loophole|outsmart|got away|\$\d|million|billion", re.I)
FACTORY = re.compile(r"billionaire.*(wife|ceo|heir|janitor|waitress|maid|single dad|son|daughter)|nobody expected|"
                     r"janitor|disguised|homeless|married|reborn|revenge|mother-in-law|full movie|movie recap|"
                     r"explained in hindi|drama|【", re.I)
CASINO = re.compile(r"casino|blackjack|slot machine|roulette|poker|las vegas|atlantic city", re.I)


def query_pool(n: int, seed: int | None = None) -> list[str]:
    rnd = random.Random(seed if seed is not None else date.today().toordinal())
    pairs = [f"{i} {m}" for i in INDUSTRIES for m in MECHANICS]
    rnd.shuffle(pairs)
    return PERSON + pairs[: max(0, n - len(PERSON))]


def channel_videos(cid: str, n: int = 40) -> dict:
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True, "playlistend": n, "no_warnings": True}) as y:
        d = y.extract_info(f"https://www.youtube.com/channel/{cid}/videos", download=False)
    vs = [{"id": e["id"], "title": e.get("title") or "", "views": e.get("view_count") or 0,
           "dur": int(e.get("duration") or 0), "pos": i} for i, e in enumerate(d.get("entries") or [])]
    return {"name": d.get("channel"), "subs": d.get("channel_follower_count") or 0,
            "videos": [v for v in vs if v["dur"] >= 60 and v["views"]]}


def load_pack(pack: Path | None) -> tuple[set[str], list[str]]:
    """Відомі id відео (03_ALL_OUTLIERS.csv) і прізвища героїв виданих кейсів (06_LUME_CASE_INDEX.md)."""
    known, names = set(), []
    if not pack:
        return known, names
    f3 = pack / "03_ALL_OUTLIERS.csv"
    if f3.exists():
        for r in csv.DictReader(f3.open(encoding="utf-8-sig")):
            m = re.search(r"(?:youtu\.be/|v=)([\w-]{11})", r.get("url", ""))
            if m:
                known.add(m.group(1))
    f6 = pack / "06_LUME_CASE_INDEX.md"
    if f6.exists():
        for line in f6.read_text(encoding="utf-8").splitlines():
            cells = [c.strip() for c in line.split("|")]
            if len(cells) > 4 and cells[1].isdigit():
                m = re.search(r"`([\w-]{11})`", line)
                if m:
                    known.add(m.group(1))
                prot = cells[3]
                for part in re.split(r"[,&/]| and ", prot):
                    last = part.strip().split(" ")[-1] if part.strip() else ""
                    if len(last) >= 4 and last.lower() not in ("unidentified", "group", "brothers", "family"):
                        names.append(last.lower())
    return known, names


def hunt(pack: Path | None = None, n_queries: int = 150, workers: int = 6) -> dict:
    known, names = load_pack(pack)
    queries = query_pool(n_queries)
    jobs = [(q, sp) for q in queries for sp in (MONTH_BY_VIEWS, WEEK_BY_VIEWS)]

    def run(job):
        try:
            return search(job[0], job[1])
        except Exception as exc:
            log.warning("search %s: %s", job[0], exc)
            return []

    with ThreadPoolExecutor(workers) as ex:
        found = {}
        for r in ex.map(run, jobs):
            for v in r:
                if v["dur"] >= 480 and v.get("cid") and not is_news(v["channel"]) and not FACTORY.search(v["title"]) \
                        and not JUNK.search(v["title"]) and STORY.search(v["title"]):
                    found[v["id"]] = v
    cids = sorted({v["cid"] for v in found.values()})
    log.info("%d кандидатів, %d каналів — сканую канали", len(found), len(cids))

    def scan(cid):
        try:
            return cid, channel_videos(cid)
        except Exception as exc:
            return cid, {"err": str(exc)[:80]}

    with ThreadPoolExecutor(10) as ex:
        chans = dict(ex.map(scan, cids))

    rows = []
    for v in found.values():
        c = chans.get(v["cid"]) or {}
        if c.get("err") or not c.get("videos"):
            continue
        others = [x["views"] for x in c["videos"] if x["id"] != v["id"]]
        med = statistics.median(others) if len(others) >= 5 else 0
        x = v["views"] / med if med else 0
        subs = c.get("subs") or 0
        hit = v["dur"] >= 600 and v["views"] >= 250_000 and x >= 5 and len(c["videos"]) >= 10 and med >= 8_000
        brk = 0 < subs <= 250_000 and v["views"] >= 10 * subs and v["dur"] >= 480
        brk = brk or (subs >= 1_000_000 and v["views"] >= 5 * subs)
        if not (hit or brk):
            continue
        niche = bool(NICHE.search(v["title"]))
        score = min(x if x else v["views"] / max(subs, 1), 60) * math.sqrt(v["views"]) * (4 if niche else 1)
        tl = v["title"].lower()
        dup = v["id"] in known or any(n in tl for n in names)
        rows.append({**v, "median": int(med), "x": round(x, 1), "subs": subs, "x_subs": round(v["views"] / subs, 1) if subs else None,
                     "hit": hit, "breakout": brk, "niche": niche, "casino": bool(CASINO.search(v["title"])),
                     "dup": dup, "score": round(score)})
    rows.sort(key=lambda r: -r["score"])
    return {"queries": len(queries), "candidates": len(found), "channels": len(cids), "rows": rows}


def _fmt(n: int) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else (f"{n / 1e3:.0f}K" if n >= 1e3 else str(n))


def write_report(res: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"lume_outliers_{date.today().isoformat()}.html"
    e = html.escape

    def card(r):
        badges = []
        if r["hit"]:
            badges.append(f'<b class="b hot">×{r["x"]:g} від медіани</b>')
        if r["breakout"]:
            badges.append(f'<b class="b brk">ПРОРИВ ×{r["x_subs"]:g} від підписників</b>')
        if r["niche"]:
            badges.append('<b class="b ok">ніша Lume</b>')
        if r["casino"]:
            badges.append('<b class="b warn">казино — вичерпано</b>')
        if r["dup"]:
            badges.append('<b class="b warn">вже в базі</b>')
        return (f'<a class="vid" href="{r["url"]}" target="_blank" rel="noopener"><div class="thumb"><img loading="lazy" '
                f'src="https://i.ytimg.com/vi/{r["id"]}/mqdefault.jpg" alt=""><span class="dur">{r["dur"] // 60} хв</span></div>'
                f'<div class="vt">{e(r["title"])}</div><div class="vm"><b>{_fmt(r["views"])}</b> · {e(r["channel"])} · '
                f'{_fmt(r["subs"])} підп.</div><div class="vm">📅 {e(r.get("published", ""))} · {e(r["ago"])}</div>'
                f'<div class="vm">{" ".join(badges)}</div></a>')

    fresh = [r for r in res["rows"] if not r["dup"]]
    page = f"""<!doctype html><html lang="uk"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Аутлаєри для Lume</title><style>
:root{{--bg:#f6f5f2;--card:#fff;--ink:#1b1b1f;--mute:#6a6a73;--line:#e3e1dc;--hot:#c2410c;--ok:#15803d;--warn:#a16207;--brk:#7c3aed}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121214;--card:#1c1c20;--ink:#ececf0;--mute:#9a9aa5;--line:#2c2c33;--hot:#fb923c;--ok:#4ade80;--warn:#facc15;--brk:#c4b5fd}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}}.wrap{{max-width:1180px;margin:0 auto;padding:20px 16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}}.vid{{display:block;text-decoration:none;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
.thumb{{position:relative;aspect-ratio:16/9;background:#000}}.thumb img{{width:100%;height:100%;object-fit:cover}}.dur{{position:absolute;right:6px;bottom:6px;background:rgba(0,0,0,.8);color:#fff;font-size:11px;padding:1px 5px;border-radius:4px}}
.vt{{font-size:13px;font-weight:600;padding:8px 8px 2px}}.vm{{font-size:12px;color:var(--mute);padding:0 8px 6px}}.b{{font-size:11px;margin-right:4px}}.hot{{color:var(--hot)}}.ok{{color:var(--ok)}}.warn{{color:var(--warn)}}.brk{{color:var(--brk)}}
</style></head><body><div class="wrap"><h1>Аутлаєри для Lume — {date.today():%d.%m.%Y}</h1>
<p>{res["queries"]} запитів · {res["candidates"]} кандидатів · {res["channels"]} каналів просканувано · хітів і проривів: {len(res["rows"])}, нових (не в базі): {len(fresh)}.</p>
<h2>Нові</h2><div class="grid">{"".join(card(r) for r in fresh[:80])}</div>
<h2>Уже в базі Lume</h2><div class="grid">{"".join(card(r) for r in res["rows"] if r["dup"])}</div></div></body></html>"""
    path.write_text(page, encoding="utf-8")
    return path

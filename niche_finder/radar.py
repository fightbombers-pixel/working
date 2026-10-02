"""Радар хайпу: теми на зльоті до піку + прозорі метрики, щоб перевірити кожну гіпотезу.

Сигнали:
1. Попит — Вікіпедія: денні перегляди статті (top-1000 за кілька днів + повний 30-денний ряд для кандидатів).
   Прискорення = вчора / медіана попередніх 14 днів. Ранній сигнал — прискорення вже велике (≥3×),
   а абсолютні перегляди ще помірні: тема росте, але ще не на піку.
2. Пошук — Google Trends RSS (US/GB): що набирають прямо зараз.
3. Пропозиція — YouTube за тиждень: скільки довгих (≥10 хв) відео вже є і скільки вони набирають.
   Багато попиту + мало довгих відео = прогалина.
4. Придатність для розслідування — опис статті у Вікіпедії (кримінал, суд, гроші, компанія, катастрофа…).

Кожна тема в звіті має посилання, щоб перевірити цифри руками: графік Вікіпедії (pageviews.wmcloud.org),
Google Trends, пошук YouTube за тиждень.
"""
import json
import logging
import math
import re
import statistics
import time
from datetime import date, timedelta
from urllib.parse import quote, quote_plus

from .net import HttpClient

log = logging.getLogger(__name__)

WIKI = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
UA = "niche-finder-radar/0.1 (https://github.com/fightbombers-pixel/working; research tool)"

SKIP_PREFIX = ("Main_Page", "Special:", "Wikipedia:", "Portal:", "File:", "Help:", "Talk:", "Category:", "Template:",
               "Deaths_in", "List_of", "Pornhub", "XXX", "Xvideos", ".xxx", ".xyz")
# що підходить для розслідування (люди + гроші + влада/система/катастрофа)
INVESTIGATE = re.compile(
    r"murder|killer|kill|convict|execut|death row|fraud|scam|ponzi|embezzl|brib|corrupt|launder|cartel|mafia|gang|"
    r"terror|hijack|attack|shooting|bomb|kidnap|abduct|missing|disappear|trial|lawsuit|court|indict|arrest|charged|"
    r"scandal|leak|whistleblow|hacker|spy|espionage|crash|disaster|accident|collapse|explosion|businessman|"
    r"billionaire|tycoon|company|corporation|bank|insurer|cult|abuse|prison|investigat|allegation|criminal|heist|robbery",
    re.I)
NOT_INVESTIGATE = re.compile(r"footballer|cricketer|tennis player|basketball|baseball|wrestler|singer|rapper|"
                             r"band|album|song|video game|season of|television season|sports|olympic|games|medal|"
                             r"actress|actor\b|model\b|tv series|miniseries|film\b", re.I)
WAVE = re.compile(r"film|documentary|miniseries|series|docuseries", re.I)  # хвиля від релізу


def wiki_top(http: HttpClient, day: date, project: str = "en.wikipedia") -> dict[str, int]:
    url = f"{WIKI}/top/{project}/all-access/{day:%Y/%m/%d}"
    for attempt in range(3):
        try:
            resp = http.get(url, headers={"User-Agent": UA})
        except Exception as exc:
            log.warning("wiki top %s: %s", day, exc)
            return {}
        if resp.status_code == 200:
            arts = resp.json()["items"][0]["articles"]
            return {a["article"]: a["views"] for a in arts if not a["article"].startswith(SKIP_PREFIX)}
        if resp.status_code == 404:  # день ще не опублікований
            return {}
        time.sleep(3 * (attempt + 1))
    return {}


def wiki_series(http: HttpClient, article: str, end: date, days: int = 30,
                project: str = "en.wikipedia") -> list[int]:
    start = end - timedelta(days=days - 1)
    url = (f"{WIKI}/per-article/{project}/all-access/user/{quote(article, safe='')}/daily/"
           f"{start:%Y%m%d}00/{end:%Y%m%d}00")
    try:
        resp = http.get(url, headers={"User-Agent": UA})
    except Exception as exc:  # 429 після всіх повторів — пропускаємо статтю, а не весь прогін
        log.warning("wiki series %s: %s", article, exc)
        return []
    if resp.status_code != 200:
        return []
    return [i["views"] for i in resp.json().get("items", [])]


def wiki_summary(http: HttpClient, article: str, project: str = "en.wikipedia") -> dict:
    lang = project.split(".")[0]
    try:
        resp = http.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(article, safe='')}",
                        headers={"User-Agent": UA})
    except Exception as exc:
        log.warning("wiki summary %s: %s", article, exc)
        return {}
    if resp.status_code != 200:
        return {}
    d = resp.json()
    return {"description": d.get("description", ""), "extract": d.get("extract", "")[:500],
            "thumbnail": (d.get("thumbnail") or {}).get("source", "")}


def momentum(series: list[int]) -> dict:
    """Прискорення останнього дня відносно медіани попередніх 14 днів + чи це вже пік."""
    if len(series) < 3:
        return {"last": series[-1] if series else 0, "baseline": 0, "accel": 0.0, "phase": "мало даних"}
    last = series[-1]
    prev = series[-15:-1] if len(series) > 15 else series[:-1]
    base = statistics.median(prev) or 1
    accel = round(last / base, 1)
    peak = max(series)
    if accel >= 3 and last >= 0.8 * peak:
        phase = "росте зараз"
    elif accel >= 3:
        phase = "після піку"   # хвиля була, вже спадає
    elif accel >= 1.5:
        phase = "розгін"
    else:
        phase = "фон"
    return {"last": last, "baseline": int(base), "accel": accel, "phase": phase, "peak": peak}


def classify(summary: dict) -> str:
    text = f"{summary.get('description', '')} {summary.get('extract', '')}"
    if WAVE.search(summary.get("description", "")) and INVESTIGATE.search(text):
        return "хвиля від релізу"
    if NOT_INVESTIGATE.search(summary.get("description", "")) and not INVESTIGATE.search(summary.get("description", "")):
        return "не наше"
    return "розслідування" if INVESTIGATE.search(text) else "інше"


def youtube_supply(query: str, limit: int = 20) -> dict:
    """Пропозиція на YouTube за тиждень: довгі відео (≥10 хв), їхні перегляди, топ-відео."""
    from .discover import _ydl_search_url
    vids = _ydl_search_url(f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp=CAISBAgDEAE%3D", limit)
    long_v = [v for v in vids if v["duration_s"] >= 600]
    return {
        "videos": len(vids),
        "long": len(long_v),
        "long_top": max((v["views"] for v in long_v), default=0),
        "all_top": max((v["views"] for v in vids), default=0),
        "top": sorted(vids, key=lambda v: -v["views"])[:4],
    }


def score(m: dict, supply: dict, kind: str, platforms: int = 0) -> dict:
    """0–10, розкладено на складові, щоб було видно, звідки бал."""
    demand = min(3.0, math.log10(1 + m["last"]) - 2)            # 1k → 1, 10k → 2, 100k+ → 3
    accel = min(3.0, math.log2(max(1.0, m["accel"])))           # 2× → 1, 4× → 2, 8×+ → 3
    gap = 2.0 if supply.get("long", 0) == 0 else max(0.0, 2.0 - supply["long"] / 3)
    fit = {"розслідування": 2.0, "хвиля від релізу": 1.5, "інше": 0.5, "не наше": 0.0}[kind]
    early = 1.0 if m["phase"] in ("росте зараз", "розгін") and m["last"] < 300_000 else 0.0
    cross = min(2.0, float(platforms))                          # тема одночасно на 1–2+ інших платформах
    parts = {"попит": round(max(0.0, demand), 1), "прискорення": round(accel, 1), "прогалина YouTube": round(gap, 1),
             "придатність": fit, "ранній сигнал": early, "інші платформи": cross}
    return {"total": round(min(10.0, sum(parts.values()) * 10 / 12), 1), "parts": parts}


def links(article: str, title: str) -> dict:
    end = date.today()
    start = end - timedelta(days=60)
    return {
        "wiki": f"https://en.wikipedia.org/wiki/{quote(article)}",
        "pageviews": f"https://pageviews.wmcloud.org/?project=en.wikipedia.org&platform=all-access&agent=user"
                     f"&range=latest-60&pages={quote(article)}",
        "trends": f"https://trends.google.com/trends/explore?date=now%207-d&q={quote_plus(title)}",
        "youtube_week": f"https://www.youtube.com/results?search_query={quote_plus(title)}&sp=CAISBAgDEAE%3D",
        "news": f"https://news.google.com/search?q={quote_plus(title)}",
    }


def deep_check(http: HttpClient, t: dict, end: date, signals: list[dict], check_youtube: bool = True) -> dict:
    """Повна перевірка теми: 30-денний ряд Вікіпедії, YouTube, інші платформи, автодоповнення, бал."""
    from .signals import autocomplete, platforms_for

    if t.get("article"):
        full = wiki_series(http, t["article"], end)
        if len(full) >= 7:
            t["series"], t["momentum"] = full, momentum(full)
    t.setdefault("series", [])
    t.setdefault("momentum", momentum(t["series"]))
    t.setdefault("summary", {})
    t.setdefault("kind", "розслідування")
    q = t.get("query") or re.sub(r"\s*\(.*?\)", "", t["title"])
    sup = {}
    if check_youtube:
        try:
            sup = youtube_supply(q)
        except Exception as exc:
            log.error("youtube %s: %s", q, exc)
    plat = platforms_for(q, signals)
    t.update({"supply": sup, "platforms": plat, "autocomplete": autocomplete(http, q.lower()),
              "score": score(t["momentum"], sup, t["kind"], len(plat)),
              "links": links(t.get("article") or q.replace(" ", "_"), q)})
    return t


# Теми, які вже пропонувались (01–02.10.2026): перевіряємо, чи гіпотеза тримається
TRACKED = [
    {"title": "Christa Pike", "article": "Christa_Pike", "hypothesis": "№1 для Revela: страта провалилась, розслідувань немає"},
    {"title": "Rui Pinto", "article": "Rui_Pinto", "query": "Rui Pinto", "hypothesis": "Хакер проти Man City; зняли захист свідка"},
    {"title": "Flydubai Flight 1073", "article": "Flydubai_Flight_1073", "query": "flydubai", "hypothesis": "Пілот напав на пілота; сценарій готовий"},
    {"title": "UnitedHealthcare", "article": "UnitedHealth_Group", "query": "UnitedHealthcare", "hypothesis": "Хайп від John Oliver, YouTube порожній"},
    {"title": "Musk (film)", "article": "Musk_(film)", "query": "Musk documentary", "hypothesis": "Хвиля до 9.10 (реліз)"},
    {"title": "Wicknell Chivayo", "article": "Wicknell_Chivayo", "query": "Wicknell Chivayo", "hypothesis": "Тендерний мільярдер загинув; конкурентів немає"},
    {"title": "Elizabeth Holmes", "article": "Elizabeth_Holmes", "query": "Elizabeth Holmes", "hypothesis": "Хвиля від A24 «You Can See Everything»"},
    {"title": "Ted Kaczynski", "article": "Ted_Kaczynski", "query": "Unabomber", "hypothesis": "Хвиля від фільму Netflix"},
    {"title": "Anna's Archive", "article": "Anna's_Archive", "query": "Anna's Archive", "hypothesis": "$341M боргу, конкурентів немає"},
    {"title": "Matthew Perry", "article": "Matthew_Perry", "query": "Matthew Perry", "hypothesis": "Хвиля від Netflix-документалки"},
    {"title": "Cornell 7", "article": "Cornell_7", "query": "Cornell 7", "hypothesis": "НЕ брати: забито + юридичний ризик"},
    {"title": "AI data center opposition", "article": "", "query": "data center opposition", "hypothesis": "Містечка проти датацентрів"},
]


def run_radar(http: HttpClient, days: int = 7, candidates: int = 60, end: date | None = None,
              check_youtube: bool = True, signals: list[dict] | None = None, deep: int = 20) -> list[dict]:
    from .signals import autocomplete, platforms_for

    signals = signals or []
    end = end or date.today() - timedelta(days=1)
    tops: dict[str, dict[str, int]] = {}
    for i in range(days):
        d = end - timedelta(days=i)
        tops[d.isoformat()] = wiki_top(http, d)
        log.info("wiki top %s: %d", d, len(tops[d.isoformat()]))
    # кандидати: найбільші перегляди вчора + нові в топі (не було на початку періоду)
    latest = next((v for k, v in sorted(tops.items(), reverse=True) if v), {})
    oldest = next((v for k, v in sorted(tops.items()) if v), {})
    ranked = sorted(latest.items(), key=lambda kv: -kv[1])
    newcomers = [a for a, v in ranked if a not in oldest]
    pool = list(dict.fromkeys([a for a, _ in ranked[:candidates]] + newcomers[:candidates]))

    days_sorted = sorted(k for k, v in tops.items() if v)
    out = []
    for art in pool:
        # ряд із денних топ-1000 (0 = статті не було в топі — для «новачків» це і є ранній сигнал)
        series = [tops[d].get(art, 0) for d in days_sorted]
        m = momentum(series)
        if m["accel"] < 1.5 and m["last"] < 50_000:
            continue
        summ = wiki_summary(http, art)
        kind = classify(summ)
        if kind == "не наше":
            continue
        out.append({"article": art, "title": art.replace("_", " "), "series": series, "momentum": m,
                    "kind": kind, "summary": summ})
    # дорогі перевірки — лише для найсильніших кандидатів
    out.sort(key=lambda t: -(math.log10(1 + t["momentum"]["last"]) + math.log2(max(1.0, t["momentum"]["accel"]))))
    out = out[:deep]
    for t in out:
        deep_check(http, t, end, signals, check_youtube)
    return sorted(out, key=lambda t: -t["score"]["total"])


def _spark(series: list[int], w: int = 220, h: int = 48) -> str:
    """Спарклайн переглядів Вікіпедії за 30 днів (одна серія, 2px лінія, підказки на точках)."""
    if len(series) < 2:
        return ""
    end = date.today() - timedelta(days=1)
    mx = max(series) or 1
    step = w / (len(series) - 1)
    pts = [(i * step, h - 4 - (v / mx) * (h - 10)) for i, v in enumerate(series)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="transparent"><title>'
        f'{(end - timedelta(days=len(series) - 1 - i)):%d.%m}: {series[i]:,} переглядів</title></circle>'
        for i, (x, y) in enumerate(pts))
    lx, ly = pts[-1]
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="Перегляди Вікіпедії за {len(series)} днів, останній день {series[-1]:,}">'
            f'<line x1="0" y1="{h-4}" x2="{w}" y2="{h-4}" class="base"/>'
            f'<polyline points="{line}" class="ln"/><circle cx="{lx:.1f}" cy="{ly:.1f}" r="4" class="pt"/>{dots}</svg>')


def rising_topics(http: HttpClient, signals: list[dict], top: int = 25, check_youtube: bool = True) -> list[dict]:
    """Зростаючі запити Google Trends, що схожі на розслідування, + перевірка автодоповненням і YouTube."""
    from .signals import autocomplete, platforms_for

    rows, seen = [], set()
    for s in sorted((x for x in signals if x["source"] == "rising"), key=lambda x: -x.get("growth", 0)):
        q = s["text"].lower()
        if q in seen or not INVESTIGATE.search(f"{q} {s.get('seed', '')}"):
            continue
        seen.add(q)
        rows.append(s)
    out = []
    for s in rows[:top]:
        sup = {}
        if check_youtube:
            try:
                sup = youtube_supply(s["text"], 15)
            except Exception as exc:
                log.error("youtube %s: %s", s["text"], exc)
        others = {k: v for k, v in platforms_for(s["text"], signals).items() if k != "rising"}
        out.append({**s, "autocomplete": autocomplete(http, s["text"]), "supply": sup, "platforms": others,
                    "links": links(s["text"].replace(" ", "_"), s["text"])})
    return out


def verdict(t: dict) -> tuple[str, str]:
    """Автовердикт по гіпотезі: чи тема ще жива і чи є місце на YouTube."""
    m, sup, plat = t["momentum"], t.get("supply", {}), t.get("platforms", {})
    alive = m["last"] >= 20_000 or m["accel"] >= 1.5 or len(plat) >= 2
    crowded = sup.get("long", 0) >= 6 and sup.get("long_top", 0) >= 500_000
    if not alive:
        return "спадає — тема охолоне, якщо не буде нового приводу", "mid"
    if crowded:
        return "жива, але вже тісно на YouTube — потрібен свій кут", "mid"
    return "підтверджується — попит є, місце на YouTube є", "good"


def write_radar_report(topics: list[dict], trends: dict[str, list[tuple[str, str]]], out_path,
                       rising: list[dict] | None = None, signals: list[dict] | None = None,
                       tracked: list[dict] | None = None) -> "Path":
    import base64
    import html as H
    import subprocess
    from pathlib import Path

    E = H.escape

    def fmt(n):
        return f"{n/1e6:.1f}M" if n >= 1e6 else (f"{n/1e3:.0f}K" if n >= 1e3 else str(n))

    def thumb(url):
        try:
            b = subprocess.run(["curl", "-s", "-m", "15", url], capture_output=True).stdout
            return "data:image/jpeg;base64," + base64.b64encode(b).decode() if len(b) > 1000 else url
        except Exception:
            return url

    names = {"google_trends": "Google Trends", "google_news": "Google News", "reddit": "Reddit", "x": "X", "rising": "Trends rising", "pinterest": "Pinterest", "threads": "Threads"}

    def plat_line(p):
        if not p:
            return "<span class='mute'>не знайдено</span>"
        return " · ".join(f'<span class="tag good" title="{E(" | ".join(v[:5]))}">{names.get(k, k)} ×{len(v)}</span>' for k, v in p.items())

    def ac_line(a):
        if not a:
            return "—"
        h = a.get("hits", {})
        ex = (a.get("youtube") or a.get("google") or [])[:4]
        return f'Google {h.get("google", 0)} · YouTube {h.get("youtube", 0)} · Amazon {h.get("amazon", 0)}' + (f' <span class="mute">({E(", ".join(ex))})</span>' if ex else "")

    phase_cls = {"росте зараз": "hot", "розгін": "good", "після піку": "mid", "фон": "mute", "мало даних": "mute"}
    cards = []
    for n, t in enumerate(topics, 1):
        m, s, sc, L = t["momentum"], t["supply"], t["score"], t["links"]
        parts = "".join(f"<li><span>{E(k)}</span><b>{v}</b></li>" for k, v in sc["parts"].items())
        vids = "".join(
            f'<a class="vid" href="{v["url"]}" target="_blank" rel="noopener"><img loading="lazy" src="{thumb(v["thumbnail"].replace("hqdefault", "mqdefault"))}" alt="">'
            f'<div class="vt">{E(v["title"])}</div><div class="vm"><b>{fmt(v["views"])}</b> · {v["duration_s"]//60} хв · {E(v["channel_title"] or "")}</div></a>'
            for v in s.get("top", []))
        sup = (f'за тиждень {s["videos"]} відео, з них довгих (≥10 хв): <b>{s["long"]}</b>; '
               f'топ довгого — {fmt(s["long_top"])}, топ загалом — {fmt(s["all_top"])}') if s else "не перевірено"
        cards.append(f'''<section class="topic"><div class="th"><h3><span class="num">{n}</span> <a href="{L["wiki"]}" target="_blank" rel="noopener">{E(t["title"])}</a></h3>
<span class="score">{sc["total"]}/10</span></div>
<p class="desc">{E(t["summary"].get("description", ""))} · <span class="tag">{E(t["kind"])}</span> <span class="tag {phase_cls.get(m["phase"], "mute")}">{E(m["phase"])}</span></p>
<div class="row"><div class="metric"><div class="lbl">Вікіпедія, 30 днів</div>{_spark(t["series"])}
<div class="nums">вчора <b>{fmt(m["last"])}</b> · фон (медіана 14 дн) {fmt(m["baseline"])} · прискорення <b>×{m["accel"]}</b> · пік {fmt(m.get("peak", 0))}</div></div>
<div class="metric"><div class="lbl">Чому такий бал</div><ul class="parts">{parts}</ul></div>
<div class="metric"><div class="lbl">Пропозиція на YouTube</div><p class="small">{sup}</p></div></div>
<p class="small"><b>Інші платформи:</b> {plat_line(t.get("platforms", {}))} · <b>Автодоповнення:</b> {ac_line(t.get("autocomplete", {}))}</p>
<p class="small">{E(t["summary"].get("extract", "")[:300])}</p>
<div class="verify"><b>Перевірити самому:</b> <a href="{L["pageviews"]}" target="_blank" rel="noopener">графік Вікіпедії</a> · <a href="{L["trends"]}" target="_blank" rel="noopener">Google Trends (7 днів)</a> · <a href="{L["youtube_week"]}" target="_blank" rel="noopener">YouTube за тиждень</a> · <a href="{L["news"]}" target="_blank" rel="noopener">Google News</a></div>
<div class="grid">{vids}</div></section>''')
    tr = "".join(f'<div><b>{E(geo)}</b><ol>' + "".join(f"<li>{E(q)} <span class='mute'>{E(v)}</span></li>" for q, v in items[:15]) + "</ol></div>"
                 for geo, items in trends.items())
    rising = rising or []
    rrows = "".join(
        f'<tr><td><b>{E(r["text"])}</b><div class="small mute">«{E(r.get("seed", ""))}» · {E(r["geo"])}</div></td><td><b>{E(r["value"])}</b></td>'
        f'<td>{ac_line(r.get("autocomplete", {}))}</td>'
        f'<td>{(str(r["supply"].get("long", 0)) + " довгих · топ " + fmt(r["supply"].get("all_top", 0))) if r.get("supply") else "—"}</td>'
        f'<td>{plat_line(r.get("platforms", {}))}</td>'
        f'<td class="small"><a href="{r["links"]["trends"]}" target="_blank" rel="noopener">Trends</a> · <a href="{r["links"]["youtube_week"]}" target="_blank" rel="noopener">YouTube</a> · <a href="{r["links"]["news"]}" target="_blank" rel="noopener">News</a></td></tr>'
        for r in rising)
    rising_html = (f'<div class="box"><h3 style="margin-top:0">Зростаючі запити Google Trends — ранні сигнали</h3>'
                   f'<p class="small">Запити, що за тиждень виросли найбільше (Breakout = понад +5000%), відфільтровані під розслідування. '
                   f'Валідна тема: є автодоповнення (люди вже це шукають), мало довгих відео на YouTube, згадки на інших платформах.</p>'
                   f'<div style="overflow-x:auto"><table class="tbl"><tr><th>Запит</th><th>Ріст</th><th>Автодоповнення</th><th>YouTube за тиждень</th><th>Інші платформи</th><th>Перевірити</th></tr>{rrows}</table></div></div>') if rising else (
        '<div class="box"><h3 style="margin-top:0">Зростаючі запити Google Trends</h3><p class="small">Цього разу Google повернув 429 (обмеження для IP сервера). '
        'Запустіть радар локально або задайте резидентні проксі в PROXY_URLS — тоді цей блок заповниться.</p></div>')
    trows = []
    for t in tracked or []:
        m, sup = t["momentum"], t.get("supply", {})
        v, cls = verdict(t)
        trows.append(
            f'<tr><td><b>{E(t["title"])}</b><div class="small mute">{E(t.get("hypothesis", ""))}</div></td>'
            f'<td>{_spark(t["series"], 160, 40)}<div class="small">вчора {fmt(m["last"])} · ×{m["accel"]} · {E(m["phase"])}</div></td>'
            f'<td class="small">{(str(sup.get("long", 0)) + " довгих · топ " + fmt(sup.get("long_top", 0))) if sup else "—"}</td>'
            f'<td>{plat_line(t.get("platforms", {}))}</td><td class="small">{ac_line(t.get("autocomplete", {}))}</td>'
            f'<td><span class="tag {cls}">{E(v)}</span><div class="small"><a href="{t["links"]["pageviews"]}" target="_blank" rel="noopener">Вікі</a> · '
            f'<a href="{t["links"]["trends"]}" target="_blank" rel="noopener">Trends</a> · <a href="{t["links"]["youtube_week"]}" target="_blank" rel="noopener">YouTube</a> · '
            f'<a href="{t["links"]["news"]}" target="_blank" rel="noopener">News</a></div></td></tr>')
    tracked_html = (f'<div class="box"><h3 style="margin-top:0">Теми, які я пропонував 01–02.10 — перевірка гіпотез</h3>'
                    f'<p class="small">Кожна гіпотеза перевірена тими самими метриками, що й нові теми. Вердикт автоматичний; поруч посилання, щоб перевірити руками.</p>'
                    f'<div style="overflow-x:auto"><table class="tbl"><tr><th>Тема і гіпотеза</th><th>Вікіпедія 30 днів</th><th>YouTube за тиждень</th><th>Інші платформи</th><th>Автодоповнення</th><th>Вердикт</th></tr>{"".join(trows)}</table></div></div>') if trows else ""
    sig = signals or []
    blocks = []
    for src in ("x", "reddit", "google_news", "threads"):
        items = [x for x in sig if x["source"] == src][:40]
        if items:
            def li(x):
                t = E(x["text"])
                link = f'<a href="{E(x["url"])}" target="_blank" rel="noopener">{t}</a>' if x.get("url") else t
                return f'<li>{link} <span class="mute">{E(x["geo"])}</span></li>'
            lis = "".join(li(x) for x in items)
            blocks.append(f'<div><b>{names.get(src, src)}</b><ol class="small">{lis}</ol></div>')
    signals_html = f'<div class="box"><h3 style="margin-top:0">Сирі сигнали: X, Reddit, Google News</h3><div class="cols">{"".join(blocks)}</div></div>' if blocks else ""
    page = f'''<!doctype html><html lang="uk"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Радар хайпу</title>
<style>
:root{{--bg:#f6f5f2;--card:#fff;--ink:#1b1b1f;--ink2:#3d3d45;--mute:#6a6a73;--line:#e3e1dc;--link:#1d4ed8;--series:#2a6fdb;--hot:#c2410c;--hotbg:#fff1e8;--good:#15803d;--goodbg:#eaf7ef;--mid:#a16207;--midbg:#fdf6e3}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#121214;--card:#1c1c20;--ink:#ececf0;--ink2:#c9c9d1;--mute:#9a9aa5;--line:#2c2c33;--link:#93b4ff;--series:#6fa0ff;--hot:#fb923c;--hotbg:#3a1f10;--good:#4ade80;--goodbg:#10291a;--mid:#facc15;--midbg:#2e2708}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif}}a{{color:var(--link)}}
.wrap{{max-width:1180px;margin:0 auto;padding:20px 16px 60px}}h1{{margin:0 0 4px;font-size:24px}}.mute,.small{{color:var(--mute)}}.small{{font-size:13px}}
.topic,.box{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:14px 0}}.th{{display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap}}.th h3{{margin:0;font-size:17px}}.th h3 a{{color:var(--ink);text-decoration:none}}
.num{{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:50%;background:var(--ink);color:var(--card);font-size:13px;margin-right:4px}}.score{{font-weight:700;font-size:18px}}
.desc{{margin:4px 0 8px}}.tag{{font-size:12px;padding:2px 8px;border-radius:99px;background:var(--bg);border:1px solid var(--line)}}.tag.hot{{background:var(--hotbg);color:var(--hot);border-color:transparent}}.tag.good{{background:var(--goodbg);color:var(--good);border-color:transparent}}.tag.mid{{background:var(--midbg);color:var(--mid);border-color:transparent}}
.row{{display:flex;flex-wrap:wrap;gap:16px}}.metric{{flex:1 1 260px;min-width:0}}.lbl{{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute);margin-bottom:4px}}
.spark .ln{{fill:none;stroke:var(--series);stroke-width:2;stroke-linejoin:round}}.spark .pt{{fill:var(--series);stroke:var(--card);stroke-width:2}}.spark .base{{stroke:var(--line);stroke-width:1}}.spark circle[fill=transparent]:hover{{fill:var(--series);fill-opacity:.25}}
.nums{{font-size:13px;color:var(--ink2)}}.parts{{list-style:none;margin:0;padding:0;font-size:13px}}.parts li{{display:flex;justify-content:space-between;border-bottom:1px dashed var(--line);padding:2px 0}}
.verify{{font-size:13px;margin:8px 0}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin-top:8px}}
.vid{{text-decoration:none;color:var(--ink);border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--bg)}}.vid img{{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:#000}}.vt{{font-size:12px;font-weight:600;padding:6px 6px 0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}.vm{{font-size:11px;color:var(--mute);padding:2px 6px 6px}}
.cols{{display:flex;flex-wrap:wrap;gap:24px}}.tbl{{border-collapse:collapse;width:100%;font-size:13px}}.tbl td,.tbl th{{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}}.cols>div{{flex:1 1 240px}}
</style></head><body><div class="wrap">
<h1>Радар хайпу</h1><p class="mute">Згенеровано {date.today():%d.%m.%Y} · дані Вікіпедії по {(date.today()-timedelta(days=1)):%d.%m} · сортування за балом 0–10</p>
<div class="box"><h3 style="margin-top:0">Як читати і як перевіряти гіпотезу</h3>
<ul class="small"><li><b>Попит</b> (0–3): скільки людей учора відкрили статтю у Вікіпедії. 1K → 1, 10K → 2, 100K+ → 3.</li>
<li><b>Прискорення</b> (0–3): учора ÷ медіана попередніх 14 днів. ×2 → 1, ×4 → 2, ×8+ → 3. Це головний ранній сигнал: тема росте швидше, ніж про неї встигли зняти.</li>
<li><b>Прогалина YouTube</b> (0–2): скільки довгих (≥10 хв) відео з'явилось за тиждень. 0 → 2 бали; 6+ → 0.</li>
<li><b>Придатність</b> (0–2): опис статті містить «суд, шахрайство, вбивство, скандал, компанія, катастрофа…» → розслідування. Фільм/серіал про реальну подію → «хвиля від релізу».</li>
<li><b>Ранній сигнал</b> (+1): тема росте, але ще не вибухнула (менше 300K переглядів на добу).</li>
<li><b>Інші платформи</b> (0–2): тема одночасно в Google Trends / Google News / Reddit / X. Збіг на кількох платформах — найкраще підтвердження, що хайп не випадковий. Наведи курсор на мітку — побачиш, які саме заголовки збіглись.</li>
<li><b>Автодоповнення</b>: скільки підказок Google / YouTube / Amazon містять тему. Якщо YouTube підказує «… documentary», «… what happened» — люди вже шукають саме відео.</li>
<li>Сума складових (макс. 12) зводиться до шкали 0–10.</li>
<li><b>Фаза</b>: «росте зараз» — прискорення ≥×3 і вчора майже пік; «розгін» — ×1.5–3; «після піку» — хвиля вже спадає (пізно, якщо немає нового приводу).</li></ul>
<p class="small"><b>Перевірка руками (5 хв на тему):</b> 1) графік Вікіпедії — пік був учора чи вже спадає? 2) Google Trends за 7 днів — крива росте? 3) YouTube за тиждень — скільки довгих відео й скільки вони набрали за 1–2 дні? 4) Google News — чи є новий привід (суд, реліз, заява) у найближчі дні? Якщо 3 з 4 «так» — тема валідна.</p>
<p class="small"><b>Як ловити теми до хайпу:</b> запускати радар щодня й дивитись фазу «розгін» з малими абсолютними цифрами; календар подій на 1–3 тижні вперед (релізи документалок і фільмів про реальні справи, дати судів, вироків і страт, річниці); статті, яких учора не було в топ-1000 Вікіпедії («новачки»).</p></div>
{tracked_html}
<div class="box"><h3 style="margin-top:0">Google Trends зараз</h3><div class="cols">{tr}</div></div>
{rising_html}{signals_html}
<h2>Нові теми з радару</h2>
{"".join(cards)}
</div></body></html>'''
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return out_path

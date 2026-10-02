"""Безкоштовні сигнали хайпу з різних платформ для радару.

- Google Trends: «Trending now» RSS по країнах + ЗРОСТАЮЧІ запити (related queries → rising) по базових словах
  у кількох країнах — найкращий ранній сигнал: запит ще маленький, але росте на сотні/тисячі відсотків.
- Google News: топ-новини RSS по країнах.
- Reddit: гарячі пости через RSS (без ключів; JSON з хмарних IP дає 403).
- X (Twitter): тренди через getdaytrends.com (без API).
- Автодоповнення Google / YouTube / Amazon: якщо після назви теми люди дописують «documentary», «what happened»,
  «update» — попит підтверджено.
- Pinterest: через Apify (платно, копійки) — лише якщо задано APIFY_TOKEN і APIFY_PINTEREST_ACTOR; вимикається.
"""
import html
import logging
import re
import time
from urllib.parse import quote_plus

import feedparser

from .net import HttpClient

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (X11; Linux x86_64) niche-finder-radar/0.1"

# базові слова для зростаючих запитів: розслідування, гроші, суди, катастрофи, скандали
SEEDS = [
    "scandal", "lawsuit", "arrested", "fraud", "scam", "investigation", "exposed", "leaked", "documentary",
    "trial", "verdict", "sentenced", "execution", "murder", "missing", "kidnapping", "cartel", "mafia",
    "hacker", "data breach", "whistleblower", "cover up", "corruption", "bribery", "bankruptcy", "collapse",
    "ceo", "billionaire", "lawsuit settlement", "class action", "recall", "plane crash", "train crash",
    "explosion", "cult", "conspiracy", "heist", "robbery", "prison", "escape", "spy", "hijack", "what happened",
]
GEOS = ["US", "GB", "CA", "AU"]
REDDIT_SUBS = ["news", "worldnews", "OutOfTheLoop", "UnresolvedMysteries", "TrueCrimeDiscussion", "aviation",
               "business", "technology", "Scams", "law"]


def _get(http: HttpClient, url: str):
    try:
        return http.get(url, headers={"User-Agent": UA})
    except Exception as exc:
        log.warning("GET %s: %s", url, exc)
        return None


def google_trends_now(http: HttpClient, geos=GEOS) -> list[dict]:
    out = []
    for geo in geos:
        r = _get(http, f"https://trends.google.com/trending/rss?geo={geo}")
        if r is None or r.status_code != 200:
            continue
        for e in feedparser.parse(r.content).entries:
            out.append({"source": "google_trends", "geo": geo, "text": e.title,
                        "value": e.get("ht_approx_traffic", ""), "url": ""})
    return out


def google_news(http: HttpClient, geos=("US", "GB")) -> list[dict]:
    out = []
    for geo in geos:
        r = _get(http, f"https://news.google.com/rss?hl=en-{geo}&gl={geo}&ceid={geo}:en")
        if r is None or r.status_code != 200:
            continue
        for e in feedparser.parse(r.content).entries:
            out.append({"source": "google_news", "geo": geo, "text": e.title, "value": "", "url": e.link})
    return out


def reddit_hot(http: HttpClient, subs=REDDIT_SUBS, per_sub: int = 25) -> list[dict]:
    # Reddit швидко дає 429 — окремий клієнт з довшою паузою і меншою кількістю повторів
    http = HttpClient(http.proxies, min_interval=6.0, max_retries=2)
    out = []
    for sub in subs:
        r = _get(http, f"https://www.reddit.com/r/{sub}/hot/.rss?limit={per_sub}")
        if r is None or r.status_code != 200:
            continue
        for e in feedparser.parse(r.content).entries:
            if e.title.lower().startswith(("welcome", "weekly", "daily", "megathread")):
                continue
            out.append({"source": "reddit", "geo": f"r/{sub}", "text": e.title, "value": "", "url": e.link})
    return out


def x_trends(http: HttpClient, countries=("united-states", "united-kingdom")) -> list[dict]:
    out = []
    for c in countries:
        r = _get(http, f"https://getdaytrends.com/{c}/")
        if r is None or r.status_code != 200:
            continue
        for m in re.finditer(r'<a[^>]+href="/[^"]*/trend/[^"]+"[^>]*>([^<]{2,80})</a>', r.text):
            out.append({"source": "x", "geo": c, "text": html.unescape(m.group(1)).strip(), "value": "", "url": ""})
    seen, uniq = set(), []
    for t in out:
        if (t["geo"], t["text"].lower()) not in seen:
            seen.add((t["geo"], t["text"].lower()))
            uniq.append(t)
    return uniq


def rising_queries(seeds=SEEDS, geos=GEOS, timeframe: str = "now 7-d", proxies: list[str] | None = None,
                   pause: float = 2.5, limit_per_seed: int = 8) -> list[dict]:
    """Зростаючі запити Google Trends (related queries → rising). 429 від Google — пропускаємо й йдемо далі."""
    from pytrends.request import TrendReq

    kw = {"proxies": proxies, "retries": 2, "backoff_factor": 1} if proxies else {}
    tr = TrendReq(hl="en-US", tz=0, timeout=(10, 25), **kw)
    out, fails = [], 0
    for geo in geos:
        for seed in seeds:
            if fails >= 5:  # Google ріже IP (429) — далі марно, потрібні проксі (PROXY_URLS)
                log.error("pytrends: 5 відмов поспіль — зупиняю зростаючі запити; задайте PROXY_URLS (резидентні проксі)")
                return out
            try:
                tr.build_payload([seed], timeframe=timeframe, geo=geo)
                rq = tr.related_queries().get(seed, {}).get("rising")
                fails = 0
            except Exception as exc:
                fails += 1
                log.warning("pytrends %s %s: %s", geo, seed, str(exc)[:80])
                time.sleep(pause * 4)
                continue
            if rq is not None:
                for _, row in rq.head(limit_per_seed).iterrows():
                    val = row["value"]
                    out.append({"source": "rising", "geo": geo, "seed": seed, "text": row["query"],
                                "value": "Breakout" if val >= 5000 else f"+{int(val)}%", "growth": int(val), "url": ""})
            time.sleep(pause)
    return out


def autocomplete(http: HttpClient, term: str) -> dict:
    """Підказки Google, YouTube і Amazon для теми: скільки є і які саме (що люди дописують)."""
    res = {}
    for name, url in (
        ("google", f"https://suggestqueries.google.com/complete/search?client=firefox&q={quote_plus(term)}"),
        ("youtube", f"https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q={quote_plus(term)}"),
    ):
        r = _get(http, url)
        try:
            res[name] = r.json()[1] if r is not None and r.status_code == 200 else []
        except Exception:
            res[name] = []
    r = _get(http, f"https://completion.amazon.com/api/2017/suggestions?mid=ATVPDKIKX0DER&alias=aps&prefix={quote_plus(term)}")
    try:
        res["amazon"] = [s["value"] for s in r.json().get("suggestions", [])] if r is not None and r.status_code == 200 else []
    except Exception:
        res["amazon"] = []
    t = term.lower()
    res["hits"] = {k: sum(1 for s in v if t in s.lower()) for k, v in res.items() if isinstance(v, list)}
    return res


def pinterest_apify(http: HttpClient, token: str, actor: str, queries: list[str]) -> list[dict]:
    """Pinterest через Apify (опційно, платно). Вимкнено, якщо немає token/actor."""
    if not (token and actor):
        return []
    from .sources.apify import first, run_actor

    try:
        items = run_actor(http, token, actor, {"queries": queries, "searchTerms": queries, "maxItems": 50})
    except Exception as exc:
        log.error("apify pinterest: %s", exc)
        return []
    return [{"source": "pinterest", "geo": "", "text": first(i, "title", "keyword", "description", default=""),
             "value": str(first(i, "saves", "repinCount", default="")), "url": first(i, "url", "link", default="")}
            for i in items if first(i, "title", "keyword", "description")]


def collect_all(http: HttpClient, rising: bool = True, seeds=SEEDS, geos=GEOS, proxies=None,
                apify_token: str = "", apify_pinterest_actor: str = "") -> list[dict]:
    sig = []
    for name, fn in (("google_trends", lambda: google_trends_now(http, geos)), ("google_news", lambda: google_news(http)),
                     ("reddit", lambda: reddit_hot(http)), ("x", lambda: x_trends(http))):
        try:
            got = fn()
            log.info("%s: %d", name, len(got))
            sig += got
        except Exception as exc:
            log.error("%s: %s", name, exc)
    if rising:
        got = rising_queries(seeds, geos, proxies=proxies)
        log.info("rising: %d", len(got))
        sig += got
    sig += pinterest_apify(http, apify_token, apify_pinterest_actor, seeds[:10])
    return sig


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def platforms_for(name: str, signals: list[dict]) -> dict[str, list[str]]:
    """На яких платформах згадується тема (за ключовими словами назви)."""
    words = [w for w in _norm(re.sub(r"\(.*?\)", "", name)).split() if len(w) > 2]
    if not words:
        return {}
    key = " ".join(words[:3]) if len(words) <= 3 else None
    hits: dict[str, list[str]] = {}
    for s in signals:
        txt = _norm(s["text"])
        ok = (key and key in txt) or (not key and all(w in txt for w in words[:3])) or \
             (len(words) >= 2 and all(w in txt for w in words[:2]))
        if ok:
            hits.setdefault(s["source"], []).append(s["text"])
    return hits

"""Пошук нових форматів, які можна клонувати: де малі канали вистрілюють на тому ж шаблоні.

Для кожного шаблону назви з SEED_FORMATS шукаємо відео за цей рік (yt-dlp), лишаємо ті, що справді
мають шаблон у назві, і дивимось на канали-автори:
- breakout = канал < 100k підписників, а відео набрало ≥ 3× його підписників.
  Багато різних breakout-каналів на одному шаблоні = переглядів дає формат, а не автор → його можна клонувати.
- канали з breakout — готові донори для `formats @канал`.
"""
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from statistics import median
from urllib.parse import quote_plus

from .formats import matches

log = logging.getLogger(__name__)

SMALL = 100_000
BREAKOUT_X = 3
THIS_YEAR_VIDEOS = "EgQIBRAB"  # фільтр пошуку YouTube: завантажено цього року, тільки відео

# (назва, пошуковий запит, обов'язкові слова в назві через "|")
SEED_FORMATS = [
    ("How X Actually Makes Money", "how actually make money it's not", "actually|make|money"),
    ("The Economics of X", "the economics of", "economics of"),
    ("The Economics of Owning a X", "the economics of owning a", "economics of owning"),
    ("Rise and Fall of X", "the rise and fall of", "rise and fall"),
    ("What Happened to X?", "what happened to the company", "what happened to"),
    ("Why X Failed", "why it failed company", "why|failed"),
    ("The Fascinating Story of X", "the fascinating story of", "fascinating story"),
    ("How X Became a Billion Dollar", "how became a billion dollar company", "billion"),
    ("POV: You're a X", "pov you are a", "pov"),
    ("POV: Building X from $0", "pov you building from $0", "pov|$0"),
    ("Every Level of X Explained", "every level of explained", "every level"),
    ("Every X Explained in N Minutes", "every explained in minutes", "every|explained in"),
    ("Why X Is So Expensive", "why is so expensive", "so expensive"),
    ("The Dark Truth About X", "the dark truth about", "dark truth"),
    ("Inside the X Industry", "inside the billion dollar industry", "industry"),
    ("A Day in the Life of a Medieval X", "a day in the life of a medieval", "day in the life"),
    ("Life of a X in History", "what life was like for a in ancient", "life"),
    ("Boring History to Sleep To", "boring history for sleep", "sleep"),
    ("The Insane Engineering of X", "the insane engineering of", "engineering of"),
    ("Why Does X Exist / Why X", "why does this exist explained", "why"),
    ("What $X Gets You in City", "what $1 million gets you in", "gets you"),
    ("Psychology of X", "the psychology of why people", "psychology"),
    ("What If X", "what if the earth", "what if"),
    ("The Most Dangerous X", "the most dangerous in the world", "most dangerous"),
    ("How X Works (Explained)", "how it actually works explained animation", "how|works"),
    ("Lost / Forgotten X", "the forgotten history of", "forgotten"),
    ("Biggest Scams / Frauds", "the biggest scam in history of", "scam"),
    ("Mafia / Cartel Economics", "how the cartel makes money", "cartel"),
    ("Countries Explained", "why this country is so", "country"),
    ("Luxury Brand Secrets", "why luxury brands", "luxury"),
]


def _ydl_search_url(url: str, limit: int = 40) -> list[dict]:
    from .sources.ytdlp import _ydl, parse_entry

    with _ydl(limit) as ydl:
        info = ydl.extract_info(url, download=False)
    return [parse_entry(e) for e in info.get("entries") or [] if e and e.get("id")]


def search_recent(query: str, limit: int = 40) -> list[dict]:
    return _ydl_search_url(f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={THIS_YEAR_VIDEOS}",
                           limit)


def subscribers(channel_id: str) -> int:
    from .sources.ytdlp import _ydl

    with _ydl(1) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/channel/{channel_id}/videos", download=False)
    return int(info.get("channel_follower_count") or 0)


def evaluate(name: str, found: list[dict], subs: dict[str, int]) -> dict:
    views = sorted((v["views"] for v in found), reverse=True)
    breakouts = [v for v in found if 0 < subs.get(v["channel_id"], 0) < SMALL
                 and v["views"] >= BREAKOUT_X * subs[v["channel_id"]]]
    b_channels = {v["channel_id"] for v in breakouts}
    channels = {v["channel_id"] for v in found}
    r = {
        "name": name,
        "videos": len(found),
        "channels": len(channels),
        "median_views": int(median(views[:10])) if views else 0,
        "breakout_channels": len(b_channels),
        "breakouts": [{"title": v["title"], "channel": v["channel_title"], "channel_id": v["channel_id"],
                       "views": v["views"], "subscribers": subs[v["channel_id"]], "url": v["url"]}
                      for v in sorted(breakouts, key=lambda v: v["views"] / max(1, subs[v["channel_id"]]),
                                      reverse=True)],
    }
    r["score"] = format_score(r)
    return r


def format_score(r: dict) -> float:
    """0–10: попит (медіана топ-10) + скільки різних малих каналів вистрілили на шаблоні."""
    demand = min(5.0, math.log10(1 + r["median_views"]))          # 100k -> 5
    clone = min(5.0, r["breakout_channels"] * 0.5)                # 10 breakout-каналів -> 5
    return round(demand + clone, 2)


def discover(seeds: list[tuple[str, str, str]] = SEED_FORMATS, workers: int = 6) -> list[dict]:
    def run(seed):
        name, query, phrase = seed
        try:
            return name, [v for v in search_recent(query) if matches(v["title"], phrase)]
        except Exception as exc:
            log.error("search %s: %s", query, exc)
            return name, []

    with ThreadPoolExecutor(workers) as ex:
        found = list(ex.map(run, seeds))

    # Підписників тягнемо лише для каналів, де відео в принципі може бути breakout (≥ 3× малого каналу)
    need = {v["channel_id"] for _, vs in found for v in vs if v["channel_id"] and v["views"] >= 3_000}
    with ThreadPoolExecutor(workers) as ex:
        subs = dict(zip(need, ex.map(lambda c: _safe(subscribers, c), need)))
    log.info("checked %d channels", len(subs))
    return sorted((evaluate(name, vs, subs) for name, vs in found), key=lambda r: r["score"], reverse=True)


def _safe(fn, arg) -> int:
    try:
        return fn(arg)
    except Exception:
        return 0


def write_discover_report(results: list[dict], out_dir) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"discover_{date.today().isoformat()}.md"
    lines = [f"# Формати, які клонують — {date.today().isoformat()}", "",
             "Breakout = канал <100k підписників, відео цього року з переглядами ≥3× підписників.", "",
             "| # | Бал | Формат | Відео | Каналів | Медіана топ-10 | Breakout-каналів |", "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        lines.append(f"| {i} | {r['score']} | {r['name']} | {r['videos']} | {r['channels']} | "
                     f"{r['median_views']:,} | {r['breakout_channels']} |")
    for r in results:
        if not r["breakouts"]:
            continue
        lines += ["", f"## {r['name']} — {r['score']}", ""]
        lines += [f"- x{b['views'] // max(1, b['subscribers'])} — [{b['title']}]({b['url']}) — {b['channel']} "
                  f"({b['subscribers']:,} підп., {b['views']:,} перегл.)" for b in r["breakouts"][:6]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path

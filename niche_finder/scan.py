"""Безкоштовний скан трендів: зростаючі запити Google Trends + перевірка попиту через автодоповнення.

1. Для кожного базового слова (seed) і країни беремо «rising» запити Google Trends за 3 місяці.
2. Відсіюємо очевидний шум (бренди-казино, логіни, новини, спорт).
3. Для кандидатів перевіряємо підказки Amazon (намір купити), YouTube (попит на контент), Google.
4. Рахуємо бал і пишемо звіт reports/trends_YYYY-MM-DD.{csv,md}.
"""
import csv
import logging
import math
import re
import sqlite3
from datetime import date
from pathlib import Path

from .net import HttpClient
from .sources import autocomplete
from .sources.google_trends import TrendsChecker

log = logging.getLogger(__name__)

# Широкі «комерційні» слова: rising-запити до них показують нові продукти, хобі, проблеми
SEEDS = [
    "app", "tool", "software", "ai", "kit", "gadget", "device", "subscription", "course", "template",
    "supplement", "skincare", "hair", "diet", "recipe", "workout", "sleep", "anxiety",
    "for dogs", "for cats", "for kids", "for baby", "for women", "for men", "for seniors",
    "gift", "decor", "garden", "kitchen", "cleaning", "organizer", "storage",
    "hobby", "craft", "diy", "printable", "planner", "journal", "side hustle", "business idea",
    "how to make", "how to start", "best way to", "alternative", "near me", "subscription box",
]

SCAN_SCHEMA = """
CREATE TABLE IF NOT EXISTS rising (
    query TEXT NOT NULL, seed TEXT NOT NULL, geo TEXT NOT NULL, value INTEGER,
    fetched_at TEXT DEFAULT (date('now')),
    PRIMARY KEY (query, seed, geo, fetched_at)
);
CREATE TABLE IF NOT EXISTS demand (
    query TEXT PRIMARY KEY, amazon INTEGER, youtube INTEGER, google INTEGER,
    checked_at TEXT DEFAULT (date('now'))
);
"""

# Шум: азартні ігри, логіни/завантаження, ціни акцій, спорт, новини, погода
NOISE = re.compile(
    r"\b(bet|betting|casino|sportsbook|fanduel|draftkings|betmgm|bet365|login|log in|sign in|download|apk|"
    r"stock|price today|share price|vs\.?|score|scores|schedule|game \d|nfl|nba|mlb|nhl|ufc|fc\b|"
    r"weather|news|died|death|arrested|election|trump|lottery|powerball|meaning|lyrics|cast|trailer|"
    r"season \d|episode|movie|imdb|wiki|today|tonight|yesterday|\d{4,})\b")


def is_noise(q: str) -> bool:
    return bool(NOISE.search(q)) or len(q) < 4 or len(q.split()) > 7


def collect_rising(conn: sqlite3.Connection, geos: list[str], seeds: list[str], proxies=None) -> int:
    conn.executescript(SCAN_SCHEMA)
    checker = TrendsChecker(proxies, pause=6.0)
    total = 0
    for geo in geos:
        for seed in seeds:
            done = conn.execute("SELECT 1 FROM rising WHERE seed=? AND geo=? AND fetched_at=date('now') LIMIT 1",
                                (seed, geo)).fetchone()
            if done:
                continue  # можна перезапускати — вже зібране сьогодні пропускається
            rows = checker.rising(seed, geo=geo)
            conn.executemany("INSERT OR REPLACE INTO rising (query, seed, geo, value) VALUES (?, ?, ?, ?)",
                             [(q, seed, geo, v) for q, v in rows])
            conn.commit()
            total += len(rows)
            log.info("rising %-14s %-3s %3d queries", seed, geo, len(rows))
    return total


def _hits(query: str, suggestions: list[str]) -> int:
    """Скільки підказок продовжують запит (тобто люди шукають саме це і уточнюють)."""
    return sum(1 for s in suggestions if s.startswith(query) or query in s)


def check_demand(conn: sqlite3.Connection, queries: list[str], http: HttpClient) -> None:
    conn.executescript(SCAN_SCHEMA)
    for q in queries:
        if conn.execute("SELECT 1 FROM demand WHERE query=? AND checked_at >= date('now','-7 days')", (q,)).fetchone():
            continue
        res = {}
        for name, fn in (("amazon", autocomplete.amazon), ("youtube", lambda h, x: autocomplete.google(h, x, True)),
                         ("google", autocomplete.google)):
            try:
                res[name] = _hits(q, fn(http, q))
            except Exception as exc:
                log.warning("%s %r: %s", name, q, exc)
                res[name] = None
        conn.execute("INSERT OR REPLACE INTO demand (query, amazon, youtube, google) VALUES (?, ?, ?, ?)",
                     (q, res["amazon"], res["youtube"], res["google"]))
        conn.commit()


def candidates(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    conn.executescript(SCAN_SCHEMA)
    rows = conn.execute(
        """SELECT query, GROUP_CONCAT(DISTINCT seed) AS seeds, GROUP_CONCAT(DISTINCT geo) AS geos,
                  COUNT(DISTINCT geo) AS n_geo, MAX(value) AS max_value
           FROM rising WHERE fetched_at >= date('now', ?) GROUP BY query""", (f"-{days} days",)).fetchall()
    return [dict(r) for r in rows if not is_noise(r["query"])]


def score(c: dict) -> float:
    growth = min(4.0, math.log10(max(c["max_value"], 1)))           # 100% → 2, 1000% → 3, Breakout → ~3.7
    geos = min(2.0, (c["n_geo"] - 1) * 1.0)                         # росте в кількох країнах
    amazon = min(2.0, (c.get("amazon") or 0) / 3)                   # купують
    youtube = min(1.0, (c.get("youtube") or 0) / 4)                 # дивляться/вчаться
    google = min(1.0, (c.get("google") or 0) / 5)                   # уточнюють у пошуку
    return round(growth + geos + amazon + youtube + google, 2)


def scan(conn: sqlite3.Connection, geos: list[str], seeds: list[str], reports_dir: Path,
         check_top: int = 300, proxies=None) -> Path:
    collect_rising(conn, geos, seeds, proxies)
    cands = candidates(conn)
    cands.sort(key=lambda c: (c["n_geo"], c["max_value"]), reverse=True)
    http = HttpClient(proxies, min_interval=0.3, user_agent="Mozilla/5.0")
    check_demand(conn, [c["query"] for c in cands[:check_top]], http)
    for c in cands:
        d = conn.execute("SELECT amazon, youtube, google FROM demand WHERE query=?", (c["query"],)).fetchone()
        if d:
            c.update(dict(d))
        c["score"] = score(c)
    cands.sort(key=lambda c: c["score"], reverse=True)
    return write_report(cands, reports_dir)


def write_report(cands: list[dict], reports_dir: Path, top: int = 100) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    cols = ["score", "query", "max_value", "n_geo", "geos", "seeds", "amazon", "youtube", "google"]
    with (reports_dir / f"trends_{stamp}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(cands)
    md = reports_dir / f"trends_{stamp}.md"
    lines = [f"# Зростаючі запити — {stamp}", "",
             f"Кандидатів після фільтра шуму: {len(cands)}. Ріст — % за 3 місяці (5000 ≈ Breakout).", "",
             "| # | Бал | Запит | Ріст | Країн | Amazon | YouTube | Google | Seed |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, c in enumerate(cands[:top], 1):
        lines.append(f"| {i} | {c['score']} | **{c['query']}** | {c['max_value']}% | {c['n_geo']} "
                     f"| {c.get('amazon', '—')} | {c.get('youtube', '—')} | {c.get('google', '—')} | {c['seeds']} |")
    md.write_text("\n".join(lines), encoding="utf-8")
    return md

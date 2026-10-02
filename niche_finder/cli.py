import argparse
import json
import logging
from collections import Counter

import requests

from . import db
from .config import Config
from .net import HttpClient
from .sources import google_trends, pinterest
from .sources.apify_social import ApifySocial
from .sources.hackernews import HackerNewsSource
from .sources.reddit import RedditSource
from .sources.threads import ThreadsSource
from .sources.x import XSource

log = logging.getLogger("niche_finder")


def collect(cfg: Config, conn, limit: int) -> None:
    http = HttpClient(cfg.proxies, user_agent=cfg.reddit_user_agent)
    reddit = RedditSource(http, cfg.reddit_client_id, cfg.reddit_client_secret)
    x = XSource(http, cfg.x_bearer_token)
    threads = ThreadsSource(http, cfg.threads_access_token)
    apify = ApifySocial(http, cfg.apify_token, {"reddit": cfg.apify_reddit_actor, "x": cfg.apify_x_actor,
                                                "threads": cfg.apify_threads_actor})

    def per_query(search):
        """Обгортка для джерел, які шукають по одному запиту."""
        def run(queries, limit):
            posts = []
            for q in queries:
                try:
                    posts += search(q, limit=limit)
                except requests.HTTPError as exc:
                    status = getattr(exc.response, "status_code", None)
                    if status in (401, 403):
                        raise
                    log.error("%s: %s", q, exc)
            return posts
        return run

    # Пріоритет: офіційний API, якщо є ключ; інакше Apify (один запуск на всі запити); інакше публічний .json
    from .sources.apify_social import THREADS_QUERIES
    queries = cfg.pain_queries
    sources = []
    if cfg.reddit_client_id or not apify.enabled:
        sources.append(("reddit", per_query(reddit.search), queries))
    else:
        sources.append(("reddit", lambda qs, n: apify.search_many("reddit", qs, n), queries))
    for name, official in (("x", x), ("threads", threads)):
        qs = THREADS_QUERIES if name == "threads" else queries
        if official.enabled:
            sources.append((name, per_query(official.search), qs))
        elif apify.enabled:
            sources.append((name, lambda q, n, name=name: apify.search_many(name, q, n), qs))
        else:
            log.warning("%s: немає ні офіційного токена, ні APIFY_TOKEN — пропускаю", name)
    if cfg.include_hackernews:
        sources.append(("hackernews", per_query(HackerNewsSource(http).search), queries))

    for name, run, qs in sources:
        try:
            posts = run(qs, limit)
        except Exception as exc:  # одне джерело не повинно валити весь збір
            status = getattr(getattr(exc, "response", None), "status_code", None)
            hint = (" — для Reddit з хмарних IP потрібні REDDIT_CLIENT_ID/SECRET або APIFY_TOKEN"
                    if name == "reddit" and status in (401, 403) else "")
            log.error("%s: %s%s", name, exc, hint)
            continue
        new = db.upsert_posts(conn, posts)
        by_query = Counter(p["query"] for p in posts)
        log.info("%s: %d posts, %d new  %s", name, len(posts), new, dict(by_query.most_common(5)))

    # Ріст сабредитів: знімок кількості підписників раз на день
    subs = Counter(r["community"] for r in conn.execute(
        "SELECT community FROM posts WHERE source='reddit' AND fetched_at >= datetime('now','-7 days')"))
    for sub, _ in subs.most_common(30 if cfg.reddit_client_id or not apify.enabled else 0):
        count = reddit.subscribers(sub)
        if count is not None:
            conn.execute("INSERT OR REPLACE INTO community_stats (source, name, subscribers) VALUES ('reddit', ?, ?)",
                         (sub, count))
    conn.commit()


def trends(cfg: Config, conn, check_top: int) -> None:
    http = HttpClient(cfg.proxies, min_interval=3.0)
    for geo in cfg.trends_geos:
        try:
            db.save_trending(conn, f"google:{geo}", google_trends.trending_now(http, geo))
        except Exception as exc:
            log.error("google trending %s: %s", geo, exc)

    pin = pinterest.PinterestSource(http, cfg.pinterest_access_token, cfg.pinterest_regions or None)
    if pin.enabled:
        for region in pin.regions:
            try:
                items = pin.top_trends(region)
                db.save_trending(conn, f"pinterest:{region}",
                                 [(t["keyword"], pinterest.traffic_label(t)) for t in items])
            except Exception as exc:
                log.error("pinterest %s: %s", region, exc)
    elif cfg.apify_token:
        apify = ApifySocial(http, cfg.apify_token, {"pinterest_trends": cfg.apify_pinterest_actor})
        try:
            items = apify.pinterest_trends(cfg.pinterest_regions or ["US", "GB+IE", "CA", "DE", "FR", "BR", "AU+NZ", "IN"])
            by_country: dict[str, list] = {}
            for t in items:
                by_country.setdefault(t["country"], []).append(t)
            for country, rows in by_country.items():
                db.save_trending(conn, f"pinterest:{country}",
                                 [(t["keyword"], pinterest.traffic_label(t)) for t in rows])
            log.info("pinterest (apify): %d trends", len(items))
        except Exception as exc:
            log.error("pinterest (apify): %s", exc)
    else:
        log.warning("Pinterest: немає ні PINTEREST_ACCESS_TOKEN, ні APIFY_TOKEN — пропускаю")

    # Перевірка росту 5y для найчастіших ключових слів, які ще не перевіряли цього тижня
    keywords = [r["keyword"] for r in conn.execute(
        """SELECT keyword, COUNT(DISTINCT post_id) AS c FROM ideas
           WHERE created_at >= datetime('now','-30 days')
             AND keyword NOT IN (SELECT keyword FROM trend_checks WHERE checked_at >= date('now','-7 days'))
           GROUP BY keyword HAVING c >= 2 ORDER BY c DESC LIMIT ?""", (check_top,))]
    if not keywords:
        return
    checker = google_trends.TrendsChecker(cfg.proxies)
    for kw in keywords:
        growth, season = checker.check(kw)
        if growth is None and season is None:
            continue
        conn.execute("INSERT OR REPLACE INTO trend_checks (keyword, geo, growth, seasonality) VALUES (?, '', ?, ?)",
                     (kw, growth, season))
        conn.commit()
        log.info("trends %s: growth=%s seasonality=%s", kw, growth, season)


def analyze(cfg: Config, conn, max_posts: int) -> None:
    from .analyze import analyze_pending

    n = analyze_pending(conn, cfg.anthropic_model, max_posts=max_posts)
    log.info("extracted %d ideas", n)


def report(cfg: Config, conn, days: int, min_mentions: int) -> None:
    from .report import write_reports
    from .score import build_niches

    niches = build_niches(conn, days=days, min_mentions=min_mentions)
    csv_path, md_path = write_reports(niches, cfg.reports_dir)
    log.info("%d niches -> %s, %s", len(niches), csv_path, md_path)


def export_posts(conn, path: str, max_posts: int) -> None:
    """Вивантажує непроаналізовані пости в JSON — щоб проаналізувати їх без API-ключа (наприклад, у чаті з Claude)."""
    rows = conn.execute(
        "SELECT id, source, community, title, body, score, comments FROM posts WHERE analyzed = 0 "
        "ORDER BY score + comments DESC LIMIT ?", (max_posts,)).fetchall()
    with open(path, "w", encoding="utf-8") as f:
        json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=1)
    log.info("exported %d posts -> %s", len(rows), path)


def import_ideas(conn, path: str) -> None:
    """Завантажує ідеї з JSON: [{post_id, keyword, problem, audience, solution_type, willingness_to_pay}]."""
    with open(path, encoding="utf-8") as f:
        ideas = json.load(f)
    known = {r[0] for r in conn.execute("SELECT id FROM posts")}
    rows = []
    for i in ideas:
        if i.get("post_id") not in known:
            continue
        rows.append({**i, "keyword": i["keyword"].lower().strip(),
                     "willingness_to_pay": max(0, min(3, int(i.get("willingness_to_pay", 0))))})
    conn.executemany(
        """INSERT INTO ideas (keyword, problem, audience, solution_type, willingness_to_pay, post_id)
           VALUES (:keyword, :problem, :audience, :solution_type, :willingness_to_pay, :post_id)""", rows)
    conn.commit()
    log.info("imported %d ideas", len(rows))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="niche_finder", description="Пошук бізнес-ніш у соцмережах і трендах")
    parser.add_argument("command", choices=["collect", "analyze", "trends", "report", "run", "export", "import-ideas", "mark-analyzed", "scan"])
    parser.add_argument("--geos", default="US,GB,CA,AU", help="країни для scan (коди Google Trends)")
    parser.add_argument("--seeds", default="", help="свої базові слова для scan через кому (за замовчуванням — вбудований список)")
    parser.add_argument("--file", default="data/posts_export.json", help="файл для export / import-ideas / mark-analyzed")
    parser.add_argument("--limit", type=int, default=200, help="постів на один запит у кожному джерелі")
    parser.add_argument("--max-posts", type=int, default=1200, help="скільки нових постів аналізувати за запуск")
    parser.add_argument("--check-top", type=int, default=25, help="скільки ключових слів перевірити в Google Trends")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--min-mentions", type=int, default=2)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.from_env()
    conn = db.connect(cfg.db_path)

    if args.command in ("collect", "run"):
        collect(cfg, conn, args.limit)
    if args.command in ("analyze", "run"):
        analyze(cfg, conn, args.max_posts)
    if args.command in ("trends", "run"):
        trends(cfg, conn, args.check_top)
    if args.command in ("report", "run"):
        report(cfg, conn, args.days, args.min_mentions)
    if args.command == "scan":
        from .scan import SEEDS, scan
        seeds = [s.strip() for s in args.seeds.split(",") if s.strip()] or SEEDS
        md = scan(conn, [g.strip() for g in args.geos.split(",")], seeds, cfg.reports_dir,
                  check_top=args.check_top if args.check_top != 25 else 300, proxies=cfg.proxies)
        log.info("report -> %s", md)
    if args.command == "export":
        export_posts(conn, args.file, args.max_posts)
    if args.command == "import-ideas":
        import_ideas(conn, args.file)
    if args.command == "mark-analyzed":
        # позначає всі пости з файлу експорту як проаналізовані (і ті, де ідей не знайшлося)
        with open(args.file, encoding="utf-8") as f:
            ids = [p["id"] for p in json.load(f)]
        conn.executemany("UPDATE posts SET analyzed = 1 WHERE id = ?", [(i,) for i in ids])
        conn.commit()
        log.info("marked %d posts analyzed", len(ids))

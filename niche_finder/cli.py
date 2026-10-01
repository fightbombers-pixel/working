import argparse
import logging
from collections import Counter

from . import db
from .config import Config
from .net import HttpClient
from .sources import google_trends, pinterest
from .sources.reddit import RedditSource
from .sources.threads import ThreadsSource
from .sources.x import XSource

log = logging.getLogger("niche_finder")


def collect(cfg: Config, conn, limit: int) -> None:
    http = HttpClient(cfg.proxies, user_agent=cfg.reddit_user_agent)
    reddit = RedditSource(http, cfg.reddit_client_id, cfg.reddit_client_secret)
    x = XSource(http, cfg.x_bearer_token, cfg.apify_token, cfg.apify_x_actor)
    threads = ThreadsSource(http, cfg.threads_access_token, cfg.apify_token, cfg.apify_threads_actor)

    sources = [("reddit", reddit.search)]
    if x.enabled:
        sources.append(("x", x.search))
    else:
        log.info("X: немає X_BEARER_TOKEN або APIFY_TOKEN+APIFY_X_ACTOR — пропускаю")
    if threads.enabled:
        sources.append(("threads", threads.search))
    else:
        log.info("Threads: немає THREADS_ACCESS_TOKEN або APIFY_TOKEN+APIFY_THREADS_ACTOR — пропускаю")

    for name, search in sources:
        for q in cfg.pain_queries:
            try:
                posts = search(q, limit=limit)
            except Exception as exc:  # одне джерело не повинно валити весь збір
                log.error("%s %s: %s", name, q, exc)
                continue
            new = db.upsert_posts(conn, posts)
            log.info("%s %s: %d posts, %d new", name, q, len(posts), new)

    # Ріст сабредитів: знімок кількості підписників раз на день
    subs = Counter(r["community"] for r in conn.execute(
        "SELECT community FROM posts WHERE source='reddit' AND fetched_at >= datetime('now','-7 days')"))
    for sub, _ in subs.most_common(30):
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
    else:
        log.info("Pinterest: немає PINTEREST_ACCESS_TOKEN — пропускаю")

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


def viral(cfg: Config, conn, channels: list[str], limit: int, top: int, check: bool, lang: str) -> None:
    from .sources.youtube import YouTubeSource
    from .viral import analyze_channel, write_report

    yt = YouTubeSource(HttpClient(cfg.proxies, min_interval=0.2), cfg.youtube_api_key)
    if not yt.enabled:
        raise SystemExit("viral: потрібен YOUTUBE_API_KEY у .env")
    if not channels:
        raise SystemExit("viral: вкажіть канал, напр. python -m niche_finder viral @UselessMoney")
    for ref in channels:
        channel, result, outliers = analyze_channel(yt, conn, ref, cfg.anthropic_model, limit=limit, top=top,
                                                    check=check, lang=lang)
        path = write_report(channel, result, outliers, cfg.reports_dir)
        log.info("%s: %d formulas -> %s", channel["title"], len(result["patterns"]), path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="niche_finder", description="Пошук бізнес-ніш у соцмережах і трендах")
    parser.add_argument("command", choices=["collect", "analyze", "trends", "report", "run", "viral"])
    parser.add_argument("channels", nargs="*", help="для viral: @handle, URL або id каналів")
    parser.add_argument("--limit", type=int, default=200, help="постів на один запит у кожному джерелі")
    parser.add_argument("--max-posts", type=int, default=1200, help="скільки нових постів аналізувати за запуск")
    parser.add_argument("--check-top", type=int, default=25, help="скільки ключових слів перевірити в Google Trends")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--min-mentions", type=int, default=2)
    parser.add_argument("--videos", type=int, default=200, help="viral: скільки останніх відео каналу брати")
    parser.add_argument("--top", type=int, default=25, help="viral: скільки outlier-відео давати Claude")
    parser.add_argument("--no-check", action="store_true", help="viral: не перевіряти формули на інших каналах")
    parser.add_argument("--lang", default="Ukrainian", help="viral: мова пояснень у звіті")
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
    if args.command == "viral":
        viral(cfg, conn, args.channels, args.videos, args.top, not args.no_check, args.lang)

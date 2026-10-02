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
        from .sources.ytdlp import YtDlpSource

        log.info("viral: немає YOUTUBE_API_KEY — беру дані через yt-dlp (без перевірки на інших каналах)")
        yt = YtDlpSource()
    if not channels:
        raise SystemExit("viral: вкажіть канал, напр. python -m niche_finder viral @UselessMoney")
    for ref in channels:
        channel, result, outliers = analyze_channel(yt, conn, ref, cfg.anthropic_model, limit=limit, top=top,
                                                    check=check, lang=lang)
        path = write_report(channel, result, outliers, cfg.reports_dir)
        log.info("%s: %d formulas -> %s", channel["title"], len(result["patterns"]), path)


def formats(cfg: Config, conn, channels: list[str], limit: int, lang: str, exclude: set[str]) -> None:
    from .formats import analyze_formats, write_formats_report
    from .sources.ytdlp import YtDlpSource

    if not channels:
        raise SystemExit("formats: вкажіть канал, напр. python -m niche_finder formats @MarcusExplainsHQ")
    for ref in channels:
        channel, found = analyze_formats(YtDlpSource(), conn, ref, cfg.anthropic_model, limit=limit, lang=lang,
                                         exclude=exclude)
        path = write_formats_report(channel, found, cfg.reports_dir)
        log.info("%s: %d formats -> %s", channel["title"], len(found), path)


def discover_cmd(cfg: Config) -> None:
    from .discover import discover, write_discover_report

    results = discover()
    path = write_discover_report(results, cfg.reports_dir)
    log.info("%d formats -> %s", len(results), path)


def radar_cmd(cfg: Config, days: int, candidates: int, rising: bool = True, pinterest: bool = False) -> None:
    import os

    from datetime import date

    from .radar import run_radar, write_radar_report

    from .radar import rising_topics
    from .signals import collect_all

    http = HttpClient(cfg.proxies, min_interval=1.2, max_retries=6)
    signals = collect_all(http, rising=rising, proxies=cfg.proxies or None, apify_token=cfg.apify_token,
                          apify_pinterest_actor=os.getenv("APIFY_PINTEREST_ACTOR", "") if pinterest else "",
                          threads_token=cfg.threads_access_token, apify_threads_actor=cfg.apify_threads_actor)
    topics = run_radar(http, days=days, candidates=candidates, signals=signals, deep=20)
    trends = {}
    for s in signals:
        if s["source"] == "google_trends":
            trends.setdefault(s["geo"], []).append((s["text"], s["value"]))
    rising_rows = rising_topics(http, signals) if rising else []
    from datetime import timedelta

    from .radar import TRACKED, deep_check

    tracked = [deep_check(http, dict(t), date.today() - timedelta(days=1), signals) for t in TRACKED]
    path = write_radar_report(topics, trends, cfg.reports_dir / f"radar_{date.today().isoformat()}.html",
                              rising=rising_rows, signals=signals, tracked=tracked)
    log.info("%d topics -> %s", len(topics), path)


def outliers_cmd(cfg: Config, queries: list[str]) -> None:
    from .outliers import QUERIES, find_outliers, write_outliers_report

    res = find_outliers(queries or QUERIES)
    path = write_outliers_report(res, cfg.reports_dir)
    log.info("%d аутлаєрів, %d малих каналів -> %s", len(res["outliers"]), len(res["small"]), path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="niche_finder", description="Пошук бізнес-ніш у соцмережах і трендах")
    parser.add_argument("command", choices=["collect", "analyze", "trends", "report", "run", "viral", "formats", "discover", "radar", "outliers"])
    parser.add_argument("channels", nargs="*", help="для viral/formats: @handle, URL або id каналів; "
                        "для outliers: власні пошукові запити (за замовчуванням — набір розслідувань/драм)")
    parser.add_argument("--limit", type=int, default=200, help="постів на один запит у кожному джерелі")
    parser.add_argument("--max-posts", type=int, default=1200, help="скільки нових постів аналізувати за запуск")
    parser.add_argument("--check-top", type=int, default=25, help="скільки ключових слів перевірити в Google Trends")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--min-mentions", type=int, default=2)
    parser.add_argument("--videos", type=int, default=200, help="viral: скільки останніх відео каналу брати")
    parser.add_argument("--top", type=int, default=25, help="viral: скільки outlier-відео давати Claude")
    parser.add_argument("--no-check", action="store_true", help="viral: не перевіряти формули на інших каналах")
    parser.add_argument("--lang", default="Ukrainian", help="viral: мова пояснень у звіті")
    parser.add_argument("--exclude", default="", help="formats: власні канали через кому (назви або id), "
                        "щоб їхні відео не рахувались як попит, напр. 'Lume,TrueCrimeVault,ago,Lumicus'")
    parser.add_argument("--radar-days", type=int, default=7, help="radar: скільки днів топу Вікіпедії брати")
    parser.add_argument("--no-rising", action="store_true", help="radar: без зростаючих запитів Google Trends")
    parser.add_argument("--pinterest", action="store_true", help="radar: Pinterest через Apify (платно; APIFY_TOKEN + APIFY_PINTEREST_ACTOR)")
    parser.add_argument("--radar-candidates", type=int, default=60, help="radar: скільки кандидатів перевіряти")
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
    if args.command == "radar":
        radar_cmd(cfg, args.radar_days, args.radar_candidates, rising=not args.no_rising, pinterest=args.pinterest)
    if args.command == "outliers":
        outliers_cmd(cfg, args.channels)
    if args.command == "discover":
        discover_cmd(cfg)
    if args.command == "formats":
        formats(cfg, conn, args.channels, args.videos, args.lang,
                {e for e in args.exclude.split(",") if e.strip()})
    if args.command == "viral":
        viral(cfg, conn, args.channels, args.videos, args.top, not args.no_check, args.lang)

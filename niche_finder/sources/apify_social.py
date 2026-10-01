"""Reddit, X, Threads і Pinterest через готові Apify-актори — один APIFY_TOKEN на все.

Apify сам підставляє резидентні проксі, тому працює навіть з хмарних серверів,
де Reddit/X блокують прямі запити. Актори можна замінити через env (APIFY_*_ACTOR),
але тоді може знадобитися правка build_input/parse під їхній формат.
"""
import logging
from datetime import datetime

from ..net import HttpClient
from .apify import first, run_actor

log = logging.getLogger(__name__)

# Обрані за ціною (pay-per-result, тариф Free, жовтень 2026):
# reddit ~$1.5 / 1000 постів, x ~$0.4 / 1000 твітів, threads ~$2.5 / 1000 + $0.02 за запуск,
# pinterest trends ~$1.15 / 1000 трендів
DEFAULT_ACTORS = {
    "reddit": "fatihtahta~reddit-scraper-search-fast",
    "x": "apidojo~tweet-scraper",
    "threads": "futurizerush~meta-threads-scraper",
    "pinterest_trends": "automation-lab~pinterest-trends-scraper",
}

# Скільки постів брати на один пошуковий запит — головний регулятор вартості.
# 10 запитів × (50 reddit + 100 x + 30 threads) ≈ $2 за прогін на тарифі Free.
DEFAULT_CAPS = {"reddit": 50, "x": 100, "threads": 30}


def build_input(platform: str, query: str, limit: int) -> dict:
    if platform == "reddit":
        return {"queries": [query], "sort": "new", "timeframe": "month", "maxPosts": limit,
                "scrapeComments": False, "includeNsfw": False, "strictSearch": True}
    if platform == "x":
        return {"searchTerms": [f"{query} -filter:retweets"], "maxItems": limit,
                "sort": "Latest", "tweetLanguage": "en"}
    if platform == "threads":
        return {"mode": "search", "keywords": [query.strip('"')], "search_filter": "recent",
                "max_posts": limit, "search_language": "english"}
    raise ValueError(platform)


def _ts(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) / (1000 if value > 1e12 else 1)
    for parse in (lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")),
                  lambda v: datetime.strptime(v, "%a %b %d %H:%M:%S %z %Y")):
        try:
            return parse(value).timestamp()
        except (ValueError, TypeError):
            continue
    return None


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _reddit_url(value) -> str | None:
    if not value:
        return None
    return value if str(value).startswith("http") else f"https://www.reddit.com{value}"


def parse(platform: str, item: dict, query: str) -> dict | None:
    if platform == "reddit":
        if first(item, "kind", "dataType", default="post") != "post":
            return None
        pid = first(item, "id", "parsedId")
        title = first(item, "title", default="")
        if not pid or not title:
            return None
        return {"id": f"reddit:{str(pid).removeprefix('t3_')}", "source": "reddit",
                "community": first(item, "subreddit", "parsedCommunityName", "communityName"),
                "title": title, "body": (first(item, "body", "selftext", "text", default="") or "")[:4000],
                "score": _int(first(item, "score", "upVotes", "ups")),
                "comments": _int(first(item, "num_comments", "numberOfComments", "numComments")),
                "url": _reddit_url(first(item, "permalink", "postUrl", "url")),
                "created_utc": _ts(first(item, "created_utc", "createdAt")),
                "query": query}

    if platform == "x":
        tid = first(item, "id", "id_str")
        text = first(item, "text", "fullText", "full_text")
        if not tid or not isinstance(text, str):
            return None
        return {"id": f"x:{tid}", "source": "x", "community": None, "title": "", "body": text,
                "score": _int(first(item, "likeCount", "favorite_count")) + _int(first(item, "retweetCount", "retweet_count")),
                "comments": _int(first(item, "replyCount", "reply_count")),
                "url": first(item, "url", "twitterUrl", default=f"https://x.com/i/status/{tid}"),
                "created_utc": _ts(first(item, "createdAt", "created_at")), "query": query}

    if platform == "threads":
        if first(item, "record_type", default="post") != "post":
            return None
        pid = first(item, "post_code", "id", "pk", "code", "post.id", "post.code")
        text = first(item, "text_content", "text", "caption.text", "post.caption.text", "post.text", "caption")
        if not pid or not isinstance(text, str):
            return None
        code = first(item, "post_code", "code", "post.code")
        user = first(item, "username", "user.username", "post.user.username", "author.username")
        url = first(item, "post_url", "url", "permalink", "post.url")
        if not url and code and user:
            url = f"https://www.threads.net/@{user}/post/{code}"
        return {"id": f"threads:{pid}", "source": "threads", "community": None, "title": "", "body": text,
                "score": _int(first(item, "like_count", "likeCount", "post.like_count", "likes")),
                "comments": _int(first(item, "reply_count", "replyCount", "text_post_app_info.direct_reply_count",
                                       "post.text_post_app_info.direct_reply_count", "replies")),
                "url": url, "created_utc": _ts(first(item, "created_at_timestamp", "timestamp", "taken_at", "post.taken_at",
                                         "created_at", "createdAt")),
                "query": query}
    raise ValueError(platform)


class ApifySocial:
    def __init__(self, http: HttpClient, token: str, actors: dict[str, str] | None = None):
        self.http = http
        self.token = token
        self.actors = {**DEFAULT_ACTORS, **{k: v for k, v in (actors or {}).items() if v}}
        self.caps = dict(DEFAULT_CAPS)

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def searcher(self, platform: str):
        def search(query: str, limit: int = 100) -> list[dict]:
            limit = min(limit, self.caps.get(platform, limit))
            items = run_actor(self.http, self.token, self.actors[platform], build_input(platform, query, limit))
            posts = [p for p in (parse(platform, i, query) for i in items) if p]
            if items and not posts:
                log.warning("%s: актор повернув %d елементів, але жоден не розпізнано. Ключі першого: %s",
                            platform, len(items), sorted(items[0].keys())[:25])
            return posts
        return search

    def pinterest_trends(self, countries: list[str], trend_types: list[str] | None = None,
                         per_country: int = 50) -> list[dict]:
        items = run_actor(self.http, self.token, self.actors["pinterest_trends"], {
            "countries": countries, "trendTypes": trend_types or ["growing"],
            "maxResultsPerCountry": per_country, "lookbackWindow": "90D",
        })
        out = []
        for i in items:
            kw = first(i, "keyword", "term", "query")
            if not kw:
                continue
            out.append({"country": first(i, "country", "region", "market", default="?"),
                        "keyword": str(kw).lower().strip(),
                        "growth_wow": first(i, "weeklyChange", "pct_growth_wow", "wow"),
                        "growth_mom": first(i, "monthlyChange", "pct_growth_mom", "mom"),
                        "growth_yoy": first(i, "yearlyChange", "pct_growth_yoy", "yoy")})
        return out

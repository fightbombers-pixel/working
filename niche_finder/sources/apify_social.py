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

DEFAULT_ACTORS = {
    "reddit": "trudax~reddit-scraper-lite",
    "x": "apidojo~tweet-scraper",
    "threads": "igview-owner~threads-search-scraper",
    "pinterest_trends": "automation-lab~pinterest-trends-scraper",
}


def build_input(platform: str, query: str, limit: int) -> dict:
    if platform == "reddit":
        return {"searches": [query.strip('"')], "searchPosts": True, "searchComments": False,
                "searchCommunities": False, "searchUsers": False, "skipComments": True,
                "sort": "new", "time": "month", "maxItems": limit, "maxPostCount": limit,
                "includeNSFW": False,
                "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]}}
    if platform == "x":
        return {"searchTerms": [f"{query} -filter:retweets"], "maxItems": limit,
                "sort": "Latest", "tweetLanguage": "en"}
    if platform == "threads":
        return {"searchQuery": query.strip('"'), "sort": "recent", "maxPosts": limit}
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


def parse(platform: str, item: dict, query: str) -> dict | None:
    if platform == "reddit":
        if first(item, "dataType", default="post") != "post":
            return None
        pid = first(item, "parsedId", "id")
        title = first(item, "title", default="")
        if not pid or not title:
            return None
        return {"id": f"reddit:{str(pid).removeprefix('t3_')}", "source": "reddit",
                "community": first(item, "parsedCommunityName", "communityName", "subreddit"),
                "title": title, "body": (first(item, "body", "selftext", "text", default="") or "")[:4000],
                "score": _int(first(item, "upVotes", "score", "ups")),
                "comments": _int(first(item, "numberOfComments", "numComments", "num_comments")),
                "url": first(item, "url", "permalink"), "created_utc": _ts(first(item, "createdAt", "created_utc")),
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
        pid = first(item, "id", "pk", "code", "post.id", "post.code")
        text = first(item, "text", "caption.text", "post.caption.text", "post.text", "caption")
        if not pid or not isinstance(text, str):
            return None
        code = first(item, "code", "post.code")
        user = first(item, "username", "user.username", "post.user.username", "author.username")
        url = first(item, "url", "permalink", "post.url")
        if not url and code and user:
            url = f"https://www.threads.net/@{user}/post/{code}"
        return {"id": f"threads:{pid}", "source": "threads", "community": None, "title": "", "body": text,
                "score": _int(first(item, "likeCount", "like_count", "post.like_count", "likes")),
                "comments": _int(first(item, "replyCount", "reply_count", "text_post_app_info.direct_reply_count",
                                       "post.text_post_app_info.direct_reply_count", "replies")),
                "url": url, "created_utc": _ts(first(item, "timestamp", "taken_at", "post.taken_at", "createdAt")),
                "query": query}
    raise ValueError(platform)


class ApifySocial:
    def __init__(self, http: HttpClient, token: str, actors: dict[str, str] | None = None):
        self.http = http
        self.token = token
        self.actors = {**DEFAULT_ACTORS, **{k: v for k, v in (actors or {}).items() if v}}

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def searcher(self, platform: str):
        def search(query: str, limit: int = 100) -> list[dict]:
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

"""X (Twitter): офіційний API v2 (recent search) або Apify-актор як дешевша альтернатива."""
import logging
from datetime import datetime, timezone

from ..net import HttpClient
from .apify import first, run_actor

log = logging.getLogger(__name__)


class XSource:
    def __init__(self, http: HttpClient, bearer_token: str = "", apify_token: str = "",
                 apify_actor: str = "", apify_query_field: str = "searchTerms"):
        self.http = http
        self.bearer_token = bearer_token
        self.apify_token = apify_token
        self.apify_actor = apify_actor
        self.apify_query_field = apify_query_field

    @property
    def enabled(self) -> bool:
        return bool(self.bearer_token or (self.apify_token and self.apify_actor))

    def search(self, query: str, limit: int = 200) -> list[dict]:
        # -is:retweet та lang:en зменшують шум; для інших мов приберіть lang
        q = f"{query} -is:retweet lang:en"
        if self.bearer_token:
            return self._search_official(q, limit)
        return self._search_apify(q, limit)

    def _search_official(self, query: str, limit: int) -> list[dict]:
        posts, next_token = [], None
        while len(posts) < limit:
            params = {"query": query, "max_results": 100,
                      "tweet.fields": "created_at,public_metrics,author_id"}
            if next_token:
                params["next_token"] = next_token
            resp = self.http.get("https://api.x.com/2/tweets/search/recent", params=params,
                                 headers={"Authorization": f"Bearer {self.bearer_token}"})
            resp.raise_for_status()
            data = resp.json()
            posts += [parse_official(t, query) for t in data.get("data", [])]
            next_token = data.get("meta", {}).get("next_token")
            if not next_token:
                break
        return posts[:limit]

    def _search_apify(self, query: str, limit: int) -> list[dict]:
        items = run_actor(self.http, self.apify_token, self.apify_actor,
                          {self.apify_query_field: [query], "maxItems": limit, "sort": "Latest"})
        return [p for p in (parse_apify(i, query) for i in items) if p]


def parse_official(t: dict, query: str) -> dict:
    m = t.get("public_metrics", {})
    created = t.get("created_at")
    return {
        "id": f"x:{t['id']}",
        "source": "x",
        "community": None,
        "title": "",
        "body": t.get("text", ""),
        "score": m.get("like_count", 0) + m.get("retweet_count", 0),
        "comments": m.get("reply_count", 0),
        "url": f"https://x.com/i/status/{t['id']}",
        "created_utc": _ts(created),
        "query": query,
    }


def parse_apify(i: dict, query: str) -> dict | None:
    tid = first(i, "id", "id_str", "tweetId")
    text = first(i, "text", "fullText", "full_text")
    if not tid or not text:
        return None
    likes = first(i, "likeCount", "favorite_count", "likes", default=0) or 0
    rts = first(i, "retweetCount", "retweet_count", "retweets", default=0) or 0
    return {
        "id": f"x:{tid}",
        "source": "x",
        "community": None,
        "title": "",
        "body": text,
        "score": int(likes) + int(rts),
        "comments": int(first(i, "replyCount", "reply_count", "replies", default=0) or 0),
        "url": first(i, "url", "twitterUrl", default=f"https://x.com/i/status/{tid}"),
        "created_utc": _ts(first(i, "createdAt", "created_at")),
        "query": query,
    }


def _ts(value) -> float | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%a %b %d %H:%M:%S %z %Y"):
        try:
            dt = datetime.strptime(value, fmt)
            return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
        except ValueError:
            continue
    return None

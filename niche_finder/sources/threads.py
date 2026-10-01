"""Threads: офіційний keyword search (Threads API) або Apify-актор.

Офіційний пошук потребує дозволу threads_keyword_search (app review у Meta);
у відповіді немає лайків чужих постів, тому score=0 — важить сама кількість згадок.
"""
from datetime import datetime

from ..net import HttpClient
from .apify import first, run_actor


class ThreadsSource:
    def __init__(self, http: HttpClient, access_token: str = "", apify_token: str = "",
                 apify_actor: str = "", apify_query_field: str = "searchTerms"):
        self.http = http
        self.access_token = access_token
        self.apify_token = apify_token
        self.apify_actor = apify_actor
        self.apify_query_field = apify_query_field

    @property
    def enabled(self) -> bool:
        return bool(self.access_token or (self.apify_token and self.apify_actor))

    def search(self, query: str, limit: int = 100) -> list[dict]:
        if self.access_token:
            return self._search_official(query, limit)
        items = run_actor(self.http, self.apify_token, self.apify_actor,
                          {self.apify_query_field: [query], "maxItems": limit})
        return [p for p in (parse_apify(i, query) for i in items) if p]

    def _search_official(self, query: str, limit: int) -> list[dict]:
        posts, after = [], None
        while len(posts) < limit:
            params = {"q": query.strip('"'), "search_type": "RECENT", "limit": min(100, limit),
                      "fields": "id,text,permalink,timestamp,username",
                      "access_token": self.access_token}
            if after:
                params["after"] = after
            resp = self.http.get("https://graph.threads.net/v1.0/keyword_search", params=params)
            resp.raise_for_status()
            data = resp.json()
            posts += [parse_official(t, query) for t in data.get("data", []) if t.get("text")]
            after = data.get("paging", {}).get("cursors", {}).get("after")
            if not after or not data.get("data"):
                break
        return posts[:limit]


def parse_official(t: dict, query: str) -> dict:
    return {
        "id": f"threads:{t['id']}",
        "source": "threads",
        "community": None,
        "title": "",
        "body": t.get("text", ""),
        "score": 0,
        "comments": 0,
        "url": t.get("permalink"),
        "created_utc": _ts(t.get("timestamp")),
        "query": query,
    }


def parse_apify(i: dict, query: str) -> dict | None:
    pid = first(i, "id", "code", "post.id")
    text = first(i, "text", "caption.text", "post.caption.text", "caption")
    if not isinstance(text, str):
        text = None
    if not pid or not text:
        return None
    return {
        "id": f"threads:{pid}",
        "source": "threads",
        "community": None,
        "title": "",
        "body": text,
        "score": int(first(i, "likeCount", "like_count", "likes", default=0) or 0),
        "comments": int(first(i, "replyCount", "reply_count", "replies", default=0) or 0),
        "url": first(i, "url", "permalink"),
        "created_utc": _ts(first(i, "timestamp", "createdAt", "taken_at")),
        "query": query,
    }


def _ts(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None

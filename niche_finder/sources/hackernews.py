"""Hacker News через публічний Algolia API — без ключів і без проксі.

Аудиторія: розробники, фаундери, технарі — добре для SaaS/інструментів.
"""
import re
import time
from html import unescape

from ..net import HttpClient


class HackerNewsSource:
    def __init__(self, http: HttpClient):
        self.http = http

    def search(self, query: str, limit: int = 200, days: int = 30) -> list[dict]:
        since = int(time.time()) - days * 86400
        posts, page = [], 0
        while len(posts) < limit:
            resp = self.http.get("https://hn.algolia.com/api/v1/search_by_date", params={
                "query": query, "tags": "(story,comment)", "hitsPerPage": 100, "page": page,
                "numericFilters": f"created_at_i>{since}",
            })
            resp.raise_for_status()
            data = resp.json()
            posts += [p for p in (parse_hit(h, query) for h in data.get("hits", [])) if p]
            page += 1
            if page >= data.get("nbPages", 0):
                break
        return posts[:limit]


def _clean(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", html or "")).strip()


def parse_hit(h: dict, query: str) -> dict | None:
    body = _clean(h.get("comment_text") or h.get("story_text") or "")
    title = h.get("title") or ""
    if not body and not title:
        return None
    return {
        "id": f"hn:{h['objectID']}",
        "source": "hackernews",
        "community": None,
        "title": title,
        "body": body[:4000],
        "score": h.get("points") or 0,
        "comments": h.get("num_comments") or 0,
        "url": f"https://news.ycombinator.com/item?id={h['objectID']}",
        "created_utc": h.get("created_at_i"),
        "query": query,
    }

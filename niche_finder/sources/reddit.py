"""Reddit: пошук постів з фразами болю + статистика сабредитів.

З REDDIT_CLIENT_ID/SECRET працює через OAuth (oauth.reddit.com, вищі ліміти),
без них — через публічні .json-ендпоінти.
"""
import logging
import time

from ..net import HttpClient

log = logging.getLogger(__name__)


class RedditSource:
    def __init__(self, http: HttpClient, client_id: str = "", client_secret: str = ""):
        self.http = http
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None
        self._token_exp = 0.0

    @property
    def base(self) -> str:
        return "https://oauth.reddit.com" if self.client_id else "https://www.reddit.com"

    def _headers(self) -> dict:
        if not self.client_id:
            return {}
        if not self._token or time.time() > self._token_exp - 60:
            resp = self.http.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "client_credentials"},
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data["access_token"]
            self._token_exp = time.time() + data.get("expires_in", 3600)
        return {"Authorization": f"bearer {self._token}"}

    def _get(self, path: str, params: dict) -> dict:
        suffix = "" if self.client_id else ".json"
        resp = self.http.get(f"{self.base}{path}{suffix}", params={**params, "raw_json": 1}, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def search(self, query: str, time_filter: str = "week", limit: int = 300, subreddit: str = "all") -> list[dict]:
        posts, after = [], None
        while len(posts) < limit:
            params = {"q": query, "sort": "new", "t": time_filter, "limit": 100, "restrict_sr": subreddit != "all"}
            if after:
                params["after"] = after
            data = self._get(f"/r/{subreddit}/search", params)["data"]
            posts += [parse_post(c["data"], query) for c in data["children"] if c["kind"] == "t3"]
            after = data.get("after")
            if not after:
                break
        return posts[:limit]

    def subscribers(self, subreddit: str) -> int | None:
        try:
            return self._get(f"/r/{subreddit}/about", {})["data"].get("subscribers")
        except Exception as exc:  # приватні/забанені сабредити
            log.warning("about r/%s failed: %s", subreddit, exc)
            return None


def parse_post(d: dict, query: str) -> dict:
    return {
        "id": f"reddit:{d['id']}",
        "source": "reddit",
        "community": d.get("subreddit"),
        "title": d.get("title", ""),
        "body": (d.get("selftext") or "")[:4000],
        "score": d.get("score", 0),
        "comments": d.get("num_comments", 0),
        "url": f"https://www.reddit.com{d.get('permalink', '')}",
        "created_utc": d.get("created_utc"),
        "query": query,
    }

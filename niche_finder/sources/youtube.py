"""YouTube Data API v3: канал, його відео зі статистикою і пошук схожих відео на інших каналах.

Квота за замовчуванням 10 000 одиниць на день: channels/playlistItems/videos — 1 одиниця за запит
(до 50 елементів), search — 100 одиниць. Аналіз каналу на 200 відео коштує ~10 одиниць.
"""
import re
from datetime import datetime, timezone

from ..net import HttpClient

API = "https://www.googleapis.com/youtube/v3"
SHORTS_MAX_SECONDS = 180  # Shorts бувають до 3 хвилин

_DURATION = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_duration(iso: str) -> int:
    m = _DURATION.fullmatch(iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def channel_ref(value: str) -> dict:
    """@handle, URL каналу або UC-id -> параметри для channels.list."""
    value = value.strip().rstrip("/")
    value = re.sub(r"/(videos|shorts|streams|featured|about)$", "", value)
    if m := re.search(r"/channel/(UC[\w-]{22})", value) or re.fullmatch(r"(UC[\w-]{22})", value):
        return {"id": m.group(1)}
    if m := re.search(r"@([\w.\-]+)$", value):
        return {"forHandle": "@" + m.group(1)}
    return {"forHandle": "@" + value.split("/")[-1]}


def parse_video(item: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    sn, st, cd = item.get("snippet", {}), item.get("statistics", {}), item.get("contentDetails", {})
    published = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
    duration = parse_duration(cd.get("duration", ""))
    thumbs = sn.get("thumbnails", {})
    thumb = next((thumbs[k]["url"] for k in ("maxres", "high", "medium", "default") if k in thumbs), "")
    return {
        "id": item["id"],
        "channel_id": sn.get("channelId", ""),
        "channel_title": sn.get("channelTitle", ""),
        "title": sn.get("title", ""),
        "description": (sn.get("description") or "")[:500],
        "tags": ",".join(sn.get("tags", [])[:15]),
        "published_at": published.isoformat(),
        "age_days": max(0.0, (now - published).total_seconds() / 86400),
        "duration_s": duration,
        "is_short": int(0 < duration <= SHORTS_MAX_SECONDS),
        "views": int(st.get("viewCount", 0)),
        "likes": int(st.get("likeCount", 0)),
        "comments": int(st.get("commentCount", 0)),
        "thumbnail": thumb,
        "url": f"https://www.youtube.com/watch?v={item['id']}",
    }


class YouTubeSource:
    def __init__(self, http: HttpClient, api_key: str):
        self.http = http
        self.api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, **params) -> dict:
        resp = self.http.get(f"{API}/{path}", params={"key": self.api_key, **params})
        resp.raise_for_status()
        return resp.json()

    def channel(self, ref: str) -> dict:
        data = self._get("channels", part="snippet,statistics,contentDetails", **channel_ref(ref))
        if not data.get("items"):
            raise ValueError(f"канал не знайдено: {ref}")
        c = data["items"][0]
        return {
            "id": c["id"],
            "title": c["snippet"]["title"],
            "handle": c["snippet"].get("customUrl", ""),
            "description": (c["snippet"].get("description") or "")[:1000],
            "subscribers": int(c["statistics"].get("subscriberCount", 0)),
            "uploads": c["contentDetails"]["relatedPlaylists"]["uploads"],
        }

    def videos(self, ids: list[str]) -> list[dict]:
        out = []
        for start in range(0, len(ids), 50):
            data = self._get("videos", part="snippet,statistics,contentDetails", id=",".join(ids[start:start + 50]))
            out += [parse_video(item) for item in data.get("items", [])]
        return out

    def channel_videos(self, uploads_playlist: str, limit: int = 200) -> list[dict]:
        ids, token = [], None
        while len(ids) < limit:
            params = {"part": "contentDetails", "playlistId": uploads_playlist, "maxResults": 50}
            if token:
                params["pageToken"] = token
            data = self._get("playlistItems", **params)
            ids += [i["contentDetails"]["videoId"] for i in data.get("items", [])]
            token = data.get("nextPageToken")
            if not token:
                break
        return self.videos(ids[:limit])

    def subscribers(self, channel_ids: list[str]) -> dict[str, int]:
        out = {}
        ids = list(dict.fromkeys(channel_ids))
        for start in range(0, len(ids), 50):
            data = self._get("channels", part="statistics", id=",".join(ids[start:start + 50]))
            out |= {c["id"]: int(c["statistics"].get("subscriberCount", 0)) for c in data.get("items", [])}
        return out

    def search(self, query: str, limit: int = 25, published_after: str | None = None) -> list[dict]:
        """Пошук відео (100 одиниць квоти) + їх статистика і підписники каналів."""
        params = {"part": "id", "type": "video", "q": query, "maxResults": min(50, limit), "order": "viewCount"}
        if published_after:
            params["publishedAfter"] = published_after
        data = self._get("search", **params)
        videos = self.videos([i["id"]["videoId"] for i in data.get("items", [])])
        subs = self.subscribers([v["channel_id"] for v in videos])
        for v in videos:
            v["channel_subscribers"] = subs.get(v["channel_id"], 0)
        return videos

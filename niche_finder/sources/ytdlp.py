"""YouTube без API-ключа через yt-dlp: відео каналу і пошук.

Плоский режим (extract_flat) — один запит на сторінку, без завантаження кожного відео, тому швидко.
Мінус: немає дати публікації, тож порядок відео береться з позиції на сторінці каналу (новіші першими),
а SKIP_NEWEST найсвіжіших пропускаються, бо ще набирають перегляди.
"""
from .youtube import SHORTS_MAX_SECONDS, channel_ref

SKIP_NEWEST = 3


def _ydl(limit: int | None = None):
    import yt_dlp

    opts = {"extract_flat": "in_playlist", "quiet": True, "no_warnings": True, "skip_download": True}
    if limit:
        opts["playlistend"] = limit
    return yt_dlp.YoutubeDL(opts)


def channel_url(ref: str) -> str:
    r = channel_ref(ref)
    base = f"https://www.youtube.com/channel/{r['id']}" if "id" in r else f"https://www.youtube.com/{r['forHandle']}"
    return base + "/videos"


def parse_entry(e: dict, position: int = 0, info: dict | None = None) -> dict:
    info = info or {}
    duration = int(e.get("duration") or 0)
    return {
        "id": e["id"],
        "channel_id": e.get("channel_id") or info.get("channel_id", ""),
        "channel_title": e.get("channel") or info.get("channel", ""),
        "title": e.get("title") or "",
        "description": (e.get("description") or "")[:500],
        "tags": "",
        "published_at": "",
        "order": -position,  # новіші першими на сторінці -> більший position = старіше
        "age_days": 0.0 if position < SKIP_NEWEST else 365.0,
        "duration_s": duration,
        "is_short": int(0 < duration <= SHORTS_MAX_SECONDS),
        "views": int(e.get("view_count") or 0),
        "likes": 0,
        "comments": 0,
        "thumbnail": f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg",
        "url": f"https://www.youtube.com/watch?v={e['id']}",
    }


def _ydl_full():
    import yt_dlp

    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True})


class YtDlpSource:
    enabled = True

    def channel(self, ref: str, limit: int = 200) -> tuple[dict, list[dict]]:
        with _ydl(limit) as ydl:
            info = ydl.extract_info(channel_url(ref), download=False)
        entries = [e for e in info.get("entries") or [] if e and e.get("id")]
        channel = {
            "id": info.get("channel_id", ""),
            "title": info.get("channel") or info.get("title", ""),
            "handle": info.get("uploader_id", ""),
            "description": (info.get("description") or "")[:1000],
            "subscribers": int(info.get("channel_follower_count") or 0),
        }
        videos = [parse_entry(e, i, info) for i, e in enumerate(entries)]
        # Для колаб-відео (кілька авторів) плоский режим не віддає переглядів — а це часто найбільші хіти.
        missing = [v for v in videos if not v["views"]]
        if missing:
            with _ydl_full() as ydl:
                for v in missing:
                    try:
                        full = ydl.extract_info(v["url"], download=False, process=False)
                        v["views"] = int(full.get("view_count") or 0)
                    except Exception:  # недоступне відео — лишається з 0 і не бере участі в порівнянні
                        pass
        return channel, [v for v in videos if v["views"]]

    def search(self, query: str, limit: int = 30) -> list[dict]:
        with _ydl() as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        return [parse_entry(e) for e in info.get("entries") or [] if e and e.get("id")]

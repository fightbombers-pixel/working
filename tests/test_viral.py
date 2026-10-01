import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from niche_finder import db
from niche_finder.sources.youtube import YouTubeSource, channel_ref, parse_duration, parse_video
from niche_finder.viral import analyze_channel, outlier_scores, pattern_score, write_report

NOW = datetime.now(timezone.utc)


def video_item(vid, views, days_ago, duration="PT10M", channel="UCsrc", title=None):
    return {"id": vid,
            "snippet": {"channelId": channel, "channelTitle": "c", "title": title or f"video {vid}",
                        "publishedAt": (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "thumbnails": {"high": {"url": f"https://i.ytimg.com/vi/{vid}/hq.jpg"}}},
            "statistics": {"viewCount": str(views)},
            "contentDetails": {"duration": duration}}


def test_parsers():
    assert parse_duration("PT1H2M3S") == 3723 and parse_duration("PT45S") == 45 and parse_duration("") == 0
    assert channel_ref("https://www.youtube.com/@UselessMoney/videos") == {"forHandle": "@UselessMoney"}
    assert channel_ref("UselessMoney") == {"forHandle": "@UselessMoney"}
    cid = "UC" + "a" * 22
    assert channel_ref(f"https://youtube.com/channel/{cid}") == {"id": cid}
    v = parse_video(video_item("s1", 100, 3, duration="PT59S"))
    assert v["is_short"] == 1 and v["views"] == 100 and 2.9 < v["age_days"] < 3.1


def test_outlier_scores_split_formats_and_skip_fresh():
    videos = [parse_video(video_item(f"l{i}", 1000, 10 + i)) for i in range(12)]
    videos.append(parse_video(video_item("hit", 8000, 30)))
    videos += [parse_video(video_item(f"s{i}", 50_000, 10 + i, duration="PT30S")) for i in range(5)]
    videos.append(parse_video(video_item("fresh", 90_000, 2)))
    scored = {v["id"]: v["outlier"] for v in outlier_scores(videos)}
    assert scored["hit"] == 8.0           # порівнюється з довгими, а не з Shorts по 50k
    assert scored["s0"] == 1.0
    assert "fresh" not in scored


class FakeYouTube(YouTubeSource):
    def __init__(self):
        super().__init__(http=None, api_key="k")
        self.items = [video_item(f"v{i}", 1000, 10 + i * 3) for i in range(15)]
        self.items[4] = video_item("v4", 20_000, 22, title="I Bought The Cheapest House In America")
        self.items[9] = video_item("v9", 9_000, 37, title="I Bought The Cheapest Car On eBay")

    def _get(self, path, **params):
        if path == "channels" and "forHandle" in params:
            return {"items": [{"id": "UCsrc", "snippet": {"title": "Useless Money", "customUrl": "@uselessmoney"},
                               "statistics": {"subscriberCount": "100000"},
                               "contentDetails": {"relatedPlaylists": {"uploads": "UUsrc"}}}]}
        if path == "playlistItems":
            return {"items": [{"contentDetails": {"videoId": i["id"]}} for i in self.items]}
        if path == "videos":
            other = [video_item("o1", 90_000, 40, channel="UCsmall"), video_item("o2", 500, 40, channel="UCbig"),
                     video_item("o3", 1, 40, channel="UCsrc")]
            pool = {i["id"]: i for i in self.items + other}
            return {"items": [pool[i] for i in params["id"].split(",") if i in pool]}
        if path == "search":
            return {"items": [{"id": {"videoId": v}} for v in ("o1", "o2", "o3")]}
        if path == "channels":
            return {"items": [{"id": "UCsmall", "statistics": {"subscriberCount": "3000"}},
                              {"id": "UCbig", "statistics": {"subscriberCount": "2000000"}},
                              {"id": "UCsrc", "statistics": {"subscriberCount": "100000"}}]}
        raise AssertionError(path)


class FakeMessages:
    def __init__(self, result):
        self.result, self.calls = result, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(self.result))])


def test_analyze_channel_end_to_end(tmp_path):
    pattern = {"name": "Найдешевше X", "formula": "I Bought The Cheapest [X] On [Place]",
               "topic_angle": "крайність ціни", "hook": "абсурдна ціна", "emotional_trigger": "недовіра",
               "why_it_works": "...", "evidence_ids": ["v4", "v9", "bogus"], "search_query": "cheapest house",
               "new_topics": [{"title": "I Bought The Cheapest Island", "why": "ще абсурдніше"}],
               "adaptations": [{"format": "x_thread", "idea": "тред про 10 найдешевших речей"}]}
    fake = FakeMessages({"channel_dna": "Аудиторія любить крайнощі цін.", "patterns": [pattern]})
    client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    conn = db.connect(tmp_path / "t.db")

    channel, result, outliers = analyze_channel(FakeYouTube(), conn, "@UselessMoney", "claude-opus-5-5",
                                                client=client)
    assert {v["id"] for v in outliers} == {"v4", "v9"}
    content = fake.calls[0]["messages"][0]["content"]
    assert "I Bought The Cheapest House" in content[0]["text"]
    assert any(b["type"] == "image" for b in content)

    p = result["patterns"][0]
    assert p["evidence_ids"] == ["v4", "v9"] and p["avg_outlier"] > 5
    val = p["validation"]
    assert val["checked"] == 2 and val["hit_rate"] == 0.5 and val["small_channel_hits"] == 1
    assert p["score"] == pattern_score(p) > 5
    assert conn.execute("SELECT COUNT(*) FROM viral_patterns").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM yt_videos").fetchone()[0] == 15

    md = write_report(channel, result, outliers, tmp_path / "reports").read_text()
    assert "I Bought The Cheapest [X] On [Place]" in md and "I Bought The Cheapest Island" in md
    assert "X-тред" in md

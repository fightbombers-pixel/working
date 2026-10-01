import json
import math
from types import SimpleNamespace

import pytest

from niche_finder import db
from niche_finder.analyze import analyze_pending
from niche_finder.net import HttpClient
from niche_finder.report import write_reports
from niche_finder.score import build_niches
from niche_finder.sources import google_trends, pinterest
from niche_finder.sources.reddit import parse_post
from niche_finder.sources.threads import parse_apify as threads_apify, parse_official as threads_official
from niche_finder.sources.x import parse_apify as x_apify, parse_official as x_official

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>Meal Prep App</title><ht:approx_traffic>2000+</ht:approx_traffic></item>
<item><title>World Cup</title><ht:approx_traffic>1000000+</ht:approx_traffic></item>
</channel></rss>"""


def test_parse_sources():
    r = parse_post({"id": "abc", "subreddit": "mealprep", "title": "I wish there was an app",
                    "selftext": "for meal prep", "score": 10, "num_comments": 4,
                    "permalink": "/r/mealprep/comments/abc/x/", "created_utc": 1.0}, "q")
    assert r["id"] == "reddit:abc" and r["url"].endswith("/abc/x/")

    x = x_official({"id": "1", "text": "would pay for this",
                    "public_metrics": {"like_count": 5, "retweet_count": 2, "reply_count": 1},
                    "created_at": "2026-09-30T10:00:00.000Z"}, "q")
    assert x["score"] == 7 and x["comments"] == 1 and x["created_utc"] > 0

    xa = x_apify({"id": "2", "fullText": "hi", "likeCount": 3, "url": "https://x.com/a/status/2"}, "q")
    assert xa["score"] == 3 and xa["url"] == "https://x.com/a/status/2"
    assert x_apify({"foo": 1}, "q") is None

    t = threads_official({"id": "9", "text": "need tool", "permalink": "https://threads.net/p/9",
                          "timestamp": "2026-09-30T10:00:00+0000"}, "q")
    assert t["source"] == "threads" and t["url"].endswith("/9")
    ta = threads_apify({"code": "C1", "caption": {"text": "help"}, "like_count": 4}, "q")
    assert ta["id"] == "threads:C1" and ta["body"] == "help" and ta["score"] == 4

    pt = pinterest.parse_trend({"keyword": "Cottagecore Decor", "pct_growth_wow": 5,
                                "pct_growth_mom": 40, "pct_growth_yoy": 300})
    assert pt["keyword"] == "cottagecore decor" and "yoy=300%" in pinterest.traffic_label(pt)


def test_rss_and_trend_math():
    assert google_trends.parse_rss(RSS) == [("meal prep app", "2000+"), ("world cup", "1000000+")]

    flat_then_up = [10.0] * 52 + [20.0] * 52
    assert google_trends.growth_ratio(flat_then_up) == pytest.approx(2.0)
    assert google_trends.growth_ratio([1.0] * 50) is None

    seasonal = [100 * (1 + math.sin(2 * math.pi * i / 52)) for i in range(260)]
    assert google_trends.seasonality(seasonal) > 0.6
    assert google_trends.seasonality(list(range(260))) < google_trends.seasonality(seasonal)


class FakeMessages:
    def __init__(self, ideas):
        self.ideas = ideas
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps({"ideas": self.ideas}))])


def test_end_to_end(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    posts = [
        {"id": "reddit:1", "source": "reddit", "community": "mealprep", "title": "is there an app for meal prep",
         "body": "", "score": 120, "comments": 30, "url": "u1", "created_utc": 0, "query": "q"},
        {"id": "x:2", "source": "x", "community": None, "title": "", "body": "would pay for a meal prep app",
         "score": 40, "comments": 5, "url": "u2", "created_utc": 0, "query": "q"},
        {"id": "reddit:3", "source": "reddit", "community": "memes", "title": "lol", "body": "",
         "score": 900, "comments": 100, "url": "u3", "created_utc": 0, "query": "q"},
    ]
    assert db.upsert_posts(conn, posts) == 3
    assert db.upsert_posts(conn, posts[:1]) == 0  # повторний збір не дублює

    # індекси в порядку сортування за engagement: 0=reddit:3, 1=reddit:1, 2=x:2
    fake = FakeMessages([
        {"post_index": 1, "keyword": "Meal Prep App", "problem": "No good meal planner", "audience": "busy people",
         "solution_type": "mobile_app", "willingness_to_pay": 2},
        {"post_index": 2, "keyword": "meal prep app", "problem": "Would pay for planner", "audience": "gym",
         "solution_type": "mobile_app", "willingness_to_pay": 3},
        {"post_index": 99, "keyword": "bogus", "problem": "", "audience": "", "solution_type": "other",
         "willingness_to_pay": 9},
    ])
    client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    assert analyze_pending(conn, "claude-opus-5-5", client=client) == 2
    assert fake.calls[0]["output_config"]["format"]["type"] == "json_schema"
    assert conn.execute("SELECT COUNT(*) FROM posts WHERE analyzed=0").fetchone()[0] == 0

    db.save_trending(conn, "pinterest:US", [("meal prep app ideas", "yoy=200%")])
    conn.execute("INSERT INTO trend_checks (keyword, geo, growth, seasonality) VALUES ('meal prep app', '', 2.0, 0.1)")

    niches = build_niches(conn)
    assert len(niches) == 1
    n = niches[0]
    assert n.keyword == "meal prep app" and n.mentions == 2 and n.sources == ["reddit", "x"]
    assert n.trending_in == ["pinterest:US"] and n.growth == 2.0 and n.score > 10

    csv_path, md_path = write_reports(niches, tmp_path / "reports")
    assert "meal prep app" in csv_path.read_text() and "**meal prep app**" in md_path.read_text()


def test_http_retries_and_rotates(monkeypatch):
    monkeypatch.setattr("niche_finder.net.time.sleep", lambda s: None)
    http = HttpClient(proxies=["http://p1:1", "http://p2:2"], min_interval=0)
    seen, statuses = [], [429, 503, 200]

    def fake_request(method, url, proxies=None, **kw):
        seen.append(proxies["https"])
        return SimpleNamespace(status_code=statuses[len(seen) - 1])

    monkeypatch.setattr(http.session, "request", fake_request)
    assert http.get("https://example.com").status_code == 200
    assert len(seen) == 3
    assert seen[1] != seen[0]  # проксі, що отримав 429, тимчасово виключається

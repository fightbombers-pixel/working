import json
from types import SimpleNamespace

from niche_finder import db
from niche_finder.formats import analyze_formats, market_check, matches, opportunity, verdict, write_formats_report
from niche_finder.sources.ytdlp import SKIP_NEWEST, channel_url, parse_entry


def entry(vid, views, title, channel="UCsrc", duration=1500):
    return {"id": vid, "title": title, "view_count": views, "duration": duration,
            "channel_id": channel, "channel": channel}


def test_ytdlp_parsing():
    assert channel_url("https://www.youtube.com/@MarcusExplainsHQ/videos") == \
        "https://www.youtube.com/@MarcusExplainsHQ/videos"
    fresh, old = parse_entry(entry("a", 10, "t"), 0), parse_entry(entry("b", 10, "t"), SKIP_NEWEST)
    assert fresh["age_days"] == 0 and old["age_days"] > 7 and old["order"] < fresh["order"]


def test_matching_and_verdicts():
    assert matches("The Rise and Fall of Nokia", "rise and fall")
    assert matches("Wie verdienen Flughäfen eigentlich Geld?", "wie|geld")
    assert not matches("How Airports Work", "make|money")
    base = {"videos": 10, "channels": 4, "median_views": 120_000}
    assert verdict(base) == "white_space"
    assert verdict({**base, "channels": 20}) == "proven"
    assert verdict({"videos": 25, "channels": 25, "median_views": 80}) == "flooded"
    assert verdict({"videos": 2, "channels": 2, "median_views": 1e6}) == "untested"
    assert opportunity(base) > opportunity({**base, "channels": 20}) > 0


class FakeSource:
    def __init__(self):
        titles = ["How Pawn Shops Actually Make Money", "What It's Like to Own a Railway",
                  "How Nightclubs Actually Make Money"]
        self.videos = [parse_entry(entry(f"v{i}", 300_000 if i in (5, 9) else 10_000,
                                         titles[0] if i == 5 else titles[2] if i == 9 else f"{titles[1]} {i}"), i)
                       for i in range(20)]
        self.queries = []

    def channel(self, ref, limit=200):
        return {"id": "UCsrc", "title": "Marcus", "handle": "@m", "subscribers": 21000}, self.videos

    def search(self, query, limit=30):
        self.queries.append(query)
        if "footballers" in query:  # мало каналів, великий попит
            return [parse_entry(entry(f"f{i}", 90_000, f"How Footballers Actually Make Money {i}", f"UC{i % 3}"))
                    for i in range(6)]
        # клон-ферма: багато каналів, мало переглядів, + відео самого каналу не рахується
        return [parse_entry(entry(f"c{i}", 40, f"How Hospitals Actually Make Money {i}", f"UCc{i}"))
                for i in range(20)] + [parse_entry(entry("own", 10**6, "How X Actually Make Money", "UCsrc"))]


def test_analyze_formats(tmp_path):
    result = {"formats": [{
        "name": "Як X насправді заробляє", "template": "How [X] Actually Make Money", "slot": "місце/бізнес",
        "winning_slots": "місця, де глядач бував", "losing_slots": "B2B", "evidence_ids": ["v5", "v9", "v1"],
        "base_query": "how actually make money", "base_match": "make|money",
        "transpositions": [
            {"axis": "niche", "target": "лікарні", "query": "how hospitals actually make money",
             "match_phrase": "make|money", "example_titles": ["How Hospitals Actually Make Money"]},
            {"axis": "persona", "target": "футболісти", "query": "how footballers actually make money",
             "match_phrase": "make|money", "example_titles": ["How Footballers Actually Make Money"]},
        ]}]}
    calls = []
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: calls.append(kw) or SimpleNamespace(
            stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(result))]))))
    src = FakeSource()
    channel, formats = analyze_formats(src, db.connect(tmp_path / "t.db"), "@m", "claude-opus-5-5", client=client)

    assert "How Pawn Shops Actually Make Money" in calls[0]["messages"][0]["content"]
    f = formats[0]
    assert [v["id"] for v in f["evidence"]] == ["v5", "v9"]          # v1 не outlier
    best, worst = f["transpositions"]
    assert best["target"] == "футболісти" and best["market"]["verdict"] == "white_space"
    assert worst["market"]["verdict"] == "flooded" and worst["market"]["videos"] == 20  # своє відео виключено
    md = write_formats_report(channel, formats, tmp_path).read_text()
    assert "вільна ніша" in md and "завалено клонами" in md


def test_market_check_excludes_own_channel():
    s = market_check(FakeSource().search, "q", "make|money", exclude_channel="UCsrc")
    assert s["top_views"] == 40


def test_discover_breakouts(monkeypatch, tmp_path):
    from niche_finder import discover as d

    def fake_search(query, limit=40):
        return [parse_entry(entry("a", 300_000, "Every Shark Explained in 9 Minutes", "UCsmall")),
                parse_entry(entry("b", 900_000, "Every Planet Explained in 9 Minutes", "UCbig")),
                parse_entry(entry("c", 5_000, "random video", "UCx"))]

    monkeypatch.setattr(d, "search_recent", fake_search)
    monkeypatch.setattr(d, "subscribers", lambda cid: {"UCsmall": 2_000, "UCbig": 5_000_000}[cid])
    [r] = d.discover([("Every X Explained", "every explained", "every|explained in")], workers=1)
    assert r["videos"] == 2 and r["breakout_channels"] == 1 and r["breakouts"][0]["channel"] == "UCsmall"
    assert "Every Shark" in d.write_discover_report([r], tmp_path).read_text()


def test_market_check_excludes_network_by_name():
    s = market_check(FakeSource().search, "q", "make|money", exclude={"UCc0", " ucc1 "})
    assert s["videos"] == 19  # UCsrc лишається (не виключений), UCc0 і UCc1 виключені


def test_radar_momentum_score_and_platforms():
    from niche_finder.radar import classify, momentum, score
    from niche_finder.signals import platforms_for

    m = momentum([1000] * 20 + [9000])
    assert m["accel"] == 9.0 and m["phase"] == "росте зараз"
    assert momentum([1000] * 10 + [9000, 2000])["phase"] in ("розгін", "фон")
    assert classify({"description": "American convicted murderer"}) == "розслідування"
    assert classify({"description": "2026 Asian Games medal table"}) == "не наше"
    sig = [{"source": "reddit", "text": "Tennessee suspends executions after Christa Pike survives"},
           {"source": "x", "text": "Christa Pike"}, {"source": "google_news", "text": "Unrelated story"}]
    assert set(platforms_for("Christa Pike", sig)) == {"reddit", "x"}
    s = score(m, {"long": 0}, "розслідування", platforms=2)
    assert 0 < s["total"] <= 10 and s["parts"]["інші платформи"] == 2.0

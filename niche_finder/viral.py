"""Віральні теми YouTube-каналу: outlier-відео -> формули (Claude) -> перевірка на інших каналах -> масштабування.

Логіка:
1. Outlier score = перегляди відео / медіана переглядів сусідніх відео того ж формату (Shorts окремо від довгих).
   Сусіди — найближчі за датою відео, бо канал росте і старі перегляди не порівнянні з новими.
2. Claude дивиться на outliers (назви, теги, обкладинки) і на провали для контрасту, витягує
   повторювані формули: шаблон назви, кут теми, хук, емоцію, чому спрацювало.
3. Кожну формулу перевіряємо пошуком на YouTube: чи заходять такі відео в інших каналів,
   особливо маленьких (перегляди >= 2× підписників) — тоді віральність у темі, а не в автора.
4. Для кожної формули — нові теми і адаптації під інші формати.
"""
import json
import logging
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from statistics import median

import anthropic

from .sources.youtube import YouTubeSource

log = logging.getLogger(__name__)

MIN_AGE_DAYS = 7       # молодші відео ще набирають перегляди
NEIGHBORS = 10         # з скількома сусідніми відео порівнювати
OUTLIER_MIN = 2.0      # з якого множника відео вважається outlier
SMALL_CHANNEL = 50_000

FORMATS = ["shorts_tiktok_reels", "long_video", "x_thread", "instagram_carousel",
           "newsletter", "podcast", "blog_seo", "digital_product"]

SYSTEM = """You reverse-engineer why some YouTube videos go viral so the idea can be repeated and scaled.
You get one channel's outlier videos (views far above the channel's normal, with the multiplier),
their thumbnails, and the channel's weakest videos for contrast.

Find the REPEATABLE formulas behind the outliers — not one-off luck. A formula is a pattern that
at least two outliers share, or one huge outlier whose mechanism is clearly transferable.
Compare with the weak videos: what do outliers have that the flops lack?

For each formula:
- formula: title template with [placeholders], e.g. "I Bought the Cheapest [X] on [Marketplace]".
- topic_angle: what the topic is really about (curiosity gap, money comparison, absurd purchase...).
- hook: what makes people click in the first second (title + thumbnail mechanism).
- emotional_trigger: the core emotion (envy, disbelief, schadenfreude, FOMO, curiosity...).
- why_it_works: 1-3 sentences, concrete, grounded in the evidence.
- evidence_ids: ids of the outlier videos that follow this formula.
- search_query: 2-5 word English YouTube search to find the same kind of video on other channels.
- new_topics: 8 fresh video titles that apply the formula to NEW subjects (not already on the channel),
  written in the channel's language, each with a one-line reason it should travel.
- adaptations: how to scale the formula into other formats; one concrete idea per format that fits,
  skip formats that genuinely don't fit.

Write everything except titles and search_query in {lang}. Also return channel_dna: 2-4 sentences
in {lang} about what this channel's audience reliably responds to. Return 3-8 formulas, best first."""

PATTERNS_SCHEMA = {
    "type": "object",
    "properties": {
        "channel_dna": {"type": "string"},
        "patterns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "formula": {"type": "string"},
                    "topic_angle": {"type": "string"},
                    "hook": {"type": "string"},
                    "emotional_trigger": {"type": "string"},
                    "why_it_works": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "search_query": {"type": "string"},
                    "new_topics": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"title": {"type": "string"}, "why": {"type": "string"}},
                            "required": ["title", "why"],
                            "additionalProperties": False,
                        },
                    },
                    "adaptations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"format": {"type": "string", "enum": FORMATS},
                                           "idea": {"type": "string"}},
                            "required": ["format", "idea"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["name", "formula", "topic_angle", "hook", "emotional_trigger", "why_it_works",
                             "evidence_ids", "search_query", "new_topics", "adaptations"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["channel_dna", "patterns"],
    "additionalProperties": False,
}


def outlier_scores(videos: list[dict], neighbors: int = NEIGHBORS, min_age: float = MIN_AGE_DAYS) -> list[dict]:
    """Додає кожному відео 'outlier' (множник до медіани сусідів того ж формату). Повертає відсортовано."""
    scored = []
    for is_short in (0, 1):
        group = sorted((v for v in videos if v["is_short"] == is_short and v["age_days"] >= min_age),
                       key=lambda v: v["published_at"])
        for i, v in enumerate(group):
            lo = max(0, min(i - neighbors // 2, len(group) - neighbors - 1))
            peers = [p["views"] for p in group[lo:lo + neighbors + 1] if p is not v]
            base = median(peers) if peers else 0
            scored.append({**v, "outlier": round(v["views"] / base, 2) if base else 1.0})
    return sorted(scored, key=lambda v: v["outlier"], reverse=True)


def _describe(v: dict) -> str:
    kind = "SHORT" if v["is_short"] else f"{v['duration_s'] // 60} min"
    return (f"[{v['id']}] x{v['outlier']} | {v['views']:,} views | {kind} | {v['published_at'][:10]}\n"
            f"title: {v['title']}\ntags: {v['tags'] or '-'}\ndescription: {v['description'][:200]}")


def build_prompt(channel: dict, outliers: list[dict], flops: list[dict], thumbnails: int = 8) -> list[dict]:
    text = (f"Channel: {channel['title']} ({channel.get('handle', '')}), {channel['subscribers']:,} subscribers\n"
            f"About: {channel.get('description', '')[:500]}\n\n"
            "=== OUTLIERS (views × channel normal) ===\n\n" + "\n\n".join(_describe(v) for v in outliers) +
            "\n\n=== WEAKEST VIDEOS (contrast) ===\n\n" + "\n\n".join(_describe(v) for v in flops))
    content: list[dict] = [{"type": "text", "text": text}]
    for v in outliers[:thumbnails]:
        if v.get("thumbnail"):
            content.append({"type": "text", "text": f"Thumbnail of [{v['id']}] (x{v['outlier']}):"})
            content.append({"type": "image", "source": {"type": "url", "url": v["thumbnail"]}})
    return content


def extract_patterns(client: anthropic.Anthropic, model: str, channel: dict, outliers: list[dict],
                     flops: list[dict], lang: str = "Ukrainian") -> dict:
    response = client.beta.messages.create(
        model=model,
        max_tokens=32000,
        system=SYSTEM.replace("{lang}", lang),
        messages=[{"role": "user", "content": build_prompt(channel, outliers, flops)}],
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": PATTERNS_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude stop_reason={response.stop_reason}")
    text = next(b.text for b in response.content if b.type == "text")
    result = json.loads(text)
    known = {v["id"]: v["outlier"] for v in outliers}
    for p in result["patterns"]:
        p["evidence_ids"] = [i for i in p["evidence_ids"] if i in known]
        mults = [known[i] for i in p["evidence_ids"]]
        p["avg_outlier"] = round(sum(mults) / len(mults), 2) if mults else 0.0
    return result


def validate(yt: YouTubeSource, pattern: dict, exclude_channel: str, days: int = 365, limit: int = 25) -> dict:
    """Чи заходить формула в інших: частка відео з переглядами >= 2× підписників каналу."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    videos = [v for v in yt.search(pattern["search_query"], limit=limit, published_after=since)
              if v["channel_id"] != exclude_channel and v["channel_subscribers"] > 0]
    hits = [v for v in videos if v["views"] >= 2 * v["channel_subscribers"]]
    small = [v for v in hits if v["channel_subscribers"] < SMALL_CHANNEL]
    top = sorted(hits, key=lambda v: v["views"] / v["channel_subscribers"], reverse=True)[:3]
    return {
        "checked": len(videos),
        "hit_rate": round(len(hits) / len(videos), 2) if videos else 0.0,
        "small_channel_hits": len(small),
        "examples": [{"title": v["title"], "url": v["url"], "views": v["views"],
                      "subscribers": v["channel_subscribers"]} for v in top],
    }


def pattern_score(p: dict) -> float:
    """0–10: сила на самому каналі (до 5) + підтвердження на інших каналах (до 5)."""
    own = min(5.0, math.log2(max(1.0, p["avg_outlier"])) * 1.5) + min(1.0, (len(p["evidence_ids"]) - 1) * 0.25)
    v = p.get("validation")
    if not v:
        return round(min(5.0, own), 2)
    other = v["hit_rate"] * 3 + min(2.0, v["small_channel_hits"] * 0.5)
    return round(min(5.0, own) + min(5.0, other), 2)


def save_videos(conn: sqlite3.Connection, videos: list[dict]) -> None:
    conn.executemany(
        """INSERT OR REPLACE INTO yt_videos (id, channel_id, title, published_at, duration_s, is_short,
                                             views, likes, comments, outlier)
           VALUES (:id, :channel_id, :title, :published_at, :duration_s, :is_short,
                   :views, :likes, :comments, :outlier)""",
        [{"outlier": None, **v} for v in videos],
    )
    conn.commit()


def save_patterns(conn: sqlite3.Connection, channel: dict, result: dict) -> None:
    conn.executemany(
        "INSERT INTO viral_patterns (channel_id, channel_title, name, score, data) VALUES (?, ?, ?, ?, ?)",
        [(channel["id"], channel["title"], p["name"], p["score"], json.dumps(p, ensure_ascii=False))
         for p in result["patterns"]],
    )
    conn.commit()


def analyze_channel(yt: YouTubeSource, conn: sqlite3.Connection, ref: str, model: str,
                    client: anthropic.Anthropic | None = None, limit: int = 200, top: int = 25,
                    check: bool = True, lang: str = "Ukrainian") -> tuple[dict, dict, list[dict]]:
    channel = yt.channel(ref)
    videos = outlier_scores(yt.channel_videos(channel["uploads"], limit=limit))
    save_videos(conn, videos)
    log.info("%s: %d videos analysed", channel["title"], len(videos))
    if len(videos) < 5:
        raise RuntimeError("замало відео старших за тиждень для порівняння")

    outliers = [v for v in videos if v["outlier"] >= OUTLIER_MIN][:top] or videos[:5]
    flops = videos[-8:]
    result = extract_patterns(client or anthropic.Anthropic(), model, channel, outliers, flops, lang=lang)

    for p in result["patterns"]:
        if check:
            try:
                p["validation"] = validate(yt, p, channel["id"])
            except Exception as exc:  # квота/мережа — формула лишається без перевірки
                log.error("validate %s: %s", p["search_query"], exc)
        p["score"] = pattern_score(p)
    result["patterns"].sort(key=lambda p: p["score"], reverse=True)
    save_patterns(conn, channel, result)
    return channel, result, outliers


FORMAT_LABELS = {
    "shorts_tiktok_reels": "Shorts / TikTok / Reels", "long_video": "Довге відео", "x_thread": "X-тред",
    "instagram_carousel": "Карусель Instagram", "newsletter": "Розсилка", "podcast": "Подкаст",
    "blog_seo": "SEO-стаття", "digital_product": "Цифровий продукт",
}


def write_report(channel: dict, result: dict, outliers: list[dict], out_dir) -> "Path":
    from datetime import date
    from pathlib import Path

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = (channel.get("handle") or channel["id"]).lstrip("@").lower()
    path = out_dir / f"viral_{slug}_{date.today().isoformat()}.md"
    by_id = {v["id"]: v for v in outliers}

    lines = [f"# Віральні формули — {channel['title']} ({channel['subscribers']:,} підписників)", "",
             result["channel_dna"], "", "## Outlier-відео", "",
             "| × норми | Перегляди | Формат | Назва |", "|---|---|---|---|"]
    for v in outliers:
        kind = "Short" if v["is_short"] else "Long"
        lines.append(f"| x{v['outlier']} | {v['views']:,} | {kind} | [{v['title']}]({v['url']}) |")

    lines += ["", "## Формули", "", "| # | Бал | Формула | Сер. × | Інші канали |", "|---|---|---|---|---|"]
    for i, p in enumerate(result["patterns"], 1):
        val = p.get("validation")
        other = f"{int(val['hit_rate'] * 100)}% з {val['checked']}, малих: {val['small_channel_hits']}" if val else "—"
        lines.append(f"| {i} | {p['score']} | {p['formula']} | x{p['avg_outlier']} | {other} |")

    for i, p in enumerate(result["patterns"], 1):
        lines += ["", f"### {i}. {p['name']} — {p['score']}/10", "", f"**Шаблон:** `{p['formula']}`", "",
                  f"- **Кут теми:** {p['topic_angle']}", f"- **Хук:** {p['hook']}",
                  f"- **Емоція:** {p['emotional_trigger']}", f"- **Чому працює:** {p['why_it_works']}", ""]
        if p["evidence_ids"]:
            lines.append("**Докази на каналі:**")
            lines += [f"- x{by_id[e]['outlier']} — [{by_id[e]['title']}]({by_id[e]['url']})"
                      for e in p["evidence_ids"] if e in by_id]
            lines.append("")
        if val := p.get("validation"):
            lines.append(f"**Інші канали** (пошук `{p['search_query']}`, рік): {int(val['hit_rate'] * 100)}% відео "
                         f"набрали ≥2× підписників, з них {val['small_channel_hits']} на каналах <50k")
            lines += [f"- [{e['title']}]({e['url']}) — {e['views']:,} переглядів при {e['subscribers']:,} підп."
                      for e in val["examples"]]
            lines.append("")
        lines.append("**Нові теми:**")
        lines += [f"{n}. **{t['title']}** — {t['why']}" for n, t in enumerate(p["new_topics"], 1)]
        lines += ["", "**Масштабування на інші формати:**"]
        lines += [f"- *{FORMAT_LABELS.get(a['format'], a['format'])}:* {a['idea']}" for a in p["adaptations"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path

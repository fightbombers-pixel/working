"""Множення форматів: беремо формат, що працює на каналі, і шукаємо, куди його ще не перенесли.

Формат = шаблон відео з одним змінним слотом: "How [X] Actually Make Money", "The Rise and Fall of [company]".
Такі формати копіюють і множать: інша ніша (бізнеси -> футбольні клуби), інша персона/кут
(власник, ексспівробітник, "пояснюю як дитині"), інша мова (DE, ES, PT, UA...).

1. Outlier-відео каналу (viral.outlier_scores) -> Claude виділяє формати і пропонує переноси.
2. Кожен перенос перевіряємо пошуком YouTube (yt-dlp): скільки відео з цим шаблоном, скільки різних
   каналів його вже роблять, медіана і максимум переглядів.
3. Класифікація:
   - white_space: попит є (медіана >= 30k), а каналів мало (<= 8) — сюди йти першим;
   - proven: попит є, але конкурентів багато — працює, потрібен сильніший кут;
   - flooded: десятки каналів і медіана < 3k — ферми AI-клонів, не йти;
   - weak / untested: попиту не видно або результатів замало.
"""
import json
import logging
import math
import sqlite3
from datetime import date
from pathlib import Path
from statistics import median

import anthropic

from .viral import OUTLIER_MIN, outlier_scores

log = logging.getLogger(__name__)

AXES = ["niche", "persona", "language", "angle"]

SYSTEM = """You find video FORMATS that can be cloned and multiplied, not single topics.
A format is a repeatable video template with one variable slot, e.g. "How [X] Actually Make Money (It's Not [Y])",
"The Rise and Fall of [Company], the [Place] [Thing] That [Achievement]", "The Economics of Owning a [Asset]".
You get one channel's videos with outlier multipliers (views vs the channel's normal) and its weakest videos.

1. Identify 1-4 formats this channel runs. For each, say which slot values made outliers vs flops and why
   (e.g. "places the viewer has physically been + a paradox 'how can this be profitable'" vs "B2B industries nobody visits").
2. Propose transpositions — the same format moved to a new place. Use all axes:
   - niche: same template, a different subject universe (tech companies -> fashion brands -> football clubs);
   - persona: different narrator/viewpoint (ex-employee, owner, insider, "explained like you're 5", country-specific);
   - language: the same format for another language market (give the query in that language);
   - angle: a twist on the template (dark side, "it's not X", numbers-first, comparison).
   12-20 transpositions per format, mixing axes.
3. For each transposition give a YouTube search query that would find such videos and a match_phrase:
   the fixed words of the template (lowercase, in the query's language) that a title must contain to count,
   with "|" between required words, e.g. "rise and fall" or "make|money" or "wie|geld".
   Also give 3 example titles you'd publish.

Write explanations in {lang}; keep queries, match phrases and example titles in the target language."""

SCHEMA = {
    "type": "object",
    "properties": {
        "formats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "template": {"type": "string"},
                    "slot": {"type": "string"},
                    "winning_slots": {"type": "string"},
                    "losing_slots": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "base_query": {"type": "string"},
                    "base_match": {"type": "string"},
                    "transpositions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "axis": {"type": "string", "enum": AXES},
                                "target": {"type": "string"},
                                "query": {"type": "string"},
                                "match_phrase": {"type": "string"},
                                "example_titles": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["axis", "target", "query", "match_phrase", "example_titles"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["name", "template", "slot", "winning_slots", "losing_slots", "evidence_ids",
                             "base_query", "base_match", "transpositions"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["formats"],
    "additionalProperties": False,
}


def _line(v: dict) -> str:
    kind = "SHORT" if v["is_short"] else f"{v['duration_s'] // 60}m"
    return f"[{v['id']}] x{v['outlier']} | {v['views']:,} | {kind} | {v['title']}"


def extract_formats(client: anthropic.Anthropic, model: str, channel: dict, videos: list[dict],
                    lang: str = "Ukrainian") -> list[dict]:
    text = (f"Channel: {channel['title']} {channel.get('handle', '')}, {channel.get('subscribers', 0):,} subscribers\n"
            f"About: {channel.get('description', '')[:400]}\n\n=== ALL VIDEOS, best first ===\n" +
            "\n".join(_line(v) for v in videos[:150]))
    response = client.beta.messages.create(
        model=model,
        max_tokens=32000,
        system=SYSTEM.replace("{lang}", lang),
        messages=[{"role": "user", "content": text}],
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude stop_reason={response.stop_reason}")
    return json.loads(next(b.text for b in response.content if b.type == "text"))["formats"]


def matches(title: str, phrase: str) -> bool:
    title = title.lower()
    return all(w.strip() in title for w in phrase.lower().split("|") if w.strip())


def market_check(search, query: str, phrase: str, limit: int = 30, exclude_channel: str = "",
                 exclude: set[str] = frozenset()) -> dict:
    """Попит і пропозиція формату в ніші за видачею YouTube.

    exclude — id або назви власних каналів (мережі), щоб свої відео не роздували медіану.
    """
    skip = {exclude_channel.lower(), *(e.strip().lower() for e in exclude)} - {""}
    found = [v for v in search(query, limit) if matches(v["title"], phrase)
             and v["channel_id"].lower() not in skip and v["channel_title"].strip().lower() not in skip]
    views = sorted((v["views"] for v in found), reverse=True)
    channels = {v["channel_id"] or v["channel_title"] for v in found}
    stats = {
        "videos": len(found),
        "channels": len(channels),
        # медіана топ-10: хвіст видачі пошуку — випадкове сміття, він не про попит
        "median_views": int(median(views[:10])) if views else 0,
        "top_views": views[0] if views else 0,
        "top": [{"title": v["title"], "channel": v["channel_title"], "views": v["views"], "url": v["url"]}
                for v in sorted(found, key=lambda v: v["views"], reverse=True)[:3]],
    }
    stats["verdict"] = verdict(stats)
    stats["score"] = opportunity(stats)
    return stats


def verdict(s: dict) -> str:
    if s["videos"] < 3:
        return "untested"
    if s["median_views"] >= 30_000:
        return "white_space" if s["channels"] <= 8 else "proven"
    if s["channels"] >= 15 and s["median_views"] < 3_000:
        return "flooded"
    return "weak"


def opportunity(s: dict) -> float:
    """0–10: попит (медіана переглядів, лог-шкала) мінус штраф за кількість каналів, що вже роблять формат."""
    demand = min(10.0, math.log10(1 + s["median_views"]) * 2)
    crowd = min(0.8, s["channels"] / 30)
    return round(demand * (1 - crowd), 2)


def analyze_formats(src, conn: sqlite3.Connection, ref: str, model: str, client: anthropic.Anthropic | None = None,
                    limit: int = 200, lang: str = "Ukrainian", exclude: set[str] = frozenset()) -> tuple[dict, list[dict]]:
    channel, videos = src.channel(ref, limit=limit)
    videos = outlier_scores(videos)
    if len(videos) < 5:
        raise RuntimeError("замало відео для порівняння")
    formats = extract_formats(client or anthropic.Anthropic(), model, channel, videos, lang=lang)
    by_id = {v["id"]: v for v in videos}

    for f in formats:
        f["evidence"] = [by_id[i] for i in f["evidence_ids"] if i in by_id and by_id[i]["outlier"] >= OUTLIER_MIN]
        try:
            f["base"] = market_check(src.search, f["base_query"], f["base_match"], exclude_channel=channel["id"], exclude=exclude)
        except Exception as exc:
            log.error("base %s: %s", f["base_query"], exc)
            f["base"] = None
        for t in f["transpositions"]:
            try:
                t["market"] = market_check(src.search, t["query"], t["match_phrase"], exclude_channel=channel["id"], exclude=exclude)
            except Exception as exc:  # одна невдала перевірка не валить решту
                log.error("check %s: %s", t["query"], exc)
                t["market"] = {"verdict": "untested", "score": 0.0, "videos": 0, "channels": 0,
                               "median_views": 0, "top_views": 0, "top": []}
            log.info("%-45s %-11s %s", t["query"][:45], t["market"]["verdict"], t["market"]["score"])
        f["transpositions"].sort(key=lambda t: t["market"]["score"], reverse=True)

    conn.executemany(
        "INSERT INTO viral_patterns (channel_id, channel_title, name, score, data) VALUES (?, ?, ?, ?, ?)",
        [(channel["id"], channel["title"], "format: " + f["name"],
          max((t["market"]["score"] for t in f["transpositions"]), default=0.0),
          json.dumps(f, ensure_ascii=False)) for f in formats],
    )
    conn.commit()
    return channel, formats


VERDICT_LABELS = {"white_space": "🟢 вільна ніша", "proven": "🟡 працює, тісно", "weak": "⚪ слабкий попит",
                  "flooded": "🔴 завалено клонами", "untested": "· мало даних"}
AXIS_LABELS = {"niche": "ніша", "persona": "персона", "language": "мова", "angle": "кут"}


def write_formats_report(channel: dict, formats: list[dict], out_dir) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = (channel.get("handle") or channel["id"]).lstrip("@").lower()
    path = out_dir / f"formats_{slug}_{date.today().isoformat()}.md"
    lines = [f"# Формати для множення — {channel['title']} ({channel.get('subscribers', 0):,} підписників)", ""]
    for f in formats:
        lines += [f"## {f['name']}", "", f"**Шаблон:** `{f['template']}` · слот: {f['slot']}", "",
                  f"- **Що заходить:** {f['winning_slots']}", f"- **Що провалюється:** {f['losing_slots']}"]
        if f.get("base"):
            b = f["base"]
            lines.append(f"- **Формат в інших каналах:** {b['channels']} каналів, медіана {b['median_views']:,} "
                         f"переглядів — {VERDICT_LABELS[b['verdict']]}")
        if f["evidence"]:
            lines.append("- **Outliers на каналі:** " + "; ".join(
                f"[{v['title']}]({v['url']}) x{v['outlier']}" for v in f["evidence"][:5]))
        lines += ["", "| Бал | Вердикт | Вісь | Куди переносимо | Каналів | Медіана | Топ | Приклади назв |",
                  "|---|---|---|---|---|---|---|---|"]
        for t in f["transpositions"]:
            m = t["market"]
            lines.append(f"| {m['score']} | {VERDICT_LABELS[m['verdict']]} | {AXIS_LABELS[t['axis']]} | "
                         f"{t['target']} | {m['channels']} | {m['median_views']:,} | {m['top_views']:,} | "
                         f"{'<br>'.join(t['example_titles'][:3])} |")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path

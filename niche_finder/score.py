"""Агрегує ідеї по ключовому слову і рахує бал ніші (максимум 15)."""
import math
import sqlite3
from dataclasses import asdict, dataclass, field


@dataclass
class Niche:
    keyword: str
    mentions: int
    sources: list[str]
    engagement: int
    wtp: float
    solution_types: list[str]
    growth: float | None = None
    seasonality: float | None = None
    trending_in: list[str] = field(default_factory=list)
    examples: list[dict] = field(default_factory=list)
    score: float = 0.0

    def as_row(self) -> dict:
        row = asdict(self)
        row["sources"] = ",".join(self.sources)
        row["solution_types"] = ",".join(self.solution_types)
        row["trending_in"] = ",".join(self.trending_in)
        row["examples"] = " | ".join(f"{e['problem']} ({e['url']})" for e in self.examples)
        return row


def compute_score(n: Niche) -> float:
    pain = min(3.0, math.log2(1 + n.mentions))                      # скільки разів згадують
    engagement = min(3.0, math.log10(1 + n.engagement))             # наскільки резонує
    wtp = n.wtp                                                     # готовність платити 0..3
    cross = min(2.0, len(n.sources) - 1)                            # біль є на кількох платформах
    growth = 0.0
    if n.growth is not None and math.isfinite(n.growth):
        growth = max(0.0, min(3.0, (n.growth - 1) * 3))             # x2 за рік = 3 бали
    elif n.growth == float("inf"):
        growth = 3.0
    trend_bonus = 1.0 if n.trending_in else 0.0                     # є в Pinterest/Google трендах
    season_penalty = 1.0 if (n.seasonality or 0) > 0.6 else 0.0     # щорічні хвилі
    return round(pain + engagement + wtp + cross + growth + trend_bonus - season_penalty, 2)


def _matches(keyword: str, term: str) -> bool:
    return keyword in term or term in keyword


def build_niches(conn: sqlite3.Connection, days: int = 30, min_mentions: int = 2) -> list[Niche]:
    rows = conn.execute(
        """SELECT i.keyword, i.problem, i.solution_type, i.willingness_to_pay,
                  p.id AS post_id, p.source, p.score, p.comments, p.url
           FROM ideas i JOIN posts p ON p.id = i.post_id
           WHERE i.created_at >= datetime('now', ?)""",
        (f"-{days} days",),
    ).fetchall()

    grouped: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        grouped.setdefault(r["keyword"], []).append(r)

    trending = conn.execute(
        "SELECT DISTINCT geo, term FROM trending WHERE fetched_at >= date('now', ?)", (f"-{days} days",)
    ).fetchall()

    niches = []
    for kw, items in grouped.items():
        post_ids = {r["post_id"] for r in items}
        if len(post_ids) < min_mentions:
            continue
        seen_posts: dict[str, sqlite3.Row] = {r["post_id"]: r for r in items}
        top = sorted(seen_posts.values(), key=lambda r: r["score"] + r["comments"], reverse=True)[:3]
        check = conn.execute(
            "SELECT growth, seasonality FROM trend_checks WHERE keyword = ? AND geo = '' "
            "ORDER BY checked_at DESC LIMIT 1", (kw,)
        ).fetchone()
        niche = Niche(
            keyword=kw,
            mentions=len(post_ids),
            sources=sorted({r["source"] for r in items}),
            engagement=sum(r["score"] + r["comments"] for r in seen_posts.values()),
            wtp=round(sum(r["willingness_to_pay"] for r in items) / len(items), 2),
            solution_types=sorted({r["solution_type"] for r in items}),
            growth=check["growth"] if check else None,
            seasonality=check["seasonality"] if check else None,
            trending_in=sorted({t["geo"] for t in trending if _matches(kw, t["term"])}),
            examples=[{"problem": r["problem"], "url": r["url"]} for r in top],
        )
        niche.score = compute_score(niche)
        niches.append(niche)
    return sorted(niches, key=lambda n: n.score, reverse=True)

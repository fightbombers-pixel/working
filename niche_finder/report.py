import csv
from datetime import date
from pathlib import Path

from .score import Niche

COLUMNS = ["score", "keyword", "mentions", "sources", "engagement", "wtp", "growth",
           "seasonality", "trending_in", "solution_types", "examples"]


def write_reports(niches: list[Niche], out_dir: Path, top: int = 30) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    csv_path = out_dir / f"niches_{stamp}.csv"
    md_path = out_dir / f"niches_{stamp}.md"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for n in niches:
            writer.writerow(n.as_row())

    lines = [f"# Ніші — {stamp}", "",
             "| # | Бал | Ніша | Згадок | Джерела | Платити | Ріст 5y | Тренди |",
             "|---|---|---|---|---|---|---|---|"]
    for i, n in enumerate(niches[:top], 1):
        growth = "—" if n.growth is None else f"x{n.growth:.2f}"
        lines.append(f"| {i} | {n.score} | **{n.keyword}** | {n.mentions} | {', '.join(n.sources)} "
                     f"| {n.wtp} | {growth} | {', '.join(n.trending_in) or '—'} |")
    lines.append("")
    for n in niches[:top]:
        lines.append(f"## {n.keyword} ({n.score})")
        lines += [f"- {e['problem']} — {e['url']}" for e in n.examples]
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path, md_path

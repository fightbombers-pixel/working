"""Витягує з постів конкретні болі/запити і нормалізує їх у ключові слова ніш (Claude API)."""
import json
import logging
import sqlite3

import anthropic

log = logging.getLogger(__name__)

BATCH_SIZE = 60

SYSTEM = """You analyze social media posts to find unmet needs that could become a business niche.
For each post that expresses a real problem, frustration, or request for a product/tool/service,
extract one idea. Skip jokes, spam, crypto shilling, self-promotion and posts with no clear need.

Field rules:
- keyword: 1-3 word English search term a buyer would type into Google, lowercase, canonical
  (e.g. "meal prep app", "dog anxiety vest"). Reuse the same keyword for posts about the same need.
- problem: one sentence, concrete.
- audience: who has the problem.
- solution_type: one of saas, mobile_app, physical_product, service, content, marketplace, other.
- willingness_to_pay: 0 = no sign, 1 = mild interest, 2 = actively looking/comparing, 3 = explicitly says they would pay.
- post_index: the [index] of the post the idea came from."""

IDEAS_SCHEMA = {
    "type": "object",
    "properties": {
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "post_index": {"type": "integer"},
                    "keyword": {"type": "string"},
                    "problem": {"type": "string"},
                    "audience": {"type": "string"},
                    "solution_type": {
                        "type": "string",
                        "enum": ["saas", "mobile_app", "physical_product", "service", "content", "marketplace", "other"],
                    },
                    "willingness_to_pay": {"type": "integer"},
                },
                "required": ["post_index", "keyword", "problem", "audience", "solution_type", "willingness_to_pay"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ideas"],
    "additionalProperties": False,
}


def format_batch(posts: list[sqlite3.Row]) -> str:
    lines = []
    for i, p in enumerate(posts):
        where = f"r/{p['community']}" if p["community"] else p["source"]
        text = f"{p['title']}\n{p['body']}".strip()[:1500]
        lines.append(f"[{i}] ({where}, score {p['score']}, comments {p['comments']})\n{text}")
    return "\n\n---\n\n".join(lines)


def extract_ideas(client: anthropic.Anthropic, model: str, posts: list[sqlite3.Row]) -> list[dict]:
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": format_batch(posts)}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": IDEAS_SCHEMA}},
        # якщо запит відхилено класифікатором безпеки, API сам перезапустить його на резервній моделі
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        log.warning("batch refused, skipping %d posts", len(posts))
        return []
    if response.stop_reason == "max_tokens":
        log.warning("batch hit max_tokens; output truncated, skipping")
        return []
    text = next(b.text for b in response.content if b.type == "text")
    ideas = json.loads(text)["ideas"]
    out = []
    for idea in ideas:
        idx = idea.pop("post_index")
        if 0 <= idx < len(posts):
            idea["post_id"] = posts[idx]["id"]
            idea["keyword"] = idea["keyword"].lower().strip()
            idea["willingness_to_pay"] = max(0, min(3, idea["willingness_to_pay"]))
            out.append(idea)
    return out


def analyze_pending(conn: sqlite3.Connection, model: str, client: anthropic.Anthropic | None = None,
                    max_posts: int = 1200) -> int:
    client = client or anthropic.Anthropic()
    posts = conn.execute(
        "SELECT * FROM posts WHERE analyzed = 0 ORDER BY score + comments DESC LIMIT ?", (max_posts,)
    ).fetchall()
    total = 0
    for start in range(0, len(posts), BATCH_SIZE):
        batch = posts[start:start + BATCH_SIZE]
        ideas = extract_ideas(client, model, batch)
        conn.executemany(
            """INSERT INTO ideas (keyword, problem, audience, solution_type, willingness_to_pay, post_id)
               VALUES (:keyword, :problem, :audience, :solution_type, :willingness_to_pay, :post_id)""",
            ideas,
        )
        conn.executemany("UPDATE posts SET analyzed = 1 WHERE id = ?", [(p["id"],) for p in batch])
        conn.commit()
        total += len(ideas)
        log.info("analyzed %d/%d posts, %d ideas so far", start + len(batch), len(posts), total)
    return total

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    community TEXT,
    title TEXT,
    body TEXT,
    score INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    url TEXT,
    created_utc REAL,
    query TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    analyzed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS community_stats (
    source TEXT NOT NULL,
    name TEXT NOT NULL,
    subscribers INTEGER,
    fetched_at TEXT DEFAULT (date('now')),
    PRIMARY KEY (source, name, fetched_at)
);
CREATE TABLE IF NOT EXISTS trending (
    geo TEXT NOT NULL,
    term TEXT NOT NULL,
    traffic TEXT,
    fetched_at TEXT DEFAULT (date('now')),
    PRIMARY KEY (geo, term, fetched_at)
);
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    problem TEXT,
    audience TEXT,
    solution_type TEXT,
    willingness_to_pay INTEGER,
    post_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS trend_checks (
    keyword TEXT NOT NULL,
    geo TEXT NOT NULL,
    growth REAL,
    seasonality REAL,
    checked_at TEXT DEFAULT (date('now')),
    PRIMARY KEY (keyword, geo, checked_at)
);
CREATE INDEX IF NOT EXISTS idx_ideas_keyword ON ideas(keyword);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_posts(conn: sqlite3.Connection, posts: list[dict]) -> int:
    """Вставляє нові пости, для вже відомих оновлює score/comments. Повертає кількість нових."""
    new = 0
    for p in posts:
        cur = conn.execute(
            """INSERT OR IGNORE INTO posts (id, source, community, title, body, score, comments, url, created_utc, query)
               VALUES (:id, :source, :community, :title, :body, :score, :comments, :url, :created_utc, :query)""",
            p,
        )
        if cur.rowcount:
            new += 1
        else:
            conn.execute("UPDATE posts SET score=:score, comments=:comments WHERE id=:id", p)
    conn.commit()
    return new


def save_trending(conn: sqlite3.Connection, geo: str, items: list[tuple[str, str]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO trending (geo, term, traffic) VALUES (?, ?, ?)",
        [(geo, term, traffic) for term, traffic in items],
    )
    conn.commit()

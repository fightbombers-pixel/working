import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Мінімальний .env-лоадер, щоб не тягнути python-dotenv."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _load_proxies() -> list[str]:
    proxies = [p.strip() for p in os.getenv("PROXY_URLS", "").split(",") if p.strip()]
    proxy_file = os.getenv("PROXY_FILE")
    if proxy_file and Path(proxy_file).exists():
        proxies += [l.strip() for l in Path(proxy_file).read_text().splitlines() if l.strip()]
    return proxies


# Фрази, якими люди описують біль або готовність платити.
PAIN_QUERIES = [
    '"I wish there was"',
    '"is there an app"',
    '"is there a tool"',
    '"would pay for"',
    '"why is there no"',
    '"looking for a tool"',
    '"does anyone know a"',
    '"frustrated with"',
    '"alternative to"',
    '"I hate how"',
]


@dataclass
class Config:
    db_path: Path = Path("data/niches.db")
    reports_dir: Path = Path("reports")
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "niche-finder/0.1"
    anthropic_model: str = "claude-opus-5-5"
    x_bearer_token: str = ""
    threads_access_token: str = ""
    pinterest_access_token: str = ""
    pinterest_regions: list[str] = field(default_factory=list)
    apify_token: str = ""
    apify_x_actor: str = ""
    apify_threads_actor: str = ""
    proxies: list[str] = field(default_factory=list)
    trends_geos: list[str] = field(default_factory=list)
    pain_queries: list[str] = field(default_factory=lambda: list(PAIN_QUERIES))

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv()
        geos = os.getenv("TRENDS_GEOS", "US,GB,CA,AU,DE,FR,IN,BR,JP")
        return cls(
            db_path=Path(os.getenv("NICHE_DB", "data/niches.db")),
            reddit_client_id=os.getenv("REDDIT_CLIENT_ID", ""),
            reddit_client_secret=os.getenv("REDDIT_CLIENT_SECRET", ""),
            reddit_user_agent=os.getenv("REDDIT_USER_AGENT", "niche-finder/0.1"),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"),
            x_bearer_token=os.getenv("X_BEARER_TOKEN", ""),
            threads_access_token=os.getenv("THREADS_ACCESS_TOKEN", ""),
            pinterest_access_token=os.getenv("PINTEREST_ACCESS_TOKEN", ""),
            pinterest_regions=[r.strip() for r in os.getenv("PINTEREST_REGIONS", "").split(",") if r.strip()],
            apify_token=os.getenv("APIFY_TOKEN", ""),
            apify_x_actor=os.getenv("APIFY_X_ACTOR", ""),
            apify_threads_actor=os.getenv("APIFY_THREADS_ACTOR", ""),
            proxies=_load_proxies(),
            trends_geos=[g.strip() for g in geos.split(",")],
        )

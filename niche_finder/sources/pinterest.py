"""Pinterest Trends через офіційний API v5.

Потрібен бізнес-акаунт, app на developers.pinterest.com і access token зі scope user_accounts:read.
Регіони обмежені (US, CA, GB+IE, DE+AT+CH, FR, BR, AU+NZ, ...) — повний список у документації
ендпоінта /v5/trends/keywords/{region}/top/{trend_type}.
"""
from ..net import HttpClient

DEFAULT_REGIONS = ["US", "CA", "GB+IE", "DE+AT+CH", "FR", "BR", "AU+NZ"]


class PinterestSource:
    def __init__(self, http: HttpClient, access_token: str = "", regions: list[str] | None = None):
        self.http = http
        self.access_token = access_token
        self.regions = regions or DEFAULT_REGIONS

    @property
    def enabled(self) -> bool:
        return bool(self.access_token)

    def top_trends(self, region: str, trend_type: str = "growing", limit: int = 50) -> list[dict]:
        # trend_type: growing | monthly | yearly | seasonal
        resp = self.http.get(
            f"https://api.pinterest.com/v5/trends/keywords/{region}/top/{trend_type}",
            params={"limit": limit},
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        resp.raise_for_status()
        return [parse_trend(t) for t in resp.json().get("trends", [])]


def parse_trend(t: dict) -> dict:
    return {
        "keyword": t.get("keyword", "").lower().strip(),
        "growth_wow": t.get("pct_growth_wow"),
        "growth_mom": t.get("pct_growth_mom"),
        "growth_yoy": t.get("pct_growth_yoy"),
    }


def traffic_label(t: dict) -> str:
    return f"yoy={t['growth_yoy']}% mom={t['growth_mom']}% wow={t['growth_wow']}%"

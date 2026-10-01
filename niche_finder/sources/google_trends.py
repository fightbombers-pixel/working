"""Google Trends: RSS «Trending Now» по країнах + перевірка росту ключових слів за 5 років.

RSS — легальне і стабільне джерело. Перевірка росту йде через pytrends (неофіційний,
Google часто відповідає 429 — тому паузи, повтори і бажано проксі).
"""
import logging
import time

import feedparser

from ..net import HttpClient

log = logging.getLogger(__name__)


def trending_now(http: HttpClient, geo: str) -> list[tuple[str, str]]:
    resp = http.get("https://trends.google.com/trending/rss", params={"geo": geo})
    resp.raise_for_status()
    return parse_rss(resp.content)


def parse_rss(content: bytes) -> list[tuple[str, str]]:
    feed = feedparser.parse(content)
    return [(e.title.lower().strip(), e.get("ht_approx_traffic", "")) for e in feed.entries]


def growth_ratio(values: list[float]) -> float | None:
    """Середнє за останні 52 тижні / середнє за попередні 52. >1.5 — помітне зростання."""
    if len(values) < 104:
        return None
    last, prev = values[-52:], values[-104:-52]
    prev_mean = sum(prev) / len(prev)
    last_mean = sum(last) / len(last)
    if prev_mean == 0:
        return float("inf") if last_mean > 0 else None
    return last_mean / prev_mean


def seasonality(values: list[float], lag: int = 52) -> float | None:
    """Автокореляція з лагом рік. Близько 1 — сезонний товар (щорічні хвилі), близько 0 — ні."""
    if len(values) < lag * 2:
        return None
    n = len(values)
    mean = sum(values) / n
    denom = sum((v - mean) ** 2 for v in values)
    if denom == 0:
        return 0.0
    num = sum((values[i] - mean) * (values[i - lag] - mean) for i in range(lag, n))
    return num / denom


class TrendsChecker:
    def __init__(self, proxies: list[str] | None = None, pause: float = 8.0):
        from pytrends.request import TrendReq

        self.client = TrendReq(hl="en-US", tz=0, timeout=(10, 30), retries=3, backoff_factor=2,
                               proxies=list(proxies or []))
        self.pause = pause

    def check(self, keyword: str, geo: str = "") -> tuple[float | None, float | None]:
        """Повертає (growth_ratio, seasonality) для ключового слова. geo="" — весь світ."""
        try:
            self.client.build_payload([keyword], timeframe="today 5-y", geo=geo)
            df = self.client.interest_over_time()
        except Exception as exc:
            log.warning("trends check failed for %r (%s): %s", keyword, geo or "world", exc)
            return None, None
        finally:
            time.sleep(self.pause)
        if df.empty or keyword not in df:
            return None, None
        values = [float(v) for v in df[keyword].tolist()]
        return growth_ratio(values), seasonality(values)

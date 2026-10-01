"""HTTP-клієнт з ротацією проксі, лімітом частоти по хосту та повторами."""
import logging
import random
import threading
import time
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class HttpClient:
    def __init__(self, proxies: list[str] | None = None, user_agent: str = "niche-finder/0.1",
                 min_interval: float = 2.0, max_retries: int = 5, timeout: float = 30.0):
        self.proxies = proxies or []
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.min_interval = min_interval  # мінімальна пауза між запитами до одного хоста
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_hit: dict[str, float] = {}
        self._lock = threading.Lock()
        self._bad: dict[str, float] = {}  # проксі -> до якого часу не використовувати

    def pick_proxy(self) -> str | None:
        if not self.proxies:
            return None
        now = time.time()
        alive = [p for p in self.proxies if self._bad.get(p, 0) < now]
        return random.choice(alive or self.proxies)

    def _throttle(self, host: str) -> None:
        with self._lock:
            wait = self._last_hit.get(host, 0) + self.min_interval - time.time()
            if wait > 0:
                time.sleep(wait + random.uniform(0, 0.5))
            self._last_hit[host] = time.time()

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        host = urlparse(url).netloc
        kwargs.setdefault("timeout", self.timeout)
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self._throttle(host)
            proxy = self.pick_proxy()
            proxies = {"http": proxy, "https": proxy} if proxy else None
            try:
                resp = self.session.request(method, url, proxies=proxies, **kwargs)
            except requests.RequestException as exc:
                last_exc = exc
                log.warning("%s %s failed via %s: %s", method, url, proxy, exc)
                if proxy:
                    self._bad[proxy] = time.time() + 300
            else:
                if resp.status_code not in RETRY_STATUSES:
                    return resp
                log.warning("%s %s -> %s (attempt %d)", method, url, resp.status_code, attempt + 1)
                if resp.status_code == 429 and proxy:
                    self._bad[proxy] = time.time() + 120
                last_exc = requests.HTTPError(f"{resp.status_code} for {url}", response=resp)
            time.sleep(min(60, 2 ** attempt) + random.uniform(0, 1))
        raise last_exc or RuntimeError(f"request failed: {url}")

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)

"""Тонка обгортка над Apify: запустити актор і отримати елементи датасету."""
import logging
import time

from ..net import HttpClient

log = logging.getLogger(__name__)
API = "https://api.apify.com/v2"


def run_actor(http: HttpClient, token: str, actor_id: str, run_input: dict, timeout: int = 900) -> list[dict]:
    """Запускає актор, чекає завершення (опитуванням) і повертає елементи датасету.

    actor_id у форматі "username~actor-name" (як у URL актора на apify.com).
    Токен іде в заголовку, а не в URL — щоб не потрапляв у логи помилок.
    """
    headers = {"Authorization": f"Bearer {token}"}
    resp = http.post(f"{API}/acts/{actor_id}/runs", headers=headers, json=run_input)
    resp.raise_for_status()
    run = resp.json()["data"]
    deadline = time.time() + timeout
    while run["status"] in ("READY", "RUNNING"):
        if time.time() > deadline:
            http.post(f"{API}/actor-runs/{run['id']}/abort", headers=headers)
            raise TimeoutError(f"{actor_id}: run {run['id']} не завершився за {timeout}s")
        time.sleep(5)
        run = http.get(f"{API}/actor-runs/{run['id']}", headers=headers).json()["data"]
    if run["status"] != "SUCCEEDED":
        raise RuntimeError(f"{actor_id}: run {run['id']} {run['status']}: {run.get('statusMessage')}")
    items = http.get(f"{API}/datasets/{run['defaultDatasetId']}/items", headers=headers,
                     params={"clean": "true"}).json()
    log.info("%s: %d items, $%.3f", actor_id, len(items), run.get("usageTotalUsd") or 0)
    return items


def first(d: dict, *keys, default=None):
    """Різні актори називають поля по-різному — беремо перше наявне (підтримує 'a.b')."""
    for key in keys:
        cur = d
        for part in key.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur is not None:
            return cur
    return default

"""Тонка обгортка над Apify: запустити актор і отримати елементи датасету."""
from ..net import HttpClient


def run_actor(http: HttpClient, token: str, actor_id: str, run_input: dict, timeout: int = 300) -> list[dict]:
    # actor_id у форматі "username~actor-name" (як у URL актора на apify.com)
    resp = http.post(
        f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items",
        params={"token": token, "timeout": timeout},
        json=run_input,
        timeout=timeout + 30,
    )
    resp.raise_for_status()
    return resp.json()


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

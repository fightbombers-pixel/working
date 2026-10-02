"""Безкоштовні підказки пошуку: Google, YouTube, Amazon.

Що люди реально вводять у пошук. Якщо трендовий запит є в підказках Amazon — є намір купити,
якщо в YouTube — попит на контент/навчання.
"""
import json
import logging

from ..net import HttpClient

log = logging.getLogger(__name__)


def google(http: HttpClient, q: str, youtube: bool = False, hl: str = "en") -> list[str]:
    params = {"client": "firefox", "hl": hl, "q": q}
    if youtube:
        params["ds"] = "yt"
    resp = http.get("https://suggestqueries.google.com/complete/search", params=params)
    resp.raise_for_status()
    data = json.loads(resp.content.decode("utf-8", errors="replace"))
    return [s.lower() for s in data[1]]


def amazon(http: HttpClient, q: str, marketplace: str = "ATVPDKIKX0DER") -> list[str]:
    # ATVPDKIKX0DER — amazon.com; A1F83G8C2ARO7P — amazon.co.uk
    resp = http.get("https://completion.amazon.com/api/2017/suggestions",
                    params={"mid": marketplace, "alias": "aps", "prefix": q})
    resp.raise_for_status()
    return [s["value"].lower() for s in resp.json().get("suggestions", [])]


def expand(http: HttpClient, seed: str, source: str = "google", letters: str = "abcdefghijklmnopqrstuvwxyz") -> set[str]:
    """seed + 'a'..'z' → сотні підказок на одне слово."""
    fn = {"google": google, "youtube": lambda h, q: google(h, q, youtube=True), "amazon": amazon}[source]
    out: set[str] = set()
    for q in [seed] + [f"{seed} {c}" for c in letters]:
        try:
            out.update(fn(http, q))
        except Exception as exc:
            log.warning("%s autocomplete %r: %s", source, q, exc)
    return out

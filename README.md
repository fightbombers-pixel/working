# niche_finder

Пошук бізнес-ніш: збирає болі людей з **Reddit, X, Threads**, тренди з **Pinterest і Google Trends**,
через Claude перетворює пости на конкретні ідеї, рахує бал і видає тижневий звіт.

```
Reddit / X / Threads ──► пости з фразами болю ("I wish there was", "would pay for", ...)
                                   │
                                   ▼
                     Claude: проблема, аудиторія, ключове слово, готовність платити
                                   │
Pinterest Trends + Google RSS ─────┤  (збіг з трендами = бонус)
Google Trends 5y ──────────────────┤  (ріст / сезонність)
                                   ▼
                    бал 0–15 → reports/niches_YYYY-MM-DD.{csv,md}
```

## Встановлення

```bash
pip install -r requirements.txt
cp .env.example .env   # заповнити ключі
```

## Джерела і ключі

**Найпростіше: один `APIFY_TOKEN` закриває Reddit, X, Threads і Pinterest.** Apify сам використовує
резидентні проксі, тому працює й із серверів/хмари, де Reddit і X блокують прямі запити.

| Джерело | Через Apify (за замовчуванням) | Офіційний API (якщо є ключ — має пріоритет) |
|---|---|---|
| Reddit | `trudax~reddit-scraper-lite` | `REDDIT_CLIENT_ID/SECRET` (app типу *script*) |
| X | `apidojo~tweet-scraper` | `X_BEARER_TOKEN` (платний) |
| Threads | `igview-owner~threads-search-scraper` | `THREADS_ACCESS_TOKEN` (потрібен app review у Meta) |
| Pinterest Trends | `automation-lab~pinterest-trends-scraper` | `PINTEREST_ACCESS_TOKEN` (бізнес-акаунт) |
| Google Trends | — | без ключів (RSS + pytrends) |
| Hacker News | — | без ключів (вимкнути: `INCLUDE_HACKERNEWS=0`) |

Актори можна замінити змінними `APIFY_REDDIT_ACTOR`, `APIFY_X_ACTOR`, `APIFY_THREADS_ACTOR`,
`APIFY_PINTEREST_ACTOR`. Якщо новий актор повертає дані в іншому форматі, у лозі буде попередження
зі списком його полів — тоді треба поправити `parse()` у `sources/apify_social.py`.

**Аналіз постів:** або `ANTHROPIC_API_KEY` (команда `analyze`, повністю автоматично), або без ключа —
`export` → проаналізувати файл у чаті з Claude → `import-ideas` + `mark-analyzed`.

## Запуск

```bash
python -m niche_finder run          # усе: збір → аналіз → тренди → звіт
python -m niche_finder collect      # тільки збір постів
python -m niche_finder analyze      # тільки аналіз нових постів
python -m niche_finder trends       # Google/Pinterest тренди + перевірка росту
python -m niche_finder report       # звіт з того, що вже в базі

# аналіз без API-ключа (наприклад, у чаті з Claude):
python -m niche_finder export --file data/posts_export.json
python -m niche_finder import-ideas --file data/ideas.json
python -m niche_finder mark-analyzed --file data/posts_export.json
```

Запускайте раз на тиждень (cron): `0 9 * * 1 cd /path/to/repo && python -m niche_finder run`.
Дані накопичуються в `data/niches.db` (SQLite), тож з кожним тижнем видно динаміку.

## Бал ніші (0–15)

| Компонент | Бали | Логіка |
|---|---|---|
| Згадки | 0–3 | log₂(1 + кількість постів) |
| Резонанс | 0–3 | log₁₀(1 + лайки + коментарі) |
| Готовність платити | 0–3 | середня оцінка Claude |
| Кросплатформність | 0–2 | біль є на 2–3 платформах одночасно |
| Ріст Google 5y | 0–3 | останній рік / попередній; x2 = 3 бали |
| В трендах | +1 | ключ є в Pinterest або Google трендах |
| Сезонність | −1 | щорічні хвилі (автокореляція > 0.6) |

## Проксі

**Для більшості джерел проксі не потрібні:**

- **Reddit з OAuth** — офіційні ліміти (~100 запитів/хв), проксі не потрібні. Без OAuth з VPS/датацентру
  Reddit часто відповідає 403/429, і тоді простіше зареєструвати app, ніж купувати проксі.
- **X / Threads / Pinterest** через офіційний API — проксі не потрібні. Через Apify — проксі вже включені в Apify.
- **Google Trends RSS** — 9 запитів на день, проксі не потрібні.

**Проксі потрібні в одному місці — перевірка росту в Google Trends (pytrends).** Google швидко дає 429,
особливо IP з датацентрів (VPS, хмара).

- **Тип:** *residential rotating* (резидентні з ротацією). Datacenter-проксі Google майже завжди банить,
  безкоштовні проксі — не варто взагалі.
- **Кількість:** не потрібно купувати список IP. Достатньо **одного gateway-ендпоінта** з ротацією
  (провайдер сам міняє IP на кожен запит), наприклад: `http://user:pass@gate.provider.com:7000`.
- **Обсяг:** 25 ключових слів на тиждень — це десятки мегабайт на місяць. Найменшого пакета (~1 GB)
  вистачить на місяці. Оплата зазвичай за гігабайт, у резидентних — кілька доларів за GB.
- **Альтернатива проксі:** платний SerpApi / DataForSEO для Google Trends — стабільніше, але дорожче на великих обсягах.

Проксі задаються в `.env` (`PROXY_URLS` або `PROXY_FILE`). `HttpClient` автоматично ротує їх і на кілька
хвилин виключає ті, що отримали 429 або помилку з'єднання.

## Обмеження

- Офіційні API (Reddit, X, Threads, Pinterest) — легальний шлях. Скрейпінг через Apify — сіра зона ToS.
- Внутрішні формати Apify-акторів різні; парсери беруть поля з кількох типових назв, але новий актор може потребувати правки.
- Google Trends показує відносний інтерес, а не обсяги — абсолютні цифри дивіться в Keyword Planner/Ahrefs.
- Ніша з високим балом — кандидат для перевірки (лендінг, тестова реклама), а не гарантія попиту.

## Тести

```bash
python -m pytest -q
```

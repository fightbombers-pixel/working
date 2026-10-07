# niche_finder — вся логіка проєкту

Документ для переносу на локальний комп'ютер: що ми будували, як це працює, що з'ясували на живих даних,
скільки коштує і як запускати. Код — у папці `niche_finder/`, дані — `data/niches.db`, звіти — `reports/`.

---

## 1. Мета

Знаходити **товари й ніші, які починають рости**, раніше за інших, і використовувати це для:

- **шортсів** (TikTok / Reels / YouTube Shorts / Pinterest) з товаром у кадрі, що їдуть на трендах;
- монетизації через **TikTok Shop affiliate**, **Amazon Associates** (Pinterest дозволяє посилання прямо в піні)
  або власний товар/дропшипінг.

Головний принцип: **тренд товару** (що почали шукати/купувати) + **трендовий формат** (хук, звук, стиль) + швидкість
(знімати в перші 1–3 тижні тренду).

---

## 2. Два режими роботи

### Режим A — `scan`: безкоштовний скан трендів (основний, $0)

```
seed-слова (app, kit, organizer, dupe, for dogs…)
   └─► Google Trends «rising» related queries за 3 міс., по кожній країні   (pytrends, безкоштовно)
         └─► фільтр шуму (казино, логіни, спорт, новини, знаменитості частково)
               └─► перевірка попиту через автодоповнення:                    (безкоштовно)
                     Amazon  → є намір купити
                     YouTube → є попит на відео/навчання
                     Google  → люди уточнюють запит
                           └─► бал + звіт reports/trends_YYYY-MM-DD.{md,csv}
```

Запуск:
```bash
python -m niche_finder scan --geos US,GB,CA,AU
python -m niche_finder scan --geos US --seeds "dupe,organizer,kit,gift for her"
```
~7 хв на країну для 46 слів. Повторний запуск того ж дня продовжує з місця зупинки.

**Бал scan (≈0–10):**
| Компонент | Формула |
|---|---|
| Ріст | `min(4, log10(% росту))` — 100% → 2, 1000% → 3, Breakout(5000+) → ~3.7 |
| Кілька країн | `min(2, країн − 1)` |
| Amazon | `min(2, підказок / 3)` |
| YouTube | `min(1, підказок / 4)` |
| Google | `min(1, підказок / 5)` |

Як читати: +100 000% = ріст майже з нуля (новий, але малий обсяг). Найнадійніше — **росте в 3–4 країнах і є
в підказках Amazon**. Amazon = 0 при сильному рості = попит є, пропозиції ще немає (рання ніша).

### Режим B — `collect → analyze → trends → report`: болі з соцмереж (платний, шумний)

```
Reddit / X / Threads / Hacker News  — пости з фразами болю ("I wish there was", "would pay for"…)
   └─► аналіз: проблема, аудиторія, ключове слово, готовність платити 0–3
         (Claude API з ANTHROPIC_API_KEY, або вручну: export → аналіз у чаті → import-ideas)
   └─► Google Trends 5 років (ріст, сезонність) + Pinterest/Google тренди (бонус)
   └─► бал 0–15 → reports/niches_YYYY-MM-DD.{md,csv}
```

Бал режиму B: згадки `log2(1+n)` (≤3) + резонанс `log10(1+лайки+коменти)` (≤3) + готовність платити (≤3)
+ кросплатформність (≤2) + ріст Google 5y `(ratio−1)·3` (≤3) + в трендах (+1) − сезонність (−1).

**Висновок з живих прогонів:** режим B дає мало корисного сигналу на гроші (див. розділ 4). Для шортсів з товарами
основним має бути режим A + тренди Pinterest.

---

## 3. Джерела: що працює, що ні, скільки коштує (перевірено жовтень 2026)

| Джерело | Як | Статус | Ціна |
|---|---|---|---|
| **Google Trends rising** | pytrends `related_queries()` | ✅ працює; інколи 429 → повтори з паузою | $0 |
| **Google Trends 5y ріст** | pytrends `interest_over_time()` | ✅; для запитів з малим обсягом (>30% нулів) ріст = None | $0 |
| **Google Trending Now** | RSS `trends.google.com/trending/rss?geo=US` | ✅, але це новини/спорт — лише бонус | $0 |
| **Amazon autocomplete** | `completion.amazon.com/api/2017/suggestions` | ✅ | $0 |
| **Google / YouTube autocomplete** | `suggestqueries.google.com` (`ds=yt` для YouTube) | ✅ | $0 |
| **Hacker News** | Algolia API | ✅, але аудиторія — технарі | $0 |
| **Pinterest Trends** | Apify `automation-lab~pinterest-trends-scraper` | ✅; коди ринків: `US, GB+IE, CA, DE, FR, BR, AU+NZ, IN…`; trendTypes: `growing, top_monthly, top_yearly, seasonal`; поле назви — `term`; фільтр `keywordsToInclude` | ~$0.001 за тренд |
| **Reddit** | напряму з хмарних серверів — 403; з домашнього IP .json зазвичай працює; через Apify `fatihtahta~reddit-scraper-search-fast` | ✅ через Apify | ~$1.5 / 1000 постів |
| **X (Twitter)** | Apify `kaitoeasyapi~twitter-x-data-tweet-scraper-pay-per-result-cheapest` (усі запити через OR в одному запуску; `from:""` обов'язково, інакше актор бере свій приклад) | ✅, але багато шуму | ~$0.25 / 1000 |
| ~~X через `apidojo~tweet-scraper`~~ | ліміт запусків на місяць для Free-тарифу Apify | ❌ | — |
| **Threads** | Apify `futurizerush~meta-threads-scraper` (`mode: search`, короткі запити, **без** `search_language`, мін. 10 постів) | ⚠️ працює, але переважно спам | ~$2.5 / 1000 + $0.02 за запуск |
| **TikTok Creative Center** | з 2026 за логіном (TikTok One); API вимагає підпис; Apify-актори або просять **cookies акаунта** (не давати!), або анонімно віддають лише 3 хештеги | ❌ автоматично; ✅ вручну в браузері | — |
| **Etsy autocomplete** | 403 | ❌ | — |

**Apify тариф Free:** $5 на місяць. Повний прогін режиму B ≈ $0.90 (Reddit $0.67 + X $0.03 + Threads $0.08 +
Pinterest $0.12). Перший місяць витрачено $3.85 — переважно на налагодження.

---

## 4. Що з'ясували на живих даних

### Режим B (болі з соцмереж), 1–2 жовтня 2026
- Зібрано 1 093 пости, витягнуто 127 ідей → 30 ніш зі ≥2 згадками.
- **Reddit** — найкраще джерело запитів «is there an app for…». **X** — особисті скарги, політика.
  **Threads** — спам («cash app»), локальні оголошення. **HN** — технічні ніші.
- Топ: порівняння медстраховки, застосунок для мов, пошуковик без AI-сміття, OSINT-інструменти,
  застосунок меж ділянки, безалкогольні альтернативи, дистрибуція музики.
- Висновок: 2–4 згадки на нішу — слабкий сигнал; дорого за якість.

### Режим A (scan), 2 жовтня 2026 — 1 950 rising-запитів, 4 країни, $0
| Ніша | Ріст 3 міс. | Країни | Попит |
|---|---|---|---|
| Натуральні GLP-1 добавки («natural ozempic») | ×100–4000, 7+ варіантів | US, GB, CA | Amazon 10/10 — **ризик: health claims, платформи банять** |
| Органайзери (плоскогубці, аптечка, комора на двері) | +14 000…30 000% | AU, GB | Amazon 10, YouTube до 10 |
| Дитячі DIY-набори (сквіші, стрази, 3D-ручка) | +100…1 000% | 3 | Amazon 10, YouTube 9 |
| Книжкові / хорор підписочні бокси | +29 000…32 000% | US, GB | Amazon мало → рання ніша |
| Підписка на іграшки для собак | +220 000% | AU | Google 8 |
| Натуральна косметика своїми руками | +350…650% | GB, US, CA | YouTube 10, Amazon 0 → набори/контент |
| Товари для сеніорів | +50…450% | 3 | Amazon 10 |
| Відео для котів/собак («netflix for cats») | +18 000…21 000% | усі 4 | YouTube 10 → канал |
| Ефірні олії для сну | +550% | 3 | Amazon 10 |
| Добавка «мікропластик детокс» | +25 500% | US, AU | товарів ще немає |

### Товари під шортси, 7 жовтня 2026
- Seed **«dupe»** — найсильніший товарний сигнал: Pottery Barn rug dupe (+8 200%), ghost plate dupe (+3 900%),
  парфуми-дупи (Lattafa, Hawas, Rayhaan), Lola blanket dupe, Hatch alarm clock dupe, Nugget couch dupe,
  Nutrafol dupe. Формат «дорогий бренд vs дуп» — один з найвірусніших у TikTok.
- Seeds «tiktok shop / viral tiktok / amazon finds» через Google — слабкі (люди гуглять сам сервіс).
- **Pinterest (US, товарні слова)**: росте сезонне — осінні/Хелловін нігті (+7 000…10 000% за місяць →
  **накладні нігті / press-on nails** як товар), осінні образи на дощ, **National Boyfriend Day gifts**
  (+4 500% mom), декорування гарбузів, hoco nails/outfits, dark celestial nails (×15 за рік).

---

## 5. Конвеєр шортсів (план)

```
1. Тренди товарів        scan (Google rising + Amazon/YouTube autocomplete)      $0
2. Тренди Pinterest      Apify pinterest-trends + keywordsToInclude              ~$0.10
3. Трендові формати      TikTok Creative Center — вручну в браузері (5 хв/день): трендові звуки, хештеги, Top Ads з CTR
4. Бриф                  товар + чому росте + 3 хуки + звук/формат + сценарій 15 сек
5. Виробництво           реальний товар у кадрі (розпаковка, до/після, демо) + AI-сценарій/озвучка/субтитри/монтаж
6. Публікація            одне відео → TikTok + Reels + Shorts + Pinterest (з партнерським посиланням)
7. Аналітика             які товари/хуки дають переходи → більше такого
```

Правила:
- **Не 100% згенероване відео**: YouTube урізає «масовий неоригінальний» контент, TikTok/Instagram вимагають
  мітку AI і гірше просувають. Генерувати — сценарій, хук, озвучку, монтаж; товар — реальний.
- Формула: **трендовий формат/хук + товар, що вирішує видиму проблему за 3 секунди**.
- Уникати health claims (GLP-1, схуднення) — бан контенту/реклами.
- Партнерські посилання — з розкриттям (#ad / #affiliate).
- Тест гіпотези: **30–50 шортсів в одній ніші за 2–3 тижні**, далі масштабувати те, що дає переходи.

Монетизація: TikTok Shop affiliate (10–20%, безкоштовні зразки; перевірте доступність для вашої країни) →
Amazon Associates + Pinterest/Shorts (1–10%, потрібні продажі протягом 180 днів) → свій товар/дропшипінг.

---

## 6. Структура коду

```
niche_finder/
  cli.py              команди: scan, collect, analyze, trends, report, run, export, import-ideas, mark-analyzed
  scan.py             режим A: SEEDS, фільтр шуму NOISE, check_demand, score, звіт trends_*
  config.py           налаштування з .env, PAIN_QUERIES
  db.py               SQLite-схема (posts, ideas, trending, trend_checks, community_stats) + rising, demand у scan.py
  net.py              HttpClient: ротація проксі, ліміт частоти по хосту, повтори на 429/5xx
  analyze.py          аналіз постів через Claude API (структурований JSON, fallbacks)
  score.py            бал режиму B
  report.py           звіт niches_*
  sources/
    google_trends.py  RSS, rising(), check() ріст/сезонність (pytrends з retries=0 — обхід бага urllib3 2.x)
    autocomplete.py   Google / YouTube / Amazon підказки
    apify.py          запуск актора (async + опитування), токен у заголовку
    apify_social.py   Reddit / X / Threads / Pinterest через Apify: build_input, parse, DEFAULT_CAPS
    reddit.py, x.py, threads.py, pinterest.py   офіційні API (якщо є ключі)
    hackernews.py     Algolia HN
tests/test_pipeline.py
```

База `data/niches.db` (SQLite) містить усе зібране: `posts` (1 093), `ideas` (127), `rising` (2 658),
`demand` (326), `trending` (690, вкл. Pinterest), `trend_checks` (30). Відкрити: `sqlite3 data/niches.db` або DB Browser for SQLite.

---

## 7. Запуск на локальному комп'ютері (Mac / Linux)

```bash
# 1. Розпакувати архів (або git clone гілки claude/happy-bell-kk5lt2)
cd niche_finder_project

# 2. Python 3.11+ і віртуальне середовище
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Ключі (не обов'язково для scan)
cp .env.example .env
#   APIFY_TOKEN=...          — для Reddit/X/Threads/Pinterest через Apify
#   ANTHROPIC_API_KEY=...    — для автоматичного аналізу постів (analyze)

# 4. Перевірка
python -m pytest -q

# 5. Безкоштовний скан трендів
python -m niche_finder scan --geos US,GB,CA,AU
open reports/trends_$(date +%F).md

# 6. (опційно) соцмережі
python -m niche_finder run
```

Щотижня автоматично (cron, понеділок 9:00):
```
0 9 * * 1 cd /шлях/до/проєкту && .venv/bin/python -m niche_finder scan --geos US,GB,CA,AU
```

Локально бонус: з домашнього IP Reddit зазвичай працює без ключів і без Apify (`collect` сам спробує напряму,
якщо `APIFY_TOKEN` не задано).

---

## 8. Безпека

- **Перевипустіть токен Apify** — він був у чаті (Apify → Settings → API & Integrations → новий токен, старий видалити).
- `.env` у `.gitignore` і не входить в архів — створіть його заново.
- Не передавайте cookies акаунтів TikTok/Instagram стороннім сервісам.

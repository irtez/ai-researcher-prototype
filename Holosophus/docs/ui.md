# UI

Веб-интерфейс для запуска задач и просмотра сессий из `workdir/<session_id>/`.
Работает поверх `ui-api` (FastAPI), стримит события живого запуска через SSE.

---

## Стек

| Слой | Что |
|---|---|
| Фреймворк | React 19 + TypeScript |
| Сборка | Vite 6 |
| Данные | TanStack React Query (polling 5 s) |
| CSS | Кастомный, без UI-библиотек |

Компоненты — `ui/src/App.tsx`. Стрим — `ui/src/live.ts`. API — `ui/src/api/client.ts`.

---

## Роутинг и Layout

| URL | Что |
|---|---|
| `/` | Панель запуска новой задачи |
| `/sessions/:id/:tab` | Сессия на выбранной вкладке |

```
┌──────────────────────────────────────────┐
│  TopBar                                  │
├─────────┬─────────────────┬──────────────┤
│ Session │  NotebookTabs   │              │
│ Sidebar │─────────────────│  Run Panel   │
│         │  TabContent     │              │
├─────────┴─────────────────┴──────────────┤
│  ArtifactDrawer                          │
└──────────────────────────────────────────┘
                  PreviewModal (overlay)
```

## Вкладки

`brief` и `run` — всегда. Остальные — по наличию артефактов.

| Вкладка | Условие | Что показывает |
|---|---|---|
| **Обзор** (`brief`) | всегда | Промпт + источники литобзора + артефакты |
| **Гипотезы** (`proposals`) | `proposal_*` | Карточки со скорами |
| **Эксперименты** (`experiments`) | `experiment_log` / `metrics_*` | Таблицы метрик + MLE-лог |
| **Статья** (`paper`) | `pdf` / `latex_source` | PDF + фигуры |
| **Рецензия** (`review`) | `review_*` | Сильные / слабые / правки |
| **Детали запуска** (`run`) | всегда | Лента событий + Langfuse + артефакты |

---

## Живой запуск

`streamManagerRun` открывает SSE на `/agents/run/stream`. Каждый `AgentEvent`
обрабатывается `reduceAgentEvent` → обновляет `LiveRunState` (агенты, транскрипт,
маркеры инструментов). Состояние сохраняется в `localStorage` и через
`PUT /ui/sessions/:id/live-state` с дебаунсом 1 s.

---

## Многопользовательский режим, ёмкость и compute-выбор

UI рассчитан на 5–10 параллельных пользователей/прогонов поверх уже-конкурентного
control plane (CodeArkt, `app:5055`). Полная спека —
[`docs/plans/2026-06-30-multiuser-concurrent-ui.md`](plans/2026-06-30-multiuser-concurrent-ui.md).

### Идентичность и изоляция (Этап 1)

- Браузер при первом заходе генерит `client_id` (localStorage
  `holosophus.ui.clientId`) и шлёт его заголовком **`X-Holo-Client`** во все
  `/ui/*` и в `POST /agents/manager`.
- Сессия тегируется владельцем-файлом `<session>/.holosophus_ui/owner`. `ui-api`
  фильтрует по владельцу **и список** (`/ui/sessions`), **и детальные эндпоинты**
  (`/ui/sessions/:id`, `/artifacts`, `/summary`, `/manifest`, `/live-state`,
  `/artifacts/:id/{raw,preview}`) — чужой клиент или запрос без заголовка → `404`
  (существование чужой сессии не раскрывается).
- Legacy-сессии (без маркера) скрыты по умолчанию (флаг `UI_SHOW_LEGACY_SESSIONS`).
- Figures per-session: `<session>/figures/paperbanana` — симлинк на общий
  OUTPUT_DIR, так что параллельные прогоны не перезатирают фигуры друг друга.
- Базовый стек — **без авторизации**: `X-Holo-Client` это анонимный browser-id
  (удобство приватности, **не** граница безопасности — заголовок можно
  подделать). Реальный логин-гейт включается opt-in overlay'ем — см.
  «[Аутентификация и командный деплой](#аутентификация-и-командный-деплой-опционально)».

### Compute-выбор (Этап 3, SC3)

- В форме запуска — селектор **«Исполнитель»**: `Auto` / `Local (CPU)` /
  `GPU (remote)` (дефолт `Auto`). Значение уходит полем `compute_mode` в
  `POST /agents/manager`.
- Жёсткий потолок на доступный прогону набор solver-инструментов: `Local` →
  только local (`bash`, …); `GPU` → только remote (`remote_bash`, …); `Auto` →
  оба. Механизм — session-keyed реестр `codearkt/run_config.py` +
  `CodeActAgent._effective_tool_names` (без мутации общего инстанса → race-free).

### Ёмкость и очередь (Этап 2, SC4–SC6)

- Диспетчер в процессе `app:5055`: `asyncio.Semaphore(MAX_ACTIVE_SESSIONS)`
  (дефолт 5) на **корневые** прогоны. Вложенные делегаты (manager→solver, тот же
  `session_id`) слот **не** берут — иначе дедлок.
- Прогон сверх лимита встаёт в очередь и стримит `session_queued` /
  `session_running` / `session_position` (payload — JSON в `content`). UI
  показывает баннер «В очереди — позиция N из M» и сам переводит прогон в running
  при освобождении слота.
- **`GET /capacity`** (на `app:5055`, проксируется nginx на `:5059`) —
  **процесс-глобальный** снимок:
  `{sessions:{active,limit,queued}, gpu:{busy,waiting,slots}, local_exec:{busy,waiting,slots}, host:{load1/5/15,cpu_count,mem_used_pct}}`
  (метрики хоста — stdlib: `os.getloadavg` + `/proc/meminfo`, без `psutil`).
  `gpu`/`local_exec` — отдельные видимые очереди ресурсов (не сваленные в общий
  `sessions`-лимит), см. «GPU — текущее состояние» ниже. Панель ёмкости в шапке
  поллит его каждые 4 s.
- **Кросс-клиентская видимость:** `/capacity` НЕ owner-scoped — он общий для всех.
  Если пользователь A запустил прогон (`active=1`), у пользователя B с другого
  браузера/IP панель покажет «Слоты 1/5» (в пределах ~4 s). Список сессий при этом
  остаётся приватным: B видит **общую нагрузку**, но не сессии A.

### GPU — текущее состояние (важно)

- «GPU-доступ» = `compute_mode=GPU` → remote-solver → `mle_kit remote_bash`.
  Провайдер — конфиг деплоя (`REMOTE_PROVIDER`: `vast` / `cloudru` / `selectel`),
  не UI-выбор.
- **Single-box провайдеры (vast/cloudru).** App-уровневый admission-гейт
  (`GPU_SLOTS=1` по умолчанию) резервирует GPU на **весь прогон**: 2-й
  одновременный GPU-прогон встаёт в отдельную видимую GPU-очередь (события
  `resource=gpu`, позиция в `/capacity.gpu.waiting`), а не просто в общую
  очередь слотов `sessions`.
- **Multi-card (Selectel, до 8 карт на общем хосте).** Каждая сессия пинится к
  своей свободной карте (`CUDA_VISIBLE_DEVICES`, per-session реестр в
  `mle_kit`, дискавери через `nvidia-smi` — занятые внешними арендаторами карты
  пропускаются). Как только `mle_kit` после первой живой дискавери поднимает
  потолок выше 1, app-уровневый admission-гейт для GPU **отключается** —
  реальным гейтом становится реестр карт `mle_kit`, и несколько GPU-прогонов
  идут параллельно на разных картах без очереди, пока карты не закончатся.
  `/capacity.gpu` в этом режиме отражает реестр `mle_kit` напрямую
  (`busy` = число сессий с картой, `slots` = обнаруженный потолок).
- **Hard cap — структурно, для обоих классов провайдеров и обоих launch-путей**
  (UI и `scripts/run_research.py`): каждый remote-вызов видит **не больше
  одной** карты — для single-box индекс всегда `0` (no-op), для Selectel —
  реально пиннится к выданному индексу.
- TODO (следующий инкремент): 429-бэкофф на OpenRouter; идле-таймаут реклейма
  зависшей/крашнутой Selectel-сессии (сейчас — открытый вопрос спеки).

### Аутентификация и командный деплой (опционально)

Базовый стек авторизации не требует. Есть **два** независимых механизма — под
разные сценарии.

#### Вариант A — app-level логин (аноним видит UI, гейт только на запуск)

Рекомендуемый для командной версии, где UI должен быть виден без логина, а
запускать исследования могут только участники команды
(`docs/plans/2026-07-05-ui-login-and-per-account-queue.md`). Работает на
**базовом** (открытом) nginx — правок конфига не требует.

Включается заданием списка логинов в `.env` (значения — plaintext, `.env`
gitignored; сгенерировать секрет и пароли: `scripts/gen_auth_secret.sh
a.stepin m.mozikov`):

```
HOLO_AUTH_USERS=a.stepin:<pass>,m.mozikov:<pass>
HOLO_AUTH_SECRET=<64 hex>          # HMAC-ключ подписи токенов
HOLO_AUTH_TOKEN_TTL_DAYS=30
MAX_ACTIVE_SESSIONS=6              # общий потолок прогонов (оба движка)
PER_OWNER_MAX_ACTIVE_SESSIONS=3   # на аккаунт
```

Поведение:

- **Аноним видит UI**, но клик «Старт» открывает модалку входа; POST на бэк не
  уходит. `POST /agents/login` проверяет пароль (constant-time) и выдаёт
  подписанный bearer-токен (`codearkt.auth`).
- **Проверяемый токен, а не заголовок:** оба runner-процесса (`app:5055` и
  host-side `cc_runner:5061`) сами верифицируют HMAC-токен на `/agents/manager`
  — подделать `X-Holo-Client` нельзя. Верифицированный логин становится
  владельцем прогона (owner-маркер, Langfuse `user`, пер-аккаунтная квота).
- **Единая очередь ёмкости на оба движка:** при включённом auth оба процесса
  считают слоты через общий cross-process ledger в workdir
  (`.holosophus_capacity/admission.json`) — «6 суммарно / 3 на аккаунт», без
  различия CodeArkt vs Claude Code. `/capacity` показывает и `yours` (ваши N/3).
- Просмотр списка сессий остаётся soft-trust на `X-Holo-Client=<логин>`
  (доверенный контур; жёсткая граница — на запуске + квоте).
- Пустой `HOLO_AUTH_USERS` = auth выключен: dev/smoke работают без логина, как
  раньше (owner падает обратно на `X-Holo-Client`).

Токен шлётся каждым запросом → на не-loopback хосте терминируйте TLS спереди.
Для публичного деплоя притяните `app:5055` на `127.0.0.1` (как в
`docker-compose.auth.yml` / public-overlay), чтобы `/agents/*` нельзя было дёрнуть
в обход nginx (хотя он и так токен-гейтед).

#### Вариант B — HTTP Basic auth на весь сайт (полностью закрытый деплой)

Когда UI не должен быть виден анониму вовсе. Гейтит **всё** — SPA и все API:

```bash
docker compose -f docker-compose.yml -f docker-compose.auth.yml --profile ui up -d
```

Что включает `docker-compose.auth.yml`:

- **HTTP Basic auth на nginx** — предопределённый список логин/пароль в
  gitignored `docker/.htpasswd` (заводится `scripts/make_htpasswd.sh <user>
  [pass]`; `docker/.htpasswd.example` — рабочий пример `demo/demo`). Auth-конфиг
  `docker/nginx-ui-auth.conf` монтируется поверх запечённого в образ
  `nginx-ui.conf` — базовый образ и dev-режим не меняются.
- **Подмена идентичности:** nginx перезаписывает клиентский `X-Holo-Client` на
  проверенный `$remote_user`. Вся owner-изоляция (список сессий, артефакты,
  live-state) становится настоящей auth-границей — подделать чужого владельца из
  браузера нельзя.
- **Логин → Langfuse `user`:** аутентифицированный логин прокидывается в поле
  `user` трейса (per-session owner-реестр `codearkt.run_config` → tracer
  `holosophos.langfuse_openai_patch`), так что прогоны фильтруются по участнику
  команды. Без auth поле `user` пустое — как было.
- **Закрывает control-API:** `app:5055` (`/agents/*` — старт/отмена прогона)
  притягивается к `127.0.0.1`, чтобы его нельзя было дёрнуть в обход nginx.

Basic auth шлёт креды каждым запросом → на любом не-loopback хосте терминируйте
TLS спереди. Чтобы дополнительно стянуть остальные backend/MCP/langfuse порты на
localhost, доложите `docker-compose.selectel-public.yml`.

---

## API-эндпоинты (ui-api)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/ui/config` | Модель, агенты, типы артефактов |
| GET | `/ui/sessions` | Список сессий (`q` — поиск) |
| GET | `/ui/sessions/:id` | Детали + стадии + закреплённые артефакты |
| GET | `/ui/sessions/:id/summary` | Данные для вкладок |
| GET | `/ui/sessions/:id/artifacts` | Артефакты (опц. фильтр `tab`) |
| GET | `/ui/artifacts/:id/preview` | Файл для предпросмотра |
| GET/PUT | `/ui/sessions/:id/live-state` | Живое состояние |
| POST | `/agents/manager` | Запуск прогона (NDJSON-стрим); тело — `messages`, `session_id`, `compute_mode`, `stream` |
| POST | `/agents/cancel` | Отмена прогона |
| GET | `/capacity` | Ёмкость/очередь/нагрузка (процесс-глобально) |

Все `/ui/*` и `POST /agents/manager` несут заголовок **`X-Holo-Client`** (owner).
`/ui/*` обслуживает `ui-api:5058`; `/agents/*` и `/capacity` — `app:5055` (nginx
проксирует оба на `:5059`). `/capacity` — единственный не owner-scoped роут
(общая ёмкость видна всем).

---

## Локальная разработка

Terminal 1:

```bash
cd Holosophus
uv run --project holosophos python -m holosophos.ui_api --host 127.0.0.1 --port 5058
```

Terminal 2:

```bash
cd Holosophus/ui
pnpm install
pnpm dev -- --host 127.0.0.1 --port 5173
```

Открыть: `http://127.0.0.1:5173`

Vite проксирует `/ui/*` → `:5058`, `/agents/*` → `:5055`.

## Docker

```bash
cd Holosophus
docker compose --profile ui up --build -d
```

Открыть: `http://127.0.0.1:5062`

`ui-api` монтирует `./workdir:/workdir:ro`. API-ключи в браузер не попадают.

## Валидация

```bash
# Backend
cd Holosophus/holosophos && uv run pytest tests/test_ui_api.py -q

# Frontend
cd Holosophus/ui && pnpm build

# Docker Compose syntax
cd Holosophus && docker compose --profile ui config --quiet
```

Ручные проверки:

1. Открыть завершённую сессию.
2. Переключить вкладки: Brief, Experiments, Paper, Review, Run Details.
3. Открыть PDF, markdown, JSON, CSV, image, код из drawer.
4. Проверить нижнюю полоску артефактов — свёрнута по умолчанию.
5. Проверить узкий viewport (~390px).

Live run smoke (когда стек поднят):

```bash
cd Holosophus
docker compose up --build -d
python scripts/smoke_test_pipeline.py
```

Запустить небольшой debug-прогон из UI — активный агент, лента событий, артефакты обновляются без перезагрузки.

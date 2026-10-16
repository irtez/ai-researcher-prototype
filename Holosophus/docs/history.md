# Holosophos: история развития (Oct 2025 — Mar 2026)

102 коммита за 6 месяцев. Эволюция от форка с YAML-промптами до production-grade мультиагентной исследовательской системы на Claude Code.

---

## Oct 2025 — Зарождение (3 коммита)

Форк `holosophos` + первые инструменты. Заложена основа мультиагентной системы: 5 агентов (Librarian, MLE Solver, Proposer, Reviewer, Writer), YAML-промпты, Docker-инфраструктура, eval-фреймворк и REPORT.md.

**Ключевые коммиты:**
- `611c05c` (Oct 29) — add report
- `5e56d2e` (Oct 29) — add holosophos fork
- `cb6b4e8` (Oct 29) — add arxiv paper saving

---

## Feb 1–16, 2026 — Монорепо-консолидация (23 коммита)

Самый инфраструктурный период. Три отдельных репозитория стали единым монорепо.

- **Монорепо**: vendored `codearkt` (+9,876 строк) и `mle_kit` (+2,644) в единый репозиторий
- **Langfuse v1**: первая интеграция трейсинга для CodeAct-шагов
- **PaperBanana MCP**: интеграция сервиса диаграмм/графиков
- **Session management**: детерминистичные session ID, изоляция workspace по сессиям
- **DevOps**: preflight-проверки окружения, портативные Docker-билды, скрипты очистки

**Ключевые коммиты:**
- `ffa0a78` (Feb 15) — vendor codearkt into Holosophus monorepo
- `4ad2bb2` (Feb 15) — integrate mle_kit into monorepo
- `552cb7b` (Feb 12) — integrate holosophos module into main structure
- `1dcda68` (Feb 12) — Langfuse v1 integration + Gemini-3-Flash support
- `cdc40fa` (Feb 13) — PaperBanana MCP in docker-compose + writer tools
- `613a63f` (Feb 15) — isolate workspace artifacts under workdir per session_id

---

## Feb 17 – Mar 9 — Пауза (~3 недели)

Нет коммитов. Период эксплуатации и ручного тестирования системы.

---

## Mar 10–16, 2026 — Надёжность и observability (12 коммитов)

Фокус на устойчивости runtime и наблюдаемости.

- **Streaming stability**: таймауты и логирование для LLM-стримов, детекция "тишины" агента
- **Tool I/O hardening**: +257 строк в `tools.py` — устойчивая обработка ошибок инструментов
- **Observability**: глубокая интеграция Langfuse в бенчмарки (GAIA, Librarian, MLE Solver и др.)
- **PaperBanana upstream**: переход от vendored к pull-from-git с семантическими версиями
- **Документация**: архитектура, data topology, observability, тестирование

**Ключевые коммиты:**
- `fdc0c92` (Mar 10) — LLM stream timeout handling
- `d880584` (Mar 16) — tool I/O hardening (+257 lines)
- `7b70222` (Mar 15) — benchmark observability design overhaul
- `464fa8f` (Mar 16) — fetch PaperBanana from upstream
- `f2deaf5` (Mar 16) — architecture, data topology, testing docs

---

## Mar 16–27, 2026 — PR merge + PaperBanana (2 коммита)

Merge PR #6 (`new_features_new_life`). Финализация интеграции PaperBanana: Docker с upstream-репо, smoke-тесты, конфигурируемые таймауты.

**Ключевые коммиты:**
- `47f5fab` (Mar 16) — merge PR #6 new_features_new_life
- `464fa8f` (Mar 16) — fetch PaperBanana from upstream

---

## Mar 28–31, 2026 — Архитектурный pivot (45 коммитов)

Самый плотный и значимый период. Полная смена оркестратора и 13 итераций оптимизации.

### Claude Code как LLM-бэкбон
Выбросили CodeArkt/OpenRouter, перешли на Claude API + Claude Code оркестрацию. Создали 6 агентов в `.claude/agents/`, `.mcp.json`, Langfuse-хуки.

### AlphaXiv MCP
Новый приоритетный академический поиск через MCP-сервер.

### 13 итераций eval librarian
Качество поиска **35.8% → 76.8%** (+2.15x). Добавлены OpenAlex, CrossRef, DBLP, Google Scholar, S2, LLM-реформулирование запросов.

### Производительность
Параллельные API-вызовы: **86 мин → 23 мин** (3.7x ускорение).

### Observability
Устранены ghost-дубликаты в Langfuse, добавлен session binding к OTEL spans.

### Crash recovery
Журнал `log.jsonl` для mle-solver, интеграционные тесты на восстановление после падения контейнера.

### Context engineering
Аудит контекста с masking-дизайном, drop Phoenix (замена на Langfuse).

**Ключевые коммиты:**
- `22651ba` (Mar 28) — migrate orchestration to Claude Code as LLM backbone
- `cbeaef2` (Mar 28) — integrate AlphaXiv MCP server
- `91fbb0e` (Mar 28) — session log viewer with text search
- `7b72371` (Mar 29) — iter13: 76.8% recall, 25min runtime (best balance)
- `1c7937f` (Mar 29) — parallel API calls: 86min → 23min (3.7x)
- `2360b9d` (Mar 30) — fix Langfuse tracing: eliminate ghost duplicates
- `6f89d88` (Mar 31) — mle-solver artifact discipline + crash recovery tests
- `e90ae6a` (Mar 31) — context engineering research, drop Phoenix

---

## May 2026 — второй remote-провайдер: cloud.ru ML Space

Vast.ai остался default, рядом приехал cloud.ru ML Space (SR006) как второй remote-GPU-провайдер — для случаев, когда нужна РФ-юрисдикция, A100-80G/H100, или партнёрская квота. ADR [`0006-cloudru-provider.md`](decisions/0006-cloudru-provider.md).

- **Provider switch**: env-driven (`REMOTE_PROVIDER=cloudru|vast`), стандартные `docker-compose.yml` / `docker-compose.mcp.yml` понимают оба, Vast-юзеров не задело.
- **Static SSH endpoint** вместо динамического lifecycle (нет SDK для instance create/destroy в SR006). GPU выделяется per-job через `client_lib.Job` binary-mode, плюс jupyter-shell для plumbing.
- **Smoke / ops surface**: `make cloudru-{smoke,job-smoke,jobs-list,jobs-killall}`, скрипт `mle_kit_mcp/scripts/cloudru_smoke.py` проверяет SSH/NFS/RU-IP/internet + опциональный `--job` GPU-allocation probe.
- **Сетевой контур**: split-tunnel OpenConnect ВПН пропускает `89.232.0.0/12` мимо `utun*`, egress идёт через `en1` = real RU residential. `/32`-байпас не нужен пока ВПН split-tunnel.
- **Знание о провайдере** живёт в [`cloudru-mlspace.md`](cloudru-mlspace.md) — единая живая дока (доступ, NFS-volumes, conda envs, failure modes, scheduler-queue реальность).

**Ключевые коммиты:**
- `1932dab` — cloud.ru deployment glue + parallel-session agent isolation
- `b14518f` — ADR-0006 + operations doc + anti-patterns
- `d884a42` — smoke: RU-IP geo check + `--job` allocation probe
- `ef44fb2` — `/job_list` REST helper (list/killall в namespace)

---

## Сквозная тема: баги и hardening инструментов

Инструменты ломались на каждом этапе — и каждый раунд фиксов делал систему заметно устойчивей.

### Feb 2026 — после монорепо-миграции

Консолидация сломала пути и соединения:
- `5aa62bc` — mle_kit потерял session_id в путях, bash/llm_proxy не находили workdir
- `2b0e3fd` — manager не мог достучаться до mle_kit (отсутствовал host header в allowlist)
- `e42a8f0` — двойное вложение session_id в workspace-путях (`workdir/abc/abc/`)
- `f789d0c` — Langfuse tracing для CodeAct-шагов генерировал невалидную структуру

### Mar 10–16 — hardening после боевых прогонов

Первые реальные исследовательские сессии обнажили хрупкость:
- `fdc0c92` — LLM-стрим зависал без таймаута, агент "молча умирал"
- `44cbd64` — детекция prolonged silence (агент молчит >N секунд — логируем)
- `6f8c831` — агенты пытались `import numpy` в execute-блоках, где доступен только stdlib. **Фикс через промпты**, не код
- `d880584` — **+257 строк** в `tools.py`: coercion типов (tuple→list, datetime→str), экранирование ArXiv-запросов, нормализация авторов, извлечение ошибок из CallToolResult

### Mar 28–31 — фиксы при смене бэкбона

Переход на Claude Code вскрыл новый класс проблем:
- `2cc7d73` — Langfuse-хуки ожидали dataclass, получали dict → crash. Добавлен `.get()` + немедленный flush
- `d46eee7` — хук мог уронить весь SDK. Обёрнуто в try/except, отфильтрованы шумные спаны (ToolSearch, TodoRead)
- `2360b9d` — **ghost-дубликаты**: race condition между pre_tool_use и _handle_assistant_tool_uses создавала фантомные observation с фейковой ~90s latency. Решено: pre_tool_use только записывает start_time, run создаётся в post_tool_use
- `4075592` — старые langfuse-хуки спамили "PostToolUse:Bash hook error" в каждом вызове
- `c2332a5` — SearXNG: отключены нерабочие движки (aol, karmasearch, startpage), увеличены таймауты

### Дропнутые инструменты и сервисы

| Что | Когда | Почему |
|-----|-------|--------|
| Google Scholar (scholarly) | Mar 29 (`544f4fd`) | Rate limit после batch-прогонов, нестабильный scraping |
| Phoenix observability | Mar 31 (`e90ae6a`) | Дублировал Langfuse, усложнял стек без пользы |
| SearXNG engines (aol, karmasearch, startpage) | Mar 29 (`c2332a5`) | Нерабочие/нестабильные, загрязняли результаты |
| Тяжёлый paper archival ritual | Mar 29 (`0571356`) | 4-5 tool calls на статью, PDF-скачивание ≠ ценность |

---

## Сквозная тема: эволюция агентских промптов

Промпты прошли через 3 отчётливые фазы и сжались в ~5 раз.

### Фаза 1: "скажи всё" (Oct 2025 – Feb 2026)

Оригинальные YAML-промпты из форка — длинные, prescriptive, с жёстко заданными workflow. Каждый агент получал полный перечень инструкций "как делать работу". Работало, но:
- Агенты следовали инструкциям буквально, даже когда контекст требовал другого
- Промпты раздувались с каждым новым инструментом
- Архивальные ритуалы (4-5 вызовов на статью) тратили бюджет на низкоценностную работу

### Фаза 2: фиксы через промпты (Mar 10–28)

Баги исполнения начали чиниться **через промпты**, а не код:
- `6f8c831` (Mar 10) — агенты крашились на `import numpy`. Вместо фикса executor добавили CRITICAL-секцию во все промпты: "только stdlib в execute-блоках, ML-пакеты через bash()"
- `63c5f91` (Mar 28) — при миграции на Claude Code повторили то же для всех 5 агентов
- `740424c` (Mar 15) — агенты выдавали транслит вместо English. Добавлена language policy в system prompt

Ключевой инсайт: **промпт — это API-контракт**, не документация. Баг в промпте = баг в продакшене.

### Фаза 3: evidence-based simplification (Mar 29–31)

Радикальное сокращение на основе A/B-тестов и исследований:

**Librarian: 1500 → 250 токенов (–83%)**
- `8906697` (Mar 30) — убраны prescriptive workflow, таблицы инструментов, архивальные ритуалы
- Оставлены 3 приоритета (AlphaXiv → ArXiv/S2 → Web) + принципы поиска
- Архитектурный сдвиг: от "агент владеет workflow" к "caller задаёт формат через task description"
- Обоснование: Anthropic Claude 4.6 guide (descriptive > prescriptive), Augment Code (400-800 токенов — sweet spot)

**MLE-Solver: verbose → minimal+ (–46% токенов, +2.5% качество)**
- `5cedd61` (Mar 29) — A/B на 7 задачах:
  - Full prompt: 117 tool calls, AUC 0.871
  - Minimal+: 75 tool calls, AUC 0.893 ← **лучше и эффективнее**
  - Vanilla: 56 calls, AUC 0.871
- Оставлено: "Make one big change at a time" + список MCP-инструментов

**Proposer: полная переработка workflow**
- `62a5fd1` (Mar 29) — 3 стратегии генерации (MCP tool / собственное рассуждение / gap-driven), unified scoring, refinement loop до 3 итераций, novelty verification через arxiv_search
- `a7f1172` (Mar 29) — scoring переехал с 1-10 на 1-5 шкалу (исследования: лучшая LLM-human alignment). Лениентность **исправлена**: слабые proposals получают 2/5 вместо 5/10

**Librarian search: 13 итераций recall**
- iter1 (baseline): 35.8% recall
- `8c7c142` — +4 search tools (OpenAlex, CrossRef, DBLP, S2): +35.8%
- `71b8bec` — S2 retry + short query: 71.6%
- `544f4fd` — LLM query reformulation, drop Google Scholar: 73.3%
- `7b72371` — iter13 финал: **76.8% recall**, 25 мин runtime

---

## Выводы

### Четыре итерации за 6 месяцев

**Итерация 1: CodeArkt + монолит (Oct 2025 – Feb 12)**

Форк holosophos, 5 агентов с YAML-промптами, оркестрация через CodeArkt + OpenRouter. Монолитный сервер, нет изоляции сессий. Первые генерации упирались в базовые проблемы — агенты не могли сохранять найденные статьи, что привело к добавлению arXiv paper saving.

**Итерация 2: problem-based development (Feb 12 – ~Mar 20)**

Пересмотр подхода после первой итерации: вместо "допиливания фич" — цикл "гоняем генерации → логируем ошибки → дебажим → гоняем снова".

Инфраструктура:
- Три репо схлопнулись в монорепо, появились Docker MCP-сервисы (academia, mle_kit), изоляция workspace по session_id
- Langfuse — observability для агентов и tool execution, переписан дважды
- PaperBanana MCP — генерация диаграмм/графиков, интегрирован в writer

Дебаг тулов — основная активность: битые пути session_id, зависания LLM-стрима, крэши на недоступных пакетах в execute-блоках, tool I/O hardening.

Пауза (Feb 17 – Mar 9) — эксплуатация и осмысление результатов.

**Итерация 3: расширение возможностей агентов (~Mar 20 – Mar 31)**

Фокус сместился с инфраструктуры на качество самих агентов — их промпты, инструменты и eval-метрики.

Новые инструменты:
- AlphaXiv MCP — приоритетный академический поиск по 2.5M статей с embedding similarity
- 4 search tools (OpenAlex, CrossRef, DBLP, S2) в librarian

Эксперименты с промптами и eval:
- Librarian: recall 35.8% → 76.8% через множество итераций (добавление/дроп search tools, LLM query reformulation). Prompt ~1500 → ~250 токенов — результат стал лучше
- MLE-Solver: minimal prompt побеждает verbose по качеству при –46% токенов
- Proposer: scoring 1-10 → 1-5 (лучшая LLM-human alignment), refinement loop

Параллельно развивается ветка `claude-code-as-back-llm` — эксперимент с Claude Code + Claude API как альтернативным оркестратором вместо CodeArkt/OpenRouter.

**Итерация 4: owner-agent development (текущая)**

Переход от общей разработки к модели "один овнер — один агент". У каждого агента ответственный, который независимо подбирает бенчмарк, экспериментирует с промптами/инструментами/моделями и гоняет eval.

С частью агентов уже выставлены метрики и архитектура по росту — librarian прошёл серьёзную прокачку (recall 76.8%, prompt –83%, runtime 3.7x). С остальными в процессе согласования. Изменения изолированы — итерации по одному агенту не ломают других.

### В числах

| | Итерация 1 (Oct) | Итерация 3–4 (Mar) | Δ |
|---|---|---|---|
| Агентов | 5 (YAML) | 6 | +1 |
| Оркестратор | CodeArkt + OpenRouter | + Claude Code (ветка) | альтернативный путь |
| MCP-серверов | 0 | 4 (academia, mle_kit, paperbanana, alphaxiv) | — |
| Librarian recall | — | 76.8% | множество итераций |
| Librarian prompt | ~1500 tok | ~250 tok | –83% |
| MLE-Solver prompt | verbose | 1 предложение | –46% tok, +2.5% quality |
| Eval runtime | 86 мин | 23 мин | 3.7x |
| Observability | нет | Langfuse + OTEL | — |
| Crash recovery | нет | log.jsonl + tests | — |
| Модель разработки | общая | owner-agent | — |

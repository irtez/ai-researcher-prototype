# cloud.ru ML Space — Jupyter Server, оперативные заметки

> Living doc. Дописываем по мере того, как накапливаются находки.
> Решение, как мы туда пришли: [ADR-0006](../decisions/0006-cloudru-provider.md).

## Доступ

- Endpoint: `ssh -i ~/.ssh/sber_id_rsa -p 2222 avatars-2.ai0001053-01349@ssh-sr006-jupyter.ai.cloud.ru`
- Регион **SR006**, выделение GPU **по джобам** через `client_lib` SDK (см. ниже). На регионе доступны **V100** (1–16 cards), **A100** 40G/80G (1–8 cards), и **`a100plus.*`** — это, по словарю самого SDK (`get_available_resources_count` column "name"), **H100 80GB** (1–8 cards). То есть H100 есть, просто Сбер их маркирует в SDK как `a100plus`. Не путать.
- Можно взять выделенную в ноутбук на день, ≤2 cards (со слов M. Mozikov, 02.05.2026) — но в `client_lib` API "attach GPU to notebook" **не выставлено**, видимо это веб-UI-фича. Через SSH доступен только path "submit job".
- Аккаунт оплачивается командой-партнёром, не личной картой.

## Сетевая обвязка с РФ-IP при поднятом ВПН

- Хост-сетап OpenConnect-bypass'а: `~/notes/russian-traffic-via-openconnect-vpn.md` (вне репо).
- Ключ: `~/.ssh/sber_id_rsa`, `chmod 600`, **никуда не коммитим**.
- Контейнерный egress (split-tunnel ВПН): подсеть cloud.ru `89.232.0.0/12` **не** попадает в `utun*` маршруты, трафик идёт напрямую через `en1` = реальный RU residential. Проверено `tcpdump`-ом из `holosophus-mle_kit-1` (2026-05-03). **`/32`-байпас не нужен** пока ВПН в split-tunnel.
- Если когда-нибудь ВПН переключат на full-tunnel — запускаем `SBER_IFACE=en1 ~/bin/sber-up.sh` после каждого реконнекта (см. ноут).

## Изоляция параллельных сессий (heavy / light)

Можно запустить **две research-сессии в параллель** так, чтобы они **не делили
remote и local mle-solver'ов**. Достигается двумя exports'ами в shell перед
запуском оркестратора (в .env держать **не нужно** — разные сессии должны
видеть разные значения):

```bash
# Сессия A — тяжёлая, идёт на cloud.ru
export MLE_SOLVER_IS_REMOTE=true
export REMOTE_PROVIDER=cloudru
python scripts/run_research.py "<heavy task>"

# Сессия B (другой терминал) — лёгкая, локально
export MLE_SOLVER_IS_REMOTE=false
python scripts/run_research.py "<light task>"
```

Эффект:
- В A менеджер видит **только** `mle-solver-remote` — не может случайно
  сорваться на локальный bash.
- В B менеджер видит **только** `mle-solver` — не дёргает remote ресурсы.
- Если флаг **не задан** → дефолт: рендерятся **оба** под-агента, менеджер
  выбирает per-task. Это исходное поведение Claude Code path до 2026-05-03.
- CodeArkt всегда был бинарным; его поведение не изменилось.

Реализация: `scripts/sync_claude_agents.py` фильтрует `mle_solver*.yaml`
по env'у `MLE_SOLVER_IS_REMOTE` и удаляет stale `.claude/agents/*.md` от
предыдущего прогона с другой настройкой.

## Holosophus-интеграция

Канон: [ADR-0006](../decisions/0006-cloudru-provider.md).

```bash
# 1. положить ключ + .env (один раз)
mkdir -p ~/.ssh && chmod 700 ~/.ssh
nano ~/.ssh/sber_id_rsa             # вставить полученный ключ
chmod 600 ~/.ssh/sber_id_rsa

# в .env (НЕ в .env_example):
REMOTE_PROVIDER=cloudru
CLOUDRU_SSH_USER=avatars-2.ai0001053-01349
CLOUDRU_SSH_KEY_HOST_PATH=~/.ssh/sber_id_rsa

# 2. поднять mle_kit (cloudru-конфиг встроен в docker-compose, переключатель — REMOTE_PROVIDER в .env)
make up           # либо `make up-mcp`, оба читают cloudru-env когда REMOTE_PROVIDER=cloudru

# 3. smoke
make cloudru-smoke
```

Полный pipeline-смоук:

```bash
MLE_SOLVER_IS_REMOTE=true REMOTE_PROVIDER=cloudru \
  python scripts/run_research.py "Print torch version, GPU model and hostname. No training."
```

## Smoke-checks (быстрая проверка "готова ли cloud.ru дать нам GPU")

Два уровня:

| Команда | Что проверяет | Время | Когда запускать |
|---|---|---|---|
| `make cloudru-smoke` | SSH/NFS/internet plumbing + **RU-egress IP** + (warn-only) GPU/torch на jupyter-shell | ~5 сек | После каждого `make up-mcp`, перед началом сессии. |
| `make cloudru-job-smoke` | Всё выше **+ submit реального binary-job на 1× A100, дождаться status=Running, прочитать `nvidia-smi`, kill** | 1-30 мин | Когда подозреваешь cluster scheduler issue. До запуска heavy research-сессии. |
| `make cloudru-jobs-list` | Список всех jobs в namespace + явный flag активных (Running/Pending/Submitted) | ~2-3 сек | До и после debug-сессий — убедиться что ничего не висит на квоте. |
| `make cloudru-jobs-killall` | Kill всех Running/Pending/Submitted в нашем namespace. Idempotent (Failed/Completed пропускает) | ~1 сек/job | Hard reset когда `cloudru-jobs-list` нашёл забытое. |

**Что добавлено в smoke-чек 2026-05-07:**

- `ru_ip` REQUIRED-проверка через `ip-api.com` — assert `countryCode=RU`. Если ВПН split-tunnel-бэкап включился неправильно или маршруты на cloud.ru ушли через `utun*`, smoke fail-нет до того, как пользователь начнёт жечь часы на странных SLA.
- `gpu_job_alloc` (только при `--job`) — submit `Job.Type.binary` на `a100.1gpu.8C.243G`, polling каждые 15с, на первом `Running` reads `nvidia-smi` из pod stdout (`~/.mlspace-logs/<job>/retry.000/rank.0/stdout`), kill. Доказывает что namespace **может** получить GPU-pod (отдельно от "может SSH-нуть в jupyter").

**Известные failure modes для `cloudru-job-smoke`:**

- `PROBE_FAIL job did not reach Running within timeout` — cluster scheduler держит в queue. **Это НЕ "cooldown" / "fairshare daily cap"** (наша гипотеза 2026-05-07 утром оказалась неверна). Реальность: SR006 queue-wait сильно variable в течение суток:
  - **<60 секунд** — поздним вечером / ночью UTC (06.05 22:55 → 23:43 UTC: <30 сек до Running);
  - **~7 минут** — сегодняшним мид-морнинг UTC (07.05 09:28 UTC);
  - **>30 минут / never** — европейский рабочий день (07.05 14:48-18:35 UTC: 6 free A100 видны в `get_available_resources_count`, но нашему namespace ни один не выдаётся).
  - `priority_class=PriorityClass.high` поведение **не меняет**.
  - Это видимо cluster-side scheduling, не баг нашей стороны и не fairshare-cooldown по таймеру.
  - **Что делать**: либо ждать ночное окно UTC (<60s wait), либо смириться с infinite-wait и не submit-ить heavy run в peak hours.
- **Диагностика "почему Pending"**: `client_lib.logs(job_name=...)` SDK-метод. Возвращает `"Job ... in queue. Try later"` пока scheduler не allocated, или pod stdout / mpirun-output после Running. **REST `/job_status`** возвращает только сухой `{"job_status": "Pending"}` — без reason / queue-position. Других REST endpoint-ов на cluster для diagnostic нет (`/job_describe`, `/job_events`, `/job_logs` всё 404). Полный list: `GET /job_status`, `POST /job_list`, `POST /delete_job/v2` — вот и весь API.
- **Timestamp-несоответствие**: `cloudru-jobs-list` `created_at` идёт в **MSK (UTC+3)** через `datetime.fromtimestamp` без tz, а pod-logs внутри в **UTC**. При корреляции "когда submit → когда Running" не забыть -3h.
- `ru_ip FAIL` — egress не РФ. Чек `~/notes/russian-traffic-via-openconnect-vpn.md`, бекап маршрута через `en1`.
- `gpu_list WARN` (без --job) — норма, jupyter-shell без GPU. С `--job` это становится FAIL для probe-pod-а.

## Особенности окружения (по first-contact smoke 2026-05-03)

| Аспект | Реальность |
|---|---|
| OS | Ubuntu 22.04, kernel 5.15.0-133-generic, x86_64 |
| Hostname pattern | `avatars-2-0` (зависит от namespace) |
| Shell | zsh по умолчанию (не bash) — проверено по `zsh: command not found` в выводе |
| sudo / apt | **нет**. Системные пакеты — только через custom Docker image (через их UI) |
| `python3` | `/usr/bin/python3` 3.10.12 — **без алиаса `python`**. Используй `python3` явно |
| `pip3` | **не установлен** в дефолтном окружении. Поднимать: `python3 -m ensurepip --user && python3 -m pip install --user <pkg>` |
| `conda` | **не установлен** в этом ноутбуке (хотя их платформа поддерживает на ≥0.0.95) |
| `torch` | **не предустановлен** — агент должен ставить сам. Тяжёлый wheel, но интернет открыт |
| GPU | SR006: 1× H100 на пачку проектов, **выделение по запросу в ноутбук**. Без выделения `nvidia-smi: command not found` |
| `$HOME` (`/home/jovyan`) | **уже NFS из коробки** — `pd50-nfs.sr006.aicloud.sbercloud.tech:/vol_fg_001/namespace_*/workspace`, 100G квота. Persistent сразу |
| Дополнительные NFS mounts | `/workspace-SR006.nfs2` (vol на сервере pd50-nfs-002, **отдельные 100G**), `/workspace-SR006.nfs3` (pd50-nfs-003 vol_fg1, **отдельные 100G**), `/mnt/shared_ru.ml.SZ-2_000177` (**512G shared** для project group) — все доступны из jupyter-shell на запись (`drwxr-xr-x jovyan jovyan`). `/workspace` и `/workspace-SR006.nfs1` — alias одного и того же volume (`pd50-nfs.../vol_fg_001/.../workspace`), не считать отдельным capacity. **Используй `nfs2/nfs3/shared` для weights/checkpoints когда `$HOME` забит** — тренировочные артефакты Klear-Reasoner-8B / Qwen3.6-27B легко съедают `$HOME` 100G. Доступность из job-pod-а не подтверждена тестом (по умолчанию pod видит `/workspace` = `$HOME`); если нужны те volumes из job-pod-а, проверять через probe. |
| Outbound HTTP/TCP | открыт (по их FAQ "all TCP outbound supported") — `llm_proxy_*` callbacks обратно на наш Mac работают |
| Idle-timeout | публично не задокументирован. Для долгих тренингов — их batch-job runner, не интерактивный SSH |
| SSH host key (ED25519) | может меняться при пересоздании; `ssh-keygen -R '[ssh-sr006-jupyter.ai.cloud.ru]:2222'` чистит stale запись |
| Биллинг | поминутный (округление вверх). Pause = 0. Stop = full state loss. Трафик: 10 GB free, далее per-GB |
| Account at zero balance | сервера авто-удаляются |
| Чужие артефакты в `$HOME` | при first-contact видны старые директории коллег (CogVideoX, EchoMimic, AVATARS_ARCHIVE.zip и т.п.) — **не трогаем**, пишем под `mle/` |

## Ловушки

- "Stop" vs "Pause" — легко перепутать. Pause сохраняет `$HOME`; Stop стирает. **На SR006 `$HOME` это NFS** (см. таблицу), так что pip-install'ы и артефакты переживут pause/stop. Но артефакты тренингов всё равно лучше явно класть в `mle/` (relative-path, протаскивается через `instance.remote_home`).
- Нет sudo → CUDA toolkit / ffmpeg ставим через user-space (`python3 -m pip install --user ...`), либо просим коллегу пересобрать custom image.
- **Нет `python` без 3** и **нет `pip3`** в дефолтном окружении — агенту нужно явно использовать `python3` и для первого pip-install сделать `python3 -m ensurepip --user`.
- Idle-kill таймаут вероятно есть, даже если не задокументирован — длинные ран'ы лучше через batch jobs.

## Bootstrap для агента (что должен сделать mle-solver-remote перед тренингом)

Записываем как памятку — пока без правок YAML-промта (тот должен оставаться провайдер-нейтральным). Если регулярно понадобится, эти шаги переедут в shared init-script на NFS.

```bash
# pip
python3 -m ensurepip --user
export PATH=$HOME/.local/bin:$PATH

# torch для H100 (sm_90, CUDA 12.1+)
python3 -m pip install --user torch --index-url https://download.pytorch.org/whl/cu121

# базовое для CV/NLP
python3 -m pip install --user numpy pandas scikit-learn transformers datasets
```

## GPU allocation на SR006 — путь через `client_lib.Job` (2026-05-06 discovery)

**Кратко:** SSH-host = persistent CPU-only Jupyter pod (`avatars-2-0`, k8s).
GPU выдаётся **только** через submit-job в отдельный pod. Inter-pod связь — **NFS `/home/jovyan`**.

**SDK:** `/home/user/conda/lib/python3.11/site-packages/client_lib.py` (v0.6.3) — преинсталлен внутри jupyter-контейнера. Внешним доступа нет: API endpoint `http://global-gw.aicloud-api.svc.cluster.local/...` — internal k8s service, поэтому submit-job делается **с jupyter-shell-а**, не с локального mle_kit-контейнера.

**Auth:** `GWAPI_KEY` хранится только в env jupyter-процесса (PID 1-узла, `jupyter-lab`). В SSH-сессии его нет. Достаём из `/proc/<jupyter-pid>/environ`:

```bash
# найти PID jupyter-lab
JL_PID=$(pgrep -f "jupyter-lab.*--ip=0.0.0.0" | head -1)

# загрузить env GW API в текущую shell
set -a
eval "$(tr "\0" "\n" < /proc/$JL_PID/environ \
        | grep -E "^(GWAPI_KEY|GWAPI_ADDR|GWAPI_V2_URL|NAMESPACE|NB_PREFIX|WORKSPACE_ID|CLUSTER_KEY)=" \
        | sed 's/=\(.*\)$/=\x27\1\x27/' | sed 's/^/export /')"
set +a
```

После этого `client_lib` готов к использованию через `/home/user/conda/bin/python3`.

**Регионы / GPU SKUs (на SR006):**

| Класс | Примеры instance_type | Память хост / GPU | Кол-во GPU |
|---|---|---|---|
| V100 | `v100.1gpu` … `v100.16gpu` | 92 Gi / GPU | 1–16 |
| A100 (40G) | `a100.1gpu.40vG.12C.230G` … `4gpu.40vG` | 230 Gi на 1 GPU | 1–4 |
| A100-SXM4 (80G, Ampere sm_80) | `a100.1gpu.8C.243G` … `a100.8gpu` | 243 Gi на 1 GPU | 1–8 |
| **H100 SXM5** ("a100plus" 80vG, Hopper sm_90) | `a100plus.1gpu.80vG.12C.96G` … `8gpu.80vG.96C.1952G` | 96–1952 Gi (выбор) | 1–8 |
| CPU | `cpu.2C.8G` … `cpu.48C.192G` | 8–192 Gi | 0 |

Полный list — `client_lib.get_instance_types(ClusterType.MT, regions=[RegionMT.SR006])`.

**Альтернатива custom-image — NFS-shared conda env (much cheaper).** Если нужен другой Python / vllm / torch чем в base image — НЕ нужен docker registry push. На cloud.ru штатный path:

1. На jupyter-shell: `conda create -p ~/.mlspace/envs/<name> python=3.11 -y`
2. `conda activate ~/.mlspace/envs/<name>` + `pip install` нужное.
3. В `Job(...)` передать `conda_env="<name>"` — job-pod автоматически активирует этот env вместо системного.
4. Env живёт в NFS (`CONDA_ENVS_PATH=/home/jovyan/.mlspace/envs/`) → доступен и из jupyter-shell, и из любого job-pod-а в этом namespace.

Пример прошлой команды на этом же tenant: `~/.mlspace/envs/opensora/`. Размер conda-env с torch+vllm обычно 25–40 GB на NFS, build ~10–15 мин одноразово.

Это **намного проще чем custom Docker image** — не нужно registry creds, не нужно UI access, всё через SDK + SSH.

**Наша env: `~/.mlspace/envs/holosophus-py311-vllm020`** (build 2026-05-07). Подтверждённые версии:
- python 3.11.15
- **torch 2.10.0+cu128** (vllm 0.19 потянул newer torch — pip resolver upgraded). ⚠️ Cluster GPU-driver `560.35.03` поддерживает CUDA до 12.6; cu128 wheels могут потребовать compat libs. **Не подтверждено работает на job-pod-е** до первого Running probe в этой env.
- vllm 0.19.1 (поддерживает Qwen3.6, Gemma 4)
- transformers 5.8.0
- numpy 2.2.6 (vllm 0.19 ломается с `numpy<2`, поэтому **прошлый pin `numpy<2` не использовать в этой env**)
- datasets, accelerate, bitsandbytes, huggingface_hub, sympy, antlr4-python3-runtime

При build: `pip install --no-cache-dir` (cluster диск тесный, см. альтернативные NFS volumes выше).

**Base images** (порядок сверху-вниз = чем больше уверенности что взлетит):

1. **`cr.ai.cloud.ru/aicloud-base-images/cuda12.1-torch2-py39:0.0.36`** ← **проверено**, у предыдущей команды на этом неймспейсе работает с `Job.Type.binary`. CUDA 12.1, torch 2.x, python 3.9, **sshd preinstalled** (обязательно для binary-mode MPI-обёртки SBER ML Space).
2. `cr.ai.cloud.ru/data-hub/ngc-pytorch:latest` — **НЕ работает** с binary-mode (нет sshd). Job висит 15+ мин и MPI-orchestrator делает `⚠️ No changes in worker statuses`. Логи: `mkdir: cannot create directory '/var/run/sshd': Permission denied`. Если очень нужен NGC — нужен другой `Job.Type` или custom image.
3. `cr.ai.cloud.ru/aicloud-base-images/horovod-cuda11.1-pt1.8.1-a100:latest` — старый CUDA 11, torch 1.8 (не подойдёт под vLLM/современный transformers без переустановки torch).
4. REGION_SPECIFIC `aicloud-jupyter/jupyter-cudaXX-ptYY-gpu-a100` — JupyterLab base images, sshd есть, но CUDA/torch старые.

**Path-mapping jupyter-shell ↔ job-pod (важно):**

В jupyter-shell домашний NFS виден как `/home/jovyan/...`. **В job-pod тот же NFS mount-нут как `/workspace/...`** (подтверждено выводом завершённого job-а 2026-05-04). При написании script-а: используй абсолютные `/workspace/<path>` пути, либо `cd "$NFS_HOME"` где `NFS_HOME=$(pwd | sed -E 's|^/home/jovyan|/workspace|')`. Иначе `>` redirection попадёт в эфемерный pod-volume и сгорит.

**Stdout / stderr job-а:** пишется в `~/.mlspace-logs/<job_name>/retry.000/rank.<N>/stdout` (через NFS, доступно из jupyter-shell **после** завершения — во время Running дир обычно пуст). Префиксы `[1,0]<stdout>:` / `[1,0]<stderr>:` — артефакт MPI-обёртки (`Job.Type.binary` запускает скрипт через mpirun, mpimaster-0).

**Что реально на нодах SR006 (подтверждено двумя независимыми job-stdout):**

| SDK instance_type | Реальное железо | Подтверждено |
|---|---|---|
| `a100.1gpu.8C.243G` | **NVIDIA A100-SXM4-80GB** (HBM2e), 128 CPU/2 TiB host RAM | 2026-05-06 v2 stdout |
| `a100plus.*` | **NVIDIA H100 80GB HBM3** (Hopper sm_90), 224 CPU/2 TiB host RAM | 2026-05-04 чужой завершённый job (`lm-mpi-job-dd8d5135.../rank.0/stdout`) |

Оба под driver 560.35.03 / CUDA 12.6. Sber-нейминг расходится с маркетинговым: `a100plus = H100`, `a100 = A100`. Не "и то и то H100" как я ошибочно предположил по одному примеру.

**Производительность (для трансформеров sub-10B):**

| | A100-SXM4 80GB | H100 SXM5 80GB |
|---|---|---|
| Память / bandwidth | 80 GB HBM2e / 1.6 TB/s | 80 GB HBM3 / 3.35 TB/s |
| BF16 dense (Tensor Cores) | ~312 TFLOPS | ~989 TFLOPS |
| FP8 (Transformer Engine) | — | ~1979 TFLOPS |
| Архитектура | Ampere sm_80 | Hopper sm_90 |

Грубо: H100 ≈ **2.5–3× быстрее A100** на bf16 SFT таких моделей и ≈ **2× быстрее** на vLLM-инференсе (последний memory-bandwidth-bound). При планировании бюджетов закладывать прямую оценку × коэффициент.

Проверять `nvidia-smi --query-gpu=name --format=csv` в первом job-е каждой кампании, не доверять SDK-имени.

**Cold-start time:** image pull NGC PyTorch (~15 GB) на cold node занимает **3–10 минут**. Status=Running появляется быстро, но реальное выполнение скрипта начинается только после pull. Не паниковать первые ~10 мин Running.

**Submit-job шаблон (binary type, 1×A100/H100-80G, NFS-shared script, **используй `/workspace/...` пути**):**

```python
# на jupyter-shell после загрузки auth-env:
from client_lib import Job, RegionMT

j = Job(
    script="bash /workspace/mle/code/run_matrix.sh",     # NFS-path в job-pod координатах
    base_image="cr.ai.cloud.ru/data-hub/ngc-pytorch:latest",
    instance_type="a100.1gpu.8C.243G",                   # 1×80G card; **не выходить за 1 GPU** (self-rule)
    region=RegionMT.SR006,
    type=Job.Type.binary,                                # `pytorch2` для distributed; `nogpu` для CPU-проб
    n_workers=1,
    job_desc="holosophus-slot2-trial",
    internet=True,                                       # outbound HTTP — ставим pip-пакеты в job
)
job_name = j.submit()
# polling: j.status() / j.logs(tail=200) (или client_lib.get_job_status)
# stdout: ~/.mlspace-logs/<job_name>/retry.000/rank.0/stdout (jupyter-shell view; дир заполняется к концу job)
```

Job-pod монтирует `/home/jovyan` через NFS (тот же что в jupyter-shell), пишет результаты обратно туда же. Всё persistent после kill/finish.

**Job lifecycle и квоты:**
- `j.submit()` → возвращает `job_name`, polling через `j.status()`/`j.logs()`/`j.kill()`.
- ≤2 cards per allocation (M. Mozikov; жёстко не проверено, в SDK ограничения нет).
- **Holosophus self-rule (Aleksei, 2026-05-06, v2):** в моменте берём **либо 2× A100**, **либо 1× H100 (`a100plus.*`)**, но не одновременно и не сверх. Параллельные GPU-job-ы запрещены без явного нового согласования. Шарим ресурс с командой-партнёром.
  - **Прогноз:** `a100plus.*` (H100) могут со временем стать недоступны — коллеги обсуждали миграцию в пользу увеличенных A100-квот. Если `get_available_resources_count(SR006)` покажет 0 H100 на устойчивой основе → переключаемся на 2×A100 как канон.
  - SKU-предпочтение по приоритету: `a100plus.1gpu.80vG.12C.244G` (H100, free) → `a100.2gpu.16C.486G` (2×A100-80G) → `a100.1gpu.8C.243G` (1×A100-80G fallback).
- Time limit на job явно не выставляется — ограничение через cluster-wide quotas.
- При out-of-balance аккаунта job-ы не стартуют, но running pod-ы добиваются.

**Текущий blocker для slot 2 rerun:**
mle_kit `remote_bash` сейчас работает в режиме **persistent SSH** (`subprocess.run(ssh user@host bash -c "<cmd>")`). На cloud.ru это всегда попадает в CPU-only jupyter-shell. Для GPU-execution нужно **переписать** `mle_kit_mcp/mle_kit_mcp/tools/remote_gpu.py` так, чтобы при `REMOTE_PROVIDER=cloudru` он:
1. (один раз за сессию) submit-нул long-running interactive-style job с reverse-SSH/tmux; или
2. На каждый `remote_bash` call submit-нул short job, ждал, читал stdout. Дорого по latency.

Решение — отдельный спец, см. ADR-0006 ext. До этого:
- Для **smoke** GPU works через прямой `client_lib.Job(...).submit()` из jupyter-shell-а.
- Для slot 2 — надо или (a) подождать архитектурный refactor `remote_gpu.py`, или (b) submit вручную один gigantic job, который запускает `run_matrix.py` end-to-end, и подождать результат на NFS.

## Известные проблемы / открытые вопросы

- При CPU-only state (jupyter-shell без attached GPU) GPU/torch чеки в `make cloudru-smoke` ожидаемо WARN, а не FAIL — это by design (smoke сам этому учит, см. docstring `cloudru_smoke.py`).
- **На jovyan нет `rsync`** (`apt update` без sudo не работает, outbound HTTP к `archive.ubuntu.com` висит). Решено в коде: `send_rsync`/`recieve_rsync` пробуют rsync, при провале fall back на `scp` (использует sshd→sftp, не требует серверного бинаря). Производительность scp хуже на больших каталогах, но max-size защита теряется. Если когда-то захочется поставить rsync на jovyan — нужен offline-источник или включение их пакетных зеркал.
- ~~При выделении H100 в ноутбук — нужен ли явный шаг в их UI или это API-вызов?~~ **Ответ (2026-05-06):** "Allocate to notebook" в `client_lib` API не выставлено (видимо, веб-UI-only). Через SSH доступен только path "submit job" в отдельный GPU-pod. H100 на SR006 присутствуют под именем `a100plus.*`. См. секцию "GPU allocation" выше.
- **Live-availability на SR006 (snapshot 2026-05-06 23:30 UTC):** A100 80G — 7 свободных 1×, 3 свободных 2×, A100 40G и V100 — 0, H100/`a100plus` — 0, **CPU-инстансы все 0** (т.е. CPU-only probe-job будет висеть в `Pending` до освобождения). Для нагрузочных GPU-задач сейчас целиться в `a100.*gpu.*C.*G` (80GB cards), не в `a100plus`.
- Версия CUDA-драйвера на хосте (определяет максимальную torch-сборку) — _выяснить через `nvidia-smi` внутри job-pod после первого submit._
- Действительно ли `~/.local/...` после `pip install --user` персистится через Stop, раз `$HOME` это NFS? Подтверждено косвенно: `/home/jovyan` это NFS volume `pd50-nfs.sr006.aicloud.sbercloud.tech:/vol_fg_001/namespace_*/workspace`, mount-edится одинаково в jupyter-shell и job-pod. То есть pip --user пакеты переживают и stop'ы, и доступны из job'ов.
- Outbound HTTP проверена: `https://api.ipify.org` работает (видим NAT IP `89.232.168.41`); `http://archive.ubuntu.com/...` висит — что-то фильтруется. _Уточнить какие именно outbound destinations доступны (важно если агенту понадобится pip install из dev mirror'а)._

## Подтверждённый smoke 2026-05-03

`make cloudru-smoke` прошёл: 7/7 REQUIRED OK, 3/3 OPTIONAL WARN (CPU-only, torch не установлен).

```
remote_home: /home/jovyan
endpoint:    avatars-2.ai0001053-01349@ssh-sr006-jupyter.ai.cloud.ru:2222

REQUIRED:
  [OK] hostname:           avatars-2-0
  [OK] whoami:             jovyan
  [OK] pwd:                /home/jovyan
  [OK] python3_version:    Python 3.10.12
  [OK] internet_outbound:  89.232.168.41   <- IP, который их NAT показывает миру
  [OK] artifact_persist_nfs: timestamp written + read on /home/jovyan
  [OK] home_is_nfs:        nfs

OPTIONAL (ожидаемо WARN на CPU-only без torch):
  [WARN] gpu_list, torch_smoke, matmul_smoke
```

Что доказано:
- SSH через split-tunnel ВПН работает из контейнера mle_kit без `/32`-байпаса.
- `instance.remote_home=/home/jovyan` корректно протаскивается, артефакты пишутся в NFS.
- pipeline-end (smoke прокидывает `remote_bash` → `subprocess ssh` → cloud.ru → echo) воспроизводим командой `make cloudru-smoke`.

Что НЕ проверено в этой сессии:
- GPU-зависимый путь (нужен выделенный H100 + `pip install torch`).
- Полный `scripts/run_research.py` end-to-end (нужны Anthropic API token + согласованное окно). Запустим явно по команде пользователя.
- Vast.ai регрессия (нет Vast-ключа под рукой; default `REMOTE_PROVIDER=vast` сохранён; SDK импорт стал ленивым; статически совместимо).

## Ссылки

- [SSH Connection to Jupyter Server](https://cloud.ru/docs/aicloud/mlspace/concepts/guides/guides__jupyter/environments__environments__ssh-connection)
- [Environments FAQ](https://cloud.ru/docs/aicloud/mlspace/concepts/faq__environments)
- [Pricing](https://cloud.ru/docs/aicloud/mlspace/concepts/general-info__tariffs)
- [Regions reference](https://cloud.ru/docs/aicloud/mlspace/concepts/references__regions)

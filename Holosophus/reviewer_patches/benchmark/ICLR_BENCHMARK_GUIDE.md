# ICLR OpenReview Benchmark Guide

Полное руководство по запуску end-to-end бенчмарка на датасете ICLR OpenReview.

## Обзор

Бенчмарк оценивает качество работы Reviewer Agent на реальных статьях ICLR с известными ground truth оценками.

### Особенности датасета ICLR OpenReview

- **Источник**: [HuggingFace - simon123905/ICLR](https://huggingface.co/datasets/simon123905/ICLR/tree/main/Miniset-1K)
- **Размер**: Miniset-1K содержит ~1000 статей
- **Структура**:
  - Категории: MLLM, RL, 3D
  - Решения: Oral, Spotlight, Poster, Reject
  - Формат: PDF + JSON (метаданные + рецензии)
- **Оценки**: 1-10 шкала (OpenReview) → нормализуется в 1-6 (наша схема)

## Быстрый старт

### 1. Запустите Holosophus стек

```bash
cd Holosophus && docker compose up --build -d
```

### 2. Проверьте импорты

```bash
cd reviewer_patches
python verify_reviewer.py
```

### 3. Запустите быстрый бенчмарк

```bash
cd Holosophus/reviewer_patches/benchmark

python benchmark/run_iclr_benchmark.py --quick
```

## Пошаговая инструкция

### Шаг 1: Загрузка датасета

Автоматическая загрузка при первом запуске бенчмарка или вручную:

```bash
cd Holosophus/reviewer_patches/benchmark

# Загрузить весь датасет
python iclr_dataset.py

# Загрузить только MLLM категорию
python iclr_dataset.py --categories MLLM

# Загрузить только Oral и Spotlight
python iclr_dataset.py --decisions oral spotlight

# Перезагрузить датасет
python iclr_dataset.py --force-download
```

**Результат**:
```
benchmark/iclr_data/
├── Miniset-1K.zip          # ZIP архив (~500MB)
└── Miniset-1K/             # Распакованные файлы
    ├── MLLM/
    │   ├── Oral/
    │   ├── Spotlight/
    │   ├── Poster/
    │   └── Reject/
    ├── RL/
    └── 3D/
```

### Шаг 2: Создание golden set

Golden set — это сбалансированная выборка статей для оценки:

```bash
# Создать golden set из 10 статей (сбалансированный)
python iclr_dataset.py --create-golden-set --golden-sample-size 10

# Создать несбалансированный golden set
python iclr_dataset.py --create-golden-set --golden-sample-size 20 --no-balance-decisions
```

**Результат**:
```
benchmark/iclr_data/golden_set/
├── golden_index.json       # Индекс всех статей
├── paper_id_1.json         # Golden review 1
├── paper_id_1.pdf          # PDF статьи 1
├── paper_id_2.json         # Golden review 2
└── ...
```

### Шаг 3: Запуск бенчмарка

#### Базовый запуск

```bash
python run_iclr_benchmark.py --sample-size 10
```

#### С расширенными функциями (Tier 1 + Tier 2)

```bash
python run_iclr_benchmark.py \
  --sample-size 20 \
  --use-a3pr \
  --use-self-critique \
  --use-arxiv-search
```

#### Быстрый режим (для тестирования)

```bash
python run_iclr_benchmark.py --quick
```

Эквивалентно:
```bash
python run_iclr_benchmark.py \
  --sample-size 5 \
  --no-images \
  --no-arxiv-search
```

#### Все опции

```bash
python run_iclr_benchmark.py \
  --dataset-dir ./iclr_data \
  --output-dir ./output \
  --sample-size 10 \
  --balance-decisions \
  --use-sections \
  --use-a3pr \
  --use-self-critique \
  --use-arxiv-search \
  --no-images \
  --stochastic
```

### Шаг 4: Анализ результатов

После завершения бенчмарка результаты сохраняются в:

```
benchmark/output/iclr_run_TIMESTAMP/
├── config.json             # Конфигурация запуска
├── golden/                 # Golden set
│   ├── golden_index.json
│   ├── paper_id_1.json
│   └── ...
├── logs/
│   ├── run_summary.json
│   └── quick_stats.json    # Быстрые метрики
├── reviews/
│   └── predicted_reviews.jsonl  # Предсказанные рецензии
└── eval/
    ├── batch_metrics.json
    └── benchmark_results.json   # Полные результаты
```

#### Просмотр quick stats

```bash
cat benchmark/output/iclr_run_TIMESTAMP/logs/quick_stats.json
```

Пример вывода:
```json
{
  "avg_mae": 0.85,
  "exact_match_rate": 0.3,
  "total_time_seconds": 245.3,
  "papers_evaluated": 10
}
```

#### Просмотр подробных результатов

```bash
cat benchmark/output/iclr_run_TIMESTAMP/eval/benchmark_results.json
```

Структура:
```json
{
  "summary": {
    "num_papers": 10,
    "avg_mae": 0.85,
    "exact_match_rate": 0.3,
    "total_time_seconds": 245.3
  },
  "by_decision": {
    "oral": {"count": 3, "avg_mae": 0.67},
    "spotlight": {"count": 3, "avg_mae": 0.89},
    "poster": {"count": 3, "avg_mae": 0.95},
    "reject": {"count": 1, "avg_mae": 0.50}
  },
  "per_paper_results": [
    {
      "paper_id": "wHebuIb6IH",
      "title": "VLMaterial: Procedural Material Generation...",
      "decision": "spotlight",
      "golden_overall": 5,
      "predicted_overall": 4,
      "mae": 1.0,
      "exact_match": false
    }
  ]
}
```

## Метрики

### Основные метрики

1. **MAE (Mean Absolute Error)**: Средняя абсолютная ошибка
   - Формула: `mean(|predicted - golden|)`
   - Диапазон: 0.0 (идеально) - 5.0 (худший)
   - Цель: < 1.0

2. **Exact Match Rate**: Доля точных совпадений
   - Формула: `count(predicted == golden) / total`
   - Диапазон: 0.0 - 1.0
   - Цель: > 0.5

3. **Per-Criterion MAE**: Ошибка по каждому критерию
   - originality, importance, claim_support, experimental_soundness, clarity, community_value, contextualization

### Вторичные метрики

4. **By-Decision MAE**: Ошибка по категориям решений
   - oral, spotlight, poster, reject

5. **Latency**: Время на статью
   - Цель: < 30с (baseline), < 90с (full features)

## Нормализация шкал

### OpenReview (1-10) → Наша схема (1-6)

| OpenReview | Описание | Наша схема | Описание |
|------------|----------|------------|----------|
| 1-2 | Strong Reject | 1 | Strong Reject |
| 3-4 | Reject | 2 | Reject |
| 5 | Borderline Reject | 3 | Borderline Reject |
| 6 | Borderline Accept | 4 | Borderline Accept |
| 7-8 | Accept | 5 | Accept |
| 9-10 | Strong Accept | 6 | Strong Accept |

### Маппинг полей ICLR → Наши критерии

| ICLR поле | Наши критерии | Маппинг |
|-----------|---------------|---------|
| soundness (1-10) | experimental_soundness, claim_support | normalize_10_to_6 |
| presentation (1-10) | clarity | normalize_10_to_6 |
| contribution (1-10) | originality, importance, community_value | normalize_10_to_6 |
| rating (1-10) | overall | normalize_10_to_6 |
| confidence (1-5) | confidence | direct (1-5) |

## Конфигурация

### Переменные окружения

Создайте `.env` в `Holosophus/reviewer_patches/`:

```bash
OPENROUTER_API_KEY=your_key_here
BASE_URL=https://openrouter.ai/api/v1
REVIEW_MODEL_NAME=openai/gpt-5
REVIEW_MAX_COMPLETION_TOKENS=32768
```

### Настройки бенчмарка

В `run_iclr_benchmark.py`:

```python
# По умолчанию
use_images = False          # Отключено для скорости
deterministic = True        # Воспроизводимый режим
use_sections = True         # Tier 1: парсинг секций
use_a3pr = False            # Tier 2: 4-stage pipeline
use_self_critique = False   # Tier 2: self-critique loop
use_arxiv_search = False    # Tier 1: arXiv search
```

## Производительность

Ориентировочное время выполнения (на статью):

| Конфигурация | Время | Δ vs Baseline |
|--------------|-------|---------------|
| Baseline (no images) | ~20с | - |
| + Section Parser | ~22с | +2с |
| + arXiv Search | ~35с | +15с |
| + A3PR Pipeline | ~65с | +45с |
| Full (A3PR + Self-Critique) | ~85с | +65с |

**Рекомендации**:
- Для быстрого тестирования: `--quick` (5 статей, ~2 минуты)
- Для полноценной оценки: `--sample-size 20` (20 статей, ~10-15 минут)
- Для production benchmark: `--sample-size 50-100` (50-100 статей, ~30-60 минут)

## Troubleshooting

### Ошибка: "Dataset not found"

**Решение**:
```bash
python iclr_dataset.py --force-download
```

### Ошибка: "No PDF files found in golden set"

**Причина**: PDF файлы не были скопированы при создании golden set.

**Решение**:
```bash
# Проверьте, что PDF существуют
ls benchmark/iclr_data/Miniset-1K/MLLM/Spotlight/*.pdf

# Пересоздайте golden set
python iclr_dataset.py --create-golden-set --golden-sample-size 10
```

### Ошибка: "ImportError: cannot import name 'ExtendedReviewResponse'"

**Причина**: Модуль `models.py` не найден.

**Решение**:
```bash
# Убедитесь, что запускаете из правильной директории
cd Holosophus/reviewer_patches/benchmark

# Проверьте, что academia контейнер запущен
cd Holosophus && docker compose ps
```

### Высокий MAE (> 2.0)

**Возможные причины**:
1. Недостаточно функций (только baseline)
2. Маленький sample size (< 10)
3. Несбалансированный golden set

**Решение**:
```bash
# Используйте Tier 1 + Tier 2 функции
python run_iclr_benchmark.py --use-a3pr --use-self-critique

# Увеличьте sample size
python run_iclr_benchmark.py --sample-size 20

# Сбалансируйте golden set
python run_iclr_benchmark.py --balance-decisions
```

### Медленная производительность (> 90с на статью)

**Решение**:
```bash
# Отключите изображения
python run_iclr_benchmark.py --no-images

# Отключите arXiv search
python run_iclr_benchmark.py --no-arxiv-search

# Используйте кэширование
# (включено по умолчанию)
```

## Примеры использования

### Пример 1: Быстрое тестирование

```bash
cd Holosophus/reviewer_patches/benchmark

# Загрузить датасет (только MLLM)
python iclr_dataset.py --categories MLLM

# Запустить быстрый бенчмарк
python run_iclr_benchmark.py --quick

# Посмотреть результаты
cat output/iclr_run_*/logs/quick_stats.json
```

### Пример 2: Полная оценка с Tier 1 + Tier 2

```bash
cd Holosophus/reviewer_patches/benchmark

# Загрузить весь датасет
python iclr_dataset.py

# Запустить бенчмарк со всеми функциями
python run_iclr_benchmark.py \
  --sample-size 20 \
  --use-sections \
  --use-a3pr \
  --use-self-critique \
  --use-arxiv-search

# Проанализировать результаты
python -c "
import json
with open('output/iclr_run_*/eval/benchmark_results.json') as f:
    data = json.load(f)
print(f\"MAE: {data['summary']['avg_mae']:.3f}\")
print(f\"Exact Match: {data['summary']['exact_match_rate']:.1%}\")
"
```

### Пример 3: Сравнение конфигураций

```bash
# Baseline
python run_iclr_benchmark.py --sample-size 10 --no-sections --no-arxiv-search --output-dir output/baseline

# + Section Parser
python run_iclr_benchmark.py --sample-size 10 --use-sections --no-arxiv-search --output-dir output/sections

# + arXiv Search
python run_iclr_benchmark.py --sample-size 10 --use-sections --use-arxiv-search --output-dir output/arxiv

# + A3PR
python run_iclr_benchmark.py --sample-size 10 --use-sections --use-arxiv-search --use-a3pr --output-dir output/a3pr

# Сравнить результаты
python benchmark/metrics_visualization.ipynb
```

## Дополнительные ресурсы

- [REVIEWER_ARCHITECTURE.md](../REVIEWER_ARCHITECTURE.md) - Архитектура Reviewer Agent
- [RUNBOOK.md](../RUNBOOK.md) - Операционные процедуры
- [iclr_dataset.py](iclr_dataset.py) - Загрузчик датасета
- [iclr_adapter.py](iclr_adapter.py) - Адаптер ICLR → ExtendedReviewResponse
- [run_iclr_benchmark.py](run_iclr_benchmark.py) - End-to-end бенчмарк

## Контакты

По вопросам и предложениям: создайте issue в репозитории Holosophus.

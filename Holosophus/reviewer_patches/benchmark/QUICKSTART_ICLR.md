# ICLR Benchmark - Quick Start

5-минутное руководство для быстрого запуска бенчмарка.

## Предварительные требования

1. ✅ Запущен Holosophus стек
2. ✅ Установлен Python 3.10+
3. ✅ Есть OPENROUTER_API_KEY

## Шаг 1: Проверка импортов (30 секунд)

```bash
cd Holosophus/reviewer_patches/benchmark
python test_iclr_imports.py
```

Ожидаемый вывод:
```
✅ All ICLR benchmark imports successful!
✅ All normalization tests passed!
✅ Parsed successfully
🎉 All tests passed!
```

## Шаг 2: Загрузка датасета (1-2 минуты)

```bash
python iclr_dataset.py --categories MLLM
```

Или загрузить весь датасет:
```bash
python iclr_dataset.py
```

## Шаг 3: Быстрый бенчмарк (3-5 минут)

```bash
python run_iclr_benchmark.py --quick
```

## Шаг 4: Просмотр результатов (30 секунд)

```bash
cat output/iclr_run_*/logs/quick_stats.json
```

## Готово! 🎉

Теперь у вас есть:
- ✅ Загруженный датасет ICLR
- ✅ Запущенный бенчмарк
- ✅ Первые результаты

## Что дальше?

### Запустить полноценный бенчмарк

```bash
python run_iclr_benchmark.py \
  --sample-size 20 \
  --use-a3pr \
  --use-self-critique \
  --use-arxiv-search
```

### Проанализировать результаты

```bash
cat output/iclr_run_*/eval/benchmark_results.json | python -m json.tool | less

jupyter notebook metrics_visualization.ipynb
```

### Сравнить конфигурации

```bash
# Baseline
python run_iclr_benchmark.py --sample-size 10 --output-dir output/baseline

# С A3PR
python run_iclr_benchmark.py --sample-size 10 --use-a3pr --output-dir output/a3pr

# Сравнить MAE
echo "Baseline MAE:"
cat output/baseline/iclr_run_*/logs/quick_stats.json | python -c "import sys,json; print(json.load(sys.stdin)['avg_mae'])"

echo "A3PR MAE:"
cat output/a3pr/iclr_run_*/logs/quick_stats.json | python -c "import sys,json; print(json.load(sys.stdin)['avg_mae'])"
```

## Troubleshooting

### Ошибка: "ModuleNotFoundError"

```bash
cd Holosophus/reviewer_patches/benchmark

cd Holosophus && docker compose ps
```

### Ошибка: "Dataset not found"

```bash
python iclr_dataset.py

ls iclr_data/Miniset-1K/
```

## Полезные команды

```bash
tree iclr_data/Miniset-1K -L 3

python -c "
from iclr_dataset import load_iclr_reviews
from collections import Counter
reviews = load_iclr_reviews('iclr_data/Miniset-1K', limit=100)
print('By decision:', Counter(r.decision for r in reviews))
print('By category:', Counter(r.category for r in reviews))
"

rm -rf output/iclr_run_*
python run_iclr_benchmark.py --quick
```

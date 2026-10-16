# Proposer Eval: как пользоваться

## Что именно мы измеряем

Eval отвечает на вопрос:

> Какая версия proposer-а генерирует более сильные гипотезы на одинаковых задачах?

На вход подаются задачи из `reports/data/proposer_ablation_tasks.jsonl`: тема, формулировка задачи, текущий gap / `bit`, список reference papers и короткий literature context.

На выходе каждый кандидат генерирует proposals. Потом система оценивает их по нескольким сигналам:
- `novelty` - насколько идея новая.
- `clarity` - насколько идея понятно описана.
- `significance` - насколько идея важная.
- `feasibility` - насколько ее реально проверить.
- `soundness` - насколько идея методологически здравая.
- `overall` - общий score.
- `pass_rate` - доля идей, которые прошли порог `feasibility >= 3` и `overall >= 4`.
- `best_of_n_overall` - лучший proposal на задачу, если генерировали несколько.
- `pairwise ELO` - кто чаще выигрывает при прямом сравнении двух вариантов.
- `diversity` - насколько разные идеи внутри одного запуска.
- `grounding` - насколько proposal опирается на reference papers.
- `latency/errors` - насколько вариант медленный и стабильный.

Главные числа для выбора лучшего варианта:
1. `pairwise ELO`
2. `best_of_n_overall`
3. `pass_rate`
4. `mean_novelty` при условии, что `mean_feasibility` не просел
5. `grounding`, если важно, чтобы идеи были хорошо привязаны к литературе

## Два разных eval runner-а

Есть два runner-а, и они измеряют разные вещи.

### `run_proposer_ablation_eval.py`
Основной runner для экспериментов.

Он нужен, когда мы хотим сравнить:
- модели;
- prompts;
- generate / score components;
- разные ideation strategies из статей;
- proposer tool-mode против proposer agent-mode.

Обычный режим `mode: tool` не запускает полный Holosophus manager. Он напрямую вызывает generate/score компоненты через academia MCP. Это controlled benchmark для ideation.

Если variant имеет:

```yaml
mode: agent
```
тогда runner поднимает настоящий `get_proposer_agent(...)` с `proposer.yaml` и указанным списком tools. Это ближе к production proposer, но все еще без полного manager/librarian/writer pipeline.

## Базовый запуск

```powershell
cd Holosophus\holosophos
uv run python reports\run_proposer_ablation_eval.py --split=smoke --variants=v0_current,v1_coi_chain --seeds=1
```

Более нормальный прогон:
```powershell
uv run python reports\run_proposer_ablation_eval.py --split=dev --variants=v0_current,v1_coi_chain --seeds=3
```

Результаты появятся в папке `reports/results/proposer_ablation_<timestamp>/`

Основные файлы:
- `summary.md` - короткая таблица для человека.
- `summary.json` - агрегированные числа для анализа.
- `normalized_proposals.jsonl` - все proposals в едином формате.
- `bitflip_scores.jsonl` - score каждого proposal.
- `pairwise_judgments.jsonl` - pairwise сравнения, если включены.
- `grounding_scores.jsonl` - оценки grounding.
- `errors.jsonl` - ошибки по variant/task/seed.

## Где задаются варианты

Все варианты лежат в `reports/proposer_variants.yaml`. Это главный файл для настройки экспериментов.reports/proposer_variants.yaml

Variant может менять:
- модель для генерации;
- prompt для генерации;
- component для генерации;
- prompt/model для scoring;
- число proposals;
- число regeneration attempts;
- режим `tool` или `agent`;
- список tools для agent-mode.

## Сравнить две модели для генерации гипотез

Например, хотим сравнить:
- `moonshotai/kimi-k2.6`
- `deepseek/deepseek-v3.2`

Добавляем в `reports/proposer_variants.yaml`:

```yaml
  v_model_kimi:
    description: Kimi K2.6 for proposal generation.
    generate:
      model: moonshotai/kimi-k2.6

  v_model_deepseek:
    description: DeepSeek V3.2 for proposal generation.
    generate:
      model: deepseek/deepseek-v3.2
```

Запускаем:

```powershell
uv run python reports\run_proposer_ablation_eval.py --split=dev --variants=v_model_kimi,v_model_deepseek --seeds=3
```

Как выбрать лучшую:
- если один вариант выше по `pairwise ELO` и `best_of_n_overall`, он обычно лучше;
- если один вариант выше по novelty, но сильно ниже по feasibility, это рискованный вариант;
- если scores близкие, нужно больше задач или seeds.

## Сравнить две версии prompt для `generate_research_proposals`

Создаем два prompt-файла:

```text
reports/proposer_prompts/improvement_prompt_a.txt
reports/proposer_prompts/improvement_prompt_b.txt
```

Потом добавляем variants:
```yaml
  v_prompt_a:
    description: Generate prompt A.
    generate:
      model: deepseek/deepseek-v3.2
      prompt: improvement_prompt_a

  v_prompt_b:
    description: Generate prompt B.
    generate:
      model: deepseek/deepseek-v3.2
      prompt: improvement_prompt_b
```

Запуск:
```powershell
uv run python reports\run_proposer_ablation_eval.py --split=dev --variants=v_prompt_a,v_prompt_b --seeds=3
```


## Сравнить scoring prompts

Если хочется проверить другой judge prompt, создаем:

```text
reports/proposer_prompts/score_my_variant.txt
```

Добавляем variant:

```yaml
  v_score_new:
    description: New scoring prompt.
    score:
      prompt: score_my_variant
```

Если новый scoring prompt требует другого system prompt, добавляем пару файлов:

```text
reports/proposer_prompts/score_system_my_variant.txt
```

```yaml
  v_score_new:
    description: New scoring prompt and system prompt.
    score:
      prompt: score_my_variant
      system_prompt: score_system_my_variant
```

## Проверить abstract vs full text в `extract_bitflip_info`

Важно: если задача уже содержит готовое поле `bit`, обычный tool-mode может не вызывать `extract_bitflip_info`.

Чтобы честно сравнить abstract vs full text, нужны задачи с `arxiv_id` и pipeline, который делает:

```text
arxiv_id -> extract_bitflip_info -> generate_research_proposals -> score_research_proposals
```

Практический путь:
1. Добавить в `bitflip.py` параметр, например:

```python
source: str = "abstract"
```

2. Внутри `extract_bitflip_info` выбирать источник:

```python
if source == "full_text":
    document = full paper text
else:
    document = paper.abstract
```

3. Добавить extract components, например:

```text
reports/proposer_components/extract_abstract.py
reports/proposer_components/extract_fulltext.py
```

4. Добавить variants:

```yaml
  v_extract_abstract:
    description: Extract BitFlip from abstract only.
    extract:
      component: abstract

  v_extract_fulltext:
    description: Extract BitFlip from full paper text.
    extract:
      component: fulltext
```

5. Добавить tasks с `arxiv_id` в `reports/data/proposer_ablation_tasks.jsonl`:

```json
{"task_id":"paper-2503-07826","split":"dev","arxiv_id":"2503.07826","instruction":"Generate research proposals based on this paper."}
```

6. Запустить:

```powershell
uv run python reports\run_proposer_ablation_eval.py --split=dev --variants=v_extract_abstract,v_extract_fulltext --seeds=3
```

Смотреть:
- вырос ли `grounding`;
- вырос ли `overall`;
- не упала ли `feasibility`;
- не выросли ли errors/latency слишком сильно.

## Проверить новый алгоритм генерации

Если статья предлагает не просто новый prompt, а новый алгоритм, делаем новый generate component:

```text
reports/proposer_components/generate_my_method.py
```

В нем должна быть async-функция:
```python
async def generate(...):
    ...
    return proposals
```

Потом variant:

```yaml
  v_my_method:
    description: My paper-inspired generation method.
    generate:
      component: my_method
```

Главное правило: на выходе component должен вернуть proposals, которые можно привести к стандартному формату:
```json
{
  "proposal_id": 0,
  "chain_of_reasoning": "...",
  "flip": "...",
  "spark": "...",
  "abstract": "...",
  "experiments": ["..."],
  "risks_and_limitations": ["..."]
}
```

## Проверить новый tool в proposer agent
Если новый tool должен использовать настоящий proposer agent, нужен `mode: agent`.

Пример:

```yaml
  v_agent_with_new_tool:
    description: Proposer agent with an extra novelty-check tool.
    mode: agent
    agent_tools:
      - web_search
      - visit_webpage
      - document_qa
      - extract_bitflip_info
      - generate_research_proposals
      - score_research_proposals
      - my_new_tool
      - text_editor
```
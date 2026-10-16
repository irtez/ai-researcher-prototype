# Metrics & Tests: Detailed Guide

Подробное описание каждой метрики, тестовых данных (фикстур) и ожидаемых результатов.

---

## Содержание

- [1. Как устроена система](#1-как-устроена-система)
- [2. Тестовые данные (фикстуры)](#2-тестовые-данные-фикстуры)
  - [2.1 Gold paper — эталонная статья](#21-gold-paper--эталонная-статья)
  - [2.2 Corrupted variants — испорченные версии](#22-corrupted-variants--испорченные-версии)
- [3. Метрики: подробное описание](#3-метрики-подробное-описание)
  - [3.1 Structural metrics](#31-structural-metrics)
  - [3.2 Citation metrics](#32-citation-metrics)
  - [3.3 Figure/Table metrics](#33-figuretable-metrics)
  - [3.4 Content slot metrics](#34-content-slot-metrics)
  - [3.5 Text quality metrics](#35-text-quality-metrics)
- [4. Матрица: какая фикстура какие метрики проверяет](#4-матрица-какая-фикстура-какие-метрики-проверяет)
- [5. Полный список тестов и ожидаемых результатов](#5-полный-список-тестов-и-ожидаемых-результатов)

---

## 1. Как устроена система

Вход: директория с LaTeX-пакетом статьи (`main.tex` + `references.bib`).

Парсер (`PaperPackage.from_directory`) автоматически:
1. Находит основной `.tex` файл (предпочитает `main.tex`, затем `paper.tex`).
2. Находит `.bib` файл (предпочитает `references.bib`).
3. Парсит LaTeX regex-ами и извлекает:
   - **sections** — список секций с именами, уровнями (section/subsection/...) и телами.
   - **cite_keys_used** — все ключи из `\cite{...}`, `\citep{...}`, `\citet{...}`.
   - **bib_keys_defined** — все ключи `@article{key, ...}` из `.bib`.
   - **labels_defined** — все `\label{...}`.
   - **refs_used** — все `\ref{...}`, `\autoref{...}`, `\cref{...}`.
   - **floats** — все `\begin{figure}...\end{figure}` и `\begin{table}...\end{table}` с их labels и наличием `\caption`.
   - **abstract_text** — текст между `\begin{abstract}` и `\end{abstract}`.

Каждая метрика получает этот `PaperPackage` и возвращает `MetricResult`:
- `name` — имя метрики.
- `value` — числовой результат (ratio 0.0–1.0 или integer).
- `fail_type` — `hard` (структурный дефект) или `soft` (качественный сигнал).
- `details` — dict с подробностями (что именно найдено/не найдено).

Метрика считается **passed** если:
- Для ratio-метрик: `value >= 1.0` (все элементы на месте).
- Для integer-метрик: `value == 0` (нет ошибок/проблем).

Source: [`metrics/base.py`](metrics/base.py)

---

## 2. Тестовые данные (фикстуры)

Все тестовые данные генерируются в памяти, не требуют скачивания. Каждый тест получает `PaperPackage` из pytest-фикстуры.

Source: [`tests/conftest.py`](tests/conftest.py)

### 2.1 Gold paper — эталонная статья

**Что это**: полноценный LaTeX-документ (~160 строк `.tex` + ~20 строк `.bib`), который специально написан так, чтобы **проходить все метрики**.

**Структура gold `.tex`**:

```
\documentclass{article}
\begin{document}

\begin{abstract}
  ✓ Упоминает task/problem ("We study the problem...")
  ✓ Упоминает method ("We propose a deterministic benchmark...")
  ✓ Упоминает result с числами ("achieves 95.3% accuracy...")
\end{abstract}

\section{Introduction}
  ✓ ≥40 слов
  ✓ Содержит "Our main contributions are:" + \begin{itemize}

\section{Related Work}
  ✓ ≥40 слов
  ✓ Содержит \cite{surveybench2024}, \cite{lo2020s2orc}, \cite{kang2018peerread}

\section{Method}
  ✓ ≥40 слов

\section{Experiments}
  ✓ ≥40 слов
  ✓ Упоминает "datasets", "baseline", "precision, recall, F1-score"
  ✓ Упоминает "batch size", "learning rate", "Adam optimizer"
  ✓ Содержит числовые значения рядом с метриками

\section{Results}
  ✓ ≥40 слов
  ✓ Содержит Table~\ref{tab:main_results}, Figure~\ref{fig:sensitivity}
  ✓ \begin{table} с \caption и \label{tab:main_results}
  ✓ \begin{figure} с \caption и \label{fig:sensitivity}

\section{Limitations}
  ✓ ≥40 слов

\section{Ethics and Broader Impact}
  ✓ ≥40 слов
  ✓ Упоминает "code is available as open source"
  ✓ Упоминает "data is released publicly"

\bibliography{references}
\end{document}
```

**Структура gold `.bib`**:
```
@inproceedings{surveybench2024, ...}   -- цитируется в Related Work
@article{lo2020s2orc, ...}            -- цитируется в Related Work
@inproceedings{kang2018peerread, ...}  -- цитируется в Related Work
```

**Почему именно так**: каждый элемент gold paper'а осознанно подобран так, чтобы удовлетворять конкретную метрику. Abstract содержит все три слота (task/method/result). Introduction содержит фразу "contributions". Каждая секция >= 40 слов. Все `\cite` резолвятся в bib. Все float'ы имеют `\caption`, `\label`, и на них есть `\ref`.

### 2.2 Corrupted variants — испорченные версии

Каждая corruption берёт gold paper и вносит **одно конкретное повреждение**. Это позволяет точно проверить, что конкретная метрика ловит конкретный дефект.

#### `paper_missing_abstract`
**Что сделано**: полностью удалён блок `\begin{abstract}...\end{abstract}` из gold `.tex`.

**Что осталось**: все секции, bibliography, figures/tables — всё на месте. Только abstract отсутствует.

**Какие метрики должны упасть**:
- `required_section_presence` — abstract в списке required, его нет → ratio < 1.0
- `abstract_slot_coverage` — нет abstract → value = 0.0

---

#### `paper_missing_sections`
**Что сделано**: удалены секции **Method** и **Limitations** (весь текст от `\section{Method}` до следующей `\section{...}` и аналогично для Limitations).

**Что осталось**: Abstract, Introduction, Related Work, Experiments, Results, Ethics, References.

**Какие метрики должны упасть**:
- `required_section_presence` — method и limitations отсутствуют → ratio < 1.0

---

#### `paper_empty_sections`
**Что сделано**: тело секции **Method** заменено на единственное слово `TODO`. Всё остальное без изменений.

**Что осталось**: секция Method формально есть (заголовок на месте), но body = "TODO" (1 слово, порог = 40).

**Какие метрики должны упасть**:
- `required_section_non_emptiness` — Method считается "empty stub" → ratio < 1.0
- `section_length_adequacy` — Method < 40 слов → ratio < 1.0

---

#### `paper_wrong_section_order`
**Что сделано**: секции **Experiments** и **Results** поменяны местами. В итоге порядок: ..., Related Work, Method, **Results**, **Experiments**, Limitations, ...

**Что осталось**: все секции на месте, всё содержимое сохранено, только порядок нарушен.

**Какие метрики должны упасть**:
- `section_order_validity` — expected order [..., experiments, results, ...], actual [..., results, experiments, ...] → value = 0.0

---

#### `paper_broken_citations`
**Что сделано**: два `\cite` ключа заменены на несуществующие:
- `\cite{surveybench2024}` → `\cite{nonexistent_paper_2099}`
- `\cite{lo2020s2orc}` → `\cite{also_nonexistent_2099}`

Bib-файл не менялся — в нём по-прежнему `surveybench2024`, `lo2020s2orc`, `kang2018peerread`.

**Что осталось**: `\cite{kang2018peerread}` остался валидным. В bib есть 3 записи, из которых 2 теперь не цитируются.

**Какие метрики должны упасть**:
- `citation_integrity` — 2 из 3 unique cite keys не резолвятся → ratio = 1/3 ≈ 0.33

---

#### `paper_unused_bib_entries`
**Что сделано**: к gold `.bib` добавлены 2 записи, которые **нигде не цитируются** в тексте:
- `@article{unused_entry_2024, ...}`
- `@article{another_unused_2023, ...}`

Tex-файл не менялся.

**Что осталось**: 3 оригинальных bib-записи цитируются. 2 новых — нет. Итого 3/5 used.

**Какие метрики должны упасть**:
- `bibliography_usage_coverage` — 3 из 5 entries cited → ratio = 0.6

---

#### `paper_missing_captions`
**Что сделано**: из `\begin{figure}...\end{figure}` удалена строка `\caption{...}`. Label остался.

**Что осталось**: figure environment на месте, `\label{fig:sensitivity}` на месте, но `\caption` отсутствует.

**Какие метрики должны упасть**:
- `caption_coverage` — 1 из 2 float'ов (table + figure) имеет caption, figure — нет → ratio = 0.5

---

#### `paper_dangling_refs`
**Что сделано**: в конец `.tex` добавлена строка:
```latex
See Figure~\ref{fig:nonexistent} and Table~\ref{tab:ghost}.
```
Ни `fig:nonexistent`, ни `tab:ghost` нигде не определены через `\label`.

**Что осталось**: все оригинальные ref'ы работают. Добавлены 2 ref'а на несуществующие labels.

**Какие метрики должны упасть**:
- `dangling_figure_table_reference_count` — 2 dangling refs (оба имеют префикс `fig:` / `tab:`, поэтому считаются float-ref'ами) → value = 2

---

#### `paper_no_contributions`
**Что сделано**: в Introduction блок "Our main contributions are: \begin{itemize}...\end{itemize}" заменён на фразу "We describe our approach in the following sections."

**Что осталось**: Introduction на месте, но без явного перечисления contributions.

**Какие метрики должны упасть**:
- `contribution_statement_presence` — ни один из паттернов не найден → value = 0.0

---

## 3. Метрики: подробное описание

### 3.1 Structural metrics

Source: [`metrics/structural.py`](metrics/structural.py)

#### `required_section_presence`
| | |
|---|---|
| **Тип** | ratio (0.0–1.0) |
| **Fail type** | hard |
| **Что проверяет** | Какая доля из 9 обязательных секций присутствует в статье |
| **Обязательные секции** | abstract, introduction, related work, method, experiments, results, limitations, ethics, references |

**Как работает**:
1. Собирает все `\section{...}` заголовки, нормализует их (lowercase, strip) и резолвит алиасы. Например, "Methodology" → "method", "Background and Related Work" → "related work", "Broader Impact" → "ethics".
2. Abstract проверяется отдельно — ищет `\begin{abstract}...\end{abstract}`.
3. References проверяются по наличию bib-записей или `\bibliography{...}` / `\begin{thebibliography}`.
4. `value = len(found) / 9`.

**Поддерживаемые алиасы** (полный список):
- "background", "literature review" → related work
- "methodology", "methods", "approach", "proposed method" → method
- "experimental setup", "evaluation", "experiment" → experiments
- "results and discussion", "discussion" → results
- "limitation", "limitations and future work" → limitations
- "ethics statement", "broader impact", "societal impact" → ethics

**Пример вывода**:
```python
MetricResult(
    name="required_section_presence",
    value=0.778,    # 7 из 9
    fail_type="hard",
    details={
        "present": ["abstract", "introduction", "related work", "experiments", "results", "limitations", "references"],
        "missing": ["method", "ethics"]
    }
)
```

---

#### `required_section_non_emptiness`
| | |
|---|---|
| **Тип** | ratio (0.0–1.0) |
| **Fail type** | hard |
| **Что проверяет** | Какая доля из присутствующих обязательных секций НЕ является заглушкой |
| **Порог** | >= 40 слов в теле секции |

**Как работает**:
1. Для каждой обязательной секции (кроме references) подсчитывает количество слов в теле.
2. Abstract считается отдельно.
3. Секция с < 40 слов попадает в `empty_stubs`.
4. `value = len(non_empty) / (len(non_empty) + len(empty_stubs))`.

**Зачем нужна**: Writer иногда создаёт секцию-заглушку вроде "TODO" или "This section will discuss..." — formально секция есть, но содержания нет. `required_section_presence` это пропустит, а `required_section_non_emptiness` поймает.

**Пример вывода**:
```python
MetricResult(
    name="required_section_non_emptiness",
    value=0.625,    # 5 из 8 non-empty
    details={
        "non_empty": ["introduction", "related work", "method", "experiments", "results"],
        "empty_stubs": ["limitations", "ethics", "abstract"],
        "missing": []
    }
)
```

---

#### `section_order_validity`
| | |
|---|---|
| **Тип** | binary (0.0 или 1.0) |
| **Fail type** | soft |
| **Что проверяет** | Идут ли обязательные секции в правильном относительном порядке |
| **Ожидаемый порядок** | introduction → related work → method → experiments → results → limitations → ethics |

**Как работает**:
1. Проходит по всем `\section` в порядке появления в `.tex`.
2. Собирает подпоследовательность из тех, которые входят в канонический порядок.
3. Сравнивает с ожидаемым: если совпадает — 1.0, нет — 0.0.

**Важно**: проверяется *относительный* порядок, а не абсолютное положение. Если в статье нет Method, но остальные идут правильно — это 1.0. Метрика не штрафует за отсутствие секций, только за неправильный порядок *присутствующих*.

---

#### `section_length_adequacy`
| | |
|---|---|
| **Тип** | ratio (0.0–1.0) |
| **Fail type** | soft |
| **Что проверяет** | Какая доля обязательных секций имеет адекватную длину |
| **Порог** | >= 40 слов |

**Как работает**:
1. Для каждой обязательной секции (кроме abstract и references) считает слова.
2. `value = len(adequate) / len(checkable)`.
3. В `details` выдаёт `word_counts` для каждой секции.

**Чем отличается от `non_emptiness`**: `non_emptiness` считает только ratio среди *присутствующих* секций. `length_adequacy` — ratio среди *имеющих заголовок* секций. Если секция пропущена целиком (нет `\section{...}`), она не учитывается ни в одной из них.

---

### 3.2 Citation metrics

Source: [`metrics/citation.py`](metrics/citation.py)

#### `citation_integrity`
| | |
|---|---|
| **Тип** | ratio (0.0–1.0) |
| **Fail type** | hard |
| **Что проверяет** | Все ли `\cite{key}` ключи в тексте имеют соответствующую запись в `.bib` |

**Как работает**:
1. Собирает все уникальные cite keys из `\cite{...}`, `\citep{...}`, `\citet{...}` (включая множественные через запятую: `\cite{a,b,c}`).
2. Собирает все bib keys из `.bib` файла.
3. `value = len(resolved) / len(unique_cite_keys)`.

**Пример**: текст содержит `\cite{smith2024}` и `\cite{jones2023}`, в bib есть только `@article{smith2024,...}` → value = 0.5, unresolved = ["jones2023"].

**Почему hard**: нерезолвленная ссылка — это `[?]` в скомпилированном PDF. Это однозначный дефект.

---

#### `bibliography_usage_coverage`
| | |
|---|---|
| **Тип** | ratio (0.0–1.0) |
| **Fail type** | soft |
| **Что проверяет** | Все ли записи из `.bib` реально цитируются в тексте |

**Как работает**:
1. Для каждого bib key проверяет, есть ли он среди cite keys в тексте.
2. `value = len(used) / len(total_bib_entries)`.

**Пример**: bib содержит 5 записей, в тексте цитируются только 3 → value = 0.6, unused = 2 ключа.

**Почему soft**: неиспользованная bib-запись не ломает PDF, но указывает на неряшливость или copy-paste из другого проекта.

**Обратная связь с `citation_integrity`**: эти две метрики — зеркала друг друга.
- `citation_integrity`: "все ли cite есть в bib?" (текст → bib).
- `bibliography_usage_coverage`: "все ли bib цитируются в тексте?" (bib → текст).

---

### 3.3 Figure/Table metrics

Source: [`metrics/figures.py`](metrics/figures.py)

#### `figure_table_reference_coverage`
| | |
|---|---|
| **Тип** | ratio (0.0–1.0) |
| **Fail type** | soft |
| **Что проверяет** | Все ли figures/tables, имеющие `\label`, упомянуты в тексте через `\ref` |

**Как работает**:
1. Собирает все `\label{...}` из `\begin{figure}...\end{figure}` и `\begin{table}...\end{table}`.
2. Проверяет, есть ли соответствующий `\ref{...}` в тексте.
3. `value = len(referenced) / len(float_labels)`.

**Пример**: figure с `\label{fig:arch}` и table с `\label{tab:results}`, но в тексте есть только `Table~\ref{tab:results}` → value = 0.5, unreferenced = ["fig:arch"].

**Зачем**: figure/table без ссылки в тексте — "сирота". Читатель не знает, когда на неё смотреть.

---

#### `dangling_figure_table_reference_count`
| | |
|---|---|
| **Тип** | integer (0 = хорошо) |
| **Fail type** | hard |
| **Что проверяет** | Сколько `\ref{fig:...}` / `\ref{tab:...}` указывают на несуществующие labels |

**Как работает**:
1. Собирает все `\ref{...}` из текста.
2. Фильтрует только те, чей label начинается с `fig:`, `tab:`, `figure:` или `table:` (эвристика по префиксу).
3. Проверяет, есть ли соответствующий `\label{...}` где-либо в документе.
4. `value = count(dangling)`.

**Почему hard**: dangling ref — это `??` в PDF. Однозначный дефект.

**Фильтрация по префиксу**: метрика не проверяет `\ref{sec:intro}` или `\ref{eq:loss}` — только float-ref'ы. Это сделано осознанно, чтобы не путать с section/equation references.

---

#### `caption_coverage`
| | |
|---|---|
| **Тип** | ratio (0.0–1.0) |
| **Fail type** | hard |
| **Что проверяет** | У каждого ли `\begin{figure}` / `\begin{table}` есть `\caption{...}` |

**Как работает**:
1. Парсит все float-environments (figure, table, включая `figure*`, `table*`).
2. Внутри каждого ищет `\caption`.
3. `value = len(with_caption) / len(all_floats)`.

**Почему hard**: figure/table без caption в академической статье — однозначный дефект.

---

#### `numbering_consistency`
| | |
|---|---|
| **Тип** | integer (0 = хорошо) |
| **Fail type** | soft |
| **Что проверяет** | Есть ли дублированные `\label{...}` в документе |

**Как работает**:
1. Собирает все `\label{...}` и считает частоту каждого.
2. Если один label встречается > 1 раза — это issue.
3. `value = count(issues)`.

**Пример**: два `\label{fig:arch}` → value = 1.

---

### 3.4 Content slot metrics

Source: [`metrics/content.py`](metrics/content.py)

Эта группа метрик проверяет не структуру LaTeX, а **содержательные элементы**, которые ожидаются в хорошей статье. Все работают через regex-эвристики.

#### `abstract_slot_coverage`
| | |
|---|---|
| **Тип** | ratio (0.0–1.0) |
| **Fail type** | soft |
| **Что проверяет** | Есть ли в abstract три ключевых компонента: task, method, result |

**Слоты и паттерны**:

| Слот | Что ищем | Примеры совпадений |
|------|---------|-------------------|
| **task** | Формулировка задачи/проблемы | "We study the problem...", "This paper addresses...", "challenge" |
| **method** | Описание предложенного метода | "We propose a...", "our framework", "architecture", "pipeline" |
| **result** | Результаты с числами | "results show...", "we achieve...", "outperform", "95.3%" |

`value = found_slots / 3`.

**Пример**: abstract содержит "We study..." (task ✓) и "our method" (method ✓), но нет числовых результатов (result ✗) → value = 0.67.

---

#### `contribution_statement_presence`
| | |
|---|---|
| **Тип** | binary (0.0 или 1.0) |
| **Fail type** | soft |
| **Что проверяет** | Есть ли в Introduction явное перечисление contributions |

**Паттерны** (ищутся в секции Introduction):
- "Our contributions are..."
- "We make the following contributions..."
- "The main contributions of this paper..."
- `\textbf{...contribution...}`
- "Contributions:" или "Contributions."

**Где ищет**: сначала в теле Introduction (через alias resolution), если Introduction не найден — во всём `.tex`.

---

#### `experiment_slot_coverage`
| | |
|---|---|
| **Тип** | ratio (0.0–1.0) |
| **Fail type** | soft |
| **Что проверяет** | Упоминаются ли в Experiments/Results 4 ключевых компонента |

**Слоты**:

| Слот | Что ищем | Примеры |
|------|---------|---------|
| **datasets** | Данные для экспериментов | "dataset", "benchmark", "training set" |
| **baselines** | С чем сравнивают | "baseline", "compared with", "prior work" |
| **metrics** | Метрики оценки | "accuracy", "precision", "F1-score", "BLEU" |
| **setup** | Детали реализации | "implementation", "GPU", "hyperparameter", "learning rate" |

`value = found_slots / 4`.

**Где ищет**: в телах секций, resolved к "experiments" или "results".

---

#### `reproducibility_slot_coverage`
| | |
|---|---|
| **Тип** | ratio (0.0–1.0) |
| **Fail type** | soft |
| **Что проверяет** | Упоминает ли статья информацию для воспроизводимости |

**Слоты**:

| Слот | Что ищем | Примеры |
|------|---------|---------|
| **code_availability** | Доступность кода | "code is available", "github", "open-source" |
| **data_availability** | Доступность данных | "data is available", "dataset is released" |
| **training_details** | Детали обучения | "trained for", "epoch", "convergence" |
| **hyperparameters** | Гиперпараметры | "learning rate", "batch size", "Adam", "optimizer" |

`value = found_slots / 4`.

**Где ищет**: во всём `.tex` файле (не ограничен одной секцией, т.к. эта информация может быть в Experiments, Appendix, или Ethics).

---

#### `metric_value_presence`
| | |
|---|---|
| **Тип** | ratio (0.0–1.0) |
| **Fail type** | soft |
| **Что проверяет** | Подкреплены ли упоминания оценочных метрик числовыми значениями |

**Как работает**:
1. Находит все упоминания evaluation metric names (accuracy, precision, recall, F1, BLEU, ROUGE, perplexity, AUC, mAP, etc.).
2. Для каждого упоминания смотрит контекст ±40 символов.
3. Если рядом есть число (напр. "accuracy of 95.3%" или "BLEU = 32.1") — считается "with value".
4. `value = with_value / total_mentions`.

**Зачем**: Writer может написать "We evaluate precision and recall" без единой цифры. Это бессмысленно.

---

### 3.5 Text quality metrics

Source: [`metrics/text_quality.py`](metrics/text_quality.py)

#### `spelling_errors`
| | |
|---|---|
| **Тип** | integer (0 = хорошо) |
| **Fail type** | soft |
| **Зависимость** | `pyspellchecker` |

**Как работает**:
1. Извлекает plain text из LaTeX: убирает преамбулу, математику, lstlisting, tikz, tabular, комментарии, команды.
2. Находит все слова >= 3 букв.
3. Прогоняет через SpellChecker с domain whitelist (arxiv, LLM, GPU, pytorch, holosophos, и т.д.).
4. Фильтрует all-caps акронимы.
5. `value = len(unknown_words)`.

**Domain whitelist** включает: arxiv, llm, gpu, bert, gpt, lstm, cnn, transformer, hyperparameter, dataset, pretrained, finetuning, softmax, relu, pytorch, tensorflow, json, yaml, ablation, benchmarking, scalability, holosophos, codearkt, langfuse, paperbanana.

---

#### `grammar_errors`
| | |
|---|---|
| **Тип** | integer (0 = хорошо) |
| **Fail type** | soft |
| **Зависимость** | `language-tool-python` (опциональна, требует JVM) |

**Как работает**:
1. Извлекает plain text (как spelling).
2. Отправляет в LanguageTool (en-US).
3. Фильтрует style-only suggestions.
4. `value = len(errors)`.

**Не включена в дефолтный набор тестов** из-за тяжёлой зависимости (Java, ~800MB). Можно подключить вручную.

---

#### `notation_consistency`
| | |
|---|---|
| **Тип** | integer (0 = хорошо) |
| **Fail type** | soft |

**Что проверяет**: использует ли автор один и тот же термин в разных формах. Например, "dataset" и "data set" в одной статье, или "fine-tune" и "finetune".

**Проверяемые группы вариантов**:

| Группа | Варианты |
|--------|----------|
| 1 | dataset / data set / data-set |
| 2 | finetune / fine-tune / fine tune |
| 3 | pretrain / pre-train / pre train |
| 4 | pretraining / pre-training / pre training |
| 5 | finetuning / fine-tuning / fine tuning |
| 6 | hyperparameter / hyper-parameter / hyper parameter |
| 7 | multi-agent / multiagent / multi agent |
| 8 | non-trivial / nontrivial / non trivial |
| 9 | real-world / realworld / real world |
| 10 | state-of-the-art / state of the art |

Если в тексте найдены >= 2 варианта из одной группы — это issue.

---

## 4. Матрица: какая фикстура какие метрики проверяет

| Фикстура | Основная метрика-мишень | Ожидаемый результат на мишени |
|----------|------------------------|-------------------------------|
| `gold_paper` | **все** | все метрики pass |
| `paper_missing_abstract` | `required_section_presence`, `abstract_slot_coverage` | presence < 1.0, abstract = 0.0 |
| `paper_missing_sections` | `required_section_presence` | < 1.0 (method + limitations missing) |
| `paper_empty_sections` | `required_section_non_emptiness`, `section_length_adequacy` | < 1.0 (method = stub) |
| `paper_wrong_section_order` | `section_order_validity` | = 0.0 |
| `paper_broken_citations` | `citation_integrity` | < 1.0 (2 unresolved keys) |
| `paper_unused_bib_entries` | `bibliography_usage_coverage` | < 1.0 (2 unused entries) |
| `paper_missing_captions` | `caption_coverage` | < 1.0 (figure without caption) |
| `paper_dangling_refs` | `dangling_figure_table_reference_count` | >= 2 |
| `paper_no_contributions` | `contribution_statement_presence` | = 0.0 |

---

## 5. Полный список тестов и ожидаемых результатов

### `test_structural.py` (6 тестов)

| Тест | Фикстура | Проверяемая метрика | Assertion |
|------|----------|--------------------|----|
| `test_gold_paper_has_all_sections` | gold | `required_section_presence` | `value == 1.0` |
| `test_detects_missing_sections` | missing_sections | `required_section_presence` | `value < 1.0`, "method" or "limitations" in missing |
| `test_detects_missing_abstract` | missing_abstract | `required_section_presence` | `value < 1.0`, "abstract" in missing |
| `test_gold_paper_no_stubs` | gold | `required_section_non_emptiness` | `value == 1.0` |
| `test_detects_empty_stubs` | empty_sections | `required_section_non_emptiness` | `value < 1.0`, `empty_stubs` non-empty |
| `test_gold_paper_correct_order` | gold | `section_order_validity` | `value == 1.0` |
| `test_detects_wrong_order` | wrong_order | `section_order_validity` | `value == 0.0` |
| `test_gold_paper_adequate_lengths` | gold | `section_length_adequacy` | `value >= 0.8` |
| `test_detects_short_sections` | empty_sections | `section_length_adequacy` | `value < 1.0` |

### `test_citation.py` (4 теста)

| Тест | Фикстура | Проверяемая метрика | Assertion |
|------|----------|--------------------|----|
| `test_gold_paper_all_resolved` | gold | `citation_integrity` | `value == 1.0`, passed |
| `test_detects_broken_citations` | broken_citations | `citation_integrity` | `value < 1.0`, `>= 2` unresolved keys |
| `test_gold_paper_all_cited` | gold | `bibliography_usage_coverage` | `value == 1.0` |
| `test_detects_unused_entries` | unused_bib | `bibliography_usage_coverage` | `value < 1.0`, `>= 2` unused keys |

### `test_figures.py` (5 тестов)

| Тест | Фикстура | Проверяемая метрика | Assertion |
|------|----------|--------------------|----|
| `test_gold_paper_all_referenced` | gold | `figure_table_reference_coverage` | `value == 1.0` |
| `test_gold_paper_no_dangling` | gold | `dangling_figure_table_reference_count` | `value == 0`, passed |
| `test_detects_dangling_refs` | dangling_refs | `dangling_figure_table_reference_count` | `value >= 2` |
| `test_gold_paper_all_have_captions` | gold | `caption_coverage` | `value == 1.0` |
| `test_detects_missing_caption` | missing_captions | `caption_coverage` | `value < 1.0` |
| `test_gold_paper_consistent` | gold | `numbering_consistency` | `value == 0` |

### `test_content.py` (6 тестов)

| Тест | Фикстура | Проверяемая метрика | Assertion |
|------|----------|--------------------|----|
| `test_gold_paper_all_slots` | gold | `abstract_slot_coverage` | `value >= 0.66` (2/3 slots minimum) |
| `test_empty_abstract` | missing_abstract | `abstract_slot_coverage` | `value == 0.0` |
| `test_gold_paper_has_contributions` | gold | `contribution_statement_presence` | `value == 1.0` |
| `test_detects_missing_contributions` | no_contributions | `contribution_statement_presence` | `value == 0.0` |
| `test_gold_paper_has_experiment_slots` | gold | `experiment_slot_coverage` | `value >= 0.75` (3/4 slots minimum) |
| `test_gold_paper_has_repro_info` | gold | `reproducibility_slot_coverage` | `value >= 0.5` (2/4 slots minimum) |
| `test_gold_paper_has_values` | gold | `metric_value_presence` | `value >= 0.5` |

### `test_text_quality.py` (2 теста)

| Тест | Фикстура | Проверяемая метрика | Assertion |
|------|----------|--------------------|----|
| `test_gold_paper_low_errors` | gold | `spelling_errors` | `value < 20` |
| `test_gold_paper_consistent` | gold | `notation_consistency` | `value <= 2` |

---

### Итого: 28 тестов

```
tests/test_structural.py     9 тестов   (4 метрики × gold + corrupted)
tests/test_citation.py        4 теста   (2 метрики × gold + corrupted)
tests/test_figures.py          6 тестов  (4 метрики × gold + corrupted)
tests/test_content.py          7 тестов  (5 метрик × gold + corrupted)
tests/test_text_quality.py     2 теста   (2 метрики × gold only)
─────────────────────────────────────────
Всего:                        28 тестов
```

Все 28 тестов проходят:
```
$ python3 -m pytest tests/ -v
...
28 passed in 0.15s
```

---

## Пример вывода evaluator на реальном Writer output

```
======================================================================
WRITER BENCHMARK REPORT
======================================================================

--- HARD metrics ---
  [-] required_section_presence                       66.67%  (FAIL)
  [-] required_section_non_emptiness                  40.00%  (FAIL)
  [-] citation_integrity                              42.86%  (FAIL)
  [-] dangling_figure_table_reference_count                4  (FAIL)
  [+] caption_coverage                               100.00%  (PASS)

--- SOFT metrics ---
  [+] section_order_validity                         100.00%  (PASS)
  [-] section_length_adequacy                         25.00%  (FAIL)
  [-] bibliography_usage_coverage                     25.00%  (FAIL)
  [-] figure_table_reference_coverage                 50.00%  (FAIL)
  [+] numbering_consistency                                0  (PASS)
  [-] abstract_slot_coverage                          66.67%  (FAIL)
  [+] contribution_statement_presence                100.00%  (PASS)
  [-] experiment_slot_coverage                        50.00%  (FAIL)
  [-] reproducibility_slot_coverage                   50.00%  (FAIL)
  [+] metric_value_presence                          100.00%  (PASS)
  [-] spelling_errors                                     50  (FAIL)
  [+] notation_consistency                                 0  (PASS)

Hard: 1/5  |  Soft: 5/12
======================================================================
```

Это результат на `research__write_full_paper__gpt_5_2__001/ase26_holosophos_paper/` — реальный output Writer агента.

# 05 — M4: Compactor (pluggable backend)

**Связано:** [overview](00_overview.md), [02_entity_extractor](02_entity_extractor.md), [03_liveness_graph](03_liveness_graph.md), [04_cache_budgeter](04_cache_budgeter.md).

## Цель

Бакенд, который собственно превращает `TypedSegment[]` в более короткий `TypedSegment[]` (компакция). Pluggable: 4 базовых стратегии (порт прошлой сессии) + `auto` (gate-routed) + опциональный RoD.

## Зачем

Сам компактор — это **не главная новизна** библиотеки (главное — entity-gate перед ним). Но он должен быть качественно реализован, прозрачно конфигурируем, и externalize'нуть свои промпты.

## Зависимости

- M0 TypedSegment (читает / пишет сегменты).
- M2 LivenessGraph (в `auto` режиме — для gate-предиката).
- Внешний: `ai` (Vercel AI SDK) — для LLM-вызовов в `rolling_summary` / `task_aware`.

## API контракт

```ts
// packages/compactors/src/types.ts
import type { TypedSegment } from '@holo/typed-segment';
import type { LanguageModel } from 'ai';

export interface CompactionResult {
  segments: TypedSegment[];
  originalTokens: number;
  compactedTokens: number;
  strategy: string;
  audit: Record<string, unknown>;
}

export interface CompactorOpts {
  /** Активный пользовательский запрос (для task_aware / auto). */
  liveQuery?: string;
  /** Целевой бюджет компактированного префикса. */
  budget: number;
  /** LLM для compactor-вызовов. */
  compactorModel: LanguageModel;
  /** Experiment + item id для cost-log. */
  experiment: string;
  itemId: string;
}

export interface Compactor {
  name: string;
  compact(history: TypedSegment[], opts: CompactorOpts): Promise<CompactionResult>;
}
```

## Backend'ы v0.1

### 1. `passthrough` — no-op

```ts
// packages/compactors/src/passthrough.ts
export const passthroughCompactor: Compactor = {
  name: 'passthrough',
  async compact(history, opts) {
    const tokens = history.reduce((s, x) => s + (x.meta.tokens ?? 0), 0);
    return {
      segments: history,
      originalTokens: tokens,
      compactedTokens: tokens,
      strategy: 'passthrough',
      audit: { decision: 'no-op (default below budget)' },
    };
  },
};
```

### 2. `naive_truncation` — last-N + system

```ts
// packages/compactors/src/naive_truncation.ts
export const naiveTruncationCompactor: Compactor = {
  name: 'naive_truncation',
  async compact(history, opts) {
    const systems = history.filter((s) => s.role === 'system');
    const nonSystem = history.filter((s) => s.role !== 'system');
    const reversed = nonSystem.slice().reverse();
    const kept: TypedSegment[] = [];
    let tokens = systems.reduce((sum, s) => sum + (s.meta.tokens ?? 0), 0);
    for (const seg of reversed) {
      tokens += seg.meta.tokens ?? 0;
      if (tokens > opts.budget) break;
      kept.unshift(seg);
    }
    // Insert marker if dropped any
    const dropped = nonSystem.length - kept.length;
    const markerSegment: TypedSegment | null = dropped > 0
      ? makeMarkerSegment(`[truncated: dropped ${dropped} earlier turns]`)
      : null;
    const result = [...systems, ...(markerSegment ? [markerSegment] : []), ...kept];
    return {
      segments: result,
      originalTokens: history.reduce((s, x) => s + (x.meta.tokens ?? 0), 0),
      compactedTokens: tokens,
      strategy: 'naive_truncation',
      audit: { dropped, kept: kept.length },
    };
  },
};
```

### 3. `rolling_summary` — chunked LLM summary

```ts
// packages/compactors/src/rolling_summary.ts
import { generateText } from 'ai';
import { ROLLING_SUMMARY_PROMPT } from './prompts/rolling_summary.yaml';

export interface RollingSummaryConfig {
  chunkSize?: number;      // default 20 turns
  keepLastChunks?: number; // default 2
  maxSummaryWords?: number; // default 200
}

export function makeRollingSummaryCompactor(config: RollingSummaryConfig = {}): Compactor {
  const W = config.chunkSize ?? 20;
  const K = config.keepLastChunks ?? 2;
  return {
    name: 'rolling_summary',
    async compact(history, opts) {
      // Chunk into windows of W turns
      const chunks = chunkSegments(history, W);
      const tailChunks = chunks.slice(-K);
      const olderChunks = chunks.slice(0, -K);

      const summaries: TypedSegment[] = [];
      for (let i = 0; i < olderChunks.length; i++) {
        const chunk = olderChunks[i];
        const renderedChunk = chunk
          .map((s) => `[t${s.meta.turn_index} ${s.role}] ${getSegmentText(s)}`)
          .join('\n');
        const prompt = ROLLING_SUMMARY_PROMPT
          .replace('{chunk}', renderedChunk)
          .replace('{maxWords}', String(config.maxSummaryWords ?? 200));

        const result = await generateText({
          model: opts.compactorModel,
          prompt,
          maxTokens: 600,
          temperature: 0.0,
        });

        summaries.push(makeSummarySegment(result.text, {
          src_turns: chunk.map((s) => s.meta.turn_index),
          chunk_idx: i,
        }));
      }

      const newSegments = [...summaries, ...tailChunks.flat()];
      return {
        segments: newSegments,
        originalTokens: history.reduce((s, x) => s + (x.meta.tokens ?? 0), 0),
        compactedTokens: newSegments.reduce((s, x) => s + (x.meta.tokens ?? 0), 0),
        strategy: 'rolling_summary',
        audit: { chunks_summarized: olderChunks.length, tail_chunks_kept: tailChunks.length },
      };
    },
  };
}
```

```yaml
# packages/compactors/prompts/rolling_summary.yaml
template: |
  Summarize the following conversation chunk in ≤ {maxWords} words.
  PRESERVE all entity references (IDs, names, dates, currency amounts) VERBATIM —
  do not paraphrase them. Use the same exact tokens as they appear in the source.
  Focus on: what the user asked for, what was done, what the result was.

  Chunk:
  {chunk}
```

### 4. `task_aware` — single LLM call with live query

```ts
// packages/compactors/src/task_aware.ts
import { TASK_AWARE_PROMPT } from './prompts/task_aware.yaml';

export const taskAwareCompactor: Compactor = {
  name: 'task_aware',
  async compact(history, opts) {
    const renderedHistory = history
      .map((s) => `[t${s.meta.turn_index} ${s.role}] ${getSegmentText(s)}`)
      .join('\n');

    const prompt = TASK_AWARE_PROMPT
      .replace('{history}', renderedHistory)
      .replace('{liveQuery}', opts.liveQuery ?? '(no active query)')
      .replace('{budget}', String(opts.budget));

    const result = await generateText({
      model: opts.compactorModel,
      prompt,
      maxTokens: Math.floor(opts.budget * 0.5),
      temperature: 0.0,
    });

    const newSegment = makeAssistantTextSegment(
      `[compacted history (task-aware), original=${historyTokens(history)}t]\n${result.text}`,
      { compaction_strategy: 'task_aware', live_query: opts.liveQuery },
    );

    return {
      segments: [newSegment],
      originalTokens: historyTokens(history),
      compactedTokens: newSegment.meta.tokens ?? 0,
      strategy: 'task_aware',
      audit: {
        prompt_tokens: result.usage.promptTokens,
        completion_tokens: result.usage.completionTokens,
        budget: opts.budget,
      },
    };
  },
};
```

```yaml
# packages/compactors/prompts/task_aware.yaml
template: |
  You are a context compactor. Reduce the following conversation history to ≤ {budget} tokens
  while preserving information relevant to the user's current query.

  CRITICAL RULES:
  1. The user's current query is: "{liveQuery}".
  2. Keep VERBATIM any text directly relevant to answering this query.
  3. Keep VERBATIM all entity references (order_ids, user_ids, product_skus, dates, currency amounts)
     that the agent might need to look up or reference.
  4. Summarize everything else aggressively.
  5. Use citation hints like [session-N, turn-T] when you compress a span.

  History:
  {history}

  Output the compacted history below (only the compacted text, no preamble):
```

### 5. `auto` — gate-routed meta-strategy

```ts
// packages/compactors/src/auto.ts
import type { LivenessGraph } from '@holo/liveness-graph';

export function makeAutoCompactor(deps: {
  livenessGraph: LivenessGraph;
  rollingSummary: Compactor;
  taskAware: Compactor;
}): Compactor {
  return {
    name: 'auto',
    async compact(history, opts) {
      const currentTurn = Math.max(...history.map((s) => s.meta.turn_index)) + 1;

      // 1. Partition: which segments are eligible (gate clean)?
      const eligible: TypedSegment[] = [];
      const pinned: TypedSegment[] = [];
      for (const s of history) {
        if (deps.livenessGraph.isEligibleForCompaction(s, currentTurn)) {
          eligible.push(s);
        } else {
          pinned.push(s);
        }
      }

      if (eligible.length === 0) {
        return passthroughCompactor.compact(history, opts);
      }

      // 2. Detect trajectory class: chat-dominant vs tool-step
      const toolCallRate = history.filter((s) => s.role === 'tool_call').length / history.length;
      const backend = toolCallRate < 0.2 ? deps.taskAware : deps.rollingSummary;

      // 3. Compact eligible block via chosen backend
      const compactionResult = await backend.compact(eligible, opts);

      // 4. Merge: compacted eligible + pinned (preserving order)
      const merged = mergeByTurnIndex(compactionResult.segments, pinned);

      return {
        ...compactionResult,
        segments: merged,
        strategy: `auto/${backend.name}`,
        audit: {
          ...compactionResult.audit,
          eligible_count: eligible.length,
          pinned_count: pinned.length,
          tool_call_rate: toolCallRate,
          chosen_backend: backend.name,
        },
      };
    },
  };
}
```

### 6. RoD (Replay-on-Demand) — optional, behind flag

```ts
// packages/compactors/src/rod.ts (v0.1: optional, off by default)
export interface RoDConfig {
  sideStore: SideStore;  // { get(id, hash): Promise<unknown>; put(id, hash, result): Promise<void> }
}

export function makeRoDCompactor(config: RoDConfig): Compactor {
  return {
    name: 'rod',
    async compact(history, opts) {
      // 1. For each tool_result with meta.idempotent === true:
      //    - Compute args_hash from associated tool_call
      //    - Store full result in sideStore
      //    - Replace tool_result content with stub: <replayable id=... summary="..."/>
      // 2. Return modified history
      // 3. Re-injection: caller invokes restoreStubsIfNeeded() when a stubbed entity is re-referenced
      // [implementation deferred — design details below]
      throw new Error('RoD: implementation deferred to optional v0.1 stretch');
    },
  };
}

// Stub format (TypedSegment with content_block.kind='text'):
// "[replayable tool_result_for=<tool_call_id> args_hash=<sha> summary=\"Got order #W123 with 3 items totaling $45.99\"]"

// Restoration trigger:
// - Verifier: if M2 LivenessGraph detects an entity_ref that exists only inside a stub'd segment,
//   re-inject the full tool_result at a fresh cache breakpoint.
// - Hallucination signal: if LLM produces an entity_value that's not in any segment, re-execute.
```

**В v0.1 RoD — за флагом `experimental_replay_on_demand: true`, off by default.** Footgun: non-idempotent tool отмеченный idempotent ломает state.

### Registry

```ts
// packages/compactors/src/registry.ts
import type { Compactor } from './types';

export const COMPACTOR_REGISTRY: Record<string, Compactor> = {
  passthrough: passthroughCompactor,
  naive_truncation: naiveTruncationCompactor,
  rolling_summary: makeRollingSummaryCompactor(),
  task_aware: taskAwareCompactor,
  // auto / rod создаются с dependency injection
};

export function getCompactor(name: string): Compactor {
  const c = COMPACTOR_REGISTRY[name];
  if (!c) throw new Error(`Unknown compactor: ${name}`);
  return c;
}
```

## Acceptance criteria

- [ ] **Smoke n=10** на LongMemEval-S: все 4 backend'а (passthrough, naive, rolling, task_aware) проходят без ошибок и репродуцируют прошлой сессии full-context baseline (accuracy ~0.65).
- [ ] **`auto`-стратегия** выбирает оракул-политику ≥ 70 % времени на смешанной τ-bench retail + LongMemEval-S сессии (внутренний benchmark, n=30).
- [ ] **Tool-pair invariant**: ни один компактор не оставляет `tool_call` без `tool_result` или наоборот. Unit-тест на 100 синтетических трасс.
- [ ] **Prompts in YAML, not code**: все промпты в `packages/compactors/prompts/*.yaml`; при изменении промпта код менять не нужно.
- [ ] **Externalized config**: `chunkSize`, `keepLastChunks` для `rolling_summary` конфигурируются через factory function.

## Тестовый план

```typescript
// packages/compactors/tests/tool_pair_invariant.spec.ts
import { test, expect } from 'vitest';
import { COMPACTOR_REGISTRY } from '../src';

test.each(Object.keys(COMPACTOR_REGISTRY))(
  '%s never orphans tool_call / tool_result pairs',
  async (name) => {
    const compactor = COMPACTOR_REGISTRY[name];
    for (let i = 0; i < 100; i++) {
      const history = generateRandomTrace({ minTurns: 5, maxTurns: 15, withTools: true });
      const result = await compactor.compact(history, { ...defaultOpts });
      const toolCallIds = new Set(result.segments.filter((s) => s.role === 'tool_call').map((s) => s.tool_call_id));
      const toolResultIds = new Set(
        result.segments.filter((s) => s.role === 'tool_result').map((s) => s.tool_result_for),
      );
      expect(toolCallIds).toEqual(toolResultIds);
    }
  },
);
```

## Переиспользуем из прошлой сессии

| Источник | Что брать | Изменения |
|---|---|---|
| `workdir/compaction_policies__20260508_0231/mle/code/compactors.py:39-56` (`compact_full_context`) | Алгоритм passthrough | Тривиальный port на TS |
| `workdir/compaction_policies__20260508_0231/mle/code/compactors.py:62-102` (`compact_naive_truncation`) | Алгоритм last-N + system | Port; marker-segment format можем переиспользовать |
| `workdir/compaction_policies__20260508_0231/mle/code/compactors.py:108-176` (`compact_rolling_summary`) | Алгоритм chunked + LLM summary; `_render_chunk` helper | Port; **prompt** выносим в YAML; chunk size — config knob |
| `workdir/compaction_policies__20260508_0231/mle/code/compactors.py:435-509` (`compact_task_aware`) | Алгоритм single LLM-call с live_query | Port; **prompt** в YAML; thinking budget — option |
| `workdir/compaction_policies__20260508_0231/mle/code/compactors.py:202-431` (`compact_type_aware`) | NOT porting в v0.1 — этот компактор требует TYPE_SHARES hardcode и сложный fallback. Идея «per-role budget» интегрирована в M2 (ELR gate) |  |
| `workdir/compaction_policies__20260508_0231/mle/code/compactors.py:514-520` (`STRATEGIES` dict) | Registry pattern | Port в TS как `COMPACTOR_REGISTRY` |

## Пакет

```
packages/compactors/
├── src/
│   ├── index.ts                    # public exports
│   ├── registry.ts                 # COMPACTOR_REGISTRY + getCompactor
│   ├── passthrough.ts
│   ├── naive_truncation.ts
│   ├── rolling_summary.ts
│   ├── task_aware.ts
│   ├── auto.ts
│   ├── rod.ts                      # optional, off by default
│   ├── helpers/
│   │   ├── render.ts               # getSegmentText, renderChunk
│   │   └── markers.ts              # makeMarkerSegment, makeSummarySegment
│   └── types.ts
├── prompts/
│   ├── rolling_summary.yaml
│   ├── task_aware.yaml
│   └── README.md                   # how to override / extend prompts
├── tests/
│   ├── passthrough.spec.ts
│   ├── naive.spec.ts
│   ├── rolling.spec.ts             # with mocked LLM
│   ├── task_aware.spec.ts          # with mocked LLM
│   ├── auto.spec.ts                # with mock LivenessGraph
│   ├── tool_pair_invariant.spec.ts
│   └── fixtures/
└── package.json
```

## Риски

- **R1 — task_aware всё ещё может пропускать entity_ids**, даже с промптом «keep verbatim». Это решает M1+M2 (extraction + gate), не сам компактор. Если M1 не нашёл entity, gate его не запинит, и task_aware может его дропнуть. Mitigation: tier-3 LLM rescue в M1 + 92% recall target.
- **R2 — Prompt drift**: если разработчик меняет prompt в YAML, behavior меняется. Mitigation: snapshot-тесты на конкретных fixed-input выходах (golden output).
- **R3 — LLM-вызов latency** на каждой компакции добавляет 200–800 ms. Mitigation: компакция стреляет ТОЛЬКО на budget/cache-breach, не каждый ход.
- **R4 — `auto` heuristic toolCallRate < 0.2 — грубая**. Не работает на mixed-mode сессиях, где первые 10 ходов чат, потом 5 ходов tool-heavy. Mitigation: pre-trained TCAD classifier — v0.2; в v0.1 — простой rate threshold.

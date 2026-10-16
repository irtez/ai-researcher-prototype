# 06 — Vercel AI SDK adapter + публичный API

**Связано:** [overview](00_overview.md), [01_typed_segment](01_typed_segment.md), все M1–M4 docs, [`docs/research/lib_b_frameworks.md`](../research/lib_b_frameworks.md).

## Цель

Главный пользовательский интерфейс библиотеки — middleware для Vercel AI SDK `prepareStep` hook, который собирает все 5 модулей (M0–M4) в zero-config pipeline. Это «лицо» библиотеки. Если этого нет — у нас нет продукта, только академический набор пакетов.

## Зачем

Vercel AI SDK — first integration target, выбранный пользователем. Из ресеча [`lib_b_frameworks.md`](../research/lib_b_frameworks.md): встроенной компакции в AI SDK **нет** (discussion #8193 заблокирован, community thread «no built-in»). Maintainer recommend'ит `prepareStep` как seam для ручной компакции. Это наша точка входа.

## Зависимости

- M0 `@holo/typed-segment` (TypedSegment + adapter)
- M1 `@holo/entity-extractor`
- M2 `@holo/liveness-graph`
- M3 `@holo/cache-budgeter`
- M4 `@holo/compactors`
- Внешний: `ai` (Vercel AI SDK ≥ 5.0)

## Минимальный API (zero-config)

```ts
// examples/next-retail-chat/app/api/chat/route.ts
import { streamText } from 'ai';
import { anthropic } from '@ai-sdk/anthropic';
import { compactionMiddleware } from '@holo/vercel-ai';

import { tools } from '../tools';

export async function POST(req: Request) {
  const { messages } = await req.json();

  const result = streamText({
    model: anthropic('claude-haiku-4-5'),
    messages,
    tools,
    prepareStep: compactionMiddleware({ mode: 'auto' }),  // ← одна строка
  });

  return result.toUIMessageStreamResponse();
}
```

Это всё. Под капотом middleware собирает M0–M4 в default-цепочку (см. `00_overview.md` § Default wiring).

## Полный API

```ts
// packages/vercel-ai/src/types.ts
import type { PrepareStepFunction, Tool, LanguageModel } from 'ai';

export interface CompactionMiddlewareConfig {
  mode: 'auto' | 'manual' | 'off';

  // === auto-mode knobs ===

  /** Budget for the prefix (in tokens). Default 50_000. */
  budget?: number;
  /** Watermark margin before triggering compaction. Default 5_000. */
  watermarkMargin?: number;

  /** Per-vendor pricing override. Anthropic Haiku 4.5 defaults applied. */
  pricing?: import('@holo/cache-budgeter').CacheBudgeterConfig['pricing'];

  // === manual-mode knobs ===

  /** Force a specific compactor backend. */
  compactor?: 'passthrough' | 'naive_truncation' | 'rolling_summary' | 'task_aware' | 'auto';

  /** Override compactor's LLM (default = same as driver, or `compactorModelOverride`). */
  compactorModel?: LanguageModel;

  // === entity-extractor knobs ===

  /** Custom regex patterns (in addition to defaults). */
  entityPatterns?: import('@holo/entity-extractor').EntityPattern[];
  /** Tier-3 LLM rescue model. Default null (off). */
  rescueModel?: LanguageModel;

  // === liveness-graph knobs ===

  /** Override default liveness threshold. Default 0.3. */
  livenessThreshold?: number;
  /** Override default recency alpha. Default 0.3. */
  recencyAlpha?: number;
  /** Open-task slots (current entities-in-work). */
  openSlots?: { type: string; value: string }[];

  // === cache scheduler knobs ===

  /** Breakpoint placement strategy. Default 'auto' (4-of-N knapsack). */
  cacheBreakpoints?: 'auto' | 'trailing' | 'static-4' | 'none';

  // === observability ===

  /** Hook fired after each compaction event. */
  onCompaction?: (audit: CompactionAudit) => void | Promise<void>;
  /** Cost-log path (JSONL). Default = no logging. */
  costLogPath?: string;

  // === experimental ===

  /** Enable RoD (Replay-on-Demand). Off by default; requires idempotent tools. */
  experimentalReplayOnDemand?: boolean;
}

export interface CompactionAudit {
  turn_index: number;
  decision: 'no-op' | 'compacted';
  reason?: string;
  original_tokens: number;
  compacted_tokens: number;
  strategy?: string;
  cache_breakpoints?: string[];      // segment ids
  liveness_eligibility_rate?: number;
  duration_ms: number;
}

export function compactionMiddleware(config: CompactionMiddlewareConfig): PrepareStepFunction;
```

## Power-user пример

```ts
const result = streamText({
  model: anthropic('claude-haiku-4-5'),
  messages,
  tools,
  prepareStep: compactionMiddleware({
    mode: 'manual',
    compactor: 'rolling_summary',
    compactorModel: anthropic('claude-haiku-4-5'),  // can be different from driver
    budget: 30_000,
    watermarkMargin: 3_000,
    entityPatterns: [
      { type: 'support_ticket', pattern: /\bTICKET-\d{6}\b/g },
    ],
    rescueModel: anthropic('claude-haiku-4-5'),
    livenessThreshold: 0.25,
    openSlots: [{ type: 'order_id', value: '#W12345678' }],
    cacheBreakpoints: 'auto',
    onCompaction: (audit) => console.log('compacted', audit),
    costLogPath: 'workdir/cost-log.jsonl',
  }),
});
```

## Tool registration helper (для RoD и для M2 schema extraction)

```ts
// packages/vercel-ai/src/define-tool.ts
import { tool, type Tool } from 'ai';
import { z } from 'zod';

export interface DefineToolOptions<I extends z.ZodObject<any>, O> {
  description: string;
  inputSchema: I;
  /** Markdown documentation — used in tool descriptions. */
  documentation?: string;
  /** If true, the tool is treated as a pure function: same input → same output.
   *  Enables RoD. Defaults to false (safe). */
  idempotent?: boolean;
  /** The actual execution. */
  execute: (input: z.infer<I>) => Promise<O>;
}

export function defineTool<I extends z.ZodObject<any>, O>(opts: DefineToolOptions<I, O>): Tool & { _idempotent: boolean } {
  const t = tool({
    description: opts.description,
    inputSchema: opts.inputSchema,
    execute: opts.execute,
  });
  return Object.assign(t, { _idempotent: opts.idempotent ?? false });
}
```

Использование:
```ts
import { defineTool } from '@holo/vercel-ai';

const getOrder = defineTool({
  description: 'Fetch order details by order_id',
  inputSchema: z.object({ order_id: z.string() }),
  idempotent: true,  // безопасно для replay
  execute: async ({ order_id }) => { /* ... */ },
});

const cancelOrder = defineTool({
  description: 'Cancel an order',
  inputSchema: z.object({ order_id: z.string() }),
  idempotent: false,  // НЕ replay'able; ВАЖНО
  execute: async ({ order_id }) => { /* ... */ },
});
```

Middleware смотрит на `_idempotent` флаг и решает, можно ли применять RoD к tool_result'ам этого инструмента.

## Реализация middleware

```ts
// packages/vercel-ai/src/middleware.ts
import { fromCoreMessages, toCoreMessages } from '@holo/typed-segment';
import { EntityExtractor } from '@holo/entity-extractor';
import { LivenessGraph, extractToolInputs } from '@holo/liveness-graph';
import { CacheBudgeter } from '@holo/cache-budgeter';
import { getCompactor, makeAutoCompactor, makeRollingSummaryCompactor } from '@holo/compactors';

export function compactionMiddleware(config: CompactionMiddlewareConfig): PrepareStepFunction {
  if (config.mode === 'off') {
    return ({ messages }) => ({ messages });
  }

  // Lazy init of stateful modules (per-conversation; reset between requests if no continuity)
  const state = createMiddlewareState(config);

  return async ({ messages, stepNumber, model, tools }) => {
    const turnStart = Date.now();

    // 1. Convert to TypedSegment
    const segments = fromCoreMessages(messages, { turn_index: stepNumber });

    // 2. Schema setup (one-time)
    if (state.firstCall && tools) {
      state.livenessGraph.setToolInputs(extractToolInputs(tools));
      state.firstCall = false;
    }
    if (config.openSlots) state.livenessGraph.setOpenSlots(config.openSlots);

    // 3. Entity extraction (new segments only — extractBatch handles idempotency)
    await state.entityExtractor.extractBatch(segments);

    // 4. Liveness observation
    state.livenessGraph.observe(segments);

    // 5. Cache-budgeter decides compaction
    const decision = state.cacheBudgeter.shouldCompact(segments, stepNumber);

    let resultSegments = segments;
    let audit: CompactionAudit = {
      turn_index: stepNumber,
      decision: 'no-op',
      original_tokens: totalTokens(segments),
      compacted_tokens: totalTokens(segments),
      duration_ms: 0,
    };

    if (decision.shouldCompact) {
      const compactor = state.compactor;
      const result = await compactor.compact(segments, {
        liveQuery: latestUserMessageText(segments),
        budget: config.budget ?? 50_000,
        compactorModel: config.compactorModel ?? model,
        experiment: 'vercel-runtime',
        itemId: state.sessionId,
      });
      resultSegments = result.segments;
      audit = {
        turn_index: stepNumber,
        decision: 'compacted',
        reason: decision.reason,
        original_tokens: result.originalTokens,
        compacted_tokens: result.compactedTokens,
        strategy: result.strategy,
        liveness_eligibility_rate: state.livenessGraph.getStats().eligibilityRate,
        duration_ms: Date.now() - turnStart,
      };
    }

    // 6. Cache breakpoint placement
    if (config.cacheBreakpoints !== 'none') {
      const plan = state.cacheBudgeter.placeBreakpoints(resultSegments, stepNumber);
      audit.cache_breakpoints = plan.breakpointAfter;
    }

    // 7. Hook + cost-log
    if (config.onCompaction) await config.onCompaction(audit);
    if (config.costLogPath) await appendCostLog(config.costLogPath, audit);

    // 8. Convert back to CoreMessages, including cache_control markers
    const outMessages = toCoreMessages(resultSegments, {
      attachAnthropicCacheControl: config.cacheBreakpoints !== 'none',
    });

    return { messages: outMessages };
  };
}
```

## Структура пакета

```
packages/vercel-ai/
├── src/
│   ├── index.ts                    # public exports: compactionMiddleware, defineTool, types
│   ├── middleware.ts               # main implementation
│   ├── define-tool.ts              # defineTool helper
│   ├── state.ts                    # createMiddlewareState (per-conversation state)
│   ├── cost-log.ts                 # appendCostLog
│   ├── helpers.ts                  # latestUserMessageText, totalTokens, etc.
│   └── types.ts
├── tests/
│   ├── integration.spec.ts         # end-to-end with mocked Anthropic
│   ├── tool-pair-invariant.spec.ts # tool_call/tool_result pairs survive
│   ├── cache-breakpoint.spec.ts    # cache_control markers correctly placed
│   ├── onCompaction.spec.ts        # hook fires with correct audit
│   └── fixtures/
│       ├── retail-trace-1.json
│       └── retail-trace-2.json
└── package.json
```

## Структура `examples/next-retail-chat/`

```
examples/next-retail-chat/
├── app/
│   ├── api/chat/route.ts           # the 3-line integration
│   ├── page.tsx                    # chat UI
│   └── layout.tsx
├── tools/                          # sample retail simulator tools
│   ├── get_order.ts                # defineTool(idempotent: true, ...)
│   ├── search_products.ts          # defineTool(idempotent: true, ...)
│   ├── cancel_order.ts             # defineTool(idempotent: false, ...)
│   └── data.ts                     # in-memory fake DB
├── README.md                       # 3-step walkthrough
└── package.json
```

README.md:

```markdown
# Retail Chat Example (Vercel AI SDK + @holo/vercel-ai)

## Setup (3 steps)

1. `pnpm install`
2. Set `ANTHROPIC_API_KEY` in `.env.local`
3. `pnpm dev`

Open http://localhost:3000 and chat with the retail assistant.
The chat uses `@holo/vercel-ai` to compact tool-result history while preserving
order_id / user_id references and cache hit rate.

## What's happening

- `app/api/chat/route.ts:18` — single line `prepareStep: compactionMiddleware({mode: 'auto'})` enables compaction.
- `tools/get_order.ts:5` — `idempotent: true` declares this is a safe read for RoD (optional).
- ...
```

## Acceptance criteria

- [ ] **3-line integration works** в чистом Next.js приложении: `pnpm install @holo/vercel-ai`, `import { compactionMiddleware }`, добавить в `prepareStep`. Конец.
- [ ] **Setup time** ≤ 5 минут от `git clone` до работающего чата с компакцией. UX probe с 3 разработчиками (Week 5).
- [ ] **Integration test**: моки на Anthropic API, делаем 12-турн trace, проверяем:
  - `cache_read_input_tokens` появляется на turn 2+.
  - На turn ~8 (когда budget breached) — `onCompaction` стреляет с `decision='compacted'`.
  - tool_call/tool_result пары всегда парны в output messages.
  - Финальный `cache_read_input_tokens / (cache_read + cache_creation + uncached) ≥ 0.60`.
- [ ] **Cost-log JSONL** записывается, формат совместим с прошлой сессии (расширенный cache-aware полями).
- [ ] **Performance**: middleware вносит < 50 ms p95 на типичном `prepareStep` без LLM-вызовов (tier-1+2 extraction only).
- [ ] **Type-safety**: каждый экспорт имеет полные TypeScript типы; `tsc --noEmit` проходит без ошибок.

## Test plan

```typescript
// packages/vercel-ai/tests/integration.spec.ts
import { test, expect } from 'vitest';
import { streamText } from 'ai';
import { compactionMiddleware } from '../src';
import { mockAnthropic } from './fixtures/mock-anthropic';

test('12-turn retail trace gets compacted at budget breach', async () => {
  const audits: CompactionAudit[] = [];
  const result = streamText({
    model: mockAnthropic({ haiku45Trace: 'retail-trace-1.json' }),
    messages: retailMessages,
    tools: retailTools,
    prepareStep: compactionMiddleware({
      mode: 'auto',
      budget: 10_000,  // low to force a compaction at turn ~6
      onCompaction: (a) => audits.push(a),
    }),
  });
  await result.consumeStream();
  expect(audits.filter((a) => a.decision === 'compacted').length).toBeGreaterThan(0);
  // Verify final cache fraction
  const lastAudit = audits.at(-1);
  expect(lastAudit?.cache_breakpoints?.length).toBeGreaterThan(0);
});

test('tool pair invariant survives end-to-end', async () => {
  // ... generate 100 random retail traces, check no orphan tool_calls in output
});
```

## Переиспользуем из прошлой сессии

Нет прямых TypeScript-аналогов. Прошлая сессия была чистым Python (через мок Anthropic SDK). Middleware — новый код. Но мы можем переиспользовать:

| Источник | Что брать |
|---|---|
| `workdir/compaction_policies__20260508_0231/mle/code/run_smoke.py` | Pattern «orchestrator с per-strategy A/B» — переносим в `eval/bench_runner.ts` (см. [07_eval_plan.md](07_eval_plan.md)). |
| `workdir/compaction_policies__20260508_0231/mle/cost_log.jsonl` | JSONL формат — расширяем cache-aware полями. |

## Риски

- **R1 — AI SDK 5 → 6 churn**. `maxSteps` → `stopWhen`, `prepareStep` API эволюционирует. Mitigation: пинн на v5 GA в peerDeps; v6 preview branch.
- **R2 — `prepareStep` не предоставляет `tools` schema** на всех версиях. Mitigation: fallback — accept `tools` отдельным config param, если runtime не отдаёт.
- **R3 — Per-conversation state vs serverless**. На Vercel Edge каждый запрос — новый instance; state не переживёт через requests. Mitigation: state-init из `messages[]` (LivenessGraph пересоздаётся, observe-bulk на всю историю); или caller передаёт persistent state через config.
- **R4 — Tool registration без `defineTool` helper'а**. Если пользователь использует raw `tool({...})` — мы не знаем `idempotent` флага. Mitigation: assume `idempotent: false`; cookbook показывает, как использовать `defineTool` для opt-in.
- **R5 — Cost-log writes могут блокировать stream**. Mitigation: async write с буферизацией.

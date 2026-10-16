# 01 — M0: TypedSegment + adapter (Vercel AI SDK)

**Связано:** [overview](00_overview.md), [02_entity_extractor](02_entity_extractor.md).

## Цель

Универсальная нормализованная схема сообщений, общая для всех модулей библиотеки (M1–M4) и пригодная для дальнейших cross-framework адаптеров (LangGraph, LlamaIndex, Anthropic Messages, OpenAI Responses — v0.2). В v0.1 имплементируем TypedSegment и lossless-адаптер только для **Vercel AI SDK CoreMessage**.

## Зачем

Каждый SDK кодирует tool_calls по-своему: Vercel `CoreMessage` parts-array; LangChain `BaseMessage` subclasses; OpenAI Responses items; Anthropic content-blocks. Универсальная компакция требует общего представления, достаточно богатого для всех нужд библиотеки: tool_call_id ↔ tool_result_for связки, entity_refs, importance hints, liveness scores, cache_breakpoint hints.

## Зависимости

- Никаких (M0 — foundation для всех остальных модулей).
- Внешний: `ai` (Vercel AI SDK, npm) — типы `CoreMessage`, `TextPart`, `ToolCallPart`, `ToolResultPart`.

## API контракт

### JSON-схема (TypeScript reference)

```ts
// packages/typed-segment/src/types.ts

export type Role =
  | 'system'
  | 'user'
  | 'assistant_text'
  | 'assistant_reasoning'
  | 'tool_call'
  | 'tool_result';

export interface EntityRef {
  type: string;        // 'order_id', 'user_id', 'email', ...
  value: string;       // the literal token
  path?: string;       // JSON-path or text offset where it was found
}

export interface ContentBlock {
  kind: 'text' | 'json' | 'bytes_ref' | 'image_ref';
  text?: string;
  json?: unknown;
  bytes_ref?: { uri: string; sha256: string };
  // Заполняется M1 EntityExtractor; читается M2 LivenessGraph.
  entity_refs?: EntityRef[];
}

export interface TypedSegment {
  id: string;                       // стабильный, по умолчанию content-hash
  role: Role;
  content_blocks: ContentBlock[];
  tool_call_id?: string;            // для пары tool_call → tool_result
  tool_result_for?: string;         // обратная ссылка на tool_call.id
  meta: {
    turn_index: number;
    session_id?: string;
    tool_name?: string;
    idempotent?: boolean;           // флаг от tool registry (нужен для RoD)
    importance_hint?: number;       // 0..1, override от пользователя
    liveness_score?: number;        // заполняется M2 LivenessGraph
    cache_breakpoint_after?: 'ephemeral' | '1h' | null;  // заполняется M3
    tokens?: number;                // токен-счёт через tokenizer
  };
}
```

### Adapter (Vercel AI SDK)

```ts
// packages/typed-segment/src/adapters/vercel.ts

import type { CoreMessage, ModelMessage } from 'ai';

export interface AdapterContext {
  turn_index: number;
  session_id?: string;
  /** Если caller хочет переопределить tokenizer */
  tokenize?: (text: string) => number;
}

/**
 * Конвертирует один Vercel CoreMessage в один или несколько TypedSegment.
 * Например, assistant с tool_calls = [assistant_text, tool_call_1, tool_call_2, ...].
 */
export function fromCoreMessage(m: CoreMessage, ctx: AdapterContext): TypedSegment[];

/**
 * Конвертирует TypedSegment обратно в CoreMessage.
 * Round-trip property: для любого валидного CoreMessage m,
 *   `toCoreMessages(fromCoreMessage(m, ctx)) ≡ [m]` по семантике.
 */
export function toCoreMessage(s: TypedSegment): CoreMessage;

/**
 * Удобный bulk-конвертер.
 */
export function fromCoreMessages(ms: CoreMessage[], ctx: AdapterContext): TypedSegment[];
export function toCoreMessages(ss: TypedSegment[]): CoreMessage[];
```

### Инварианты

- **`tool_call_id` и `tool_result_for` всегда парные.** Компакторы и trimmer'ы не имеют права их разорвать. Если M4 удаляет `tool_call` сегмент, он обязан удалить соответствующий `tool_result`, и наоборот.
- **`meta.cache_breakpoint_after` — единственная мутация, разрешённая на сегменте после append'а.** Только M3 ротирует breakpoints; остальные модули — read-only по `meta`.
- **`entity_refs` immutable после первого прохода extractor'а.** LLM rescue tier пишет один раз; повторных проходов нет.
- **`content_blocks` immutable после append.** Компакция создаёт *новый* сегмент (snapshot), не модифицирует существующий.
- **`id` стабилен.** По умолчанию = `sha256(role + content_blocks.serialize()).slice(0, 16)`. Если caller хочет свой id — передаёт явно.

### Маппинг ролей при `toCoreMessage()`

| TypedSegment.role | Vercel CoreMessage.role |
|---|---|
| `system` | `system` |
| `user` | `user` |
| `assistant_text` | `assistant` (text part) |
| `assistant_reasoning` | `assistant` (text part, с маркером `[reasoning]` в начале) |
| `tool_call` | `assistant` (tool-call part) |
| `tool_result` | `tool` (tool-result part) |

При обратной конвертации: один assistant-message с парой `tool_call + text` parts расщепляется на 2 `TypedSegment` (assistant_text + tool_call). При обратной сборке (`toCoreMessages`) consecutive `assistant_*` сегменты в одном turn'е сливаются обратно в один `CoreMessage`.

## Реализация — заметки

### Tokenization

```ts
// packages/typed-segment/src/tokenize.ts
import { encoding_for_model } from 'tiktoken';

const enc = encoding_for_model('gpt-4o');  // cl100k_base
export function defaultTokenize(text: string): number {
  return enc.encode(text).length;
}
```

Tokenizer — pluggable; default = cl100k_base (как в прошлой сессии). Caller может передать свой через `AdapterContext.tokenize`.

### ID generation

```ts
import { createHash } from 'node:crypto';
function generateId(role: Role, content_blocks: ContentBlock[]): string {
  const h = createHash('sha256');
  h.update(role);
  for (const cb of content_blocks) {
    h.update(cb.kind);
    if (cb.text) h.update(cb.text);
    if (cb.json) h.update(JSON.stringify(cb.json));
    if (cb.bytes_ref) h.update(cb.bytes_ref.sha256);
  }
  return h.digest('hex').slice(0, 16);
}
```

Стабильность важна: M2 LivenessGraph и M3 CacheBudgeter индексируют сегменты по `id`.

### Стабильная JSON-сериализация

Когда `content_block.kind === 'json'`, при сериализации в `toCoreMessage` нужен **stable order ключей**, иначе кеш-хеш будет нестабилен. Используем `fast-json-stable-stringify` или встроенный JSON.stringify с явным sorter'ом.

## Acceptance criteria

- [ ] **Round-trip fidelity:** `toCoreMessages(fromCoreMessages(m, ctx))` семантически тождественно `m` на 5 000 property-test трасс.
- [ ] **Property tests** покрывают: text, tool_call с args (Zod-schema), tool_result с JSON content, multi-block assistant (text + tool_call), image (как placeholder через `image_ref`), system, user.
- [ ] **Invariant tests:** при попытке мутировать `content_blocks` существующего сегмента — runtime error в dev-режиме.
- [ ] **Tokenization integration:** `meta.tokens` заполняется автоматически при создании сегмента.
- [ ] **Performance:** конверсия `fromCoreMessage` < 100 µs p95 для типичного assistant + tool_call message (без JSON-парсинга больших tool_result'ов).

## Тестовый план

```typescript
// packages/typed-segment/tests/round-trip.spec.ts
import { test } from 'vitest';
import * as fc from 'fast-check';
import { fromCoreMessages, toCoreMessages } from '../src';

test.prop([fc.array(arbCoreMessage, { minLength: 1, maxLength: 20 })])(
  'round-trip CoreMessage[] preserves semantics',
  (messages) => {
    const segments = fromCoreMessages(messages, { turn_index: 0 });
    const restored = toCoreMessages(segments);
    expect(restored).toMatchObject(messages);
  },
);
```

`arbCoreMessage` — кастомный fast-check arbitrary, генерирующий валидные Vercel CoreMessage с tool_call/tool_result парами, text content, optional images.

## Переиспользуем из прошлой сессии

| Источник | Что брать |
|---|---|
| `workdir/compaction_policies__20260508_0231/mle/code/segments.py:13-31` | 6-class `Role` enum (точно тот же набор). Порт в TS Literal type. |
| `workdir/compaction_policies__20260508_0231/mle/code/segments.py:34-56` | `Segment` dataclass с `role/content/meta/tokens`. Расширяем под TypedSegment с `content_blocks` (мульти-блочный content) и `entity_refs` (новое поле). |
| `workdir/compaction_policies__20260508_0231/mle/code/segments.py:63-95` | `flatten_longmemeval_history` — **НЕ переиспользовать как есть** (LongMemEval-specific); сделать generic `flattenFromVercel` на той же идее. |

## Пакет

```
packages/typed-segment/
├── src/
│   ├── index.ts                 # public exports
│   ├── types.ts                 # TypedSegment / Role / ContentBlock / EntityRef
│   ├── tokenize.ts              # default tokenizer (cl100k_base)
│   ├── id.ts                    # generateId()
│   └── adapters/
│       ├── vercel.ts            # fromCoreMessage / toCoreMessage
│       └── index.ts             # re-exports
├── tests/
│   ├── round-trip.spec.ts
│   ├── invariants.spec.ts
│   └── tokenize.spec.ts
├── package.json
├── tsconfig.json
└── vitest.config.ts
```

`package.json` minimal:
- `name`: `@holo/typed-segment` (или окончательное название после кикоффа)
- `dependencies`: `ai` (peerDep), `tiktoken`, `fast-json-stable-stringify`
- `devDependencies`: `vitest`, `fast-check`, `typescript`

## Риски

- **Vercel AI SDK API churn**: v5 → v6 преобразование может сменить `ModelMessage`-структуру. Пинн на `ai@^5` в peerDeps; v6-адаптер — отдельная ветка.
- **tiktoken на edge runtimes**: Vercel Edge не поддерживает Node-нативные модули. Fallback: lighter token estimator или WASM-tiktoken (`@dqbd/tiktoken`).
- **Image / bytes_ref в content_block**: в v0.1 не парсим изображения, только placeholder. Кто-нибудь может попытаться использовать `image_ref` с invalid URI — добавить валидацию (но не блокировать).

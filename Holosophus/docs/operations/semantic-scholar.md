# Semantic Scholar and bibliography verification

The Academia MCP service uses `S2_API_KEY` from its environment when provided.
Set it in the deployment's private `.env`, then recreate Academia. Compose
already passes that file to the service. Leave the value empty for anonymous
access. The key is sent only as `x-api-key` to the configured S2 endpoint,
not to other tools. Do not put real keys in source control or logs.

```dotenv
S2_API_KEY=
S2_MAX_RETRIES=3
```

Semantic Scholar documents a shared unauthenticated quota and an introductory
individual-key rate of 1 request/second across endpoints.
[Official API overview](https://webflow.semanticscholar.org/product/api).
Keyed requests are paced within the Academia process. Multiple service replicas
sharing a key do not share this in-memory pacing state.

## Retry policy

Search, paper details, citations and references use the same S2-specific client.
Other providers retain their existing HTTP helper behavior.

- At most three retries, four attempts total. `S2_MAX_RETRIES` can reduce this
  count, but values above three do not remove the safety limit.
- Transient connection failures, 429 and 500/502/503/504 use waits of 2, 4 and
  8 seconds. Nonretryable HTTP errors are returned immediately.
- A valid `Retry-After` overrides the default wait. Seconds and HTTP dates
  are accepted. The server cooldown is shared by subsequent S2 calls in the
  process. If it would exceed the remaining budget, the call returns unavailable
  without waiting or retrying before the requested time.
- Each S2 request has a 30-second scheduling budget. New attempts and waits
  are admitted only within it. Connect/read timeouts are at most 3/5 seconds
  and are reduced by the remaining budget. As with Requests generally, these
  are transport timeouts, not a guarantee against every OS/DNS or streaming delay.
- One `verify_references` invocation shares a 60-second online budget across
  all entries, retries and arXiv-to-title fallbacks. Entries beyond the remaining
  budget are explicitly skipped. This prevents multiplying long waits by every
  citation in an unavailable catalog.

No API key is required to launch research. An unavailable S2 lookup is reported
as unverified, so an agent may use another source and report remaining uncertainty.
The client does not start a new agent run or an approval cycle.

## Meaning of verification fields

`resolved` counts citation keys that exist in the supplied BibTeX file. It does
not establish that the cited source exists or supports the associated claim.

`existence_checked` retains its old numeric meaning: attempted online lookups.
Its description and summary no longer call every attempt a verified source.
`existence_confirmed` counts matching catalog records. `existence_unavailable`
counts failed or timed-out lookups. `existence_skipped` counts entries not
attempted because of missing identifiers, the count cap or the time budget.

`existence_status` is `complete`, `partial` or `not_requested`. Coverage is
separate from validity: a complete search may still find mismatched metadata.
The existing `ok` meaning remains compatible: missing keys, not-found records
and title mismatches make it false. Network failures alone do not. Inspect
`existence_status`, counters and `issues` before claiming all sources verified.
Catalog misses should be cross-checked when appropriate, and catalog matching
itself does not verify a quotation or scientific claim.

## Offline checks

`academia_mcp/tests/test_external_search_offline.py` uses a local HTTP server
and a controllable clock. It covers retries, Retry-After, keys and transport
limits without calling S2. `tests/test_verify_references.py` covers partial
results, structural failures and the shared bibliography budget. Run both in
an Academia development environment with pytest installed.

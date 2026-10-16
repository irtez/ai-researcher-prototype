import assert from "node:assert/strict";
import test from "node:test";
import { QueryClient } from "@tanstack/react-query";

import {
  discardSyntheticAttachAfter404,
  sessionRefreshQueryKeys,
  isNewRunBlocked
} from "../src/sessionLifecycle.ts";

test("missing attach endpoint clears only the synthetic state from that attach generation", () => {
  const synthetic = { sessionId: "session-1", status: "running" } as const;
  const idle = { sessionId: null, status: "idle" } as const;

  assert.equal(discardSyntheticAttachAfter404(synthetic, "session-1", 4, 4, idle), idle);

  const newerOwnedStream = { sessionId: "session-1", status: "running", owner: "post" } as const;
  assert.equal(
    discardSyntheticAttachAfter404(newerOwnedStream, "session-1", 4, 5, idle),
    newerOwnedStream
  );
});

test("session refresh refetches a same-path preview but preserves another session cache", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } }
  });
  let sessionOneSource = "old text";
  let sessionOneReads = 0;
  let sessionTwoReads = 0;
  const sessionOneQuery = {
    queryKey: ["preview", "session-1", "paper.md"],
    queryFn: async () => {
      sessionOneReads += 1;
      return sessionOneSource;
    }
  };
  const sessionTwoQuery = {
    queryKey: ["preview", "session-2", "paper.md"],
    queryFn: async () => {
      sessionTwoReads += 1;
      return "other session text";
    }
  };

  assert.equal(await client.fetchQuery(sessionOneQuery), "old text");
  assert.equal(await client.fetchQuery(sessionTwoQuery), "other session text");
  sessionOneSource = "revised text";

  for (const queryKey of sessionRefreshQueryKeys("session-1")) {
    await client.invalidateQueries({ queryKey });
  }

  assert.equal(await client.fetchQuery(sessionOneQuery), "revised text");
  assert.equal(await client.fetchQuery(sessionTwoQuery), "other session text");
  assert.equal(sessionOneReads, 2);
  assert.equal(sessionTwoReads, 1);
  client.clear();
});

test("server completion unlocks stale live caches, but an active runner keeps the launch blocked", () => {
  const cached = { sessionId: "session-1", status: "running" };
  for (const status of ["complete", "cancelled", "error"]) {
    assert.equal(isNewRunBlocked(cached, { session_id: "session-1", status, active: false }), false);
    assert.equal(isNewRunBlocked(cached, { session_id: "session-1", status }), false);
  }
  for (const status of ["running", "queued", "paused", "waiting_for_human", "waiting_rate_limit"]) {
    assert.equal(isNewRunBlocked({ ...cached, status: "complete" }, { session_id: "session-1", status, active: true }), true);
    assert.equal(isNewRunBlocked({ ...cached, status: "complete" }, { session_id: "session-1", status }), true);
  }
});

test("an in-flight submission stays blocked despite metadata from the previous completed iteration", () => {
  assert.equal(isNewRunBlocked(
    { sessionId: "session-1", status: "running" },
    { session_id: "session-1", status: "complete", active: false },
    true
  ), true);
});

test("unrelated or missing metadata cannot release a cached active run", () => {
  const cached = { sessionId: "session-1", status: "running" };
  assert.equal(isNewRunBlocked(cached, { session_id: "session-2", status: "complete", active: false }), true);
  assert.equal(isNewRunBlocked(cached), true);
  assert.equal(isNewRunBlocked(cached, { session_id: "session-1", status: "unknown" }), true);
  assert.equal(isNewRunBlocked({ sessionId: null, status: "idle" }), false);
});

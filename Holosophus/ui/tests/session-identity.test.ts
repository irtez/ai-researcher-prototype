import assert from "node:assert/strict";
import test from "node:test";
import { newResearchSessionId } from "../src/sessionIdentity.ts";

test("new session IDs are UUIDs independent of prompt and model", () => {
  const ids = new Set(Array.from({ length: 100 }, () => newResearchSessionId()));
  assert.equal(ids.size, 100);
  for (const id of ids) assert.match(id, /^research_[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$/);
});

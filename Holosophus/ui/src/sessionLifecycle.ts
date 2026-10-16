export function discardSyntheticAttachAfter404<T extends { sessionId: string | null }>(
  state: T,
  attachedSessionId: string,
  attachGeneration: number,
  currentGeneration: number,
  emptyState: T
): T {
  return state.sessionId === attachedSessionId && attachGeneration === currentGeneration
    ? emptyState
    : state;
}

export function sessionRefreshQueryKeys(sessionId: string): ReadonlyArray<readonly unknown[]> {
  return [
    ["sessions"],
    ["session", sessionId],
    ["iterations", sessionId],
    ["iteration", sessionId],
    ["summary", sessionId],
    ["artifacts", sessionId],
    ["preview", sessionId]
  ];
}

const ACTIVE_RUN_STATUSES = new Set(["running", "queued", "paused", "waiting_for_human", "waiting_rate_limit"]);
const TERMINAL_RUN_STATUSES = new Set(["complete", "cancelled", "error", "idle"]);

/** A cached stream snapshot is only a fallback, never proof that a run is still active. */
export function isNewRunBlocked(
  cached: { sessionId: string | null; status: string },
  server?: { session_id: string; status?: string; active?: boolean },
  submitting = false
): boolean {
  // The last response can describe the previous iteration while a POST/upload is in flight.
  if (submitting) return true;
  if (server && server.session_id === cached.sessionId) {
    if (typeof server.active === "boolean") return server.active;
    if (server.status && ACTIVE_RUN_STATUSES.has(server.status)) return true;
    if (server.status && TERMINAL_RUN_STATUSES.has(server.status)) return false;
  }
  return ACTIVE_RUN_STATUSES.has(cached.status);
}

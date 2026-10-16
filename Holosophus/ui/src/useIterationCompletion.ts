import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getAuthToken } from "./api/auth";
import { getClientId } from "./api/clientId";
import { watchIterationCompletion } from "./iterationCompletion";
import { sessionRefreshQueryKeys } from "./sessionLifecycle";

export function useIterationCompletion(sessionId?: string, iterationId?: string | null) {
  const client = useQueryClient();
  const attempted = useRef(new Set<string>());
  const owner = getClientId();
  const token = getAuthToken();
  useEffect(() => {
    if (!sessionId || !iterationId) return;
    const key = JSON.stringify([owner, sessionId, iterationId]);
    if (attempted.current.has(key)) return;
    let stop: (() => void) | undefined;
    // Defer actual opening through React StrictMode's setup/cleanup rehearsal.
    // Mark only a real attempt, and never reconnect on a render or a poll.
    const timer = window.setTimeout(() => {
      attempted.current.add(key);
      stop = watchIterationCompletion(sessionId, iterationId, { client_id: owner, token }, () => {
        for (const queryKey of sessionRefreshQueryKeys(sessionId)) {
          void client.invalidateQueries({ queryKey });
        }
      });
    }, 0);
    return () => { window.clearTimeout(timer); stop?.(); };
  }, [client, owner, token, sessionId, iterationId]);
}

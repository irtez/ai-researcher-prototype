import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { uiApi } from "./api/client";
import { iterationLiveState } from "./liveState";
import { iterationSelectionKey, readIterationPages, selectedIterationId } from "./iterationHistory";
import type { IterationDetail } from "./iterationHistory";
import { useIterationCompletion } from "./useIterationCompletion";

export function useIterationHistory(sessionId?: string, enabled = true) {
  const client = useQueryClient();
  const [selectionBySession, setSelectionBySession] = useState<Record<string, string>>({});
  let selection = sessionId ? selectionBySession[sessionId] : undefined;
  if (sessionId && selection === undefined) {
    try { selection = window.localStorage.getItem(iterationSelectionKey(sessionId)) ?? undefined; } catch { /* Optional storage. */ }
  }
  const select = (id: string) => {
    if (!sessionId) return;
    setSelectionBySession((previous) => ({ ...previous, [sessionId]: id }));
    try { window.localStorage.setItem(iterationSelectionKey(sessionId), id); } catch { /* Optional storage. */ }
  };
  const index = useQuery({
    queryKey: ["iterations", sessionId],
    queryFn: () => uiApi.iterations(sessionId!),
    enabled: enabled && Boolean(sessionId),
    refetchInterval: 3000,
    retry: false
  });
  const id = selectedIterationId(index.data, selection);
  // Follow execution, even when the user is reading an older iteration.
  useIterationCompletion(enabled ? sessionId : undefined, index.data?.active_iteration_id);
  const detail = useQuery({
    queryKey: ["iteration", sessionId, id],
    queryFn: () => readIterationPages(
      (after) => uiApi.iteration(sessionId!, id!, after),
      client.getQueryData<IterationDetail>(["iteration", sessionId, id])
    ),
    enabled: enabled && Boolean(sessionId && id),
    refetchInterval: index.data?.active_iteration_id === id ? 2000 : 5000,
    retry: false
  });
  const latestId = index.data?.latest_result_iteration_id;
  const latest = useQuery({
    queryKey: ["iteration-result", sessionId, latestId],
    queryFn: () => uiApi.iteration(sessionId!, latestId!),
    enabled: enabled && Boolean(sessionId && latestId && latestId !== id),
    staleTime: Infinity,
    retry: false
  });
  const state = useMemo(() => detail.data ? iterationLiveState(detail.data) : undefined, [detail.data]);
  return {
    index: index.data,
    selectedId: id,
    selection: selection ?? "latest",
    select,
    detail: detail.data,
    state,
    latest: latestId === id ? detail.data : latest.data,
    error: enabled ? index.error || detail.error || latest.error : null,
    loading: index.isPending || (Boolean(id) && detail.isPending)
  };
}

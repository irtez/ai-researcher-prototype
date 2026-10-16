import type {
  ArtifactTab,
  ArtifactsResponse,
  PreviewPayload,
  SessionDetail,
  SessionsResponse,
  TabSummaries,
  UiConfig
} from "../types";
import type { UploadInputsResponse } from "../runRequest";
import type { LiveRunState } from "../live";
import type { IterationDetail, IterationIndex } from "../iterationHistory";
import { getClientId } from "./clientId";
import { authHeaders } from "./auth";

class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string): Promise<T> {
  return requestJson<T>(path, {
    headers: { Accept: "application/json" }
  });
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  // Single chokepoint for every /ui/* call (GET via request(), PUT via
  // saveLiveState): attach the browser-client identity so ui-api can scope the
  // response to this owner.
  const headers = {
    ...(init?.headers as Record<string, string> | undefined),
    "X-Holo-Client": getClientId(),
    // Harmless on /ui/* (viewing is owner-filtered on X-Holo-Client, not the
    // token); carried so a future hard-verified read path can use it.
    ...authHeaders()
  };
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = (await response.json()) as { detail?: string };
      message = payload.detail || message;
    } catch {
      // Keep the HTTP status text.
    }
    throw new ApiError(response.status, message);
  }
  return (await response.json()) as T;
}

function params(searchParams: Record<string, string | boolean | number | undefined | null>): string {
  const query = new URLSearchParams();
  Object.entries(searchParams).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      query.set(key, String(value));
    }
  });
  const serialized = query.toString();
  return serialized ? `?${serialized}` : "";
}

export const uiApi = {
  config() {
    return request<UiConfig>("/ui/config");
  },
  sessions(search?: string) {
    return request<SessionsResponse>(`/ui/sessions${params({ q: search })}`);
  },
  session(sessionId: string) {
    return request<SessionDetail>(`/ui/sessions/${encodeURIComponent(sessionId)}`);
  },
  iterations(sessionId: string) {
    return request<IterationIndex>(`/ui/sessions/${encodeURIComponent(sessionId)}/iterations`);
  },
  iteration(sessionId: string, iterationId: string, after = 0) {
    return request<IterationDetail>(
      `/ui/sessions/${encodeURIComponent(sessionId)}/iterations/${encodeURIComponent(iterationId)}${params({ after, limit: 1000 })}`
    );
  },
  uploadInputs(sessionId: string, files: File[]) {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    return requestJson<UploadInputsResponse>(
      `/ui/sessions/${encodeURIComponent(sessionId)}/inputs`,
      {
        method: "POST",
        headers: { Accept: "application/json" },
        body
      }
    );
  },
  uploadFeedbackInputs(sessionId: string, files: File[], uploadId: string) {
    const body = new FormData();
    files.forEach(file => body.append("files", file));
    return requestJson<UploadInputsResponse>(
      `/ui/sessions/${encodeURIComponent(sessionId)}/inputs${params({ feedback_upload_id: uploadId })}`,
      { method: "POST", headers: { Accept: "application/json" }, body }
    );
  },
  // Deletes the run's workdir (artifacts, live state, logs). Langfuse traces
  // live in their own volume and are NOT touched.
  deleteSession(sessionId: string) {
    return requestJson<{ status: string; session_id: string }>(
      `/ui/sessions/${encodeURIComponent(sessionId)}`,
      { method: "DELETE", headers: { Accept: "application/json" } }
    );
  },
  summary(sessionId: string) {
    return request<TabSummaries>(`/ui/sessions/${encodeURIComponent(sessionId)}/summary`);
  },
  artifacts(sessionId: string, tab?: ArtifactTab) {
    return request<ArtifactsResponse>(
      `/ui/sessions/${encodeURIComponent(sessionId)}/artifacts${params({ tab })}`
    );
  },
  preview(sessionId: string, path: string) {
    return request<PreviewPayload>(
      `/ui/artifacts/${encodeURIComponent(sessionId)}/preview${params({ path })}`
    );
  },
  liveState(sessionId: string) {
    return request<{ session_id: string; state: LiveRunState }>(
      `/ui/sessions/${encodeURIComponent(sessionId)}/live-state`
    );
  },
  saveLiveState(sessionId: string, state: LiveRunState) {
    return requestJson<{ status: string; session_id: string; path: string; size_bytes: number }>(
      `/ui/sessions/${encodeURIComponent(sessionId)}/live-state`,
      {
        method: "PUT",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json"
        },
        body: JSON.stringify(state)
      }
    );
  }
};

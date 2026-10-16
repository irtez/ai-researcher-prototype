export type ArtifactTab = "brief" | "proposals" | "experiments" | "paper" | "review" | "run";

export type SessionMode = "research" | "debug" | "smoke" | "benchmark" | "workspace";

export interface SessionListItem {
  title_pending?: boolean;
  session_id: string;
  title: string;
  mode: SessionMode;
  model_slug: string | null;
  research_profile?: "general" | "chemistry";
  created_at: string;
  updated_at: string;
  status: string;
  artifact_counts: Record<string, number>;
  has_started_prompt: boolean;
}

export interface SessionsResponse {
  sessions: SessionListItem[];
}

export interface UiConfig {
  model_configs?: Partial<Record<"claude_code" | "codearkt", import("./launchConfig").ModelConfiguration[]>>;
  model_slug?: string | null;
  model_choices?: string[];
  claude_code_default_model?: string | null;
  claude_code_model_choices?: string[];
  agents: Array<{
    name: string;
    description: string;
    max_iterations: number;
    planning_interval: number;
    tools: string[];
  }>;
  pipelines: Record<string, string[]>;
  artifact_types: string[];
  // App-level team login: true when starting a run requires a login (the UI
  // gates "Старт" behind a login modal). Absent/false on open dev deploys.
  auth_required?: boolean;
}

export interface Artifact {
  path: string;
  name: string;
  type: string;
  tab: ArtifactTab;
  size_bytes: number;
  modified_at: string;
  version?: string;
  preview_kind: string;
  pinned: boolean;
  ignored?: boolean;
}

export interface ArtifactsResponse {
  session_id: string;
  artifacts: Artifact[];
}

export interface Stage {
  id: string;
  label: string;
  agent: string;
  status: "complete" | "running" | "limited" | "pending" | "cancelled" | "error" | "unknown" | "not_run";
  artifact_count: number;
}

export interface SessionDetail {
  model_label?: string | null;
  effort?: string | null;
  session_id: string;
  status: string;
  task_type: string;
  // Which orchestrator created the session (ui-api reads the
  // .holosophus_ui/orchestrator marker; "codearkt" when the marker is absent).
  orchestrator?: string;
  model_slug?: string | null;
  research_profile?: "general" | "chemistry";
  active_agent: string | null;
  started_prompt_path: string | null;
  summary: {
    title: string;
    best_metric: { label: string; value: number; source_path: string } | null;
    latest_pdf: string | null;
    review_score: number | string | null;
  };
  stages: Stage[];
  pinned_artifacts: Artifact[];
}

export interface ExperimentSummary {
  source_path: string;
  iteration_id: string;
  steps: Array<{ phase: string; summary: string; artifacts: string[] }>;
  error?: string;
}

export interface PaperDocumentState {
  registered: true;
  status: "checking" | "ready" | "blocked" | "abandoned" | "error";
  tex_path: string | null;
  pdf_path: string | null;
  pdf_available: boolean;
  error: string | null;
  changed_since_check?: boolean | null;
}

export interface TabSummaries {
  brief?: {
    started_prompt?: { path: string; content: string; truncated: boolean; size_bytes: number } | null;
    paper_count?: number;
    source_paths?: string[];
    markdown_sources?: Array<{ path: string; content: string; truncated: boolean; size_bytes: number }>;
  };
  proposals?: {
    proposal_count?: number;
    source_paths?: string[];
    proposals?: Array<{
      source_path: string;
      proposal_id?: string | number;
      title?: string;
      summary?: string | null;
      scores?: Record<string, string | number | boolean | null>;
      raw?: Record<string, unknown>;
      error?: string;
    }>;
  };
  experiments?: {
    metrics_tables?: Array<{ source_path: string; rows: Array<Record<string, unknown>>; error?: string }>;
    csv_tables?: Array<{ source_path: string }>;
    log_phases?: Array<Record<string, unknown>>;
    summaries?: ExperimentSummary[];
  };
  paper?: {
    document?: PaperDocumentState | null;
    pdfs?: string[];
    latex_sources?: string[];
    figures?: string[];
    paperbanana_figures?: string[];
    references?: string[];
  };
  review?: {
    review_artifacts?: string[];
    markdown_review?: { path: string; content: string; truncated: boolean; size_bytes: number } | null;
    overall_score?: number | string | null;
    recommendation?: string | null;
    confidence?: number | string | null;
    strengths?: string[];
    weaknesses?: string[];
    required_fixes?: string[];
    questions?: string[];
    limitations?: string[];
    format_issues?: string[];
    review_json?: unknown;
  };
  run?: {
    logs?: string[];
    trace_exports?: string[];
    workspace_file_count?: number;
  };
}

export type PreviewPayload =
  | {
      kind: "image" | "pdf";
      path: string;
      url: string;
      size_bytes: number;
    }
  | {
      kind: "json";
      path: string;
      content: unknown;
      size_bytes: number;
    }
  | {
      kind: "markdown" | "jsonl" | "csv" | "text";
      path: string;
      content: string;
      truncated: boolean;
      size_bytes: number;
    }
  | {
      kind: "binary";
      path: string;
      size_bytes: number;
    };

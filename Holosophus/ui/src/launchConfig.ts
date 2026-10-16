import type { ComputeMode, Orchestrator } from "./live";

export type ResearchProfile = "general" | "chemistry";

export interface LaunchSelection {
  researchProfile: ResearchProfile;
  orchestrator: Orchestrator;
  computeMode: ComputeMode;
  modelName: string;
  effort?: string;
}

export interface ModelConfiguration {
  id: string;
  label: string;
  efforts: string[];
  default_effort: string | null;
}

export interface LaunchModelConfig {
  codearktDefault?: string | null;
  codearktChoices?: string[];
  claudeCodeDefault?: string | null;
  claudeCodeChoices?: string[];
  models?: Partial<Record<Orchestrator, ModelConfiguration[]>>;
}

export function selectedModelConfig(selection: LaunchSelection, config: LaunchModelConfig): ModelConfiguration | undefined {
  return config.models?.[selection.orchestrator]?.find(model => model.id === effectiveModel(selection, config));
}

export function effectiveEffort(selection: LaunchSelection, config: LaunchModelConfig): string | undefined {
  const model = selectedModelConfig(selection, config);
  if (!model?.efforts.length) return undefined;
  return selection.effort && model.efforts.includes(selection.effort)
    ? selection.effort : model.default_effort ?? undefined;
}

export function modelLabel(id: string | undefined, orchestrator: Orchestrator, config: LaunchModelConfig): string | undefined {
  return config.models?.[orchestrator]?.find(model => model.id === id)?.label ?? id;
}

const FALLBACK_CLAUDE_CODE_MODELS = ["opus", "sonnet", "haiku"];

export const DEFAULT_LAUNCH_SELECTION: LaunchSelection = {
  researchProfile: "general",
  orchestrator: "claude_code",
  computeMode: "Auto",
  modelName: ""
};

export function modelChoicesFor(orchestrator: Orchestrator, config: LaunchModelConfig): string[] {
  if (orchestrator === "claude_code") {
    return unique(config.claudeCodeChoices?.length ? config.claudeCodeChoices : FALLBACK_CLAUDE_CODE_MODELS);
  }
  return unique(config.codearktChoices ?? []);
}

export function defaultModelFor(orchestrator: Orchestrator, config: LaunchModelConfig): string | undefined {
  const configured = orchestrator === "claude_code" ? config.claudeCodeDefault : config.codearktDefault;
  if (configured) {
    return configured;
  }
  return orchestrator === "claude_code" ? "opus" : undefined;
}

export function effectiveModel(selection: LaunchSelection, config: LaunchModelConfig): string | undefined {
  return selection.modelName || defaultModelFor(selection.orchestrator, config);
}

export function selectOrchestrator(selection: LaunchSelection, orchestrator: Orchestrator): LaunchSelection {
  if (selection.researchProfile === "chemistry" && orchestrator !== "claude_code") {
    return selection;
  }
  if (selection.orchestrator === orchestrator) {
    return selection;
  }
  return { ...selection, orchestrator, modelName: "" };
}

export function selectResearchProfile(
  selection: LaunchSelection,
  researchProfile: ResearchProfile
): LaunchSelection {
  if (researchProfile === "general") {
    return { ...selection, researchProfile };
  }
  return {
    ...selection,
    researchProfile,
    orchestrator: "claude_code",
    computeMode: "Local",
    modelName: selection.orchestrator === "claude_code" ? selection.modelName : "",
    ...(selection.orchestrator !== "claude_code" && selection.effort !== undefined ? { effort: undefined } : {})
  };
}

function unique(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

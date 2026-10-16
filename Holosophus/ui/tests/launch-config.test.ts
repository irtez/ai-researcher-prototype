import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_LAUNCH_SELECTION,
  effectiveModel,
  effectiveEffort,
  modelLabel,
  modelChoicesFor,
  selectOrchestrator,
  selectResearchProfile
} from "../src/launchConfig.ts";

const config = {
  codearktDefault: "deepseek/deepseek-v3.2",
  codearktChoices: ["deepseek/deepseek-v3.2", "openai/gpt-5.2"],
  claudeCodeDefault: "opus",
  claudeCodeChoices: ["opus", "sonnet", "haiku"]
};

test("Claude Code uses its own default and model list", () => {
  assert.equal(effectiveModel(DEFAULT_LAUNCH_SELECTION, config), "opus");
  assert.deepEqual(modelChoicesFor("claude_code", config), ["opus", "sonnet", "haiku"]);
});

test("effort is resolved against the selected configuration", () => {
  const catalog = { ...config, models: { claude_code: [
    { id: "opus", label: "Модель A", efforts: ["low", "high"], default_effort: "high" },
    { id: "haiku", label: "Модель B", efforts: [], default_effort: null }
  ] } };
  assert.equal(effectiveEffort(DEFAULT_LAUNCH_SELECTION, catalog), "high");
  assert.equal(effectiveEffort({ ...DEFAULT_LAUNCH_SELECTION, effort: "low" }, catalog), "low");
  assert.equal(effectiveEffort({ ...DEFAULT_LAUNCH_SELECTION, effort: "max" }, catalog), "high");
  assert.equal(effectiveEffort({ ...DEFAULT_LAUNCH_SELECTION, modelName: "haiku", effort: "high" }, catalog), undefined);
  assert.equal(modelLabel("opus", "claude_code", catalog), "Модель A");
  assert.equal(selectResearchProfile({ ...DEFAULT_LAUNCH_SELECTION, effort: "low" }, "chemistry").effort, "low");
});

test("switching orchestrators clears the previous orchestrator model", () => {
  const withClaudeModel = { ...DEFAULT_LAUNCH_SELECTION, modelName: "sonnet" };
  const codearkt = selectOrchestrator(withClaudeModel, "codearkt");

  assert.equal(codearkt.orchestrator, "codearkt");
  assert.equal(codearkt.modelName, "");
  assert.equal(effectiveModel(codearkt, config), "deepseek/deepseek-v3.2");
});

test("chemistry visibly forces Claude Code and Local", () => {
  const codearkt = {
    ...DEFAULT_LAUNCH_SELECTION,
    orchestrator: "codearkt" as const,
    computeMode: "GPU" as const,
    modelName: "openai/gpt-5.2"
  };
  const chemistry = selectResearchProfile(codearkt, "chemistry");

  assert.deepEqual(chemistry, {
    researchProfile: "chemistry",
    orchestrator: "claude_code",
    computeMode: "Local",
    modelName: ""
  });
  assert.deepEqual(selectOrchestrator(chemistry, "codearkt"), chemistry);
});

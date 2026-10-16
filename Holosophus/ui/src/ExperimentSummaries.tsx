import type { ReactNode } from "react";
import type { Artifact, ExperimentSummary } from "./types";
import { t } from "./i18n";

export function ExperimentSummaries({ summaries, artifacts, onOpenArtifact, renderSummary }: {
  summaries: ExperimentSummary[];
  artifacts: Artifact[];
  onOpenArtifact: (artifact: Artifact) => void;
  renderSummary: (content: string, sourcePath: string) => ReactNode;
}) {
  const iterations = [...new Set(summaries.map((item) => item.iteration_id))];
  const fileLink = (path: string) => {
    const artifact = artifacts.find((item) => item.path === path);
    return artifact
      ? <button className="experiment-file-link" onClick={() => onOpenArtifact(artifact)}>{path}</button>
      : <span className="experiment-file-path">{path}</span>;
  };
  if (!summaries.length) return null;
  return <section className="experiment-summaries">
    <h2>{t("experiments.summary")}</h2>
    {iterations.map((iteration) => <section className="experiment-summary-iteration" key={iteration} data-iteration={iteration}>
      <h3>{Number(iteration) === 1 ? t("history.initial") : t("history.feedback", { n: Number(iteration) - 1 })}</h3>
      {summaries.filter((item) => item.iteration_id === iteration).map((item) => <div className="experiment-summary-document" key={item.source_path}>
        <div className="experiment-summary-source">{fileLink(item.source_path)}</div>
        {item.error && <p className="experiment-summary-error" role="alert">{t("experiments.summaryError")}</p>}
        {item.steps.map((step, index) => <article className="experiment-summary-step" key={index}>
          <h4>{step.phase}</h4>
          <div className="experiment-summary-text">{renderSummary(step.summary, item.source_path)}</div>
          {step.artifacts.length > 0 && <ul className="experiment-summary-files">
            {step.artifacts.map((path, i) => <li key={i}>{fileLink(path)}</li>)}
          </ul>}
        </article>)}
      </div>)}
    </section>)}
  </section>;
}

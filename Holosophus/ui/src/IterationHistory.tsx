import type { ReactNode } from "react";
import { t, tv, getLang } from "./i18n";
import { feedbackNumber, iterationDocuments, documentLink } from "./iterationHistory";
import type { Iteration, IterationDetail, IterationIndex } from "./iterationHistory";

export function iterationLabel(index: IterationIndex, item: Iteration): string {
  return item.kind === "legacy" ? t("history.legacy") : item.kind === "initial"
    ? t("history.initial") : t("history.feedback", { n: feedbackNumber(index, item.id) });
}

export function IterationSelector({ index, selectedId, selection, onSelect }: {
  index: IterationIndex;
  selectedId?: string;
  selection: string;
  onSelect: (id: string) => void;
}) {
  if (!index.iterations.length) return null;
  return <div className="iteration-selector">
    <label>{t("history.select")}
      <select aria-label={t("history.select")} value={selectedId ?? ""} onChange={(event) => onSelect(event.target.value)}>
        {index.iterations.map((item) => <option value={item.id} key={item.id}>
          {iterationLabel(index, item)} — {tv("runStatus", item.status)}
        </option>)}
      </select>
    </label>
    {selection !== "latest" && <button onClick={() => onSelect("latest")}>{t("history.follow")}</button>}
    {index.latest_result_iteration_id && index.latest_result_iteration_id !== selectedId &&
      <button onClick={() => onSelect(index.latest_result_iteration_id!)}>{t("history.latest")}</button>}
  </div>;
}

export function IterationHistory({ index, iteration, latest, renderDocument }: {
  index: IterationIndex;
  iteration: IterationDetail;
  latest?: IterationDetail;
  renderDocument: (content: string, sourcePath?: string) => ReactNode;
}) {
  const document = iteration.result_content ? iteration : latest;
  const hasSelectedResult = document?.id === iteration.id;
  const approvals = iteration.approval_state;
  const documents = iterationDocuments(iteration);
  const attachments = iteration.settings?.input_attachments ??
    (iteration.settings?.input_files ?? []).map(path => ({ path, name: path.split("/").pop() ?? path }));
  return <section className="iteration-history" aria-label={t("history.details")}>
    <header className="iteration-heading">
      <h1>{iterationLabel(index, iteration)}</h1>
      <span className={`iteration-status status-${iteration.status}`}>{tv("runStatus", iteration.status)}</span>
    </header>
    {iteration.error && <p className="operator-error" role="alert">{iteration.error}</p>}
    {iteration.kind === "legacy" && <p className="history-note">{t("history.legacyNote")}</p>}
    {!hasSelectedResult && <p className="history-note">{t("history.noResult")}</p>}
    {document?.result_content && document.result && <section className="iteration-result" aria-label={t("history.result")}>
      <h2>{hasSelectedResult ? t("history.result") : `${t("history.latest")} — ${iterationLabel(index, document)}`}</h2>
      {renderDocument(document.result_content, document.result.source_path)}
    </section>}
    <details className="history-context" open={!document?.result_content}>
      <summary>{t("history.requestAndPlans")}</summary>
      <section className="history-message history-request" aria-label={t(iteration.kind === "feedback" ? "history.remark" : "history.request")}>
        <h3>{t(iteration.kind === "feedback" ? "history.remark" : "history.request")}</h3>
        {iteration.user_message ? renderDocument(iteration.user_message) : <p>{t("history.notSaved")}</p>}
        {attachments.length > 0 && <div className="iteration-inputs">
          <h4>{t("launch.attachedFiles")}</h4>
          <ul>{attachments.map(file => <li key={file.path}>
            <a href={documentLink(iteration.session_id, "started_prompt.md", file.path.split("/").map(encodeURIComponent).join("/"))}
              title={file.path} target="_blank" rel="noreferrer">{file.name}</a>
          </li>)}</ul>
        </div>}
      </section>
      <div className="iteration-documents">
        {documents.filter(document => !document.additional).map((document) => {
          const origin = index.iterations.find(item => item.id === document.reusedFrom);
          return <details className="history-document" data-document-stage={document.stage} key={document.document_path}>
            <summary>
              <strong>{t(document.stage === "plan" ? "history.currentPlan" : "history.sources")}</strong>
              {document.approval && <span className="history-document-status"> — {tv("decision", document.approval.status)}</span>}
              {document.reusedFrom && <span className="history-document-origin">{t("history.sourcesFrom", {
                iteration: origin ? iterationLabel(index, origin) : document.reusedFrom
              })}</span>}
              <small className="history-document-path">{document.document_path}</small>
            </summary>
            {renderDocument(document.document_text, document.document_path)}
          </details>;
        })}
      </div>
      {documents.some(document => document.additional) && <details className="history-other-plans">
        <summary>{t("history.planDocuments")}</summary>
        {documents.filter(document => document.additional).map(document => <details className="history-document" key={document.document_path}>
          <summary>{document.document_path}</summary>
          {renderDocument(document.document_text, document.document_path)}
        </details>)}
      </details>}
      {Boolean(approvals?.decisions?.length) && <details className="history-approval-log">
        <summary>{t("history.approvalLog")} ({approvals!.decisions.length})</summary>
        {approvals!.decisions.map((decision, n) => <details className="history-document" key={decision.qid ?? n}>
        <summary>{tv("approvalStage", decision.stage ?? "question")} — {tv("decision", decision.status)}
          {decision.resolved_at && <time dateTime={new Date(decision.resolved_at * 1000).toISOString()}> {new Date(decision.resolved_at * 1000).toLocaleString(getLang())}</time>}
        </summary>
        {decision.text && <p className="history-remark">{decision.text}</p>}
        {decision.document_text && renderDocument(decision.document_text, decision.document_path)}
        </details>)}
      </details>}
      {iteration.answer && <section className="iteration-answer history-message history-answer" aria-label={t("history.answer")}>
        <h3>{t("history.answer")}</h3>
        {renderDocument(iteration.answer)}
      </section>}
    </details>
    <p className="history-note">{t("history.artifactsNote")}</p>
  </section>;
}

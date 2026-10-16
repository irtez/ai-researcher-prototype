import { useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, ArrowUpRight, Bot, Check, ChevronRight, Circle, LoaderCircle, Terminal, X } from "lucide-react";
import { getLang, t } from "../i18n";
import { buildActivity, type ActivityEvent, type ActivityNode, type ActivityStatus,
  type AgentActivity, type ToolActivity } from "./activity";
import "./ActivityFeed.css";

type RenderContent = (text: string) => ReactNode;
const time = (value: string) => !value ? "" : /^\d\d:\d\d/.test(value) ? value
  : new Date(value).toLocaleTimeString(getLang(), { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const statusLabel = (value: ActivityStatus) => value === "unknown" ? t("activity.unknownStatus") : t(`stageStatus.${value}`);
const agentLabel = (agent: AgentActivity) => agent.label || t("activity.agent");

function StatusIcon({ status }: { status: ActivityStatus }) {
  const Icon = status === "running" ? LoaderCircle : status === "complete" ? Check : status === "error" ? X : Circle;
  return <Icon size={14} aria-hidden="true" />;
}

function Status({ status, background = false }: { status: ActivityStatus; background?: boolean }) {
  return <span className={`activity-status activity-status-${status}`}>
    <StatusIcon status={status} />
    {status === "running" && background ? t("activity.background") : statusLabel(status)}
  </span>;
}

export function ActivityFeed({ events, renderContent, stickyOffset = 0, managerContext, terminal, legacy = false, visible = true }: {
  events: ActivityEvent[]; renderContent: RenderContent; stickyOffset?: number;
  managerContext?: ReactNode; terminal?: string; legacy?: boolean; visible?: boolean;
}) {
  const { roots, agents } = useMemo(() => buildActivity(events, terminal), [events, terminal]);
  const [selected, setSelected] = useState("manager");
  const root = useRef<HTMLDivElement>(null);
  const tabs = useRef<HTMLDivElement>(null);
  const positions = useRef(new Map<string, number>());
  const mounted = useRef(false);
  const prefix = useId();
  const scrollContainer = () => {
    const panel = root.current?.closest<HTMLElement>(".workbench");
    return panel && panel.scrollHeight > panel.clientHeight + 1 ? panel : null;
  };
  const openView = (id: string) => {
    if (selected === id) return;
    positions.current.set(selected, scrollContainer()?.scrollTop ?? window.scrollY);
    setSelected(id);
  };
  useLayoutEffect(() => {
    if (!visible) return;
    if (!mounted.current) { mounted.current = true; return; }
    const tab = tabs.current?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (tabs.current && tab) {
      const container = tabs.current.getBoundingClientRect();
      const item = tab.getBoundingClientRect();
      const delta = item.left < container.left ? item.left - container.left
        : item.right > container.right ? item.right - container.right : 0;
      tabs.current.scrollBy({ left: delta, behavior: "instant" });
    }
    const container = scrollContainer();
    const top = (root.current?.getBoundingClientRect().top ?? 0)
      - (container?.getBoundingClientRect().top ?? 0) + (container?.scrollTop ?? window.scrollY);
    (container ?? window).scrollTo({ top: positions.current.get(selected) ?? Math.max(0, top - stickyOffset), behavior: "instant" });
  }, [selected, visible]);
  useLayoutEffect(() => {
    if (!visible) return;
    const target = scrollContainer() ?? window;
    const remember = () => positions.current.set(selected, scrollContainer()?.scrollTop ?? window.scrollY);
    target.addEventListener("scroll", remember, { passive: true });
    return () => target.removeEventListener("scroll", remember);
  }, [selected, visible]);

  const views = [{ id: "manager", label: t("activity.manager"), task: "", status: undefined }, ...agents.map(agent => ({ ...agent, label: agentLabel(agent) }))];
  return <div className="activity-workspace" ref={root} aria-label={t("activity.feed")}>
    <div ref={tabs} className="activity-tabs" role="tablist" aria-label={t("activity.views")} style={{ top: stickyOffset }}>
      {views.map((view, index) => <button key={view.id} type="button" role="tab"
        id={`${prefix}-tab-${view.id}`} aria-controls={`${prefix}-panel-${view.id}`}
        data-view-key={view.id} aria-selected={selected === view.id} tabIndex={selected === view.id ? 0 : -1}
        title={view.task || view.label} onClick={() => openView(view.id)}
        onKeyDown={event => {
          let next = index;
          if (event.key === "ArrowRight") next = (index + 1) % views.length;
          else if (event.key === "ArrowLeft") next = (index + views.length - 1) % views.length;
          else if (event.key === "Home") next = 0;
          else if (event.key === "End") next = views.length - 1;
          else return;
          event.preventDefault();
          openView(views[next].id);
          document.getElementById(`${prefix}-tab-${views[next].id}`)?.focus({ preventScroll: true });
        }}>
        <span className="activity-tab-label"><strong>{view.label}</strong>{view.task && <span>{view.task}</span>}</span>
        {view.status && <span className={`activity-status activity-status-${view.status}`} title={statusLabel(view.status)}>
          <StatusIcon status={view.status} />
        </span>}
      </button>)}
    </div>
    <div role="tabpanel" id={`${prefix}-panel-manager`} aria-labelledby={`${prefix}-tab-manager`}
      hidden={selected !== "manager"} data-view="manager" className="activity-panel">
      {managerContext}
      {legacy && <p className="activity-history-note">{t("activity.legacy")}</p>}
      <Nodes nodes={roots} renderContent={renderContent} label={t("activity.manager")} onOpen={openView} />
      {!roots.length && <p className="activity-empty" role="status">{t(terminal === "running" ? "activity.waitingForManager" : "activity.noEvents")}</p>}
    </div>
    {agents.map(agent => <div role="tabpanel" key={agent.id} id={`${prefix}-panel-${agent.id}`}
      aria-labelledby={`${prefix}-tab-${agent.id}`} hidden={selected !== agent.id}
      data-view={agent.id} className="activity-panel">
      <header className="activity-view-heading">
        <button className="activity-back" onClick={() => openView("manager")}><ArrowLeft size={14} />{t("activity.back")}</button>
        <div><h2>{agentLabel(agent)}</h2><Status status={agent.status} background={agent.background} /></div>
        <p>{agent.task}</p><time>{time(agent.at)}{agent.finishedAt && `–${time(agent.finishedAt)}`}</time>
        {(agent.legacy || agent.incomplete) && <p className="activity-history-note">{t(agent.legacy ? "activity.legacy" : "activity.missingStart")}</p>}
        {agent.prompt && <details className="activity-task"><summary>{t("activity.task")}</summary>{renderContent(agent.prompt)}</details>}
      </header>
      <Nodes nodes={agent.children} renderContent={renderContent} label={agentLabel(agent)} onOpen={openView} />
      {agent.summary && !agent.children.some(node => node.kind === "message" && node.text.trim() === agent.summary?.trim()) &&
        <article className={`activity-message activity-agent-answer activity-agent-answer-${agent.status}`}>
          <header><strong>{t("activity.agentAnswer")}</strong><time>{time(agent.finishedAt ?? "")}</time></header>
          {renderContent(agent.summary)}
        </article>}
      {agent.truncated && <p className="activity-history-note">{t("activity.truncated")}</p>}
      {!agent.children.length && !agent.summary && <p className="activity-empty">{t(agent.status === "running" ? "activity.started" : "activity.noEvents")}</p>}
    </div>)}
  </div>;
}

function Nodes({ nodes, renderContent, label, onOpen }: {
  nodes: ActivityNode[]; renderContent: RenderContent; label: string; onOpen: (id: string) => void;
}) {
  return <div className="activity-feed">{nodes.map(node => {
    if (node.kind === "agent") return <AgentCard key={node.id} node={node} onOpen={onOpen} />;
    if (node.kind === "tool") return <ToolBlock key={node.id} node={node} />;
    if (node.kind === "note") return <details key={node.id} className="activity-tool activity-note">
      <summary>{node.status ? statusLabel(node.status) : t("activity.savedEvent")}<time>{time(node.at)}</time></summary><pre>{node.text}</pre>
      {node.truncated && <p className="activity-history-note">{t("activity.truncated")}</p>}
    </details>;
    return <article className="activity-message" key={node.id} data-activity-id={node.id}>
      <header><strong>{label}</strong><time>{time(node.at)}</time></header>
      {renderContent(node.text)}
    </article>;
  })}</div>;
}

function AgentCard({ node, onOpen }: { node: AgentActivity; onOpen: (id: string) => void }) {
  return <section className="activity-agent-card" data-activity-id={node.id}>
    <Bot size={19} aria-hidden="true" />
    <div className="activity-agent-title"><strong>{agentLabel(node)}</strong><span>{node.task || (node.legacy ? t("activity.roleHistory") : "")}</span>
      <button onClick={() => onOpen(node.id)}>{t("activity.openAgent")}<ArrowUpRight size={14} /></button>
    </div>
    <div className="activity-agent-meta"><Status status={node.status} background={node.background} />
      <time>{time(node.at)}{node.finishedAt && `–${time(node.finishedAt)}`}</time></div>
  </section>;
}

function ToolBlock({ node }: { node: ToolActivity }) {
  const shortName = node.name.replace(/^mcp__.+?__/, "");
  return <details className={`activity-tool activity-tool-${node.status}`} data-activity-id={node.id}>
    <summary>
      <ChevronRight size={13} className="activity-tool-chevron" aria-hidden="true" /><Terminal size={14} aria-hidden="true" />
      <code>{shortName || t("activity.unknownTool")}</code><span className="activity-tool-summary">{node.summary}</span>
      <Status status={node.status} /><time>{time(node.at)}</time>
      {node.status === "error" && <span className="activity-tool-error">{node.output?.split("\n")[0]}</span>}
    </summary>
    <div className="activity-tool-details">
      <code className="activity-full-name">{node.name}</code>
      {node.incomplete && <p className="activity-history-note">{t("activity.missingCall")}</p>}
      {node.input !== undefined && <><h4>{t("activity.arguments")}</h4><pre>{JSON.stringify(node.input, null, 2)}</pre></>}
      <h4>{t("activity.response")}{node.finishedAt && <time> · {time(node.finishedAt)}</time>}</h4>
      {node.output !== undefined ? <pre>{node.output || t("activity.emptyResponse")}</pre> : <p>{t(node.status === "running" ? "activity.awaitingResponse" : "activity.missingResponse")}</p>}
      {node.truncated && <p className="activity-history-note">{t("activity.truncated")}</p>}
    </div>
  </details>;
}

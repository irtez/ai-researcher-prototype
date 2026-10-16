import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { ExperimentSummaries } from "./ExperimentSummaries";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Circle,
  ClipboardList,
  Clock,
  Copy,
  Download,
  FileCode2,
  FileImage,
  FileJson,
  FileText,
  FlaskConical,
  FolderOpen,
  Gauge,
  Layers3,
  Loader2,
  LogIn,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  Paperclip,
  Play,
  ScrollText,
  Search,
  Settings,
  Square,
  Star,
  Trash2,
  X
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ReactElement, ReactNode } from "react";
import { uiApi } from "./api/client";
import { ActivityFeed } from "./activity/ActivityFeed";
import { activityFromState } from "./activity/fromEvents";
import { PendingNotice } from "./PendingNotice";
import {
  attachSessionEvents,
  cancelAgentRun,
  cancelLiveRun,
  completeLiveRun,
  emptyLiveState,
  failLiveRun,
  fetchAgentSession,
  fetchCapacity,
  fetchQuestion,
  newLiveRun,
  optimisticStages,
  reduceAgentEvent,
  sendAnswer,
  sendControl,
  sendSteer,
  streamManagerRun,
  AgentRunError
} from "./live";
import type {
  AgentSessionMetadata,
  CapacityResponse,
  ComputeMode,
  GpuTelemetry,
  LiveRunState,
  Orchestrator,
  PendingQuestion,
  SteerMode
} from "./live";
import { getUsername, isLoggedIn, login as authLogin, logout as authLogout } from "./api/auth";
import type { Artifact, ArtifactTab, PaperDocumentState, PreviewPayload, SessionListItem, Stage, TabSummaries } from "./types";
import { t, tv, getLang } from "./i18n";
import { ArtifactBrowser } from "./ArtifactBrowser";
import { paperPdf, hasPaper } from "./artifacts";
import { LaunchExamples } from "./LaunchExamples";
import type { LaunchExample } from "./launchExamples";
import {
  DEFAULT_LAUNCH_SELECTION,
  defaultModelFor,
  effectiveModel,
  modelChoicesFor,
  selectedModelConfig, effectiveEffort, modelLabel,
  selectOrchestrator,
  selectResearchProfile
} from "./launchConfig";
import type { LaunchModelConfig, LaunchSelection, ResearchProfile } from "./launchConfig";
import { uploadThenStart, prepareFeedbackAttachments } from "./runRequest";
import type { FeedbackAttachments } from "./runRequest";
import {
  DEFAULT_APPROVAL_DRAFT,
  approvalDraftFromPolicy,
  approvalPolicyFromDraft,
  isApprovalDraftValid,
  secondsUntilApproval
} from "./approvalPolicy";
import type { ApprovalDraft, ApprovalPolicy } from "./approvalPolicy";
import { discardSyntheticAttachAfter404, isNewRunBlocked, sessionRefreshQueryKeys } from "./sessionLifecycle";
import { newResearchSessionId } from "./sessionIdentity";
import { useIterationHistory } from "./useIterationHistory";
import { IterationHistory, IterationSelector } from "./IterationHistory";
import { documentLink, iterationStages, selectedIterationId } from "./iterationHistory";
import type { IterationDetail } from "./iterationHistory";

const DocumentLinkContext = createContext<(href: string) => string>(sanitizeMarkdownHref);

type RouteState = {
  sessionId?: string;
  tab: ArtifactTab;
};

type RunRequest = {
  prompt: string;
  mode: "research" | "debug";
  compute_mode: ComputeMode;
  // Per-session model override; "" means "use the server's configured
  // default" -- same optional contract as compute_mode.
  model_name: string;
  effort?: string;
  // SC1: which orchestrator to run on; defaults to "codearkt" so an
  // unchanged selection behaves exactly as before.
  orchestrator: Orchestrator;
  research_profile: ResearchProfile;
  files: File[];
  prepared_session_id?: string;
  input_files?: string[];
  approvals: ApprovalPolicy;
};

type FeedbackCallbacks = {
  onAccepted: () => void;
  onRejected: (message: string) => void;
  onRetrying: (busy: boolean) => void;
};

type PendingAction =
  | { kind: "new"; request: RunRequest }
  | { kind: "feedback"; sessionId: string; remark: string; approvals: ApprovalPolicy; callbacks?: FeedbackCallbacks; attachments: FeedbackAttachments };

type StartedRunMeta = {
  effort?: string;
  orchestrator?: Orchestrator;
  sessionId: string;
  model: string | undefined;
  researchProfile: ResearchProfile;
};

// labelKey (not a literal label) so the tab text re-resolves on language change
// instead of freezing at module-eval time.
const TABS: Array<{ id: ArtifactTab; labelKey: string; icon: LucideIcon }> = [
  { id: "run", labelKey: "tab.run", icon: Activity },
  { id: "brief", labelKey: "tab.brief", icon: BookOpen },
  { id: "proposals", labelKey: "tab.proposals", icon: ClipboardList },
  { id: "experiments", labelKey: "tab.experiments", icon: FlaskConical },
  { id: "paper", labelKey: "tab.paper", icon: ScrollText },
  { id: "review", labelKey: "tab.review", icon: Star }
];

const TAB_IDS = new Set<ArtifactTab>(TABS.map((tab) => tab.id));
const LIVE_STATE_LAST_KEY = "holosophus.ui.lastLiveSession";
const LIVE_STATE_PREFIX = "holosophus.ui.liveState.";
const TOP_META_COLLAPSED_KEY = "holosophus.ui.topMetaCollapsed";
let liveStateSaveTimer: number | undefined;
let pendingDiskLiveState: LiveRunState | null = null;

function App() {
  const queryClient = useQueryClient();
  const abortControllerRef = useRef<AbortController | null>(null);
  const streamGenerationRef = useRef(0);
  const refreshTimersRef = useRef<Record<string, number>>({});
  // SC1: remember the orchestrator the active run started on so its cancel is
  // routed to the same backend.
  const runOrchestratorRef = useRef<Orchestrator>("codearkt");
  // Which session currently has a live event stream attached (the original
  // POST stream or a re-attach). Guards against a second, competing consumer.
  const attachedStreamRef = useRef<string | null>(null);
  const [route, setRoute] = useState<RouteState>(() => parseRoute());
  const [sessionSearch, setSessionSearch] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [artifactSearch, setArtifactSearch] = useState("");
  const [artifactType, setArtifactType] = useState("all");
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [liveState, setLiveState] = useState<LiveRunState>(() => loadLastLiveState());
  // App-level team login: the current login (null = anonymous) and the modal.
  // `pendingActionRef` holds a launch or feedback request made while logged out, so a
  // successful login resumes it automatically.
  const [username, setUsername] = useState<string | null>(() => getUsername());
  const [loginOpen, setLoginOpen] = useState(false);
  const pendingActionRef = useRef<PendingAction | null>(null);
  const [launchSelection, setLaunchSelection] = useState<LaunchSelection>(DEFAULT_LAUNCH_SELECTION);
  const [launchPhase, setLaunchPhase] = useState<"idle" | "uploading" | "starting">("idle");
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [startedRunMeta, setStartedRunMeta] = useState<StartedRunMeta | null>(null);
  const [initializingSessionId, setInitializingSessionId] = useState<string | null>(null);

  useEffect(() => {
    const onPopState = () => setRoute(parseRoute());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    return () => {
      Object.values(refreshTimersRef.current).forEach((timer) => window.clearTimeout(timer));
    };
  }, []);

  const sessionsQuery = useQuery({
    queryKey: ["sessions", sessionSearch],
    queryFn: () => uiApi.sessions(sessionSearch),
    refetchInterval: query => query.state.data?.sessions.some(session => session.title_pending) ? 2000 : false,
    refetchOnWindowFocus: false,
    staleTime: 5000
  });

  const configQuery = useQuery({
    queryKey: ["config"],
    queryFn: () => uiApi.config(),
    refetchOnWindowFocus: false,
    staleTime: 60000
  });

  const sessions = sessionsQuery.data?.sessions ?? [];
  const modelSlug = configQuery.data?.model_slug ?? undefined;
  const modelChoices = configQuery.data?.model_choices ?? [];
  const modelConfig: LaunchModelConfig = {
    codearktDefault: modelSlug,
    codearktChoices: modelChoices,
    claudeCodeDefault: configQuery.data?.claude_code_default_model,
    claudeCodeChoices: configQuery.data?.claude_code_model_choices,
    models: configQuery.data?.model_configs
  };
  // App-level auth: whether starting a run requires a login for this deploy.
  const authRequired = configQuery.data?.auth_required ?? false;
  const selectedSessionId = route.sessionId;
  const initializingSession = Boolean(selectedSessionId && initializingSessionId === selectedSessionId);
  const sessionReadable = Boolean(selectedSessionId) && !initializingSession;
  const history = useIterationHistory(selectedSessionId, sessionReadable);
  const executionIteration = history.index?.iterations.find((item) => item.id === selectedIterationId(history.index));

  const agentSessionQuery = useQuery({
    queryKey: ["agent-session", selectedSessionId],
    queryFn: ({ signal }) => fetchAgentSession(selectedSessionId!, "claude_code", signal),
    enabled: sessionReadable,
    refetchOnWindowFocus: true,
    refetchInterval: selectedSessionId ? 3000 : false,
    retry: false
  });

  const sessionQuery = useQuery({
    queryKey: ["session", selectedSessionId],
    queryFn: () => uiApi.session(selectedSessionId!),
    enabled: sessionReadable,
    refetchOnWindowFocus: false,
    // Cheap poll whenever a session is open. Progress normally arrives on the
    // agent event stream, but that stream is opened once at run start and never
    // re-attaches, so a dropped connection leaves the UI frozen while the run
    // carries on server-side. Polling the server's own view is what keeps the
    // session readable in that case.
    refetchInterval: selectedSessionId ? 5000 : false,
    staleTime: 5000
  });

  // The landing page can restore a stale "running" cache without opening that
  // session. Resolve its real status as well; reuse the normal session query key.
  const cachedSessionQuery = useQuery({
    queryKey: ["session", liveState.sessionId],
    queryFn: () => uiApi.session(liveState.sessionId!),
    enabled: Boolean(liveState.sessionId) && liveState.sessionId !== selectedSessionId && launchPhase === "idle",
    refetchOnWindowFocus: true,
    refetchInterval: liveState.sessionId && liveState.sessionId !== selectedSessionId ? 5000 : false,
    staleTime: 5000
  });
  const selectedRun = agentSessionQuery.data ?? sessionQuery.data;
  const cachedRun = liveState.sessionId === selectedSessionId ? selectedRun : cachedSessionQuery.data;
  const runInProgress = isNewRunBlocked(
    liveState, cachedRun, launchPhase !== "idle" || Boolean(abortControllerRef.current)
  ) || isNewRunBlocked({ sessionId: selectedSessionId ?? null, status: "idle" }, selectedRun);

  // Trust the *server's* status, not the live-state one: live state is fed by
  // the same stream that may have died, so it can sit on "running" forever (or
  // miss the run finishing entirely).
  const serverRunStatus = sessionQuery.data?.status;
  const serverRunActive =
    Boolean(agentSessionQuery.data?.active) || serverRunStatus === "running" || serverRunStatus === "queued";
  // Heavier than the detail call, so only while the run is actually moving.
  const activeRunPoll = serverRunActive ? 6000 : false;

  const summaryQuery = useQuery({
    queryKey: ["summary", selectedSessionId],
    queryFn: () => uiApi.summary(selectedSessionId!),
    enabled: sessionReadable,
    refetchOnWindowFocus: false,
    refetchInterval: activeRunPoll,
    staleTime: 5000
  });

  const artifactsQuery = useQuery({
    queryKey: ["artifacts", selectedSessionId],
    queryFn: () => uiApi.artifacts(selectedSessionId!),
    enabled: sessionReadable,
    refetchOnWindowFocus: false,
    refetchInterval: activeRunPoll,
    staleTime: 5000
  });

  const previewQuery = useQuery({
    queryKey: ["preview", selectedSessionId, previewPath],
    queryFn: () => uiApi.preview(selectedSessionId!, previewPath!),
    enabled: Boolean(selectedSessionId && previewPath),
    refetchOnWindowFocus: false,
    staleTime: 30000
  });

  const artifacts = artifactsQuery.data?.artifacts ?? [];
  const paperDocument = summaryQuery.data?.paper?.document;
  const visibleTabs = useMemo(() => computeVisibleTabs(artifacts, route.tab, paperDocument), [artifacts, route.tab, paperDocument]);
  const activeTab = visibleTabs.includes(route.tab) ? route.tab : visibleTabs[0];
  const visibleLiveState = liveState.sessionId === selectedSessionId ? liveState : undefined;
  const iterationState = history.selectedId ? history.state : visibleLiveState;

  useEffect(() => {
    persistLiveState(liveState);
  }, [liveState]);

  // Re-attach to a run whose stream this browser lost (or never had -- page
  // reload mid-run). The server replays history then follows live, so we
  // rebuild the live state from scratch and reduce every event; an unbroken
  // stream and a re-attached one converge to the same view. Retries with
  // backoff while the server still reports the run active; a 404 means there
  // is nothing to attach to (backend restarted, or a claude_code run -- the
  // cc_runner has no events endpoint) and the polling fallback carries on.
  const detailOrchestrator = agentSessionQuery.data ? "claude_code" : sessionQuery.data?.orchestrator;
  useEffect(() => {
    const sessionId = selectedSessionId;
    if (!sessionId || !serverRunActive) {
      return;
    }
    if (attachedStreamRef.current === sessionId) {
      return; // the original POST stream (or an earlier attach) is already consuming
    }
    let cancelled = false;
    const controller = new AbortController();
    const generation = ++streamGenerationRef.current;
    attachedStreamRef.current = sessionId;
    const orchestrator: Orchestrator = detailOrchestrator === "claude_code" ? "claude_code" : "codearkt";
    // Stop must route to the backend that owns the resumed run, not a default.
    runOrchestratorRef.current = orchestrator;

    const attachLoop = async () => {
      let delayMs = 1000;
      while (!cancelled) {
        try {
          setLiveState(newLiveRun(sessionId, orchestrator === "claude_code" ? "message" : "delta"));
          await attachSessionEvents(
            sessionId,
            (event) => {
              if (streamGenerationRef.current !== generation) return;
              setLiveState((current) => reduceAgentEvent(current, event));
              if (event.event_type === "observation" || event.event_type === "agent_end") {
                refreshSessionData(event.session_id || sessionId, event.event_type === "agent_end");
              }
            },
            controller.signal,
            orchestrator
          );
          // Clean end = the root agent's terminal event was streamed.
          if (streamGenerationRef.current === generation) {
            setLiveState((current) => (current.sessionId === sessionId ? completeLiveRun(current) : current));
          }
          refreshSessionData(sessionId, true);
          return;
        } catch (error) {
          if (cancelled || controller.signal.aborted) {
            return;
          }
          if (error instanceof AgentRunError && error.status === 404) {
            setLiveState((current) =>
              discardSyntheticAttachAfter404(
                current,
                sessionId,
                generation,
                streamGenerationRef.current,
                completeLiveRun(newLiveRun(sessionId))
              )
            );
            return; // metadata polling now owns active/completed status
          }
          await new Promise((resolve) => window.setTimeout(resolve, delayMs));
          delayMs = Math.min(delayMs * 2, 15000);
        }
      }
    };
    void attachLoop();

    return () => {
      cancelled = true;
      controller.abort();
      if (attachedStreamRef.current === sessionId) {
        attachedStreamRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSessionId, serverRunActive, detailOrchestrator]);

  useEffect(() => {
    if (!selectedSessionId || liveState.sessionId === selectedSessionId) {
      return;
    }
    const restored = loadLiveState(selectedSessionId);
    if (restored) {
      setLiveState(restored);
      return;
    }
    let cancelled = false;
    void uiApi
      .liveState(selectedSessionId)
      .then((payload) => {
        const restoredFromDisk = reviveLiveState(payload.state);
        if (!cancelled && restoredFromDisk) {
          setLiveState(restoredFromDisk);
        }
      })
      .catch(() => {
        // Missing disk state is expected for old sessions.
      });
    return () => {
      cancelled = true;
    };
  }, [liveState.sessionId, selectedSessionId]);

  useEffect(() => {
    if (selectedSessionId && artifactsQuery.isSuccess && activeTab !== route.tab) {
      navigateTo(selectedSessionId, activeTab, true, setRoute);
    }
  }, [activeTab, route.tab, selectedSessionId, artifactsQuery.isSuccess]);

  const selectedSession = sessions.find((session) => session.session_id === selectedSessionId);
  const detail = sessionQuery.data;
  const selectedOrchestrator: Orchestrator =
    agentSessionQuery.data || detail?.orchestrator === "claude_code" ? "claude_code" : "codearkt";
  const matchingStartedRun = startedRunMeta?.sessionId === selectedSessionId ? startedRunMeta : undefined;
  const savedModel = [agentSessionQuery.data?.model_name, detail?.model_slug, selectedSession?.model_slug].find(
    (value): value is string => Boolean(value && value !== "ui")
  );
  const displayedModelId = selectedSessionId
    ? savedModel || matchingStartedRun?.model || defaultModelFor(selectedOrchestrator, modelConfig)
    : effectiveModel(launchSelection, modelConfig);
  const modelDisplayName = selectedSessionId
    ? agentSessionQuery.data?.model_label ?? detail?.model_label ?? modelLabel(displayedModelId, selectedOrchestrator, modelConfig)
    : modelLabel(displayedModelId, launchSelection.orchestrator, modelConfig);
  const displayedEffort = selectedSessionId ? agentSessionQuery.data?.effort ?? detail?.effort ?? matchingStartedRun?.effort : effectiveEffort(launchSelection, modelConfig);
  const displayedModel = `${modelDisplayName ?? ""}${displayedEffort ? ` · ${tv("effort", displayedEffort)}` : ""}`;
  const displayedProfile: ResearchProfile = selectedSessionId
    ? agentSessionQuery.data?.research_profile ??
      detail?.research_profile ??
      selectedSession?.research_profile ??
      matchingStartedRun?.researchProfile ??
      "general"
    : launchSelection.researchProfile;
  const capacitySupported = selectedSessionId
    ? !agentSessionQuery.data && detail?.orchestrator === "codearkt"
    : launchSelection.orchestrator === "codearkt";
  const capacityQuery = useQuery({
    queryKey: ["capacity"],
    queryFn: () => fetchCapacity(),
    enabled: capacitySupported,
    refetchOnWindowFocus: false,
    refetchInterval: capacitySupported ? 4000 : false,
    retry: 1
  });
  const capacity = capacitySupported ? capacityQuery.data : undefined;
  const summary = summaryQuery.data;
  const pinnedArtifacts = detail?.pinned_artifacts.length ? detail.pinned_artifacts : artifacts.filter((item) => item.pinned).slice(0, 5);

  const invalidateSessionData = (sessionId: string) => {
    for (const queryKey of sessionRefreshQueryKeys(sessionId)) {
      void queryClient.invalidateQueries({ queryKey });
    }
  };

  const refreshSessionData = (sessionId: string, immediate = false) => {
    const existingTimer = refreshTimersRef.current[sessionId];
    if (immediate) {
      if (existingTimer !== undefined) {
        window.clearTimeout(existingTimer);
        delete refreshTimersRef.current[sessionId];
      }
      invalidateSessionData(sessionId);
      return;
    }
    if (existingTimer !== undefined) {
      return;
    }
    refreshTimersRef.current[sessionId] = window.setTimeout(() => {
      delete refreshTimersRef.current[sessionId];
      invalidateSessionData(sessionId);
    }, 1500);
  };

  const openArtifact = (artifact: Artifact) => {
    if (selectedSessionId) {
      navigateTo(selectedSessionId, artifact.tab, false, setRoute);
    }
    setPreviewPath(artifact.path);
  };

  const selectSession = (sessionId: string) => {
    navigateTo(sessionId, "run", false, setRoute);
    setPreviewPath(null);
  };

  const openNewRun = () => {
    window.history.pushState(null, "", "/");
    setRoute({ tab: "run" });
    setPreviewPath(null);
  };

  const startRun = async (request: RunRequest) => {
    // App-level team login gate: anonymous users can browse the UI, but a run
    // needs a login. Stash the request and open the login modal; a successful
    // login resumes it (see handleLogin). Server-enforced regardless (401), so
    // this is UX -- it never becomes the security boundary.
    if (authRequired && !isLoggedIn()) {
      pendingActionRef.current = { kind: "new", request };
      setLoginOpen(true);
      return;
    }

    const sessionModelSlug = effectiveModel(
      {
        researchProfile: request.research_profile,
        orchestrator: request.orchestrator,
        computeMode: request.compute_mode,
        modelName: request.model_name
      },
      modelConfig
    );
    const sessionId = request.prepared_session_id ?? (request.orchestrator === "claude_code"
      ? newResearchSessionId() : buildSessionId(request.mode, request.prompt, sessionModelSlug));
    setLaunchError(null);
    setLaunchPhase(request.input_files ? "starting" : request.files.length ? "uploading" : "starting");

    const beginRun = async (inputFiles: string[]) => {
      setLaunchPhase("starting");
      setInitializingSessionId(sessionId);
      setStartedRunMeta({
        sessionId,
        model: sessionModelSlug,
        effort: request.effort,
        orchestrator: request.orchestrator,
        researchProfile: request.research_profile
      });
      const abortController = new AbortController();
      const generation = ++streamGenerationRef.current;
      abortControllerRef.current = abortController;
      runOrchestratorRef.current = request.orchestrator;
      setLiveState(newLiveRun(sessionId, request.orchestrator === "claude_code" ? "message" : "delta"));
      navigateTo(sessionId, "run", false, setRoute);
      setPreviewPath(null);
      attachedStreamRef.current = sessionId;

      try {
        await streamManagerRun(
          {
            sessionId,
            prompt: request.prompt,
            computeMode: request.compute_mode,
            modelName: request.model_name,
            effort: request.effort,
            orchestrator: request.orchestrator,
            researchProfile: request.research_profile,
            inputFiles,
            approvals: request.approvals,
            resume: false
          },
          (event) => {
            if (streamGenerationRef.current !== generation) return;
            // The runner creates history and registers the session before its
            // first event. Reads are meaningful only after that acknowledgement.
            setInitializingSessionId((current) => current === sessionId ? null : current);
            setLiveState((current) => reduceAgentEvent(current, event));
            if (event.event_type === "observation" || event.event_type === "agent_end") {
              refreshSessionData(event.session_id || sessionId, event.event_type === "agent_end");
            }
          },
          abortController.signal
        );
        if (streamGenerationRef.current === generation) {
          setLiveState((current) => (current.sessionId === sessionId ? completeLiveRun(current) : current));
        }
      } catch (error) {
        if (streamGenerationRef.current !== generation) return;
        if (abortController.signal.aborted) {
          setLiveState((current) => (current.sessionId === sessionId ? cancelLiveRun(current) : current));
        } else if (error instanceof AgentRunError && error.status === 401) {
          // The inputs are already stored in this prepared session. Keep their
          // returned paths so logging in retries only the manager request.
          authLogout();
          setUsername(null);
          pendingActionRef.current = {
            kind: "new",
            request: {
              ...request,
              files: [],
              prepared_session_id: sessionId,
              input_files: inputFiles
            }
          };
          setLoginOpen(true);
          setLiveState((current) => failLiveRun(current, new Error(t("login.required"))));
        } else {
          // The stream dying does NOT mean the run died: the run is a detached
          // task server-side. Only mark failure when the server agrees the run
          // is gone; otherwise leave the state running and let polling continue.
          const stillRunning = await isRunStillActive(sessionId);
          if (!stillRunning) {
            setLiveState((current) => failLiveRun(current, error));
          }
        }
      } finally {
        setInitializingSessionId((current) => current === sessionId ? null : current);
        if (streamGenerationRef.current === generation && attachedStreamRef.current === sessionId) {
          attachedStreamRef.current = null;
        }
        if (abortControllerRef.current === abortController) {
          abortControllerRef.current = null;
        }
        refreshSessionData(sessionId, true);
        void queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
      }
    };

    try {
      if (request.input_files) {
        await beginRun(request.input_files);
      } else {
        await uploadThenStart(sessionId, request.files, uiApi.uploadInputs, beginRun);
      }
    } catch (error) {
      if (hasHttpStatus(error, 401)) {
        authLogout();
        setUsername(null);
        pendingActionRef.current = { kind: "new", request };
        setLoginOpen(true);
        setLaunchError(t("login.required"));
      } else {
        const message = error instanceof Error ? error.message : String(error);
        setLaunchError(t("launch.uploadFailed", { message }));
      }
    } finally {
      setLaunchPhase("idle");
    }
  };

  const resumeRun = async (sessionId: string, remark: string, approvals: ApprovalPolicy, callbacks?: FeedbackCallbacks, attachments: FeedbackAttachments = { files: [] }) => {
    if (authRequired && !isLoggedIn()) {
      pendingActionRef.current = { kind: "feedback", sessionId, remark, approvals, callbacks, attachments };
      setLoginOpen(true);
      return;
    }
    if (abortControllerRef.current) {
      throw new Error(t("feedback.alreadyActive"));
    }

    let inputFiles: string[];
    try {
      inputFiles = await prepareFeedbackAttachments(sessionId, attachments, uiApi.uploadFeedbackInputs);
    } catch (error) {
      if (hasHttpStatus(error, 401)) {
        authLogout();
        setUsername(null);
        pendingActionRef.current = { kind: "feedback", sessionId, remark, approvals, callbacks, attachments };
        setLoginOpen(true);
        return;
      }
      throw new Error(t("launch.uploadFailed", { message: error instanceof Error ? error.message : String(error) }));
    }
    if (abortControllerRef.current) throw new Error(t("feedback.alreadyActive"));

    const abortController = new AbortController();
    const generation = ++streamGenerationRef.current;
    abortControllerRef.current = abortController;
    runOrchestratorRef.current = "claude_code";
    attachedStreamRef.current = sessionId;
    setLiveState(newLiveRun(sessionId, "message"));
    history.select("latest");
    navigateTo(sessionId, "run", false, setRoute);
    setPreviewPath(null);
    void queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });

    try {
      await streamManagerRun(
        {
          sessionId,
          prompt: remark,
          orchestrator: "claude_code",
          approvals,
          inputFiles,
          feedbackUploadId: attachments.uploadId,
          resume: true
        },
        (event) => {
          if (streamGenerationRef.current !== generation) return;
          setLiveState((current) => reduceAgentEvent(current, event));
          if (event.event_type === "observation" || event.event_type === "agent_end") {
            refreshSessionData(event.session_id || sessionId, event.event_type === "agent_end");
          }
        },
        abortController.signal,
        () => {
          if (streamGenerationRef.current !== generation) return;
          callbacks?.onAccepted();
          refreshSessionData(sessionId, true);
          void queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
        }
      );
      if (streamGenerationRef.current === generation) {
        setLiveState((current) => (current.sessionId === sessionId ? completeLiveRun(current) : current));
      }
    } catch (error) {
      if (streamGenerationRef.current !== generation) return;
      if (abortController.signal.aborted) {
        setLiveState((current) => (current.sessionId === sessionId ? cancelLiveRun(current) : current));
      } else if (error instanceof AgentRunError && error.status === 401) {
        authLogout();
        setUsername(null);
        pendingActionRef.current = { kind: "feedback", sessionId, remark, approvals, callbacks, attachments };
        setLoginOpen(true);
        setLiveState((current) =>
          current.sessionId === sessionId ? failLiveRun(current, new Error(t("login.required"))) : current
        );
      } else if (error instanceof AgentRunError && error.status === 409) {
        throw new Error(t("feedback.alreadyActive"));
      } else {
        let stillRunning = false;
        try {
          stillRunning = (await fetchAgentSession(sessionId, "claude_code")).active;
        } catch {
          // The error below is the only reliable state when metadata is unavailable.
        }
        if (!stillRunning) {
          setLiveState((current) => (current.sessionId === sessionId ? failLiveRun(current, error) : current));
          throw new Error(t("feedback.failed"));
        }
      }
    } finally {
      if (streamGenerationRef.current === generation && attachedStreamRef.current === sessionId) {
        attachedStreamRef.current = null;
      }
      if (abortControllerRef.current === abortController) {
        abortControllerRef.current = null;
      }
      refreshSessionData(sessionId, true);
      await queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
    }
  };

  const handleLogin = async (loginName: string, password: string) => {
    const name = await authLogin(loginName, password);
    setUsername(name);
    setLoginOpen(false);
    // Re-scope the session list / capacity to the freshly logged-in owner.
    void queryClient.invalidateQueries({ queryKey: ["sessions"] });
    void queryClient.invalidateQueries({ queryKey: ["capacity"] });
    const pending = pendingActionRef.current;
    pendingActionRef.current = null;
    if (pending) {
      if (pending.kind === "new") {
        void startRun(pending.request);
      } else {
        pending.callbacks?.onRetrying(true);
        void resumeRun(pending.sessionId, pending.remark, pending.approvals, pending.callbacks, pending.attachments)
          .catch(error => pending.callbacks?.onRejected(error instanceof Error ? error.message : String(error)))
          .finally(() => pending.callbacks?.onRetrying(false));
      }
    }
  };

  const handleLogout = () => {
    authLogout();
    setUsername(null);
    pendingActionRef.current = null;
    // Back to the anonymous client-id: the list becomes "no runs" for this box.
    void queryClient.invalidateQueries({ queryKey: ["sessions"] });
    void queryClient.invalidateQueries({ queryKey: ["capacity"] });
  };

  const deleteSession = async (sessionId: string) => {
    await uiApi.deleteSession(sessionId);
    // Drop the browser-local copy too, or a deleted run would be resurrected
    // from localStorage on the next load.
    clearStoredLiveState(sessionId);
    if (sessionId === selectedSessionId) {
      openNewRun();
    }
    await queryClient.invalidateQueries({ queryKey: ["sessions"] });
  };

  const stopRun = async () => {
    const sessionId = selectedSessionId ?? liveState.sessionId;
    if (!sessionId) {
      return;
    }
    abortControllerRef.current?.abort();
    setLiveState((current) => cancelLiveRun(current));
    try {
      await cancelAgentRun(sessionId, runOrchestratorRef.current);
    } finally {
      refreshSessionData(sessionId, true);
    }
  };

  return (
    <div className="app-shell">
      <TopBar
        sessionId={selectedSessionId}
        selectedSession={selectedSession}
        detail={detail}
        agentSession={agentSessionQuery.data}
        liveState={visibleLiveState}
        iterationStatus={executionIteration?.kind !== "legacy" ? executionIteration?.status : undefined}
        displayModel={displayedModel}
        researchProfile={displayedProfile}
        loading={sessionQuery.isFetching || artifactsQuery.isFetching}
        onStop={() => void stopRun()}
        capacity={capacity}
        showCapacity={capacitySupported}
        capacityLoading={capacityQuery.isFetching}
        capacityError={capacityQuery.error}
        authRequired={authRequired}
        username={username}
        onLogin={() => setLoginOpen(true)}
        onLogout={handleLogout}
      />

      {selectedSessionId && !initializingSession && (
        <OperatorControls
          key={selectedSessionId}
          sessionId={selectedSessionId}
          metadata={agentSessionQuery.data}
          metadataError={agentSessionQuery.error}
          metadataLoading={agentSessionQuery.isFetching}
          status={visibleLiveState?.status}
          onFeedback={(remark, approvals, callbacks, attachments) => resumeRun(selectedSessionId, remark, approvals, callbacks, attachments)}
          onRefresh={() => {
            void agentSessionQuery.refetch();
            refreshSessionData(selectedSessionId, true);
          }}
        />
      )}

      <div className={`workspace ${sidebarCollapsed ? "sidebar-collapsed" : ""} ${selectedSessionId ? "" : "no-run-panel"}`}>
        <SessionSidebar
          collapsed={sidebarCollapsed}
          sessions={sessions}
          selectedSessionId={selectedSessionId}
          search={sessionSearch}
          loading={sessions.length === 0 && sessionsQuery.isFetching}
          error={sessionsQuery.error}
          onSearch={setSessionSearch}
          onToggle={() => setSidebarCollapsed((value) => !value)}
          onNew={openNewRun}
          onSelect={selectSession}
          onDelete={deleteSession}
          runInProgress={runInProgress}
        />

        <main className="notebook">
          {history.index && <IterationSelector index={history.index} selectedId={history.selectedId}
            selection={history.selection} onSelect={history.select} />}
          {selectedSessionId && (
            <NotebookTabs
              visibleTabs={visibleTabs}
              activeTab={activeTab}
              onSelect={(tab) => navigateTo(selectedSessionId, tab, false, setRoute)}
            />
          )}

          <section className="workbench">
            {initializingSession && <div className="empty-compact" role="status">{t("launch.preparingSession")}</div>}
            {history.error && <ErrorLine label={t("history.loadError")} />}
            {history.selectedId && history.loading && <div className="empty-compact">{t("common.loading")}</div>}
            {selectedSessionId && !initializingSession && <div hidden={activeTab !== "run"} className="run-view-container">
              <RunDetailsTab key={`${selectedSessionId}:${history.selectedId ?? "live"}`}
                visible={activeTab === "run"}
                sessionId={selectedSessionId} summary={summary} artifacts={artifacts}
                liveState={iterationState} onOpenArtifact={openArtifact}
                runContext={history.index && history.detail && <IterationHistory index={history.index} iteration={history.detail}
              latest={history.latest} renderDocument={(content, sourcePath) => (
                <DocumentLinkContext.Provider value={sourcePath
                  ? (href) => documentLink(selectedSessionId!, sourcePath, href) : sanitizeMarkdownHref}>
                  <MarkdownPreview content={content} />
                </DocumentLinkContext.Provider>
              )} />}
              />
            </div>}
            {selectedSessionId ? (initializingSession ? null : (
              <TabContent
                tab={activeTab}
                sessionId={selectedSessionId}
                summary={summary}
                artifacts={artifacts}
                loading={!summary && artifacts.length === 0 && (summaryQuery.isFetching || artifactsQuery.isFetching)}
                error={summaryQuery.error || artifactsQuery.error}
                liveState={iterationState}
                onOpenArtifact={openArtifact}
              />
            )) : (
              <TaskLaunchPanel
                disabled={runInProgress}
                selection={launchSelection}
                onSelectionChange={setLaunchSelection}
                modelConfig={modelConfig}
                launchPhase={launchPhase}
                launchError={launchError}
                telemetry={capacity?.gpu_telemetry}
                onStart={startRun}
              />
            )}
          </section>
        </main>

        {selectedSessionId && <ResearchRunPanel detail={detail} liveState={iterationState}
          iteration={history.detail} hasHistory={Boolean(history.selectedId)} loading={history.loading || sessionQuery.isFetching} />}
      </div>

      <ArtifactDrawer
        open={drawerOpen}
        artifacts={artifacts}
        pinnedArtifacts={pinnedArtifacts}
        search={artifactSearch}
        type={artifactType}
        onSearch={setArtifactSearch}
        onType={setArtifactType}
        onToggle={() => setDrawerOpen((value) => !value)}
        onOpenArtifact={openArtifact}
      />

      <PreviewModal
        sessionId={selectedSessionId}
        path={previewPath}
        preview={previewQuery.data}
        loading={previewQuery.isFetching}
        error={previewQuery.error}
        onClose={() => setPreviewPath(null)}
      />

      {loginOpen && (
        <LoginModal
          onSubmit={handleLogin}
          onClose={() => {
            setLoginOpen(false);
            pendingActionRef.current = null;
          }}
        />
      )}
    </div>
  );
}

function TopBar({
  sessionId,
  selectedSession,
  detail,
  agentSession,
  liveState,
  iterationStatus,
  displayModel,
  researchProfile,
  loading,
  onStop,
  capacity,
  showCapacity,
  capacityLoading,
  capacityError,
  authRequired,
  username,
  onLogin,
  onLogout
}: {
  sessionId?: string;
  selectedSession?: SessionListItem;
  detail?: {
    status: string;
    task_type: string;
    active_agent: string | null;
    orchestrator?: string;
    model_slug?: string | null;
    research_profile?: ResearchProfile;
  } | null;
  agentSession?: AgentSessionMetadata;
  liveState?: LiveRunState;
  iterationStatus?: string;
  displayModel?: string;
  researchProfile: ResearchProfile;
  loading: boolean;
  onStop: () => void;
  capacity?: CapacityResponse;
  showCapacity: boolean;
  capacityLoading: boolean;
  capacityError: unknown;
  authRequired: boolean;
  username: string | null;
  onLogin: () => void;
  onLogout: () => void;
}) {
  const activeAgent = iterationStatus && ["complete", "error", "cancelled"].includes(iterationStatus)
    ? undefined : liveState?.currentAgent ?? detail?.active_agent;
  const liveStatus = liveState?.status;
  const activeStatus =
    liveStatus && ["queued", "paused", "waiting_for_human", "waiting_rate_limit"].includes(liveStatus)
      ? liveStatus
      : agentSession?.status === "queued"
        ? "queued"
        : "running";
  const status = agentSession?.active
    ? activeStatus
    : iterationStatus ?? liveStatus ?? detail?.status ?? selectedSession?.status ?? "idle";
  const canStop = ["running", "queued", "paused", "waiting_for_human", "waiting_rate_limit"].includes(status);
  const isQueued = status === "queued";
  // Orchestrator badge (purpose-named, AP-11) from ui-api's session detail; the
  // marker's absence means the standard orchestrator.
  const orchestratorLabel = detail ? formatOrchestrator(detail.orchestrator) : undefined;
  // One control folds the whole status strip (session / model / mode / run /
  // capacity) so the header can get out of the way. Remembered per browser.
  const [metaCollapsed, setMetaCollapsed] = useState(readTopMetaCollapsed);
  const toggleMeta = () =>
    setMetaCollapsed((value) => {
      const next = !value;
      try {
        window.localStorage.setItem(TOP_META_COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        // Storage can be denied (private mode); the toggle still works in-session.
      }
      return next;
    });
  return (
    <header className="top-bar">
      <div className="brand-lockup">
        <div className="brand-mark">
          <Layers3 size={18} />
        </div>
        <div>
          <div className="brand-name">AI Researcher</div>
        </div>
      </div>

      <div className="top-meta">
        <button
          className="icon-button meta-toggle"
          onClick={toggleMeta}
          title={metaCollapsed ? t("topbar.expandMeta") : t("topbar.collapseMeta")}
          aria-expanded={!metaCollapsed}
          aria-controls="top-meta-strip"
        >
          {metaCollapsed ? <ChevronsRight size={15} /> : <ChevronsLeft size={15} />}
        </button>
        {!metaCollapsed && (
          <div className="top-meta-strip" id="top-meta-strip">
            <span className="meta-pill strong">{t("topbar.sessionPrefix")}: {sessionId ?? selectedSession?.session_id ?? t("common.none")}</span>
            <span className="meta-pill">{formatModelName(displayModel)}</span>
            <span className="meta-pill">{formatResearchProfile(researchProfile)}</span>
            {orchestratorLabel && <span className="meta-pill" title={t("topbar.orchestratorTitle")}>{orchestratorLabel}</span>}
            <span className="meta-pill">{formatTaskType(detail?.task_type ?? selectedSession?.mode ?? "workspace")}</span>
            <span className={`run-indicator ${isQueued ? "queued" : ""}`}>
              {isQueued ? <Clock size={15} /> : loading ? <Loader2 size={15} className="spin" /> : <Activity size={15} />}
              {isQueued ? formatQueuedLabel(liveState) : activeAgent ?? formatRunStatus(status)}
            </span>
            {showCapacity && <CapacityPanel capacity={capacity} loading={capacityLoading} error={capacityError} />}
          </div>
        )}
      </div>

      <div className="top-bar-actions">
        {authRequired &&
          (username ? (
            <>
              <span className="meta-pill" title={t("topbar.loggedInTitle")}>
                {t("topbar.signedInAs", { name: username })}
              </span>
              <button className="command-button" onClick={onLogout} title={t("topbar.logout")}>
                <LogOut size={15} />
                <span>{t("topbar.logout")}</span>
              </button>
            </>
          ) : (
            <button className="command-button" onClick={onLogin} title={t("topbar.loginToRun")}>
              <LogIn size={15} />
              <span>{t("topbar.login")}</span>
            </button>
          ))}

        <button className="command-button danger" disabled={!canStop} onClick={onStop} title={t("topbar.stopTitle")}>
          <Square size={15} />
          <span>{t("topbar.stop")}</span>
        </button>

      </div>
    </header>
  );
}

// Operator controls for a live claude_code run (docs/plans/2026-08-13-ui-operator-control-cc.md):
// pause/resume, graceful stop-and-checkpoint, steer (now/next), and the ask_human answer box.
// Self-contained: it owns its steer, approval, and feedback inputs and polls the selected
// session's durable metadata. `paused` is optimistic because the driver emits no paused event.
function OperatorControls({
  sessionId,
  metadata,
  metadataError,
  metadataLoading,
  status,
  onFeedback,
  onRefresh
}: {
  sessionId: string;
  metadata?: AgentSessionMetadata;
  metadataError: unknown;
  metadataLoading: boolean;
  status?: LiveRunState["status"];
  onFeedback: (remark: string, approvals: ApprovalPolicy, callbacks: FeedbackCallbacks, attachments: FeedbackAttachments) => Promise<void>;
  onRefresh: () => void;
}) {
  const [steerText, setSteerText] = useState("");
  const [steerMode, setSteerMode] = useState<SteerMode>("next");
  const [paused, setPaused] = useState(false);
  const [question, setQuestion] = useState<PendingQuestion | null>(null);
  const questionCard = useRef<HTMLElement>(null);
  const [answerText, setAnswerText] = useState("");
  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackFiles, setFeedbackFiles] = useState<File[]>([]);
  const feedbackAttachments = useRef<FeedbackAttachments>({ files: [] });
  const changeFeedbackFiles = (files: File[]) => {
    setFeedbackFiles(files);
    feedbackAttachments.current = { files, uploadId: files.length ? newResearchSessionId().slice("research_".length) : undefined };
  };
  const [approvals, setApprovals] = useState<ApprovalDraft>({ ...DEFAULT_APPROVAL_DRAFT });
  const [busy, setBusy] = useState(false);
  const [feedbackBusy, setFeedbackBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [questionRefresh, setQuestionRefresh] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const approvalsHydrated = useRef(false);
  const active = Boolean(metadata?.active);
  const feedbackConfig = metadata?.requested_launch ?? metadata;

  useEffect(() => {
    if (metadata && !approvalsHydrated.current) {
      setApprovals(approvalDraftFromPolicy(metadata.approvals));
      approvalsHydrated.current = true;
    }
  }, [metadata]);

  // A selected session can have a pending approval after reload even when no
  // browser-local stream state survived. Sequence numbers prevent a slower old
  // request from replacing a newer question response.
  useEffect(() => {
    let cancelled = false;
    let sequence = 0;
    const tick = () => {
      const currentSequence = ++sequence;
      void fetchQuestion(sessionId, "claude_code")
        .then((nextQuestion) => {
          if (!cancelled && currentSequence === sequence) {
            setQuestion(nextQuestion);
          }
        })
        .catch(() => {
          // Keep the current card during a transient error. It is safer than
          // making an unanswered question disappear.
        });
    };
    tick();
    const id = window.setInterval(tick, 3000);
    return () => {
      cancelled = true;
      sequence += 1;
      window.clearInterval(id);
    };
  }, [sessionId, questionRefresh]);

  useEffect(() => {
    if (!question?.auto_approve_at) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [question?.auto_approve_at]);

  const run = (fn: () => Promise<void>) => {
    setBusy(true);
    setActionError(null);
    void fn()
      .catch((error) => setActionError(error instanceof Error ? error.message : String(error)))
      .finally(() => setBusy(false));
  };

  const onPause = () =>
    run(async () => {
      await sendControl(sessionId, "pause", "claude_code");
      setPaused(true);
    });
  const onResume = () =>
    run(async () => {
      await sendControl(sessionId, "resume", "claude_code");
      setPaused(false);
    });
  const onStopGraceful = () => run(() => sendControl(sessionId, "stop", "claude_code"));
  const onSteer = () => {
    const text = steerText.trim();
    if (!text) return;
    run(async () => {
      await sendSteer(sessionId, text, steerMode, "claude_code");
      setSteerText("");
    });
  };
  const submitQuestion = (action: "approve" | "revise" | "answer") => {
    const displayedQuestion = question;
    if (!displayedQuestion) return;
    const text = answerText.trim();
    if ((action === "revise" || action === "answer") && !text) return;
    run(async () => {
      try {
        await sendAnswer(sessionId, displayedQuestion.qid, action, text, "claude_code");
        setAnswerText("");
        setQuestion((current) => (current?.qid === displayedQuestion.qid ? null : current));
        setQuestionRefresh((value) => value + 1);
        onRefresh();
      } catch (error) {
        if (error instanceof AgentRunError && error.status === 409) {
          setActionError(t("approval.stale"));
          setQuestionRefresh((value) => value + 1);
          onRefresh();
          return;
        }
        throw error;
      }
    });
  };
  const submitFeedback = () => {
    const text = feedbackText.trim();
    if (!text || !metadata?.can_resume || active || question || !isApprovalDraftValid(approvals)) return;
    setFeedbackBusy(true);
    setActionError(null);
    const attachments = feedbackAttachments.current;
    void onFeedback(text, approvalPolicyFromDraft(approvals), {
        onAccepted: () => {
          setFeedbackText("");
          setFeedbackOpen(false);
          if (feedbackAttachments.current === attachments) changeFeedbackFiles([]);
        },
        onRejected: setActionError,
        onRetrying: setFeedbackBusy
      }, attachments)
      .then(onRefresh)
      .catch((error) => setActionError(error instanceof Error ? error.message : String(error)))
      .finally(() => setFeedbackBusy(false));
  };

  const approvalQuestion = question?.stage === "sources" || question?.stage === "plan";
  const countdown = question ? secondsUntilApproval(question.auto_approve_at, now) : null;
  const canEditPolicy = Boolean(metadata?.can_resume && !active && !question && !feedbackBusy);
  const unavailable = hasHttpStatus(metadataError, 404);

  return (
    <div className="operator-controls">
      {question && <PendingNotice key={question.qid} questionId={question.qid} stage={question.stage} target={questionCard} />}
      {question && (
        <section ref={questionCard} className="approval-card" aria-live="polite" tabIndex={-1}>
          <div className="approval-card-head">
            <strong>
              {approvalQuestion
                ? t(question.stage === "sources" ? "approval.sourcesTitle" : "approval.planTitle")
                : t("approval.questionTitle")}
            </strong>
            {question.document_path && <span>{question.document_path}</span>}
          </div>
          {(question.question || approvalQuestion) && <p className="approval-question">{approvalQuestion
            ? t(question.stage === "sources" ? "approval.confirmSources" : "approval.confirmPlan")
            : question.question}</p>}
          {question.context && (
            <div className="approval-context">
              <MarkdownPreview content={question.context} />
            </div>
          )}
          {countdown !== null && (
            <p className="approval-auto-note">
              {countdown > 0
                ? t("approval.autoCountdown", { seconds: countdown })
                : t("approval.autoPending")}
            </p>
          )}
          <div className="approval-actions">
            <input
              value={answerText}
              onChange={(event) => setAnswerText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !approvalQuestion) submitQuestion("answer");
              }}
              placeholder={approvalQuestion ? t("approval.correctionPlaceholder") : t("approval.answerPlaceholder")}
            />
            {approvalQuestion ? (
              <>
                <button className="command-button success" disabled={busy} onClick={() => submitQuestion("approve")}>
                  {t("approval.approve")}
                </button>
                <button
                  className="command-button"
                  disabled={busy || !answerText.trim()}
                  onClick={() => submitQuestion("revise")}
                >
                  {t("approval.sendCorrection")}
                </button>
              </>
            ) : (
              <button
                className="command-button"
                disabled={busy || !answerText.trim()}
                onClick={() => submitQuestion("answer")}
              >
                {t("approval.answer")}
              </button>
            )}
          </div>
          {actionError && <div className="operator-error" role="alert">{actionError}</div>}
        </section>
      )}

      {metadata?.last_decision && (
        <div className="latest-decision" aria-live="polite">
          <strong>{t("approval.latestDecision")}</strong>
          <span>{formatDecision(metadata.last_decision.status)}</span>
          {metadata.last_decision.stage && <span>{formatApprovalStage(metadata.last_decision.stage)}</span>}
          {metadata.last_decision.text && ["changes_requested", "answered"].includes(metadata.last_decision.status) && (
            <span>{metadata.last_decision.text}</span>
          )}
        </div>
      )}

      {active && (
        <div className="operator-bar">
          {paused ? (
            <button className="command-button" disabled={busy} onClick={onResume}>{t("operator.resume")}</button>
          ) : (
            <button className="command-button" disabled={busy} onClick={onPause}>{t("operator.pause")}</button>
          )}
          <button className="command-button" disabled={busy} onClick={onStopGraceful} title={t("operator.stopTitle")}>
            {t("operator.stopCheckpoint")}
          </button>
          <input
            value={steerText}
            onChange={(event) => setSteerText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") onSteer();
            }}
            placeholder={t("operator.steerPlaceholder")}
          />
          <select value={steerMode} onChange={(event) => setSteerMode(event.target.value as SteerMode)}>
            <option value="next">{t("operator.next")}</option>
            <option value="now">{t("operator.now")}</option>
          </select>
          <button className="command-button" disabled={busy || !steerText.trim()} onClick={onSteer}>
            {t("operator.steer")}
          </button>
          {(status === "waiting_rate_limit" || metadata?.status === "queued") && (
            <span className="meta-pill">
              {metadata?.status === "queued" ? t("operator.queued") : t("operator.rateLimit")}
            </span>
          )}
          {!question && actionError && <div className="operator-error" role="alert">{actionError}</div>}
        </div>
      )}

      {!active && metadata?.can_resume && !question && (
        <section className={`feedback-panel ${feedbackOpen ? "" : "feedback-collapsed"}`}>
          <button className="command-button feedback-toggle" aria-expanded={feedbackOpen}
            aria-controls="feedback-editor" disabled={feedbackBusy} onClick={() => setFeedbackOpen((open) => !open)}>
            {feedbackOpen ? <ChevronLeft size={15} /> : <Play size={15} />}
            {t(feedbackOpen ? "feedback.collapse" : "feedback.submit")}
          </button>
          {feedbackOpen && <div id="feedback-editor" className="feedback-editor">
            <div className="feedback-heading">
              <div>
                <strong>{t("feedback.title")}</strong>
                <p>{t("feedback.savedConfig", {
                  profile: tv("profile", metadata.research_profile),
                  model: formatModelName(feedbackConfig?.model_label ?? feedbackConfig?.model_name) + (feedbackConfig?.effort ? ` · ${tv("effort", feedbackConfig.effort)}` : ""),
                  compute: formatComputeMode(feedbackConfig?.compute_mode ?? metadata.compute_mode)
                })}</p>
              </div>
              {metadataLoading && <Loader2 size={15} className="spin" />}
            </div>
            <textarea
              value={feedbackText}
              onChange={(event) => setFeedbackText(event.target.value)}
              placeholder={t("feedback.placeholder")}
              rows={3}
              disabled={feedbackBusy}
            />
            <ApprovalSettings draft={approvals} onChange={setApprovals} disabled={!canEditPolicy} />
            <FileAttachments files={feedbackFiles} onChange={changeFeedbackFiles} disabled={feedbackBusy} />
            {feedbackFiles.length > 0 && approvals.sources && <p className="history-note">{t("feedback.newInputsApproval")}</p>}
            <div className="feedback-submit-row">
              {!isApprovalDraftValid(approvals) && <span className="operator-error">{t("approval.invalidTimeout")}</span>}
              {actionError && <span className="operator-error" role="alert">{actionError}</span>}
              <button
                className="command-button start"
                disabled={feedbackBusy || !feedbackText.trim() || !isApprovalDraftValid(approvals)}
                onClick={submitFeedback}
              >
                {feedbackBusy ? <Loader2 size={15} className="spin" /> : <Play size={15} />}
                {t("feedback.submit")}
              </button>
            </div>
          </div>}
        </section>
      )}

      {!metadata && unavailable && (
        <div className="feedback-unavailable">{t("feedback.unavailableLegacy")}</div>
      )}
      {!metadata && !unavailable && Boolean(metadataError) && (
        <div className="feedback-unavailable operator-error">{t("feedback.metadataError")}</div>
      )}
      {metadata && !active && !metadata.can_resume && !question && (
        <div className="feedback-unavailable">{t("feedback.unavailableState")}</div>
      )}
    </div>
  );
}

function ApprovalSettings({
  draft,
  onChange,
  disabled
}: {
  draft: ApprovalDraft;
  onChange: (draft: ApprovalDraft) => void;
  disabled: boolean;
}) {
  const setStage = (stage: "sources" | "plan", checked: boolean) => {
    const next = { ...draft, [stage]: checked };
    if (!next.sources && !next.plan) {
      next.autoApprove = false;
    }
    onChange(next);
  };
  const noStages = !draft.sources && !draft.plan;
  return (
    <fieldset className="approval-settings" disabled={disabled}>
      <legend>{t("approval.settingsTitle")}</legend>
      <label>
        <input type="checkbox" checked={draft.sources} onChange={(event) => setStage("sources", event.target.checked)} />
        <span>{t("approval.sourcesSwitch")}</span>
      </label>
      <label>
        <input type="checkbox" checked={draft.plan} onChange={(event) => setStage("plan", event.target.checked)} />
        <span>{t("approval.planSwitch")}</span>
      </label>
      <div className="approval-auto-row">
        <label>
          <input
            type="checkbox"
            checked={draft.autoApprove}
            disabled={disabled || noStages}
            onChange={(event) => onChange({ ...draft, autoApprove: event.target.checked })}
          />
          <span>{t("approval.autoSwitch")}</span>
        </label>
        <input
          className="approval-seconds"
          type="number"
          min="1"
          step="1"
          value={draft.autoApproveSeconds}
          disabled={disabled || noStages || !draft.autoApprove}
          onChange={(event) => onChange({ ...draft, autoApproveSeconds: event.target.value })}
          aria-label={t("approval.secondsAria")}
        />
        <span>{t("approval.secondsSuffix")}</span>
      </div>
      {draft.autoApprove && !noStages && <p>{t("approval.autoExplanation")}</p>}
      {!isApprovalDraftValid(draft) && <p className="operator-error">{t("approval.invalidTimeout")}</p>}
    </fieldset>
  );
}

async function isRunStillActive(sessionId: string): Promise<boolean> {
  try {
    const detail = await uiApi.session(sessionId);
    return detail.status === "running" || detail.status === "queued";
  } catch {
    // Server unreachable: no evidence either way -- treat as not running so
    // the user sees the failure rather than a run that looks alive forever.
    return false;
  }
}

function readTopMetaCollapsed() {
  try {
    return window.localStorage.getItem(TOP_META_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

function formatQueuedLabel(liveState?: LiveRunState) {
  if (!liveState || liveState.queuePosition === undefined) {
    return t("queue.queued");
  }
  return liveState.queueDepth
    ? t("queue.queuedPosOf", { pos: liveState.queuePosition, depth: liveState.queueDepth })
    : t("queue.queuedPos", { pos: liveState.queuePosition });
}

function CapacityPanel({
  capacity,
  loading,
  error
}: {
  capacity?: CapacityResponse;
  loading: boolean;
  error: unknown;
}) {
  if (!capacity) {
    if (error) {
      // Capacity is a nice-to-have status pill; a failed fetch shouldn't disrupt the header.
      return null;
    }
    return (
      <span className="meta-pill capacity-pill" title={t("capacity.title")}>
        {loading ? <Loader2 size={13} className="spin" /> : <Gauge size={13} />}
        <span>{t("capacity.loading")}</span>
      </span>
    );
  }
  const { sessions, yours, gpu, local_exec: localExec, host } = capacity;
  const memLabel = typeof host.mem_used_pct === "number" ? `${host.mem_used_pct.toFixed(0)}%` : t("common.na");
  const gpuPill = formatQueuePill("GPU", gpu);
  const localExecPill = formatQueuePill("local-exec", localExec);
  // App-level auth: your own active/limit ("твои N/3"), when logged in.
  const yoursPill =
    yours && typeof yours.active === "number"
      ? `${t("capacity.yours")} ${yours.active}${yours.limit ? `/${yours.limit}` : ""}`
      : undefined;
  const title = [
    t("capacity.slotsTitle", { active: sessions.active, limit: sessions.limit, queued: sessions.queued }),
    yoursPill
      ? yours?.limit
        ? t("capacity.yoursTitle", { active: yours?.active ?? 0, limit: yours?.limit ?? 0 })
        : t("capacity.yoursTitleNoLimit", { active: yours?.active ?? 0 })
      : "",
    formatQueueTitle("GPU", gpu),
    formatQueueTitle(t("capacity.localExecutor"), localExec),
    t("capacity.hostLoad", { load1: host.load1.toFixed(2), cpu: host.cpu_count, mem: memLabel })
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <span className="meta-pill capacity-pill" title={title}>
      <Gauge size={13} />
      <span>
        {t("capacity.slots")} {sessions.active}/{sessions.limit}
        {sessions.queued > 0 ? ` · ${t("capacity.queue")} ${sessions.queued}` : ""}
        {yoursPill ? ` · ${yoursPill}` : ""}
        {gpuPill ? ` · ${gpuPill}` : ""}
        {localExecPill ? ` · ${localExecPill}` : ""}
        {` · ${t("capacity.load")} ${host.load1.toFixed(1)}/${host.cpu_count}`}
        {` · RAM ${memLabel}`}
      </span>
    </span>
  );
}

// Compact "GPU 0/1 · ждут 2" pill text for the header. A missing queue (older backend
// that doesn't report gpu/local_exec yet) renders nothing rather than a fake state.
function formatQueuePill(label: string, queue?: { busy: number; waiting: number; slots: number }) {
  if (!queue) {
    return undefined;
  }
  if (queue.slots <= 0) {
    // slots===0 means the resource lease isn't reporting capacity (down/misconfigured);
    // "0/0" would read as "free", so call it out instead of hiding it.
    return `${label} —`;
  }
  return `${label} ${queue.busy}/${queue.slots}${queue.waiting > 0 ? ` · ${t("capacity.waiting", { n: queue.waiting })}` : ""}`;
}

function formatQueueTitle(label: string, queue?: { busy: number; waiting: number; slots: number }) {
  if (!queue) {
    return undefined;
  }
  if (queue.slots <= 0) {
    return t("capacity.unavailable", { label });
  }
  const free = queue.slots - queue.busy;
  return `${t("capacity.busyOf", { label, busy: queue.busy, slots: queue.slots, free })}${queue.waiting > 0 ? `, ${t("capacity.waiting", { n: queue.waiting })}` : ""}.`;
}

function SessionSidebar({
  collapsed,
  sessions,
  selectedSessionId,
  search,
  loading,
  error,
  onSearch,
  onToggle,
  onNew,
  onSelect,
  onDelete,
  runInProgress
}: {
  collapsed: boolean;
  sessions: SessionListItem[];
  selectedSessionId?: string;
  search: string;
  loading: boolean;
  error: unknown;
  onSearch: (value: string) => void;
  onToggle: () => void;
  onNew: () => void;
  onSelect: (sessionId: string) => void;
  onDelete: (sessionId: string) => Promise<void>;
  runInProgress: boolean;
}) {
  // Two-step delete: the trash icon only arms the row, and a separate explicit
  // button commits. Deleting a run's artifacts is irreversible, so a single
  // stray click must never be enough.
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const commitDelete = async (sessionId: string) => {
    setBusyId(sessionId);
    setDeleteError(null);
    try {
      await onDelete(sessionId);
      setConfirmId(null);
    } catch (error) {
      const status = (error as { status?: number }).status;
      setDeleteError(status === 409 ? t("sidebar.deleteRunning") : t("sidebar.deleteFailed"));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <aside className="session-sidebar">
      <div className="sidebar-head">
        {!collapsed && <span>{t("sidebar.sessions")}</span>}
        <button className="icon-button" onClick={onToggle} title={collapsed ? t("sidebar.expand") : t("sidebar.collapse")}>
          {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
        </button>
      </div>

      {!collapsed && (
        <>
          <div className="sidebar-action">
            <button className="command-button start" type="button" disabled={runInProgress} onClick={onNew}>
              <Play size={15} />
              <span>{t("sidebar.newTask")}</span>
            </button>
          </div>

          <label className="search-box">
            <Search size={15} />
            <input value={search} onChange={(event) => onSearch(event.target.value)} placeholder={t("sidebar.searchSessions")} />
          </label>

          <div className="sidebar-list">
            {loading && <LoadingLine label={t("sidebar.loading")} />}
            {Boolean(error) && <ErrorLine label={t("sidebar.loadError")} />}
            {!loading && !error && sessions.length === 0 && <div className="empty-compact">{t("sidebar.noSessions")}</div>}
            {sessions.map((session) => (
              <div
                key={session.session_id}
                className={`session-row-wrap ${session.session_id === selectedSessionId ? "selected" : ""}`}
              >
                <button className="session-row" onClick={() => onSelect(session.session_id)}>
                  <span className="session-title">{session.title}</span>
                  <span className="session-subtitle">
                    {formatMode(session.mode)} — {t("sidebar.artifacts")}: {formatCount(session.artifact_counts)}
                  </span>
                </button>
                <button
                  className="session-delete"
                  title={t("sidebar.delete")}
                  aria-label={t("sidebar.delete")}
                  onClick={() => {
                    setDeleteError(null);
                    setConfirmId(confirmId === session.session_id ? null : session.session_id);
                  }}
                >
                  <Trash2 size={14} />
                </button>
                {confirmId === session.session_id && (
                  <div className="session-confirm">
                    <span className="session-confirm-text">{t("sidebar.deleteConfirm")}</span>
                    <span className="session-confirm-hint">{t("sidebar.deleteHint")}</span>
                    {deleteError && <span className="session-confirm-error">{deleteError}</span>}
                    <div className="session-confirm-actions">
                      <button
                        className="command-button danger"
                        disabled={busyId === session.session_id}
                        onClick={() => void commitDelete(session.session_id)}
                      >
                        {busyId === session.session_id ? <Loader2 size={14} className="spin" /> : <Trash2 size={14} />}
                        <span>{t("sidebar.deleteYes")}</span>
                      </button>
                      <button className="command-button" onClick={() => setConfirmId(null)}>
                        <span>{t("sidebar.deleteNo")}</span>
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </aside>
  );
}

function FileAttachments({ files, onChange, disabled }: {
  files: File[]; onChange: (files: File[]) => void; disabled: boolean;
}) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  return <div className="input-files">
    <input ref={fileInputRef} className="visually-hidden" type="file" multiple
      accept=".csv,.xls,.xlsx,.docx,.json,.jsonl,.png,.jpg,.jpeg,.pdf,image/png,image/jpeg,application/pdf"
      disabled={disabled} onChange={event => {
        const next = [...files];
        for (const file of Array.from(event.target.files ?? [])) {
          if (!next.some(item => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified)) next.push(file);
        }
        if (next.length !== files.length) onChange(next);
        event.target.value = "";
      }} />
    <button type="button" className="command-button attach-files" disabled={disabled}
      onClick={() => fileInputRef.current?.click()}><Paperclip size={15} /><span>{t("launch.attachFiles")}</span></button>
    {files.length > 0 && <div className="input-file-list" aria-label={t("launch.attachedFiles")}>
      {files.map((file, index) => <div className="input-file" key={`${file.name}-${file.size}-${file.lastModified}`}>
        <span title={file.name}>{file.name}</span><small>{formatBytes(file.size)}</small>
        <button type="button" className="icon-button" disabled={disabled} aria-label={t("launch.removeFile", { name: file.name })}
          onClick={() => onChange(files.filter((_, n) => n !== index))}><X size={14} /></button>
      </div>)}
    </div>}
  </div>;
}

function TaskLaunchPanel({
  onStart,
  disabled,
  selection,
  onSelectionChange,
  modelConfig,
  launchPhase,
  launchError,
  telemetry
}: {
  onStart: (request: RunRequest) => Promise<void>;
  disabled: boolean;
  selection: LaunchSelection;
  onSelectionChange: (selection: LaunchSelection) => void;
  modelConfig: LaunchModelConfig;
  launchPhase: "idle" | "uploading" | "starting";
  launchError: string | null;
  telemetry?: GpuTelemetry;
}) {
  // Name the executor options after the hardware the run will actually land on,
  // read from live telemetry rather than hardcoded -- the same UI ships to
  // deployments with completely different cards. Falls back to the generic
  // label when nothing has been probed yet.
  const localDevice = telemetry?.local?.cards?.[0]?.name;
  const remoteDevice = telemetry?.remote?.cards?.[0]?.name;
  const [prompt, setPrompt] = useState("");
  const [approvals, setApprovals] = useState<ApprovalDraft>({ ...DEFAULT_APPROVAL_DRAFT });
  const [files, setFiles] = useState<File[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const busy = launchPhase !== "idle";
  const canStart = prompt.trim().length > 0 && !disabled && !busy && isApprovalDraftValid(approvals);
  const choices = modelChoicesFor(selection.orchestrator, modelConfig);
  const defaultModel = defaultModelFor(selection.orchestrator, modelConfig);
  const displayedModel = modelLabel(effectiveModel(selection, modelConfig), selection.orchestrator, modelConfig);
  const selectedConfiguration = selectedModelConfig(selection, modelConfig);
  const selectedEffort = effectiveEffort(selection, modelConfig);

  const changeProfile = (researchProfile: ResearchProfile) => {
    onSelectionChange(selectResearchProfile(selection, researchProfile));
  };

  const applyExample = (example: LaunchExample) => {
    if (disabled || busy) return;
    if ((prompt.trim() || files.length) && !window.confirm("Заменить текущее задание и прикреплённые файлы выбранным примером?")) return;
    const next = selectResearchProfile(selection, example.profile);
    // Both profiles support all compute modes. Only a runtime switch may
    // require the existing model/effort compatibility reset.
    next.computeMode = selection.computeMode;
    onSelectionChange(next);
    setPrompt(example.prompt);
    setFiles(example.files.map(file => new File([file.content], file.name, { type: file.type })));
  };

  return (
    <form
      className="task-launch-panel"
      onSubmit={(event) => {
        event.preventDefault();
        if (!canStart) {
          return;
        }
        void onStart({
          prompt: prompt.trim(),
          mode: "research",
          compute_mode: selection.computeMode,
          model_name: effectiveModel(selection, modelConfig) ?? "",
          effort: effectiveEffort(selection, modelConfig),
          orchestrator: selection.orchestrator,
          research_profile: selection.researchProfile,
          files,
          approvals: approvalPolicyFromDraft(approvals)
        });
      }}
    >
      <div className="launch-copy">
        <h1>{t("launch.title")}</h1>
      </div>
      <div className="profile-selector" role="group" aria-label={t("launch.profileAria")}>
        {(["general", "chemistry"] as const).map((profile) => (
          <button
            key={profile}
            type="button"
            className={`profile-option ${selection.researchProfile === profile ? "active" : ""}`}
            onClick={() => changeProfile(profile)}
            disabled={disabled || busy}
          >
            {t(`profile.${profile}`)}
          </button>
        ))}
      </div>
      <p className="launch-profile-description">{t(`profileDescription.${selection.researchProfile}`)}</p>
      <textarea
        value={prompt}
        onChange={(event) => setPrompt(event.target.value)}
        placeholder={t("launch.placeholder")}
        rows={10}
        aria-label="Задание для исследования"
        aria-describedby="launch-prompt-guide"
        disabled={disabled || busy}
      />
      <p className="launch-prompt-guide" id="launch-prompt-guide">{t("launch.promptGuide")}</p>
      <LaunchExamples disabled={disabled || busy} onApply={applyExample} />
      <FileAttachments files={files} onChange={setFiles} disabled={disabled || busy} />
      {launchError && (
        <div className="launch-error" role="alert">
          <AlertTriangle size={16} />
          <span>{launchError}</span>
        </div>
      )}
      <div className="launch-controls">
        <div className="launch-selection-summary" aria-live="polite">
          <span>{formatResearchProfile(selection.researchProfile)}</span>
          <span>{formatOrchestrator(selection.orchestrator)}</span>
          <span>{formatComputeMode(selection.computeMode)}</span>
          <span>{t("launch.modelSummary", { model: formatModelName(displayedModel) })}{selectedEffort ? ` · ${tv("effort", selectedEffort)}` : ""}</span>
        </div>
        <div className="settings-menu run-settings-menu">
          <button
            type="button"
            className="icon-button settings-trigger"
            onClick={() => setSettingsOpen((value) => !value)}
            title={t("launch.settings")}
            aria-haspopup="menu"
            aria-expanded={settingsOpen}
            disabled={disabled || busy}
          >
            <Settings size={17} />
          </button>
          {settingsOpen && (
            <>
              <button className="settings-backdrop" aria-hidden tabIndex={-1} onClick={() => setSettingsOpen(false)} />
              <div className="settings-popover run-settings-popover" role="menu">
                <div className="settings-section-label">{t("launch.settings")}</div>
                <div className="composer-controls">
                  <select
                    value={selection.computeMode}
                    onChange={(event) =>
                      onSelectionChange({ ...selection, computeMode: event.target.value as ComputeMode })
                    }
                    disabled={disabled || busy}
                    aria-label={t("launch.executorAria")}
                    title={t("launch.executorTitle")}
                  >
                    <option value="Auto">{t("launch.executorAuto")}</option>
                    <option value="Local">
                      {localDevice ? t("launch.executorLocalNamed", { device: localDevice }) : t("launch.executorLocal")}
                    </option>
                    <option value="GPU">
                      {remoteDevice ? t("launch.executorGpuNamed", { device: remoteDevice }) : t("launch.executorGpu")}
                    </option>
                  </select>
                  <p className="compute-hint">{t(`launch.computeHint.${selection.computeMode}`)}</p>
                  <select
                    value={selection.modelName}
                    onChange={(event) => onSelectionChange({ ...selection, modelName: event.target.value })}
                    disabled={disabled || busy}
                    aria-label={t("launch.modelAria")}
                    title={t("launch.modelTitle")}
                  >
                    <option value="">{t("launch.modelDefault", { model: formatModelName(modelLabel(defaultModel, selection.orchestrator, modelConfig)) })}</option>
                    {choices
                      .filter((choice) => selection.orchestrator === "claude_code" || choice !== defaultModel)
                      .map((choice) => (
                      <option key={choice} value={choice}>{`${t("launch.modelPrefix")}: ${formatModelName(modelLabel(choice, selection.orchestrator, modelConfig))}`}</option>
                      ))}
                  </select>
                  {!!selectedConfiguration?.efforts.length && <label className="model-effort-select">
                    {t("launch.effort")}
                    <select aria-label={t("launch.effort")} value={selectedEffort ?? ""}
                      disabled={disabled || busy}
                      onChange={event => onSelectionChange({ ...selection, effort: event.target.value })}>
                      {selectedConfiguration.efforts.map(value => <option key={value} value={value}>{tv("effort", value)}</option>)}
                    </select>
                  </label>}
                  <select
                    value={selection.orchestrator}
                    onChange={(event) =>
                      onSelectionChange(selectOrchestrator(selection, event.target.value as Orchestrator))
                    }
                    disabled={disabled || busy || selection.researchProfile === "chemistry"}
                    aria-label={t("launch.orchestratorAria")}
                    title={t("launch.orchestratorTitle")}
                  >
                    <option value="codearkt">{t("orchestrator.codearkt")}</option>
                    <option value="claude_code">{t("orchestrator.claude_code")}</option>
                  </select>
                  {selection.orchestrator === "claude_code" && (
                    <ApprovalSettings draft={approvals} onChange={setApprovals} disabled={disabled || busy} />
                  )}
                </div>
              </div>
            </>
          )}
        </div>
        <button className="command-button start" type="submit" disabled={!canStart}>
          {busy ? <Loader2 size={15} className="spin" /> : <Play size={15} />}
          <span>
            {launchPhase === "uploading"
              ? t("launch.uploading")
              : launchPhase === "starting"
                ? t("launch.starting")
                : t("launch.start")}
          </span>
        </button>
      </div>
    </form>
  );
}

function NotebookTabs({
  visibleTabs,
  activeTab,
  onSelect
}: {
  visibleTabs: ArtifactTab[];
  activeTab: ArtifactTab;
  onSelect: (tab: ArtifactTab) => void;
}) {
  return (
    <nav className="tabs">
      {TABS.filter((tab) => visibleTabs.includes(tab.id)).map((tab) => {
        const Icon = tab.icon;
        return (
          <button
            key={tab.id}
            className={`tab-button ${activeTab === tab.id ? "active" : ""}`}
            onClick={() => onSelect(tab.id)}
          >
            <Icon size={16} />
            <span>{t(tab.labelKey)}</span>
          </button>
        );
      })}
    </nav>
  );
}

function TabContent({
  tab,
  summary,
  sessionId,
  artifacts,
  loading,
  error,
  liveState,
  onOpenArtifact
}: {
  tab: ArtifactTab;
  sessionId?: string;
  summary?: TabSummaries;
  artifacts: Artifact[];
  loading: boolean;
  error: unknown;
  liveState?: LiveRunState;
  onOpenArtifact: (artifact: Artifact) => void;
}) {
  if (tab === "run") return null;
  if (loading) {
    return <LoadingState label={t("notebook.loading")} />;
  }
  if (error) {
    return <ErrorState label={t("notebook.loadError")} />;
  }

  if (tab === "brief") {
    return <BriefTab summary={summary} artifacts={artifacts} onOpenArtifact={onOpenArtifact} />;
  }
  if (tab === "proposals") {
    return <ProposalTab summary={summary} artifacts={artifacts} onOpenArtifact={onOpenArtifact} />;
  }
  if (tab === "experiments") {
    return <ExperimentsTab sessionId={sessionId} summary={summary} artifacts={artifacts} onOpenArtifact={onOpenArtifact} />;
  }
  if (tab === "paper") {
    return <PaperTab sessionId={sessionId} summary={summary} artifacts={artifacts} onOpenArtifact={onOpenArtifact} />;
  }
  if (tab === "review") {
    return <ReviewTab sessionId={sessionId} summary={summary} artifacts={artifacts} onOpenArtifact={onOpenArtifact} />;
  }
  return <RunDetailsTab sessionId={sessionId} summary={summary} artifacts={artifacts} liveState={liveState} onOpenArtifact={onOpenArtifact} />;
}

function BriefTab({
  summary,
  artifacts,
  onOpenArtifact
}: {
  summary?: TabSummaries;
  artifacts: Artifact[];
  onOpenArtifact: (artifact: Artifact) => void;
}) {
  const sources = summary?.brief?.source_paths ?? [];
  return (
    <div className="tab-surface">
      <header className="tab-header">
        <div>
          <h1>{t("brief.title")}</h1>
          <p>{t("brief.meta", { papers: summary?.brief?.paper_count ?? 0, sources: (summary?.brief?.source_paths ?? []).length })}</p>
        </div>
      </header>

      {sources.length === 0 && <p className="history-note">{t("brief.empty")}</p>}

      {(summary?.brief?.markdown_sources ?? []).map((source) => (
        <section className="content-block" key={source.path}>
          <h2>{source.path}</h2>
          <MarkdownPreview content={source.content} />
        </section>
      ))}

      <ArtifactGrid artifacts={artifacts.filter((artifact) => artifact.tab === "brief" && artifact.type !== "prompt")} onOpenArtifact={onOpenArtifact} />
    </div>
  );
}

function ProposalTab({
  summary,
  artifacts,
  onOpenArtifact
}: {
  summary?: TabSummaries;
  artifacts: Artifact[];
  onOpenArtifact: (artifact: Artifact) => void;
}) {
  const proposalArtifacts = artifacts.filter((artifact) => artifact.tab === "proposals");
  const proposals = summary?.proposals?.proposals ?? [];
  return (
    <div className="tab-surface">
      <header className="tab-header">
        <div>
          <h1>{t("proposals.title")}</h1>
          <p>{t("proposals.meta", { count: summary?.proposals?.proposal_count ?? proposalArtifacts.length })}</p>
        </div>
      </header>
      {proposals.length > 0 && (
        <div className="proposal-grid">
          {proposals.map((proposal, index) => (
            <article className="proposal-card" key={`${proposal.source_path}-${proposal.proposal_id ?? index}`}>
              <h2>{proposal.title ?? t("proposals.item", { index: index + 1 })}</h2>
              {proposal.summary && <p>{proposal.summary}</p>}
              {proposal.error ? (
                <ErrorLine label={proposal.error} />
              ) : (
                <div className="score-row">
                  {Object.entries(proposal.scores ?? {}).map(([score, value]) => (
                    <span className="score-pill" key={score}>
                      {score}: {formatCell(value)}
                    </span>
                  ))}
                </div>
              )}
              <small>{proposal.source_path}</small>
            </article>
          ))}
        </div>
      )}
      <ArtifactGrid artifacts={proposalArtifacts} onOpenArtifact={onOpenArtifact} />
    </div>
  );
}

function ExperimentsTab({
  sessionId,
  summary,
  artifacts,
  onOpenArtifact
}: {
  sessionId?: string;
  summary?: TabSummaries;
  artifacts: Artifact[];
  onOpenArtifact: (artifact: Artifact) => void;
}) {
  const metrics = summary?.experiments?.metrics_tables ?? [];
  return (
    <div className="tab-surface">
      <header className="tab-header">
        <div>
          <h1>{t("experiments.title")}</h1>
          <p>{t("experiments.meta", { metrics: metrics.length, summaries: (summary?.experiments?.summaries ?? []).length })}</p>
        </div>
      </header>

      {sessionId && <ArtifactFigures sessionId={sessionId}
        artifacts={artifacts.filter(artifact => artifact.tab === "experiments")}
        title={t("experiments.figures")} onOpenArtifact={onOpenArtifact} />}

      {metrics.map((table) => (
        <section className="content-block" key={table.source_path}>
          <h2>{table.source_path}</h2>
          {table.error ? <ErrorLine label={table.error} /> : <DataTable rows={table.rows} />}
        </section>
      ))}

      <ExperimentSummaries summaries={summary?.experiments?.summaries ?? []} artifacts={artifacts} onOpenArtifact={onOpenArtifact}
        renderSummary={(content, sourcePath) => (
          <DocumentLinkContext.Provider value={sessionId
            ? (href) => documentLink(sessionId, sourcePath, href) : sanitizeMarkdownHref}>
            <MarkdownPreview content={content} />
          </DocumentLinkContext.Provider>
        )} />

      {(summary?.experiments?.log_phases ?? []).length > 0 && (
        <details className="content-block experiment-legacy-log">
          <summary>{t("experiments.legacyLog")}</summary>
          <DataTable rows={summary?.experiments?.log_phases} />
        </details>
      )}

      <ArtifactGrid artifacts={artifacts.filter((artifact) => artifact.tab === "experiments")} onOpenArtifact={onOpenArtifact} />
    </div>
  );
}

function PaperTab({
  sessionId,
  summary,
  artifacts,
  onOpenArtifact
}: {
  sessionId?: string;
  summary?: TabSummaries;
  artifacts: Artifact[];
  onOpenArtifact: (artifact: Artifact) => void;
}) {
  const document = summary?.paper?.document;
  const pdf = paperPdf(artifacts, document);
  const pdfUrl = sessionId && pdf ? `${rawArtifactUrl(sessionId, pdf.path)}&v=${encodeURIComponent(pdf.version ?? pdf.modified_at)}` : undefined;
  return (
    <div className="tab-surface">
      <header className="tab-header">
        <div>
          <h1>{t("paper.title")}</h1>
        </div>
      </header>
      {document?.status === "error" ? (
        <p className="empty-compact" role="status">{t("paper.registrationError")}</p>
      ) : <>
        {document && (document.status !== "ready" || pdf) && (
          <p className="empty-compact" role="status">{t(
            document.status === "ready" && document.changed_since_check
              ? "paper.changedSinceCheck" : `paper.status.${document.status}`
          )}</p>
        )}
        {document?.error && <p className="empty-compact">{document.error}</p>}
        {!pdf && <p className="empty-compact">{t("paper.missingPdf")}</p>}
        {!pdf && document?.pdf_path && (
          <p className="empty-compact">{t("paper.expectedFile", { path: document.pdf_path })}</p>
        )}
      </>}
      {pdf && pdfUrl && (
        <section className="content-block">
          <h2>{pdf.path}</h2>
          <p>{t("paper.latestPdf")}</p>
          <div className="paper-pdf-shell">
            <div className="preview-actions">
              <a className="download-link" href={pdfUrl} target="_blank" rel="noreferrer">
                <Download size={15} />
                <span>{t("paper.openPdf")}</span>
              </a>
            </div>
            <iframe className="paper-inline-pdf" title={pdf.path} src={pdfUrl} />
          </div>
        </section>
      )}
      {sessionId && <ArtifactFigures sessionId={sessionId}
        artifacts={artifacts.filter(artifact => artifact.tab === "paper")}
        title={t("paper.figures")} onOpenArtifact={onOpenArtifact} />}
      <ArtifactGrid artifacts={artifacts.filter((artifact) => artifact.tab === "paper")} onOpenArtifact={onOpenArtifact} />
    </div>
  );
}

function ArtifactFigures({ sessionId, artifacts, title, onOpenArtifact }: {
  sessionId: string;
  artifacts: Artifact[];
  title: string;
  onOpenArtifact: (artifact: Artifact) => void;
}) {
  const figures = artifacts.filter(artifact => artifact.preview_kind === "image").slice(0, 6);
  if (!figures.length) return null;
  return (
    <section className="content-block">
      <h2>{title}</h2>
      <div className="figure-strip">
        {figures.map(figure => (
          <button className="figure-thumb" key={figure.path} onClick={() => onOpenArtifact(figure)}>
            <img src={rawArtifactUrl(sessionId, figure.path)} alt={figure.name} loading="lazy" />
            <span>{figure.name}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function ReviewTab({
  sessionId,
  summary,
  artifacts,
  onOpenArtifact
}: {
  sessionId?: string;
  summary?: TabSummaries;
  artifacts: Artifact[];
  onOpenArtifact: (artifact: Artifact) => void;
}) {
  const hasStructuredReview = summary?.review?.review_json != null;
  const document = summary?.review?.markdown_review;
  return (
    <div className="tab-surface">
      <header className="tab-header">
        <div>
          <h1>{t("review.title")}</h1>
          {hasStructuredReview && <p>
            {t("review.meta", {
              score: summary?.review?.overall_score ?? t("review.pending"),
              rec: summary?.review?.recommendation ?? t("review.noDecision")
            })}
          </p>}
          {summary?.review?.confidence != null && <p>{t("review.confidence", { score: summary.review.confidence })}</p>}
        </div>
      </header>
      {hasStructuredReview && <div className="review-columns">
        <ReviewList title={t("review.strengths")} items={summary?.review?.strengths ?? []} />
        <ReviewList title={t("review.weaknesses")} items={summary?.review?.weaknesses ?? []} />
        <ReviewList title={t("review.requiredFixes")} items={summary?.review?.required_fixes ?? []} />
        <ReviewList title={t("review.questions")} items={summary?.review?.questions ?? []} />
        <ReviewList title={t("review.limitations")} items={summary?.review?.limitations ?? []} />
        <ReviewList title={t("review.formatIssues")} items={summary?.review?.format_issues ?? []} />
      </div>}
      {!hasStructuredReview && document && (
        <section className="content-block">
          <DocumentLinkContext.Provider value={sessionId
            ? href => documentLink(sessionId, document.path, href) : sanitizeMarkdownHref}>
            <MarkdownPreview content={document.content} />
          </DocumentLinkContext.Provider>
          {document.truncated && sessionId && (
            <a href={rawArtifactUrl(sessionId, document.path)} target="_blank" rel="noreferrer">{document.path}</a>
          )}
        </section>
      )}
      <ArtifactGrid artifacts={artifacts.filter((artifact) => artifact.tab === "review")} onOpenArtifact={onOpenArtifact} />
    </div>
  );
}

function ReviewList({ title, items }: { title: string; items: string[] }) {
  return (
    <section className="review-list">
      <h2>{title}</h2>
      {items.length ? (
        <ul>
          {items.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      ) : (
        <div className="empty-compact">{t("review.noItems")}</div>
      )}
    </section>
  );
}

function RunDetailsTab({
  sessionId,
  summary,
  artifacts,
  liveState,
  runContext,
  visible = true,
  onOpenArtifact
}: {
  sessionId?: string;
  summary?: TabSummaries;
  artifacts: Artifact[];
  liveState?: LiveRunState;
  runContext?: ReactNode;
  visible?: boolean;
  onOpenArtifact: (artifact: Artifact) => void;
}) {
  const run = summary?.run;
  const activity = useMemo(() => liveState ? activityFromState(liveState) : { events: [], legacy: false }, [liveState]);
  const runArtifacts = artifacts.filter((artifact) => artifact.tab === "run");
  return (
    <div className="tab-surface">
      <header className="tab-header">
        <div>
          <h1>{t("run.title")}</h1>
          <p>
            {t("run.meta", { artifacts: runArtifacts.length, traces: (run?.trace_exports ?? []).length })}
            {liveState ? t("run.liveEvents", { n: (liveState.recordedEvents ?? liveState.events).length }) : ""}
          </p>
        </div>
      </header>
      {liveState?.status === "queued" && <QueuedBanner liveState={liveState} />}
      {liveState?.error && <ErrorLine label={liveState.error} />}
      <ActivityFeed events={activity.events} legacy={activity.legacy} terminal={liveState?.status} visible={visible}
        managerContext={runContext} renderContent={content => <MarkdownPreview content={content} />} />
      {(summary?.run?.trace_exports ?? []).length > 0 && (
        <section className="content-block">
          <h2>{t("run.langfuseExports")}</h2>
          <div className="artifact-grid">
            {(summary?.run?.trace_exports ?? []).map((path) => {
              const artifact = artifacts.find((item) => item.path === path);
              return artifact ? (
                <button key={path} className="artifact-tile" onClick={() => onOpenArtifact(artifact)}>
                  <ArtifactIcon artifact={artifact} />
                  <span>{path}</span>
                  <small>{artifact.type}</small>
                </button>
              ) : (
                <a className="artifact-tile link-tile" key={path} href={sessionId ? rawArtifactUrl(sessionId, path) : "#"}>
                  <FileJson size={16} />
                  <span>{path}</span>
                  <small>{t("run.traceExport")}</small>
                </a>
              );
            })}
          </div>
        </section>
      )}
      <ArtifactGrid artifacts={runArtifacts} onOpenArtifact={onOpenArtifact} />
    </div>
  );
}

function QueuedBanner({ liveState }: { liveState: LiveRunState }) {
  return (
    <div className="queued-banner">
      <Clock size={16} />
      <span>{t("queued.banner", { label: formatQueuedLabel(liveState) })}</span>
    </div>
  );
}

function isToolNoise(content: string) {
  const trimmed = cleanToolOutput(content).trim();
  return !trimmed || /^\[tool-start\]\s+/i.test(trimmed) || /^\[tool-end\]\s+executor call completed/i.test(trimmed);
}

function cleanToolOutput(content: string) {
  const normalized = normalizeStreamText(content);
  return normalized
    .split(/\r?\n/)
    .map((line) => {
      const trimmed = line.trim();
      if (/^(?:Output|Last expression):\s*$/i.test(trimmed)) {
        return "";
      }
      return line.replace(/^(\s*)(?:Output|Last expression):\s*/i, "$1");
    })
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function normalizeStreamText(content: string) {
  const text = content.replace(/\r\n/g, "\n").trim();
  const lines = text.split("\n");
  const tokenLines = lines.filter((line) => line.trim());
  const nonEmpty = tokenLines.map((line) => line.trim());
  if (nonEmpty.length < 16) {
    return text;
  }

  const shortLineRatio = nonEmpty.filter((line) => line.length <= 18).length / nonEmpty.length;
  const singleTokenRatio = nonEmpty.filter((line) => !/\s/.test(line) && line.length <= 18).length / nonEmpty.length;
  if (shortLineRatio < 0.75 || singleTokenRatio < 0.7) {
    return text;
  }

  return tokenLines
    .reduce(joinStreamLine, "")
    .replace(/\s+([,.;:!?%)\]}])/g, "$1")
    .replace(/([\[{(])\s+/g, "$1")
    .trim();
}

function joinStreamLine(prior: string, line: string) {
  const token = line.trim();
  if (!prior) {
    return token;
  }
  if (!/^\s/.test(line)) {
    return `${prior}${token}`;
  }
  if (shouldAttachToken(prior, token)) {
    return `${prior}${token}`;
  }
  return `${prior} ${token}`;
}

function shouldAttachToken(prior: string, token: string) {
  if (/^[,.;:!?%)\]}>]$/.test(token)) {
    return true;
  }
  if (/^[,.;:!?%)\]}>]/.test(token)) {
    return true;
  }
  if (/[\[{(<]$/.test(prior)) {
    return true;
  }
  if (prior.endsWith("_") || token.startsWith("_")) {
    return true;
  }
  if (prior.endsWith("/") || token.startsWith("/") || token.startsWith(".")) {
    return true;
  }
  if (prior.endsWith("#")) {
    return true;
  }
  if (prior.endsWith("*") || token.startsWith("*")) {
    return true;
  }
  if (/^['"]$/.test(token) || /['"]$/.test(prior)) {
    return true;
  }
  return false;
}

function ResearchRunPanel({
  detail,
  liveState,
  iteration,
  hasHistory,
  loading
}: {
  detail?: { active_agent: string | null; stages: Stage[] } | null;
  liveState?: LiveRunState;
  iteration?: IterationDetail;
  hasHistory?: boolean;
  loading: boolean;
}) {
  const baseStages = optimisticStages(iteration?.kind === "legacy" ? detail?.stages : undefined, undefined);
  const stages = iteration && liveState
    ? iterationStages(baseStages, iteration, liveState)
    : hasHistory ? baseStages : optimisticStages(detail?.stages, liveState);
  const activeAgent = liveState?.currentAgent ?? (hasHistory ? undefined : detail?.active_agent) ?? t("common.none");
  return (
    <aside className="run-panel">
      <div className="panel-title">
        <span>{t("runPanel.run")}</span>
        {loading && <Loader2 size={15} className="spin" />}
      </div>
      {liveState?.status === "queued" && <QueuedBanner liveState={liveState} />}
      <div className="active-agent">
        <span>{t("runPanel.currentAgent")}</span>
        <strong>{liveState?.status === "queued" ? t("runPanel.waitingSlot") : activeAgent}</strong>
      </div>
      <div className="stage-list">
        {stages.map((stage) => (
          <div className={`stage-row stage-${stage.status}`} key={stage.id} data-stage={stage.id} data-status={stage.status}>
            {stage.status === "complete" ? (
              <CheckCircle2 size={17} className="status-complete" />
            ) : stage.status === "running" ? (
              <Activity size={17} className="status-running" />
            ) : stage.status === "error" || stage.status === "cancelled" || stage.status === "limited" ? (
              <AlertTriangle size={17} className="status-error" />
            ) : (
              <Circle size={17} className="status-pending" />
            )}
            <div>
              <span>{formatStageLabel(stage.label)}</span>
              <small>
                {stage.agent} — {hasHistory ? tv("stageStatus", stage.status) : `${t("sidebar.artifacts")}: ${stage.artifact_count}`}
              </small>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}

function ArtifactDrawer({
  open,
  artifacts,
  pinnedArtifacts,
  search,
  type,
  onSearch,
  onType,
  onToggle,
  onOpenArtifact
}: {
  open: boolean;
  artifacts: Artifact[];
  pinnedArtifacts: Artifact[];
  search: string;
  type: string;
  onSearch: (value: string) => void;
  onType: (value: string) => void;
  onToggle: () => void;
  onOpenArtifact: (artifact: Artifact) => void;
}) {
  const artifactTypes = useMemo(() => Array.from(new Set(artifacts.map((artifact) => artifact.type))).sort(), [artifacts]);
  const filteredArtifacts = artifacts.filter((artifact) => {
    const matchesSearch = artifact.path.toLowerCase().includes(search.toLowerCase());
    const matchesType = type === "all" || artifact.type === type;
    return matchesSearch && matchesType;
  });

  return (
    <footer className={`artifact-drawer ${open ? "open" : ""}`}>
      <div className="artifact-strip">
        <span className="strip-label">{t("drawer.artifacts")}</span>
        <div className="pinned-row">
          {pinnedArtifacts.map((artifact) => (
            <button key={artifact.path} className="artifact-chip" onClick={() => onOpenArtifact(artifact)} title={artifact.path}>
              <ArtifactIcon artifact={artifact} />
              <span>{artifact.name}</span>
            </button>
          ))}
        </div>
        <button className="command-button" onClick={onToggle}>
          {open ? <ChevronRight size={15} /> : <ChevronLeft size={15} />}
          <span>{t("drawer.allArtifacts")}</span>
        </button>
      </div>

      {open && (
        <div className="drawer-body">
          <div className="drawer-controls">
            <label className="search-box">
              <Search size={15} />
              <input value={search} onChange={(event) => onSearch(event.target.value)} placeholder={t("drawer.searchArtifacts")} />
            </label>
            <select value={type} onChange={(event) => onType(event.target.value)}>
              <option value="all">{t("drawer.allTypes")}</option>
              {artifactTypes.map((artifactType) => (
                <option key={artifactType} value={artifactType}>
                  {artifactType}
                </option>
              ))}
            </select>
          </div>
          <ArtifactBrowser artifacts={filteredArtifacts} search={search} showSearch={false} onOpenArtifact={onOpenArtifact} />
        </div>
      )}
    </footer>
  );
}

function PreviewModal({
  sessionId,
  path,
  preview,
  loading,
  error,
  onClose
}: {
  sessionId?: string;
  path: string | null;
  preview?: PreviewPayload;
  loading: boolean;
  error: unknown;
  onClose: () => void;
}) {
  if (!path) {
    return null;
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section className="preview-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
        <header className="preview-head">
          <div>
            <h2>{path}</h2>
            <p>{preview ? `${formatPreviewPayloadKind(preview)} - ${formatBytes(preview.size_bytes)}` : t("common.loading")}</p>
          </div>
          <button className="icon-button" onClick={onClose} title={t("preview.close")}>
            <X size={18} />
          </button>
        </header>
        <div className="preview-body">
          {loading && <LoadingState label={t("preview.loading")} />}
          {Boolean(error) && <ErrorState label={t("preview.loadError")} />}
          {!loading && !error && preview && (
            <DocumentLinkContext.Provider value={sessionId
              ? href => documentLink(sessionId, preview.path, href) : sanitizeMarkdownHref}>
              <PreviewBody preview={preview} />
            </DocumentLinkContext.Provider>
          )}
        </div>
      </section>
    </div>
  );
}

function LoginModal({
  onSubmit,
  onClose
}: {
  onSubmit: (username: string, password: string) => Promise<void>;
  onClose: () => void;
}) {
  const [loginName, setLoginName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const canSubmit = loginName.trim().length > 0 && password.length > 0 && !submitting;

  const submit = async () => {
    if (!canSubmit) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(loginName.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("login.failed"));
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="login-modal"
        role="dialog"
        aria-modal="true"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="login-head">
          <div>
            <h2>{t("login.title")}</h2>
            <p>{t("login.subtitle")}</p>
          </div>
          <button className="icon-button" onClick={onClose} title={t("login.close")}>
            <X size={18} />
          </button>
        </header>
        <form
          className="login-form"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label>
            <span>{t("login.loginLabel")}</span>
            <input
              type="text"
              value={loginName}
              autoFocus
              autoComplete="username"
              onChange={(event) => setLoginName(event.target.value)}
              placeholder="a.stepin"
              disabled={submitting}
            />
          </label>
          <label>
            <span>{t("login.passwordLabel")}</span>
            <input
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(event) => setPassword(event.target.value)}
              disabled={submitting}
            />
          </label>
          {error && <div className="login-error">{error}</div>}
          <button className="command-button start" type="submit" disabled={!canSubmit}>
            {submitting ? <Loader2 size={15} className="spin" /> : <LogIn size={15} />}
            <span>{t("login.submit")}</span>
          </button>
        </form>
      </section>
    </div>
  );
}

function PreviewBody({ preview }: { preview: PreviewPayload }) {
  if (preview.kind === "image") {
    return <img className="image-preview" src={preview.url} alt={preview.path} />;
  }
  if (preview.kind === "pdf") {
    return (
      <div className="pdf-frame">
        <div className="preview-actions">
          <a className="download-link" href={preview.url} target="_blank" rel="noreferrer">
            <Download size={15} />
            <span>{t("preview.download")}</span>
          </a>
        </div>
        <iframe className="pdf-preview" title={preview.path} src={preview.url} />
      </div>
    );
  }
  if (preview.kind === "json") {
    return <JsonTree value={preview.content} />;
  }
  if (preview.kind === "markdown") {
    return <MarkdownPreviewPanel content={preview.content} copyable />;
  }
  if (preview.kind === "text") {
    if (isSourcePreviewPath(preview.path)) {
      return <SourcePreview content={preview.content} path={preview.path} />;
    }
    return <MarkdownPreviewPanel content={preview.content} copyable />;
  }
  if (preview.kind === "csv") {
    return <CsvPreview content={preview.content} />;
  }
  if ("content" in preview) {
    return <pre className="code-preview">{preview.content}</pre>;
  }
  return <div className="empty-compact">{t("preview.binary")}</div>;
}

function MarkdownPreviewPanel({ content, copyable = false }: { content: string; copyable?: boolean }) {
  const [copied, setCopied] = useState(false);

  const copyContent = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="markdown-panel">
      {copyable && (
        <button className="markdown-copy-button" type="button" onClick={() => void copyContent()} title={copied ? t("copy.copied") : t("copy.copy")}>
          {copied ? <CheckCircle2 size={15} /> : <Copy size={15} />}
        </button>
      )}
      <MarkdownPreview content={content} />
    </div>
  );
}

function SourcePreview({ content, path }: { content: string; path: string }) {
  const language = sourceLanguage(path);
  const lines = content.split(/\r?\n/);
  return (
    <div className={`source-preview source-${language}`}>
      <div className="source-preview-head">
        <span>{sourceLanguageLabel(language)}</span>
        <span>{t("source.lines", { n: lines.length })}</span>
      </div>
      <pre className="source-code" aria-label={path}>
        {lines.map((line, index) => (
          <span className="source-line" key={index}>
            <span className="source-line-number">{index + 1}</span>
            <code>{highlightSourceLine(line, language)}</code>
          </span>
        ))}
      </pre>
    </div>
  );
}

type SourceLanguage = "bibtex" | "code" | "latex";

function highlightSourceLine(line: string, language: SourceLanguage): ReactNode[] {
  if (language === "latex") {
    return tokenizeSourceLine(line, /(%.*$|\\[a-zA-Z@]+|\\.|[$&_^{}[\]])/g, classifyLatexToken);
  }
  if (language === "bibtex") {
    return tokenizeSourceLine(
      line,
      /(%.*$|@\w+|"(?:\\.|[^"\\])*"|\b(?:author|booktitle|doi|journal|pages|publisher|title|url|volume|year)\b|[{}=,])/gi,
      classifyBibtexToken
    );
  }
  return tokenizeSourceLine(
    line,
    /(#.*$|\/\/.*$|\/\*.*?\*\/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\b(?:async|await|class|const|def|elif|else|except|export|finally|for|from|function|if|import|in|interface|let|return|try|type|while|with|yield)\b|\b\d+(?:\.\d+)?\b)/g,
    classifyCodeToken
  );
}

function tokenizeSourceLine(line: string, pattern: RegExp, classify: (token: string) => string | undefined): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(line)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(line.slice(lastIndex, match.index));
    }
    const token = match[0];
    nodes.push(
      <span className={classify(token)} key={`${match.index}-${token}`}>
        {token}
      </span>
    );
    lastIndex = match.index + token.length;
    if (token.length === 0) {
      pattern.lastIndex += 1;
    }
  }
  if (lastIndex < line.length) {
    nodes.push(line.slice(lastIndex));
  }
  return nodes.length ? nodes : [line || " "];
}

function classifyCodeToken(token: string) {
  if (/^(#|\/\/|\/\*)/.test(token)) {
    return "source-token-comment";
  }
  if (/^["'`]/.test(token)) {
    return "source-token-string";
  }
  if (/^\d/.test(token)) {
    return "source-token-number";
  }
  return "source-token-keyword";
}

function classifyLatexToken(token: string) {
  if (token.startsWith("%")) {
    return "source-token-comment";
  }
  if (token.startsWith("\\")) {
    return "source-token-command";
  }
  return "source-token-punctuation";
}

function classifyBibtexToken(token: string) {
  if (token.startsWith("%")) {
    return "source-token-comment";
  }
  if (token.startsWith("@")) {
    return "source-token-command";
  }
  if (token.startsWith("\"")) {
    return "source-token-string";
  }
  if (/^[{}=,]$/.test(token)) {
    return "source-token-punctuation";
  }
  return "source-token-keyword";
}

function JsonTree({ value }: { value: unknown }) {
  return (
    <div className="json-tree">
      <JsonNode name={t("json.root")} value={value} depth={0} />
    </div>
  );
}

function JsonNode({ name, value, depth }: { name: string; value: unknown; depth: number }) {
  if (Array.isArray(value)) {
    return (
      <details open={depth < 2}>
        <summary>
          <span className="json-key">{name}</span>
          <span className="json-muted">{t("json.array", { n: value.length })}</span>
        </summary>
        <div className="json-children">
          {value.map((item, index) => (
            <JsonNode key={index} name={String(index)} value={item} depth={depth + 1} />
          ))}
        </div>
      </details>
    );
  }

  if (isRecord(value)) {
    const entries = Object.entries(value);
    return (
      <details open={depth < 2}>
        <summary>
          <span className="json-key">{name}</span>
          <span className="json-muted">{t("json.object", { n: entries.length })}</span>
        </summary>
        <div className="json-children">
          {entries.map(([key, item]) => (
            <JsonNode key={key} name={key} value={item} depth={depth + 1} />
          ))}
        </div>
      </details>
    );
  }

  return (
    <div className="json-leaf">
      <span className="json-key">{name}</span>
      <span>{formatCell(value)}</span>
    </div>
  );
}

export function MarkdownPreview({ content }: { content: string }) {
  const lines = content.split(/\r?\n/);
  const elements: ReactElement[] = [];
  let paragraph: string[] = [];
  let listItems: string[] = [];
  let listKind: "ol" | "ul" | null = null;
  let codeLines: string[] = [];
  let quoteLines: string[] = [];
  let tableRows: string[][] = [];
  let inCode = false;

  const flushParagraph = () => {
    if (paragraph.length) {
      elements.push(<p key={`p-${elements.length}`}>{renderInlineMarkdown(paragraph.join(" "))}</p>);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (listItems.length) {
      const ListTag = listKind === "ol" ? "ol" : "ul";
      elements.push(
        <ListTag key={`list-${elements.length}`}>
          {listItems.map((item, index) => (
            <li key={index}>{renderInlineMarkdown(item, `li-${elements.length}-${index}`)}</li>
          ))}
        </ListTag>
      );
      listItems = [];
      listKind = null;
    }
  };
  const flushCode = () => {
    elements.push(
      <pre className="code-preview compact" key={`code-${elements.length}`}>
        {codeLines.join("\n")}
      </pre>
    );
    codeLines = [];
  };
  const flushQuote = () => {
    if (quoteLines.length) {
      elements.push(
        <blockquote key={`quote-${elements.length}`}>
          <p>{renderInlineMarkdown(quoteLines.join(" "))}</p>
        </blockquote>
      );
      quoteLines = [];
    }
  };
  const flushTable = () => {
    if (!tableRows.length) {
      return;
    }
    const hasSeparator = tableRows.length > 1 && tableRows[1].every(isMarkdownTableSeparatorCell);
    const header = tableRows[0];
    const body = tableRows.slice(hasSeparator ? 2 : 1);
    elements.push(
      <div className="markdown-table-wrap" key={`table-${elements.length}`}>
        <table>
          <thead>
            <tr>
              {header.map((cell, index) => (
                <th key={index}>{renderInlineMarkdown(cell, `th-${elements.length}-${index}`)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {header.map((_, columnIndex) => (
                  <td key={columnIndex}>{renderInlineMarkdown(row[columnIndex] ?? "", `td-${elements.length}-${rowIndex}-${columnIndex}`)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
    tableRows = [];
  };
  const flushBlocks = () => {
    flushParagraph();
    flushList();
    flushQuote();
    flushTable();
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.startsWith("```")) {
      if (inCode) {
        flushCode();
      } else {
        flushBlocks();
      }
      inCode = !inCode;
      continue;
    }

    if (inCode) {
      codeLines.push(line);
      continue;
    }

    const trimmed = line.trim();
    if (!trimmed) {
      flushBlocks();
      continue;
    }

    const startsTable = isMarkdownTableLine(trimmed) && index + 1 < lines.length && isMarkdownTableSeparatorLine(lines[index + 1].trim());
    if (startsTable || (tableRows.length > 0 && isMarkdownTableLine(trimmed))) {
      flushParagraph();
      flushList();
      flushQuote();
      tableRows.push(splitMarkdownTableRow(trimmed));
      continue;
    }
    flushTable();

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushBlocks();
      elements.push(<hr key={`hr-${elements.length}`} />);
      continue;
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(trimmed);
    if (heading) {
      flushBlocks();
      const level = heading[1].length;
      const text = heading[2];
      if (level === 1) {
        elements.push(<h1 key={`h-${elements.length}`}>{renderInlineMarkdown(text)}</h1>);
      } else if (level === 2) {
        elements.push(<h2 key={`h-${elements.length}`}>{renderInlineMarkdown(text)}</h2>);
      } else {
        elements.push(<h3 key={`h-${elements.length}`}>{renderInlineMarkdown(text)}</h3>);
      }
      continue;
    }

    const quote = /^>\s?(.*)$/.exec(trimmed);
    if (quote) {
      flushParagraph();
      flushList();
      quoteLines.push(quote[1]);
      continue;
    }

    const listItem = /^([-*]|\d+\.)\s+(.+)$/.exec(trimmed);
    if (listItem) {
      flushParagraph();
      flushQuote();
      const nextKind = /\d+\./.test(listItem[1]) ? "ol" : "ul";
      if (listKind && listKind !== nextKind) {
        flushList();
      }
      listKind = nextKind;
      listItems.push(listItem[2]);
      continue;
    }

    flushList();
    flushQuote();
    paragraph.push(trimmed);
  }

  if (inCode) {
    flushCode();
  }
  flushBlocks();

  return <div className="markdown-preview">{elements}</div>;
}

function renderInlineMarkdown(text: string, keyPrefix = "inline"): ReactNode[] {
  const nodes: ReactNode[] = [];
  const tokenPattern = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|!?\[[^\]]*\]\([^)]+\))/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = tokenPattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }

    const token = match[0];
    const key = `${keyPrefix}-${nodes.length}`;
    if (token.startsWith("`")) {
      nodes.push(
        <code className="inline-code" key={key}>
          {token.slice(1, -1)}
        </code>
      );
    } else if (token.startsWith("**")) {
      nodes.push(<strong key={key}>{renderInlineMarkdown(token.slice(2, -2), key)}</strong>);
    } else if (token.startsWith("*")) {
      nodes.push(<em key={key}>{renderInlineMarkdown(token.slice(1, -1), key)}</em>);
    } else if (token.startsWith("!")) {
      const image = /^!\[([^\]]*)\]\(([^)]+)\)$/.exec(token);
      if (image) nodes.push(<DocumentImage href={image[2]} alt={image[1]} key={key} />);
    } else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      if (link) {
        nodes.push(
          <DocumentLink href={link[2]} key={key}>
            {renderInlineMarkdown(link[1], key)}
          </DocumentLink>
        );
      } else {
        nodes.push(token);
      }
    }

    lastIndex = match.index + token.length;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }
  return nodes;
}

function DocumentLink({ href, children }: { href: string; children: ReactNode }) {
  const resolve = useContext(DocumentLinkContext);
  const target = resolve(href);
  if (!target || target === "#") return <span>{children}</span>;
  return <a href={target} target="_blank" rel="noreferrer">{children}</a>;
}

function DocumentImage({ href, alt }: { href: string; alt: string }) {
  const resolve = useContext(DocumentLinkContext);
  const src = resolve(href);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const label = alt || t("markdown.image");
  // Reuse document-relative resolution and reject non-web schemes before loading.
  if (!src || src.startsWith("#")) return <span>{label}</span>;
  try {
    if (!["http:", "https:"].includes(new URL(src, window.location.href).protocol)) {
      return <span>{label}</span>;
    }
  } catch {
    return <span>{label}</span>;
  }
  return (
    <a className="markdown-image-link" href={src} target="_blank" rel="noreferrer">
      {failedSrc === src ? label : (
        <img className="markdown-image" src={src} alt={label} loading="lazy" decoding="async"
          referrerPolicy="no-referrer" onError={() => setFailedSrc(src)} />
      )}
    </a>
  );
}

function sanitizeMarkdownHref(href: string) {
  const trimmed = href.trim();
  if (/^(https?:|mailto:|#|\/)/i.test(trimmed) || /^[\w./-]+$/.test(trimmed)) {
    return trimmed;
  }
  return "#";
}

function isMarkdownTableLine(line: string) {
  return line.includes("|") && splitMarkdownTableRow(line).length > 1;
}

function isMarkdownTableSeparatorLine(line: string) {
  return isMarkdownTableLine(line) && splitMarkdownTableRow(line).every(isMarkdownTableSeparatorCell);
}

function isMarkdownTableSeparatorCell(cell: string) {
  return /^:?-{3,}:?$/.test(cell.trim());
}

function splitMarkdownTableRow(line: string) {
  return line
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function CsvPreview({ content }: { content: string }) {
  const rows = parseCsv(content).slice(0, 80);
  if (!rows.length) {
    return <div className="empty-compact">{t("common.noRows")}</div>;
  }
  const [header, ...body] = rows;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {header.map((cell, index) => (
              <th key={index}>{cell}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {header.map((_, columnIndex) => (
                <td key={columnIndex}>{row[columnIndex] ?? ""}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function parseCsv(content: string) {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let inQuotes = false;

  for (let index = 0; index < content.length; index += 1) {
    const char = content[index];
    const next = content[index + 1];
    if (char === '"' && next === '"' && inQuotes) {
      cell += '"';
      index += 1;
      continue;
    }
    if (char === '"') {
      inQuotes = !inQuotes;
      continue;
    }
    if (char === "," && !inQuotes) {
      row.push(cell);
      cell = "";
      continue;
    }
    if ((char === "\n" || char === "\r") && !inQuotes) {
      if (char === "\r" && next === "\n") {
        index += 1;
      }
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
      continue;
    }
    cell += char;
  }

  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((parsedRow) => parsedRow.some((parsedCell) => parsedCell.length > 0));
}

function ArtifactGrid({ artifacts, onOpenArtifact }: { artifacts: Artifact[]; onOpenArtifact: (artifact: Artifact) => void }) {
  if (artifacts.length === 0) {
    return <div className="empty-compact">{t("artifacts.emptyTab")}</div>;
  }
  return <ArtifactBrowser artifacts={artifacts} onOpenArtifact={onOpenArtifact} />;
}

function DataTable({ rows }: { rows?: Array<Record<string, unknown>> }) {
  if (!rows?.length) {
    return <div className="empty-compact">{t("common.noRows")}</div>;
  }
  const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 8);
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 25).map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column}>{formatCell(row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ArtifactIcon({ artifact }: { artifact: Artifact }) {
  if (artifact.preview_kind === "image") {
    return <FileImage size={16} />;
  }
  if (artifact.preview_kind === "json" || artifact.preview_kind === "jsonl") {
    return <FileJson size={16} />;
  }
  if (artifact.type.includes("code") || artifact.type === "latex_source") {
    return <FileCode2 size={16} />;
  }
  if (artifact.preview_kind === "pdf") {
    return <ScrollText size={16} />;
  }
  return <FileText size={16} />;
}

function LoadingState({ label }: { label: string }) {
  return (
    <div className="state-line">
      <Loader2 size={18} className="spin" />
      <span>{label}</span>
    </div>
  );
}

function ErrorState({ label }: { label: string }) {
  return (
    <div className="state-line error">
      <AlertTriangle size={18} />
      <span>{label}</span>
    </div>
  );
}

function LoadingLine({ label }: { label: string }) {
  return (
    <span className="inline-state">
      <Loader2 size={14} className="spin" />
      {label}
    </span>
  );
}

function ErrorLine({ label }: { label: string }) {
  return (
    <span className="inline-state error">
      <AlertTriangle size={14} />
      {label}
    </span>
  );
}

function EmptyWorkbench({ loading }: { loading: boolean }) {
  if (loading) {
    return (
      <div className="empty-workbench">
        <LoadingState label={t("sidebar.loading")} />
      </div>
    );
  }
  return (
    <div className="empty-workbench">
      <FolderOpen size={34} />
      <h1>{t("workbench.noSessions")}</h1>
    </div>
  );
}

function computeVisibleTabs(
  artifacts: Artifact[], requestedTab: ArtifactTab,
  document?: PaperDocumentState | null
): ArtifactTab[] {
  const types = new Set(artifacts.map((artifact) => artifact.type));
  const visible = new Set<ArtifactTab>(["run"]);
  if (["literature_brief", "paper_index", "paper_note"].some(type => types.has(type))) {
    visible.add("brief");
  }
  if (types.has("proposal_data") || types.has("proposal_note")) {
    visible.add("proposals");
  }
  if (["experiment_code", "experiment_log", "experiment_summary", "experiment_figure", "metrics_json", "metrics_table", "checkpoint"].some((type) => types.has(type))) {
    visible.add("experiments");
  }
  if (hasPaper(artifacts, document)) {
    visible.add("paper");
  }
  if (types.has("review_data") || types.has("review_note")) {
    visible.add("review");
  }
  // Old /paper bookmarks must not bring back a tab created by an arbitrary plot.
  if (requestedTab !== "paper") visible.add(requestedTab);
  return TABS.map((tab) => tab.id).filter((tab) => visible.has(tab));
}

function buildSessionId(mode: RunRequest["mode"], prompt: string, modelSlug?: string) {
  const slug = slugify(prompt).slice(0, 34) || "task";
  const model = slugify(modelSlug ?? "model").slice(0, 34) || "model";
  const stamp = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
  return `${mode}__${slug}__${model}__${stamp}`;
}

function slugify(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9а-яё]+/gi, "_")
    .replace(/^_+|_+$/g, "");
}

function rawArtifactUrl(sessionId: string, path: string) {
  return `/ui/artifacts/${encodeURIComponent(sessionId)}/raw?path=${encodeURIComponent(path)}`;
}

function summarizeEventContent(content?: string | null) {
  if (!content) {
    return "";
  }
  const normalized = cleanToolOutput(content);
  const line = normalized
    .split(/\r?\n/)
    .map((item) => item.trim())
    .find((item) => item && !isToolNoise(item));
  return (line ?? normalized).trim().slice(0, 180);
}

function parseRoute(): RouteState {
  const parts = window.location.pathname.split("/").filter(Boolean);
  if (parts[0] === "sessions" && parts[1]) {
    const tab = isTab(parts[2]) ? parts[2] : "run";
    return { sessionId: decodeURIComponent(parts[1]), tab };
  }
  return { tab: "run" };
}

function navigateTo(sessionId: string, tab: ArtifactTab, replace: boolean, setRoute: (route: RouteState) => void) {
  const nextRoute = { sessionId, tab };
  const path = `/sessions/${encodeURIComponent(sessionId)}/${tab}`;
  if (replace) {
    window.history.replaceState(null, "", path);
  } else {
    window.history.pushState(null, "", path);
  }
  setRoute(nextRoute);
}

function isTab(value?: string): value is ArtifactTab {
  return Boolean(value && TAB_IDS.has(value as ArtifactTab));
}

function loadLastLiveState() {
  if (typeof window === "undefined") {
    return emptyLiveState();
  }
  const lastSessionId = window.localStorage.getItem(LIVE_STATE_LAST_KEY);
  return (lastSessionId && loadLiveState(lastSessionId)) || emptyLiveState();
}

function loadLiveState(sessionId: string): LiveRunState | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(liveStateStorageKey(sessionId));
    if (!raw) {
      return null;
    }
    return reviveLiveState(JSON.parse(raw));
  } catch {
    return null;
  }
}

function persistLiveState(state: LiveRunState) {
  if (typeof window === "undefined" || !state.sessionId) {
    return;
  }
  const compacted = compactLiveState(state);
  try {
    window.localStorage.setItem(LIVE_STATE_LAST_KEY, compacted.sessionId!);
    window.localStorage.setItem(liveStateStorageKey(compacted.sessionId!), JSON.stringify(compacted));
  } catch {
    // Browser storage can be full; live streaming should continue even if persistence fails.
  }
  scheduleDiskLiveStateSave(compacted);
}

function scheduleDiskLiveStateSave(state: LiveRunState) {
  if (typeof window === "undefined" || !state.sessionId) {
    return;
  }
  pendingDiskLiveState = state;
  if (liveStateSaveTimer !== undefined) {
    window.clearTimeout(liveStateSaveTimer);
  }
  const delayMs = state.status === "running" || state.status === "queued" ? 1000 : 100;
  liveStateSaveTimer = window.setTimeout(() => {
    const pending = pendingDiskLiveState;
    pendingDiskLiveState = null;
    liveStateSaveTimer = undefined;
    if (!pending?.sessionId) {
      return;
    }
    void uiApi.saveLiveState(pending.sessionId, pending).catch(() => {
      // ui-api may be rebuilding or offline; localStorage still keeps a browser-local copy.
    });
  }, delayMs);
}

function liveStateStorageKey(sessionId: string) {
  return `${LIVE_STATE_PREFIX}${sessionId}`;
}

function clearStoredLiveState(sessionId: string) {
  // Deleting the run server-side is only half the job: the browser keeps its own
  // copy of the live state (and a pointer to the last one), which would otherwise
  // resurrect a deleted run in the UI on the next load.
  try {
    window.localStorage.removeItem(liveStateStorageKey(sessionId));
    if (window.localStorage.getItem(LIVE_STATE_LAST_KEY) === sessionId) {
      window.localStorage.removeItem(LIVE_STATE_LAST_KEY);
    }
  } catch {
    // Storage unavailable (private mode): nothing cached to clear.
  }
}

function compactLiveState(state: LiveRunState): LiveRunState {
  return {
    ...state,
    recordedEvents: undefined,
    messages: state.messages?.slice(-200).map((message) => ({ ...message, content: limitText(message.content, 120000) })),
    rawPlan: limitText(state.rawPlan, 60000),
    transcriptByAgent: Object.fromEntries(
      Object.entries(state.transcriptByAgent).map(([agent, transcript]) => [agent, limitText(transcript, 120000)])
    ),
    events: state.events.slice(-200).map(compactAgentEvent),
    toolMarkers: state.toolMarkers.slice(-80).map((marker) => ({
      ...marker,
      content: limitText(marker.content, 20000)
    }))
  };
}

function compactAgentEvent(event: LiveRunState["events"][number]) {
  return {
    ...event,
    activity: undefined,
    content: event.content ? limitText(event.content, 4000) : event.content
  };
}

function reviveLiveState(value: unknown): LiveRunState | null {
  if (!isRecord(value) || typeof value.sessionId !== "string") {
    return null;
  }
  const fallback = emptyLiveState();
  const status = typeof value.status === "string" ? value.status : fallback.status;
  return {
    sessionId: value.sessionId,
    status: status === "idle" ? "complete" : (status as LiveRunState["status"]),
    activeAgents: Array.isArray(value.activeAgents) ? value.activeAgents.map(String) : [],
    activeAgentCounts: isRecord(value.activeAgentCounts) ? Object.fromEntries(Object.entries(value.activeAgentCounts)
      .filter(([, count]) => typeof count === "number" && count >= 0)) as Record<string, number> : undefined,
    currentAgent: typeof value.currentAgent === "string" ? value.currentAgent : undefined,
    events: Array.isArray(value.events) ? (value.events as LiveRunState["events"]) : [],
    messages: Array.isArray(value.messages) ? value.messages.filter((message) =>
      isRecord(message) && typeof message.id === "string" && typeof message.agent === "string" && typeof message.content === "string"
    ) as LiveRunState["messages"] : undefined,
    messageMode: value.messageMode === "message" ? "message" : "delta",
    rawPlan: typeof value.rawPlan === "string" ? value.rawPlan : "",
    transcriptByAgent: isRecord(value.transcriptByAgent)
      ? Object.fromEntries(Object.entries(value.transcriptByAgent).map(([agent, transcript]) => [agent, String(transcript)]))
      : {},
    observationCount: typeof value.observationCount === "number" ? value.observationCount : 0,
    toolMarkers: Array.isArray(value.toolMarkers) ? (value.toolMarkers as LiveRunState["toolMarkers"]) : [],
    startedAgents: Array.isArray(value.startedAgents) ? value.startedAgents.map(String) : [],
    endedAgents: Array.isArray(value.endedAgents) ? value.endedAgents.map(String) : [],
    queuePosition: typeof value.queuePosition === "number" ? value.queuePosition : undefined,
    queueDepth: typeof value.queueDepth === "number" ? value.queueDepth : undefined,
    error: typeof value.error === "string" ? value.error : undefined
  };
}

function limitText(value: string, maxLength: number) {
  return value.length > maxLength ? value.slice(-maxLength) : value;
}

function formatCount(counts: Record<string, number>) {
  return Object.values(counts).reduce((sum, count) => sum + count, 0);
}

function formatMode(mode: string) {
  return tv("mode", mode);
}

function formatOrchestrator(value?: string) {
  // Purpose-named labels, no internal codename (AP-11). Absent marker ==
  // standard orchestrator.
  const key = value ?? "codearkt";
  if (key === "codearkt" || key === "claude_code") {
    return tv("orchestrator", key);
  }
  return `${t("launch.orchestratorAria")}: ${value}`;
}

function formatResearchProfile(value: ResearchProfile) {
  return `${t("launch.profilePrefix")}: ${tv("profile", value)}`;
}

function formatDecision(status: string) {
  return tv("decision", status);
}

function formatApprovalStage(stage: "sources" | "plan") {
  return tv("approvalStage", stage);
}

function formatComputeMode(value: ComputeMode) {
  if (value === "Local") {
    return t("launch.executorLocal");
  }
  if (value === "GPU") {
    return t("launch.executorGpu");
  }
  return t("launch.executorAuto");
}

function formatModelName(value?: string) {
  if (!value) {
    return t("topbar.modelUnknown");
  }
  if (value === "opus" || value === "sonnet" || value === "haiku") {
    return value.charAt(0).toUpperCase() + value.slice(1);
  }
  return value;
}

function hasHttpStatus(error: unknown, status: number): boolean {
  return typeof error === "object" && error !== null && "status" in error && error.status === status;
}

function formatTaskType(value: string) {
  return tv("taskType", value);
}

function formatRunStatus(status: string) {
  return tv("runStatus", status);
}

function formatStageLabel(label: string) {
  return tv("stage", label);
}

const CODE_PREVIEW_SUFFIXES = new Set([
  ".c",
  ".cpp",
  ".css",
  ".go",
  ".h",
  ".hpp",
  ".html",
  ".java",
  ".jl",
  ".js",
  ".jsx",
  ".jsonl",
  ".py",
  ".r",
  ".rs",
  ".sh",
  ".sql",
  ".toml",
  ".ts",
  ".tsx",
  ".yaml",
  ".yml"
]);

function formatPreviewPayloadKind(preview: PreviewPayload) {
  if (preview.kind === "text" && isSourcePreviewPath(preview.path)) {
    return sourceLanguageLabel(sourceLanguage(preview.path));
  }
  return formatPreviewKind(preview.kind);
}

function isSourcePreviewPath(path: string) {
  const suffix = fileSuffix(path);
  return suffix === ".tex" || suffix === ".bib" || CODE_PREVIEW_SUFFIXES.has(suffix);
}

function sourceLanguage(path: string): SourceLanguage {
  const suffix = fileSuffix(path);
  if (suffix === ".tex") {
    return "latex";
  }
  if (suffix === ".bib") {
    return "bibtex";
  }
  return "code";
}

function sourceLanguageLabel(language: SourceLanguage) {
  if (language === "latex") {
    return "LaTeX";
  }
  if (language === "bibtex") {
    return "BibTeX";
  }
  return t("source.codeLabel");
}

function fileSuffix(path: string) {
  const cleanPath = path.toLowerCase().split(/[?#]/)[0];
  const dotIndex = cleanPath.lastIndexOf(".");
  return dotIndex >= 0 ? cleanPath.slice(dotIndex) : "";
}

function formatPreviewKind(kind: string) {
  return tv("previewKind", kind);
}

function formatBytes(bytes: number) {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatCell(value: unknown) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(4);
  }
  if (typeof value === "string" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export default App;

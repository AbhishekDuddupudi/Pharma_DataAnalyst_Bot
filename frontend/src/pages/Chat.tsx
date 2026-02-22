import { useCallback, useEffect, useRef, useState } from "react";
import {
  getSessions,
  getSessionMessages,
  streamChat,
} from "../api/client";
import type {
  Session,
  Message,
  SqlTask,
  TableArtifact,
  ChartArtifact,
  RetryData,
  CompleteData,
} from "../api/client";
import { useAuth } from "../auth/AuthContext";
import Sidebar from "../components/Sidebar";

/* ── Pipeline step labels (10 nodes) ──────────────── */

const STEP_LABELS: Record<string, string> = {
  preprocess_input: "Preprocess",
  scope_policy_check: "Scope Check",
  semantic_grounding: "Grounding",
  analysis_planner: "Plan",
  sql_generator: "Generate SQL",
  sql_validator: "Validate",
  sql_repair: "Repair",
  sql_executor: "Execute",
  viz_builder: "Visualise",
  response_synthesizer: "Synthesise",
};

const STEP_KEYS = Object.keys(STEP_LABELS);

/* ── Strip residual markdown from old stored messages ─ */

function stripMarkdown(text: string): string {
  return text
    .replace(/^#{1,6}\s+/gm, "")       // headings
    .replace(/\*\*(.+?)\*\*/g, "$1")    // bold
    .replace(/\*(.+?)\*/g, "$1")        // italic
    .replace(/`{1,3}([^`]*)`{1,3}/g, "$1") // inline code / fenced
    .replace(/^[\s]*[-•]\s+/gm, "- ")   // normalize bullets
    .replace(/\n{3,}/g, "\n\n")         // collapse blank lines
    .trim();
}

/* ── Artifact types ───────────────────────────────── */

interface Artifacts {
  sqlTasks: SqlTask[];
  tables: TableArtifact[];
  chart: ChartArtifact | null;
}

const EMPTY_ARTIFACTS: Artifacts = { sqlTasks: [], tables: [], chart: null };

/* ── Component ────────────────────────────────────── */

export default function Chat() {
  const { user } = useAuth();

  /* ── core state ─────────────────────────────────── */
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  /* ── streaming state ────────────────────────────── */
  const [streamingText, setStreamingText] = useState("");
  const [activeStep, setActiveStep] = useState<string | null>(null);
  const [completedSteps, setCompletedSteps] = useState<Set<string>>(new Set());
  const [showProgress, setShowProgress] = useState(false);
  const [retries, setRetries] = useState<RetryData[]>([]);
  const [streamAssumptions, setStreamAssumptions] = useState<string[]>([]);
  const [streamFollowUps, setStreamFollowUps] = useState<string[]>([]);
  /* After streaming ends, hold the last stream's data until messages reload */
  const [lastStreamArtifacts, setLastStreamArtifacts] = useState<Artifacts>(EMPTY_ARTIFACTS);
  const [lastStreamMeta, setLastStreamMeta] = useState<{ assumptions: string[]; followups: string[] } | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  /* ── load sessions on mount ─────────────────────── */
  useEffect(() => {
    refreshSessions();
  }, []);

  const refreshSessions = useCallback(async () => {
    try {
      const list = await getSessions();
      setSessions(list);
    } catch {
      /* silent */
    }
  }, []);

  /* ── load messages when active session changes ──── */
  useEffect(() => {
    if (activeSessionId === null) {
      setMessages([]);
      return;
    }
    loadMessages(activeSessionId);
  }, [activeSessionId]);

  async function loadMessages(sessionId: number) {
    try {
      const msgs = await getSessionMessages(sessionId);
      setMessages(msgs);
    } catch {
      setMessages([]);
    }
  }

  /* ── scroll to bottom ──────────────────────────── */
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingText, activeStep]);

  /* ── handlers ───────────────────────────────────── */

  function handleSelectSession(id: number) {
    abortRef.current?.abort();
    resetStreamState();
    setActiveSessionId(id);
  }

  function handleNewChat() {
    abortRef.current?.abort();
    resetStreamState();
    setActiveSessionId(null);
    setMessages([]);
    setInput("");
  }

  function handleFollowUp(question: string) {
    setInput(question);
  }

  function resetStreamState() {
    setSending(false);
    setStreamingText("");
    setActiveStep(null);
    setCompletedSteps(new Set());
    setShowProgress(false);
    setRetries([]);
    setStreamAssumptions([]);
    setStreamFollowUps([]);
    setLastStreamArtifacts(EMPTY_ARTIFACTS);
    setLastStreamMeta(null);
  }

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;

    setSending(true);
    setInput("");
    setStreamingText("");
    setActiveStep(null);
    setCompletedSteps(new Set());
    setShowProgress(true);
    setRetries([]);
    setStreamAssumptions([]);
    setStreamFollowUps([]);
    setLastStreamArtifacts(EMPTY_ARTIFACTS);
    setLastStreamMeta(null);

    // Optimistic user bubble
    const optimistic: Message = {
      id: -Date.now(),
      session_id: activeSessionId ?? 0,
      role: "user",
      content: text,
      sql_query: null,
      metadata: null,
      artifacts_json: null,
      assumptions: null,
      followups: null,
      metrics_json: null,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimistic]);

    let resolvedSessionId = activeSessionId;
    let prevStep: string | null = null;
    const streamArtifacts: Artifacts = { sqlTasks: [], tables: [], chart: null };

    const controller = streamChat(
      { session_id: activeSessionId ?? undefined, message: text },
      {
        onRequestId(_data) {
          // Could display request_id in UI if needed
        },

        onSession(data) {
          resolvedSessionId = data.session_id;
          if (activeSessionId === null) {
            setActiveSessionId(data.session_id);
          }
        },

        onStatus(data) {
          if (prevStep) {
            setCompletedSteps((prev) => new Set(prev).add(prevStep!));
          }
          prevStep = data.step;
          setActiveStep(data.step);
        },

        onToken(data) {
          setStreamingText((prev) => prev + data.text);
        },

        onArtifactSql(data) {
          streamArtifacts.sqlTasks = data.tasks;
        },

        onArtifactTable(data) {
          streamArtifacts.tables = [...streamArtifacts.tables, data];
        },

        onArtifactChart(data) {
          streamArtifacts.chart = data;
        },

        onAnswerMeta(data) {
          setStreamAssumptions(data.assumptions ?? []);
          setStreamFollowUps(data.follow_ups ?? []);
        },

        onRetry(data) {
          setRetries((prev) => [...prev, data]);
        },

        onMetrics() {
          // Metrics stored per-message in DB; no local state needed
        },

        async onComplete(_data: CompleteData) {
          if (prevStep) {
            setCompletedSteps((prev) => new Set(prev).add(prevStep!));
          }
          setActiveStep(null);
          setShowProgress(false);
          setLastStreamArtifacts({ ...streamArtifacts });
          setLastStreamMeta({
            assumptions: streamAssumptions ?? [],
            followups: streamFollowUps ?? [],
          });
          if (resolvedSessionId !== null) {
            await loadMessages(resolvedSessionId);
          }
          setStreamingText("");
          setSending(false);
          await refreshSessions();
        },

        onError(data) {
          console.error("Stream error:", data.message);
          setShowProgress(false);
          setStreamingText("");
          setSending(false);
          setMessages((prev) =>
            prev.filter((m) => m.id !== optimistic.id),
          );
        },
      },
    );

    abortRef.current = controller;
  }

  /* ── render ─────────────────────────────────────── */
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelectSession={handleSelectSession}
        onNewChat={handleNewChat}
      />

      <div className="flex flex-1 flex-col">
        {/* Header */}
        <header className="flex h-14 shrink-0 items-center border-b border-border px-6">
          <h1 className="text-sm font-semibold text-neutral-100">
            {activeSessionId
              ? sessions.find((s) => s.id === activeSessionId)?.title ??
                "Untitled chat"
              : "New chat"}
          </h1>
          <span className="ml-auto text-xs text-neutral-500">
            {user?.display_name ?? user?.email ?? ""}
          </span>
        </header>

        {/* Messages area */}
        <div className="flex-1 overflow-y-auto px-6 py-6">
          {messages.length === 0 && !streamingText && !showProgress ? (
            <div className="flex h-full items-center justify-center">
              <p className="max-w-md text-center text-sm leading-relaxed text-neutral-500">
                Ask a question about your pharmaceutical data.
              </p>
            </div>
          ) : (
            <div className="mx-auto max-w-2xl space-y-4">
              {messages.map((m, idx) => {
                if (m.role === "user") {
                  return <UserBubble key={m.id} message={m} />;
                }
                // For the last assistant message right after streaming,
                // overlay stream artifacts until reloaded from DB.
                const isLast = idx === messages.length - 1 && !sending;
                return (
                  <AssistantBlock
                    key={m.id}
                    message={m}
                    streamOverlay={
                      isLast && lastStreamArtifacts.sqlTasks.length > 0
                        ? { artifacts: lastStreamArtifacts, meta: lastStreamMeta }
                        : undefined
                    }
                    onFollowUp={handleFollowUp}
                  />
                );
              })}

              {showProgress && (
                <ProgressPanel
                  activeStep={activeStep}
                  completedSteps={completedSteps}
                  retries={retries}
                />
              )}

              {streamingText && (
                <div className="flex justify-start">
                  <div className="max-w-[80%] rounded-lg bg-surface-overlay px-4 py-2.5 text-sm leading-relaxed text-neutral-200">
                    <p className="whitespace-pre-wrap">{streamingText}</p>
                    <span className="inline-block h-4 w-1.5 animate-pulse bg-neutral-400 align-text-bottom" />
                  </div>
                </div>
              )}

              <div ref={bottomRef} />
            </div>
          )}
        </div>

        {/* Input bar */}
        <div className="border-t border-border px-6 py-4">
          <form onSubmit={handleSend} className="mx-auto flex max-w-2xl gap-3">
            <input
              type="text"
              placeholder="Type your question..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={sending}
              className="input-base flex-1"
            />
            <button
              type="submit"
              disabled={sending || !input.trim()}
              className="btn-primary disabled:opacity-50"
            >
              {sending ? "Sending…" : "Send"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}

/* ── Sub-components ────────────────────────────────── */

function UserBubble({ message: m }: { message: Message }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] rounded-lg bg-accent px-4 py-2.5 text-sm leading-relaxed text-white">
        <p className="whitespace-pre-wrap">{m.content}</p>
      </div>
    </div>
  );
}

interface StreamOverlay {
  artifacts: { sqlTasks: SqlTask[]; tables: TableArtifact[]; chart: ChartArtifact | null };
  meta: { assumptions: string[]; followups: string[] } | null;
}

function AssistantBlock({
  message: m,
  streamOverlay,
  onFollowUp,
}: {
  message: Message;
  streamOverlay?: StreamOverlay;
  onFollowUp: (q: string) => void;
}) {
  const [activeTab, setActiveTab] = useState<"sql" | "table" | "chart">("sql");
  const [selectedTableIdx, setSelectedTableIdx] = useState(0);

  // Extract artifacts from DB data, falling back to legacy `metadata` then stream overlay
  const aj = m.artifacts_json;
  const legacy = (!aj && m.metadata) ? m.metadata as Record<string, unknown> : null;
  const sqlSource = aj?.sql_tasks ?? (legacy?.sql_tasks as unknown[]) ?? null;
  const tableSource = aj?.tables ?? (legacy?.tables as unknown[]) ?? null;
  const chartSource = (aj?.chart ?? legacy?.chart ?? null) as Record<string, unknown> | null;

  const sqlTasks: SqlTask[] =
    sqlSource?.map((t: Record<string, unknown>) => ({ title: String(t.title ?? ""), sql: String(t.sql ?? "") })) ??
    streamOverlay?.artifacts.sqlTasks ??
    [];
  const tables: TableArtifact[] =
    tableSource?.map((t: Record<string, unknown>) => ({
      task_title: String(t.title ?? t.task_title ?? ""),
      columns: (t.columns ?? []) as string[],
      rows: (t.rows ?? []) as unknown[][],
      row_count: ((t.rows ?? []) as unknown[][]).length,
      truncated: false,
    })) ??
    streamOverlay?.artifacts.tables ??
    [];
  const chart: ChartArtifact | null =
    chartSource ? (chartSource as unknown as ChartArtifact) : streamOverlay?.artifacts.chart ?? null;

  const assumptions: string[] =
    m.assumptions ??
    (legacy?.assumptions as string[] | undefined) ??
    streamOverlay?.meta?.assumptions ??
    [];
  const followups: string[] =
    m.followups ??
    (legacy?.follow_ups as string[] | undefined) ??
    streamOverlay?.meta?.followups ??
    [];

  const hasSql = sqlTasks.length > 0;
  const hasTable = tables.length > 0;
  const hasChart = chart?.available === true;
  const hasAnyArtifact = hasSql || hasTable || hasChart;

  // Strip markdown from old messages that were stored with markdown formatting
  const displayContent = stripMarkdown(m.content);

  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] space-y-2">
        {/* Answer bubble — plain text only, NO markdown, NO SQL */}
        <div className="rounded-lg bg-surface-overlay px-4 py-2.5 text-sm leading-relaxed text-neutral-200">
          <p className="whitespace-pre-wrap">{displayContent}</p>
        </div>

        {/* Assumptions section */}
        {assumptions.length > 0 && (
          <div className="rounded-lg border border-border/50 bg-surface-raised px-3 py-2">
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-neutral-500">
              Assumptions
            </p>
            <ul className="space-y-0.5">
              {assumptions.map((a, i) => (
                <li key={i} className="text-xs text-neutral-400">
                  - {a}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Follow-up chips */}
        {followups.length > 0 && (
          <FollowUpChips followups={followups} onFollowUp={onFollowUp} />
        )}

        {/* Artifact tabs (per-message) */}
        {hasAnyArtifact && (
          <ArtifactTabs
            sqlTasks={sqlTasks}
            tables={tables}
            chart={chart}
            activeTab={activeTab}
            onTabChange={setActiveTab}
            selectedTableIdx={selectedTableIdx}
            onSelectTable={setSelectedTableIdx}
          />
        )}

        {/* Metrics footer */}
        {m.metrics_json && (
          <div className="flex flex-wrap gap-3 px-1 text-[10px] text-neutral-500">
            {m.metrics_json.total_ms != null && <span>Total: {m.metrics_json.total_ms}ms</span>}
            {m.metrics_json.llm_ms != null && <span>LLM: {m.metrics_json.llm_ms}ms</span>}
            {m.metrics_json.db_ms != null && <span>DB: {m.metrics_json.db_ms}ms</span>}
            {m.metrics_json.rows_returned != null && (
              <span>Rows: {m.metrics_json.rows_returned}</span>
            )}
            {(m.metrics_json as Record<string, unknown>).langfuse_url ? (
              <a
                href={(m.metrics_json as Record<string, unknown>).langfuse_url as string}
                target="_blank"
                rel="noopener noreferrer"
                className="underline hover:text-indigo-500"
              >
                View Trace &rarr;
              </a>
            ) : (m.metrics_json as Record<string, unknown>).langfuse_trace_id ? (
              <span>Trace: {String((m.metrics_json as Record<string, unknown>).langfuse_trace_id).slice(0, 8)}</span>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Follow-up chips with "More" dropdown ─────────── */

function FollowUpChips({
  followups,
  onFollowUp,
}: {
  followups: string[];
  onFollowUp: (q: string) => void;
}) {
  const [showMore, setShowMore] = useState(false);
  const visible = followups.slice(0, 3);
  const hidden = followups.slice(3);

  return (
    <div className="flex flex-wrap items-center gap-2">
      {visible.map((q, i) => (
        <button
          key={i}
          onClick={() => onFollowUp(q)}
          className="rounded-full border border-accent/40 bg-accent/10 px-3 py-1 text-xs text-accent-hover transition-colors hover:bg-accent/20"
        >
          {q}
        </button>
      ))}
      {hidden.length > 0 && (
        <div className="relative">
          <button
            onClick={() => setShowMore(!showMore)}
            className="rounded-full border border-border bg-surface-overlay px-3 py-1 text-xs text-neutral-400 transition-colors hover:text-neutral-200"
          >
            More ({hidden.length})
          </button>
          {showMore && (
            <div className="absolute bottom-full left-0 z-10 mb-1 min-w-[200px] rounded-lg border border-border bg-surface-raised p-1 shadow-lg">
              {hidden.map((q, i) => (
                <button
                  key={i}
                  onClick={() => {
                    onFollowUp(q);
                    setShowMore(false);
                  }}
                  className="block w-full rounded px-3 py-1.5 text-left text-xs text-neutral-300 transition-colors hover:bg-surface-overlay"
                >
                  {q}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Per-message artifact tabs ────────────────────── */

function ArtifactTabs({
  sqlTasks,
  tables,
  chart,
  activeTab,
  onTabChange,
  selectedTableIdx,
  onSelectTable,
}: {
  sqlTasks: SqlTask[];
  tables: TableArtifact[];
  chart: ChartArtifact | null;
  activeTab: "sql" | "table" | "chart";
  onTabChange: (tab: "sql" | "table" | "chart") => void;
  selectedTableIdx: number;
  onSelectTable: (idx: number) => void;
}) {
  const hasSql = sqlTasks.length > 0;
  const hasTable = tables.length > 0;
  const hasChart = chart?.available === true;

  const tabs: { key: "sql" | "table" | "chart"; label: string; enabled: boolean }[] = [
    { key: "sql", label: `SQL${sqlTasks.length > 1 ? ` (${sqlTasks.length})` : ""}`, enabled: hasSql },
    { key: "table", label: `Table${tables.length > 1 ? ` (${tables.length})` : ""}`, enabled: hasTable },
    { key: "chart", label: "Chart", enabled: hasChart },
  ];

  const currentTable = tables[selectedTableIdx];

  return (
    <div className="rounded-lg border border-border bg-surface-raised">
      {/* Tab row */}
      <div className="flex border-b border-border">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => t.enabled && onTabChange(t.key)}
            className={`px-4 py-2 text-xs font-medium transition-colors ${
              activeTab === t.key && t.enabled
                ? "border-b-2 border-accent text-white"
                : t.enabled
                  ? "text-neutral-500 hover:text-neutral-300"
                  : "cursor-default text-neutral-600"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="p-4">
        {activeTab === "sql" && (
          <div className="space-y-3">
            {hasSql ? (
              sqlTasks.map((task, i) => (
                <div key={i}>
                  <p className="mb-1 text-xs font-semibold text-neutral-300">
                    {task.title}
                  </p>
                  <pre className="overflow-x-auto rounded bg-surface p-3 font-mono text-xs text-neutral-300">
                    {task.sql}
                  </pre>
                  {task.error && (
                    <p className="mt-1 text-xs text-red-400">⚠ {task.error}</p>
                  )}
                </div>
              ))
            ) : (
              <p className="text-xs text-neutral-500">No SQL available.</p>
            )}
          </div>
        )}

        {activeTab === "table" && (
          <div>
            {hasTable ? (
              <>
                {tables.length > 1 && (
                  <div className="mb-3 flex gap-2">
                    {tables.map((t, i) => (
                      <button
                        key={i}
                        onClick={() => onSelectTable(i)}
                        className={`rounded px-2 py-1 text-xs font-medium transition-colors ${
                          i === selectedTableIdx
                            ? "bg-accent/20 text-accent-hover"
                            : "bg-surface-overlay text-neutral-500 hover:text-neutral-300"
                        }`}
                      >
                        {t.task_title}
                      </button>
                    ))}
                  </div>
                )}
                {currentTable && (
                  <div>
                    {tables.length === 1 && (
                      <p className="mb-2 text-xs font-semibold text-neutral-300">
                        {currentTable.task_title}
                      </p>
                    )}
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-xs">
                        <thead>
                          <tr className="border-b border-border">
                            {currentTable.columns.map((col) => (
                              <th
                                key={col}
                                className="px-3 py-2 font-semibold text-neutral-300"
                              >
                                {col}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {currentTable.rows.map((row, ri) => (
                            <tr key={ri} className="border-b border-border/50">
                              {(row as unknown[]).map((cell, ci) => (
                                <td
                                  key={ci}
                                  className="px-3 py-1.5 text-neutral-400"
                                >
                                  {String(cell)}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <p className="mt-2 text-xs text-neutral-500">
                      {currentTable.row_count} row
                      {currentTable.row_count !== 1 ? "s" : ""}
                      {currentTable.truncated ? " (truncated)" : ""}
                    </p>
                  </div>
                )}
              </>
            ) : (
              <p className="text-xs text-neutral-500">No table data available.</p>
            )}
          </div>
        )}

        {activeTab === "chart" && (
          <div>
            {hasChart ? (
              <div className="space-y-2">
                <p className="text-xs text-neutral-300">
                  <span className="font-semibold">Chart type:</span>{" "}
                  {chart!.chart_type}
                </p>
                {chart!.title && (
                  <p className="text-xs text-neutral-300">
                    <span className="font-semibold">Title:</span>{" "}
                    {chart!.title}
                  </p>
                )}
                <p className="text-xs text-neutral-300">
                  <span className="font-semibold">X:</span> {chart!.x_column}{" "}
                  | <span className="font-semibold">Y:</span>{" "}
                  {chart!.y_column}
                </p>
                <p className="mt-2 text-xs text-neutral-500 italic">
                  Chart rendering coming soon — spec is ready.
                </p>
              </div>
            ) : (
              <p className="text-xs text-neutral-500">No chart available.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ProgressPanel({
  activeStep,
  completedSteps,
  retries,
}: {
  activeStep: string | null;
  completedSteps: Set<string>;
  retries: RetryData[];
}) {
  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[80%] rounded-lg border border-border bg-surface-raised px-4 py-3">
        <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">
          Progress
        </p>
        <div className="flex flex-wrap gap-x-1 gap-y-1">
          {STEP_KEYS.map((key, i) => {
            const isDone = completedSteps.has(key);
            const isActive = key === activeStep;

            let cls =
              "rounded px-2 py-0.5 text-xs font-medium transition-colors ";
            if (isDone) {
              cls += "bg-emerald-500/20 text-emerald-400";
            } else if (isActive) {
              cls += "bg-accent/20 text-accent-hover animate-pulse";
            } else {
              cls += "bg-surface-overlay text-neutral-500";
            }

            return (
              <span key={key}>
                <span className={cls}>{STEP_LABELS[key]}</span>
                {i < STEP_KEYS.length - 1 && (
                  <span className="mx-0.5 text-neutral-600">→</span>
                )}
              </span>
            );
          })}
        </div>

        {retries.length > 0 && (
          <div className="mt-2 space-y-1">
            {retries.map((r, i) => (
              <p key={i} className="text-[10px] text-amber-400">
                ⟳ Retry {r.attempt}/{r.max} ({r.type}) — {r.reason}
              </p>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

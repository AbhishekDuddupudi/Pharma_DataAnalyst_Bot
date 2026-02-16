import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
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
  MetricsData,
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

/* ── Artifact types ───────────────────────────────── */

interface Artifacts {
  sqlTasks: SqlTask[];
  tables: TableArtifact[];
  chart: ChartArtifact | null;
}

type ArtifactTab = "answer" | "sql" | "table" | "chart";

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
  const [artifacts, setArtifacts] = useState<Artifacts>(EMPTY_ARTIFACTS);
  const [activeTab, setActiveTab] = useState<ArtifactTab>("answer");
  const [showProgress, setShowProgress] = useState(false);
  const [lastArtifacts, setLastArtifacts] = useState<Artifacts>(EMPTY_ARTIFACTS);
  const [showArtifactTabs, setShowArtifactTabs] = useState(false);
  const [metrics, setMetrics] = useState<MetricsData | null>(null);
  const [selectedTableIdx, setSelectedTableIdx] = useState(0);
  const [retries, setRetries] = useState<RetryData[]>([]);

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
    setShowArtifactTabs(false);
  }

  function handleNewChat() {
    abortRef.current?.abort();
    resetStreamState();
    setActiveSessionId(null);
    setMessages([]);
    setInput("");
    setShowArtifactTabs(false);
  }

  function resetStreamState() {
    setSending(false);
    setStreamingText("");
    setActiveStep(null);
    setCompletedSteps(new Set());
    setShowProgress(false);
    setArtifacts(EMPTY_ARTIFACTS);
    setActiveTab("answer");
    setMetrics(null);
    setSelectedTableIdx(0);
    setRetries([]);
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
    setShowArtifactTabs(false);
    setArtifacts(EMPTY_ARTIFACTS);
    setActiveTab("answer");
    setMetrics(null);
    setSelectedTableIdx(0);
    setRetries([]);

    // Optimistic user bubble
    const optimistic: Message = {
      id: -Date.now(),
      session_id: activeSessionId ?? 0,
      role: "user",
      content: text,
      sql_query: null,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimistic]);

    let resolvedSessionId = activeSessionId;
    let prevStep: string | null = null;
    const streamArtifacts: Artifacts = { sqlTasks: [], tables: [], chart: null };
    let streamMetrics: MetricsData | null = null;

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
          setArtifacts((prev) => ({ ...prev, sqlTasks: data.tasks }));
        },

        onArtifactTable(data) {
          streamArtifacts.tables = [...streamArtifacts.tables, data];
          setArtifacts((prev) => ({
            ...prev,
            tables: [...prev.tables, data],
          }));
        },

        onArtifactChart(data) {
          streamArtifacts.chart = data;
          setArtifacts((prev) => ({ ...prev, chart: data }));
        },

        onRetry(data) {
          setRetries((prev) => [...prev, data]);
        },

        onMetrics(data) {
          streamMetrics = data;
          setMetrics(data);
        },

        async onComplete(_data: CompleteData) {
          if (prevStep) {
            setCompletedSteps((prev) => new Set(prev).add(prevStep!));
          }
          setActiveStep(null);
          setShowProgress(false);
          setLastArtifacts({ ...streamArtifacts });
          setShowArtifactTabs(true);
          if (streamMetrics) setMetrics(streamMetrics);

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
              {messages.map((m) => (
                <MessageBubble key={m.id} message={m} />
              ))}

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

              {showArtifactTabs && !sending && (
                <ArtifactPanel
                  artifacts={lastArtifacts}
                  activeTab={activeTab}
                  onTabChange={setActiveTab}
                  metrics={metrics}
                  selectedTableIdx={selectedTableIdx}
                  onSelectTable={setSelectedTableIdx}
                />
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

function MessageBubble({ message: m }: { message: Message }) {
  return (
    <div
      className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
    >
      <div
        className={`max-w-[80%] rounded-lg px-4 py-2.5 text-sm leading-relaxed ${
          m.role === "user"
            ? "bg-accent text-white"
            : "bg-surface-overlay text-neutral-200"
        }`}
      >
        <p className="whitespace-pre-wrap">{m.content}</p>
        {m.sql_query && (
          <pre className="mt-2 overflow-x-auto rounded bg-surface p-2 font-mono text-xs text-neutral-400">
            {m.sql_query}
          </pre>
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

        {/* Retry events */}
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

function ArtifactPanel({
  artifacts,
  activeTab,
  onTabChange,
  metrics,
  selectedTableIdx,
  onSelectTable,
}: {
  artifacts: Artifacts;
  activeTab: ArtifactTab;
  onTabChange: (tab: ArtifactTab) => void;
  metrics: MetricsData | null;
  selectedTableIdx: number;
  onSelectTable: (idx: number) => void;
}) {
  const tabs: { key: ArtifactTab; label: string }[] = [
    { key: "answer", label: "Answer" },
    { key: "sql", label: `SQL${artifacts.sqlTasks.length > 1 ? ` (${artifacts.sqlTasks.length})` : ""}` },
    { key: "table", label: `Table${artifacts.tables.length > 1 ? ` (${artifacts.tables.length})` : ""}` },
    { key: "chart", label: "Chart" },
  ];

  const currentTable = artifacts.tables[selectedTableIdx];

  return (
    <div className="rounded-lg border border-border bg-surface-raised">
      {/* Tab row */}
      <div className="flex border-b border-border">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => onTabChange(t.key)}
            className={`px-4 py-2 text-xs font-medium transition-colors ${
              activeTab === t.key
                ? "border-b-2 border-accent text-white"
                : "text-neutral-500 hover:text-neutral-300"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="p-4">
        {activeTab === "answer" && (
          <p className="text-xs text-neutral-400">
            See the assistant message above.
          </p>
        )}

        {activeTab === "sql" && (
          <div className="space-y-3">
            {artifacts.sqlTasks.length > 0 ? (
              artifacts.sqlTasks.map((task, i) => (
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
              <p className="text-xs text-neutral-500">No SQL generated.</p>
            )}
          </div>
        )}

        {activeTab === "table" && (
          <div>
            {artifacts.tables.length > 1 && (
              <div className="mb-3 flex gap-2">
                {artifacts.tables.map((t, i) => (
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
            {currentTable ? (
              <div>
                {artifacts.tables.length === 1 && (
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
                          {row.map((cell, ci) => (
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
                  {currentTable.row_count} row{currentTable.row_count !== 1 ? "s" : ""}
                  {currentTable.truncated ? " (truncated)" : ""}
                </p>
              </div>
            ) : (
              <p className="text-xs text-neutral-500">No table data.</p>
            )}
          </div>
        )}

        {activeTab === "chart" && (
          <div>
            {artifacts.chart?.available ? (
              <div className="space-y-2">
                <p className="text-xs text-neutral-300">
                  <span className="font-semibold">Chart type:</span>{" "}
                  {artifacts.chart.chart_type}
                </p>
                {artifacts.chart.title && (
                  <p className="text-xs text-neutral-300">
                    <span className="font-semibold">Title:</span>{" "}
                    {artifacts.chart.title}
                  </p>
                )}
                <p className="text-xs text-neutral-300">
                  <span className="font-semibold">X:</span>{" "}
                  {artifacts.chart.x_column} |{" "}
                  <span className="font-semibold">Y:</span>{" "}
                  {artifacts.chart.y_column}
                </p>
                <p className="mt-2 text-xs text-neutral-500 italic">
                  Chart rendering coming soon — spec is ready.
                </p>
              </div>
            ) : (
              <p className="text-xs text-neutral-500">
                No chart available for this query.
              </p>
            )}
          </div>
        )}
      </div>

      {/* Metrics footer */}
      {metrics && (
        <div className="flex gap-4 border-t border-border px-4 py-2 text-[10px] text-neutral-500">
          <span>Total: {metrics.total_ms}ms</span>
          <span>LLM: {metrics.llm_ms}ms</span>
          <span>DB: {metrics.db_ms}ms</span>
          <span>Rows: {metrics.rows_returned}</span>
          <span>Tokens: {metrics.tokens_streamed}</span>
          {metrics.retries_used > 0 && (
            <span className="text-amber-500">Retries: {metrics.retries_used}</span>
          )}
        </div>
      )}
    </div>
  );
}

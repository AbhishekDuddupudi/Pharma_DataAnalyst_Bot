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

const EMPTY_ARTIFACTS: Artifacts = { sqlTasks: [], tables: [], chart: null };

/* ── Markdown renderer ────────────────────────────── */

function MarkdownText({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children: c }) => (
          <h3 className="mt-3 mb-1.5 text-base font-bold text-neutral-100">{c}</h3>
        ),
        h2: ({ children: c }) => (
          <h4 className="mt-2.5 mb-1 text-sm font-bold text-neutral-100">{c}</h4>
        ),
        h3: ({ children: c }) => (
          <h5 className="mt-2 mb-1 text-sm font-semibold text-neutral-200">{c}</h5>
        ),
        p: ({ children: c }) => <p className="mb-2 last:mb-0">{c}</p>,
        strong: ({ children: c }) => (
          <strong className="font-semibold text-neutral-100">{c}</strong>
        ),
        ul: ({ children: c }) => (
          <ul className="mb-2 ml-4 list-disc space-y-0.5">{c}</ul>
        ),
        ol: ({ children: c }) => (
          <ol className="mb-2 ml-4 list-decimal space-y-0.5">{c}</ol>
        ),
        li: ({ children: c }) => <li className="text-neutral-300">{c}</li>,
        code: ({ children: c, className }) => {
          const isBlock = className?.includes("language-");
          if (isBlock) {
            return (
              <pre className="my-2 overflow-x-auto rounded bg-surface p-2.5 font-mono text-xs text-neutral-300">
                <code>{c}</code>
              </pre>
            );
          }
          return (
            <code className="rounded bg-surface-overlay px-1 py-0.5 font-mono text-xs text-accent-hover">
              {c}
            </code>
          );
        },
        pre: ({ children: c }) => <>{c}</>,
        table: ({ children: c }) => (
          <div className="my-2 overflow-x-auto">
            <table className="w-full text-left text-xs">{c}</table>
          </div>
        ),
        th: ({ children: c }) => (
          <th className="border-b border-border px-2 py-1 font-semibold text-neutral-300">
            {c}
          </th>
        ),
        td: ({ children: c }) => (
          <td className="border-b border-border/50 px-2 py-1 text-neutral-400">
            {c}
          </td>
        ),
      }}
    >
      {children}
    </ReactMarkdown>
  );
}

/* ── Collapsible panel ────────────────────────────── */

function CollapsibleSection({
  label,
  count,
  children,
}: {
  label: string;
  count?: number;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const displayLabel = count && count > 1 ? `${label} (${count})` : label;

  return (
    <div className="mt-2">
      <button
        onClick={() => setOpen(!open)}
        className="text-xs font-medium text-accent hover:text-accent-hover transition-colors"
      >
        {open ? `Hide ${displayLabel}` : `Show ${displayLabel}`}
      </button>
      {open && <div className="mt-1.5">{children}</div>}
    </div>
  );
}

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
  const [showProgress, setShowProgress] = useState(false);
  const [metrics, setMetrics] = useState<MetricsData | null>(null);
  const [retries, setRetries] = useState<RetryData[]>([]);
  const [streamAssumptions, setStreamAssumptions] = useState<string[]>([]);
  const [streamFollowUps, setStreamFollowUps] = useState<string[]>([]);
  const [lastStreamArtifacts, setLastStreamArtifacts] = useState<Artifacts>(EMPTY_ARTIFACTS);
  const [lastStreamMeta, setLastStreamMeta] = useState<{ assumptions: string[]; follow_ups: string[] } | null>(null);

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

  function resetStreamState() {
    setSending(false);
    setStreamingText("");
    setActiveStep(null);
    setCompletedSteps(new Set());
    setShowProgress(false);
    setArtifacts(EMPTY_ARTIFACTS);
    setMetrics(null);
    setRetries([]);
    setStreamAssumptions([]);
    setStreamFollowUps([]);
    setLastStreamArtifacts(EMPTY_ARTIFACTS);
    setLastStreamMeta(null);
  }

  function handleFollowUp(question: string) {
    setInput(question);
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
    setArtifacts(EMPTY_ARTIFACTS);
    setMetrics(null);
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
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimistic]);

    let resolvedSessionId = activeSessionId;
    let prevStep: string | null = null;
    const streamArtifacts: Artifacts = { sqlTasks: [], tables: [], chart: null };
    let streamMetrics: MetricsData | null = null;
    let finalMeta: { assumptions: string[]; follow_ups: string[] } | null = null;

    const controller = streamChat(
      { session_id: activeSessionId ?? undefined, message: text },
      {
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

        onAnswerMeta(data) {
          finalMeta = data;
          setStreamAssumptions(data.assumptions ?? []);
          setStreamFollowUps(data.follow_ups ?? []);
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
          setLastStreamArtifacts({ ...streamArtifacts });
          setLastStreamMeta(finalMeta);
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
              {messages.map((m, idx) => {
                const isLastAssistant =
                  m.role === "assistant" &&
                  !sending &&
                  idx === messages.length - 1;

                const msgArtifacts = buildArtifactsForMessage(m);
                const effectiveArtifacts =
                  isLastAssistant && !m.metadata && lastStreamArtifacts.sqlTasks.length > 0
                    ? lastStreamArtifacts
                    : msgArtifacts;

                const msgMeta = m.metadata;
                const effectiveAssumptions =
                  isLastAssistant && !msgMeta && lastStreamMeta
                    ? lastStreamMeta.assumptions
                    : msgMeta?.assumptions ?? [];
                const effectiveFollowUps =
                  isLastAssistant && !msgMeta && lastStreamMeta
                    ? lastStreamMeta.follow_ups
                    : msgMeta?.follow_ups ?? [];

                return (
                  <MessageBubble
                    key={m.id}
                    message={m}
                    artifacts={effectiveArtifacts}
                    assumptions={effectiveAssumptions}
                    followUps={effectiveFollowUps}
                    onFollowUp={handleFollowUp}
                    metrics={isLastAssistant ? metrics : null}
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
                    <MarkdownText>{streamingText}</MarkdownText>
                    <span className="inline-block h-4 w-1.5 animate-pulse bg-neutral-400 align-text-bottom" />

                    {streamAssumptions.length > 0 && (
                      <AssumptionsBlock assumptions={streamAssumptions} />
                    )}
                    {streamFollowUps.length > 0 && (
                      <FollowUpChips
                        questions={streamFollowUps}
                        onFollowUp={handleFollowUp}
                      />
                    )}

                    <StreamingArtifacts artifacts={artifacts} />
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

/* ── Helper: build artifacts from message metadata ── */

function buildArtifactsForMessage(m: Message): Artifacts {
  if (m.role !== "assistant" || !m.metadata) return EMPTY_ARTIFACTS;
  const meta = m.metadata;
  const sqlTasks: SqlTask[] = (meta.sql_tasks ?? []).map((t) => ({
    title: t.title,
    sql: t.sql,
  }));
  const tables: TableArtifact[] = (meta.tables ?? []).map((t) => ({
    task_title: t.title,
    columns: t.columns,
    rows: t.rows,
    row_count: t.rows.length,
    truncated: false,
  }));
  const chart: ChartArtifact | null = meta.chart ?? null;
  return { sqlTasks, tables, chart };
}

/* ── Sub-components ────────────────────────────────── */

function MessageBubble({
  message: m,
  artifacts,
  assumptions,
  followUps,
  onFollowUp,
  metrics,
}: {
  message: Message;
  artifacts: Artifacts;
  assumptions: string[];
  followUps: string[];
  onFollowUp: (q: string) => void;
  metrics: MetricsData | null;
}) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-lg bg-accent px-4 py-2.5 text-sm leading-relaxed text-white">
          <p className="whitespace-pre-wrap">{m.content}</p>
        </div>
      </div>
    );
  }

  const hasSql = artifacts.sqlTasks.length > 0;
  const hasTables = artifacts.tables.length > 0;
  const hasChart = artifacts.chart?.available === true;

  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] rounded-lg bg-surface-overlay px-4 py-2.5 text-sm leading-relaxed text-neutral-200">
        <MarkdownText>{m.content}</MarkdownText>

        {assumptions.length > 0 && (
          <AssumptionsBlock assumptions={assumptions} />
        )}

        {followUps.length > 0 && (
          <FollowUpChips questions={followUps} onFollowUp={onFollowUp} />
        )}

        {hasSql && (
          <CollapsibleSection label="SQL" count={artifacts.sqlTasks.length}>
            <SqlPanel tasks={artifacts.sqlTasks} />
          </CollapsibleSection>
        )}

        {hasTables && (
          <CollapsibleSection label="Table" count={artifacts.tables.length}>
            <TablePanel tables={artifacts.tables} />
          </CollapsibleSection>
        )}

        {hasChart && (
          <CollapsibleSection label="Chart">
            <ChartPanel chart={artifacts.chart!} />
          </CollapsibleSection>
        )}

        {metrics && (
          <div className="mt-2 flex flex-wrap gap-3 border-t border-border/50 pt-2 text-[10px] text-neutral-500">
            <span>Total: {metrics.total_ms}ms</span>
            <span>LLM: {metrics.llm_ms}ms</span>
            <span>DB: {metrics.db_ms}ms</span>
            <span>Rows: {metrics.rows_returned}</span>
            {metrics.retries_used > 0 && (
              <span className="text-amber-500">Retries: {metrics.retries_used}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function StreamingArtifacts({ artifacts }: { artifacts: Artifacts }) {
  const hasSql = artifacts.sqlTasks.length > 0;
  const hasTables = artifacts.tables.length > 0;
  const hasChart = artifacts.chart?.available === true;
  if (!hasSql && !hasTables && !hasChart) return null;

  return (
    <>
      {hasSql && (
        <CollapsibleSection label="SQL" count={artifacts.sqlTasks.length}>
          <SqlPanel tasks={artifacts.sqlTasks} />
        </CollapsibleSection>
      )}
      {hasTables && (
        <CollapsibleSection label="Table" count={artifacts.tables.length}>
          <TablePanel tables={artifacts.tables} />
        </CollapsibleSection>
      )}
      {hasChart && (
        <CollapsibleSection label="Chart">
          <ChartPanel chart={artifacts.chart!} />
        </CollapsibleSection>
      )}
    </>
  );
}

function AssumptionsBlock({ assumptions }: { assumptions: string[] }) {
  return (
    <div className="mt-2 rounded bg-surface/50 px-3 py-2">
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-neutral-500">
        Assumptions
      </p>
      <ul className="space-y-0.5">
        {assumptions.map((a, i) => (
          <li key={i} className="text-xs text-neutral-400">
            • {a}
          </li>
        ))}
      </ul>
    </div>
  );
}

function FollowUpChips({
  questions,
  onFollowUp,
}: {
  questions: string[];
  onFollowUp: (q: string) => void;
}) {
  return (
    <div className="mt-2.5 flex flex-wrap gap-2">
      {questions.map((q, i) => (
        <button
          key={i}
          onClick={() => onFollowUp(q)}
          className="rounded-full border border-accent/40 bg-accent/10 px-3 py-1 text-xs text-accent-hover hover:bg-accent/20 transition-colors"
        >
          {q}
        </button>
      ))}
    </div>
  );
}

function SqlPanel({ tasks }: { tasks: SqlTask[] }) {
  return (
    <div className="space-y-2">
      {tasks.map((task, i) => (
        <div key={i}>
          <p className="mb-1 text-xs font-semibold text-neutral-300">
            {task.title}
          </p>
          <pre className="overflow-x-auto rounded bg-surface p-2.5 font-mono text-xs text-neutral-300">
            {task.sql}
          </pre>
          {task.error && (
            <p className="mt-1 text-xs text-red-400">⚠ {task.error}</p>
          )}
        </div>
      ))}
    </div>
  );
}

function TablePanel({ tables }: { tables: TableArtifact[] }) {
  const [selectedIdx, setSelectedIdx] = useState(0);
  const current = tables[selectedIdx];
  if (!current) return <p className="text-xs text-neutral-500">No table data.</p>;

  return (
    <div>
      {tables.length > 1 && (
        <div className="mb-2 flex gap-2">
          {tables.map((t, i) => (
            <button
              key={i}
              onClick={() => setSelectedIdx(i)}
              className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
                i === selectedIdx
                  ? "bg-accent/20 text-accent-hover"
                  : "bg-surface-overlay text-neutral-500 hover:text-neutral-300"
              }`}
            >
              {t.task_title}
            </button>
          ))}
        </div>
      )}
      {tables.length === 1 && (
        <p className="mb-1.5 text-xs font-semibold text-neutral-300">
          {current.task_title}
        </p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-border">
              {current.columns.map((col) => (
                <th
                  key={col}
                  className="px-3 py-1.5 font-semibold text-neutral-300"
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {current.rows.map((row, ri) => (
              <tr key={ri} className="border-b border-border/50">
                {(row as unknown[]).map((cell, ci) => (
                  <td key={ci} className="px-3 py-1 text-neutral-400">
                    {String(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-1.5 text-xs text-neutral-500">
        {current.row_count} row{current.row_count !== 1 ? "s" : ""}
        {current.truncated ? " (truncated)" : ""}
      </p>
    </div>
  );
}

function ChartPanel({ chart }: { chart: ChartArtifact }) {
  return (
    <div className="space-y-1.5">
      <p className="text-xs text-neutral-300">
        <span className="font-semibold">Chart type:</span> {chart.chart_type}
      </p>
      {chart.title && (
        <p className="text-xs text-neutral-300">
          <span className="font-semibold">Title:</span> {chart.title}
        </p>
      )}
      <p className="text-xs text-neutral-300">
        <span className="font-semibold">X:</span> {chart.x_column} |{" "}
        <span className="font-semibold">Y:</span> {chart.y_column}
      </p>
      <p className="text-xs text-neutral-500 italic">
        Chart rendering coming soon — spec is ready.
      </p>
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

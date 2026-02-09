import { useCallback, useEffect, useRef, useState } from "react";
import {
  getSessions,
  getSessionMessages,
  streamChat,
} from "../api/client";
import type { Session, Message } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import Sidebar from "../components/Sidebar";

/* ── Pipeline step labels (order matters) ─────────── */

const STEP_LABELS: Record<string, string> = {
  preprocess_input: "Preprocess",
  analysis_planner: "Plan",
  sql_generator: "Generate SQL",
  sql_validator: "Validate",
  sql_executor: "Run Query",
  response_synthesizer: "Write Answer",
};

const STEP_KEYS = Object.keys(STEP_LABELS);

/* ── Artifact types ───────────────────────────────── */

interface Artifacts {
  sql: string | null;
  table: { columns: string[]; rows: unknown[][] } | null;
  chart: Record<string, unknown> | null;
}

type ArtifactTab = "answer" | "sql" | "table" | "chart";

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
  const [artifacts, setArtifacts] = useState<Artifacts>({
    sql: null,
    table: null,
    chart: null,
  });
  const [activeTab, setActiveTab] = useState<ArtifactTab>("answer");
  const [showProgress, setShowProgress] = useState(false);
  const [lastArtifacts, setLastArtifacts] = useState<Artifacts>({
    sql: null,
    table: null,
    chart: null,
  });
  const [showArtifactTabs, setShowArtifactTabs] = useState(false);

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
    // Cancel in-flight stream if any
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
    setArtifacts({ sql: null, table: null, chart: null });
    setActiveTab("answer");
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
    setArtifacts({ sql: null, table: null, chart: null });
    setActiveTab("answer");

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
    const streamArtifacts: Artifacts = { sql: null, table: null, chart: null };

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
          // Mark previous step as completed
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
          streamArtifacts.sql = data.sql;
          setArtifacts((prev) => ({ ...prev, sql: data.sql }));
        },

        onArtifactTable(data) {
          streamArtifacts.table = { columns: data.columns, rows: data.rows };
          setArtifacts((prev) => ({
            ...prev,
            table: { columns: data.columns, rows: data.rows },
          }));
        },

        onArtifactChart(data) {
          streamArtifacts.chart = data.chartSpec;
          setArtifacts((prev) => ({ ...prev, chart: data.chartSpec }));
        },

        async onComplete() {
          // Mark last step done
          if (prevStep) {
            setCompletedSteps((prev) => new Set(prev).add(prevStep!));
          }
          setActiveStep(null);
          setShowProgress(false);
          setLastArtifacts({ ...streamArtifacts });
          setShowArtifactTabs(true);

          // Reload persisted messages from server
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
          // Remove optimistic message
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
              {/* Persisted messages */}
              {messages.map((m) => (
                <MessageBubble key={m.id} message={m} />
              ))}

              {/* Progress panel (while streaming) */}
              {showProgress && (
                <ProgressPanel
                  activeStep={activeStep}
                  completedSteps={completedSteps}
                />
              )}

              {/* Streaming assistant bubble */}
              {streamingText && (
                <div className="flex justify-start">
                  <div className="max-w-[80%] rounded-lg bg-surface-overlay px-4 py-2.5 text-sm leading-relaxed text-neutral-200">
                    <p className="whitespace-pre-wrap">{streamingText}</p>
                    <span className="inline-block h-4 w-1.5 animate-pulse bg-neutral-400 align-text-bottom" />
                  </div>
                </div>
              )}

              {/* Artifact tabs (after completion, for the last assistant message) */}
              {showArtifactTabs && !sending && (
                <ArtifactPanel
                  artifacts={lastArtifacts}
                  activeTab={activeTab}
                  onTabChange={setActiveTab}
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
}: {
  activeStep: string | null;
  completedSteps: Set<string>;
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
      </div>
    </div>
  );
}

function ArtifactPanel({
  artifacts,
  activeTab,
  onTabChange,
}: {
  artifacts: Artifacts;
  activeTab: ArtifactTab;
  onTabChange: (tab: ArtifactTab) => void;
}) {
  const tabs: { key: ArtifactTab; label: string }[] = [
    { key: "answer", label: "Answer" },
    { key: "sql", label: "SQL" },
    { key: "table", label: "Table" },
    { key: "chart", label: "Chart" },
  ];

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
          <div>
            {artifacts.sql ? (
              <pre className="overflow-x-auto rounded bg-surface p-3 font-mono text-xs text-neutral-300">
                {artifacts.sql}
              </pre>
            ) : (
              <p className="text-xs text-neutral-500">Not available yet.</p>
            )}
          </div>
        )}

        {activeTab === "table" && (
          <div>
            {artifacts.table ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-border">
                      {artifacts.table.columns.map((col) => (
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
                    {artifacts.table.rows.map((row, ri) => (
                      <tr key={ri} className="border-b border-border/50">
                        {row.map((cell, ci) => (
                          <td key={ci} className="px-3 py-1.5 text-neutral-400">
                            {String(cell)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-xs text-neutral-500">Not available yet.</p>
            )}
          </div>
        )}

        {activeTab === "chart" && (
          <p className="text-xs text-neutral-500">
            {artifacts.chart
              ? "Chart visualization available in P6."
              : "Not available yet."}
          </p>
        )}
      </div>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import {
  getSessions,
  getSessionMessages,
  sendChat,
} from "../api/client";
import type { Session, Message } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import Sidebar from "../components/Sidebar";

export default function Chat() {
  const { user } = useAuth();

  /* ── state ──────────────────────────────────────── */
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  const bottomRef = useRef<HTMLDivElement>(null);

  /* ── load sessions on mount ─────────────────────── */
  useEffect(() => {
    refreshSessions();
  }, []);

  async function refreshSessions() {
    try {
      const list = await getSessions();
      setSessions(list);
    } catch {
      // silent
    }
  }

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

  /* ── scroll to bottom on new messages ───────────── */
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  /* ── handlers ───────────────────────────────────── */
  function handleSelectSession(id: number) {
    setActiveSessionId(id);
  }

  function handleNewChat() {
    setActiveSessionId(null);
    setMessages([]);
    setInput("");
  }

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;

    setSending(true);
    setInput("");

    // Optimistic: show user message immediately
    const optimistic: Message = {
      id: -Date.now(),
      session_id: activeSessionId ?? 0,
      role: "user",
      content: text,
      sql_query: null,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimistic]);

    try {
      const res = await sendChat({
        session_id: activeSessionId ?? undefined,
        message: text,
      });

      // If a new session was created, select it
      if (activeSessionId === null) {
        setActiveSessionId(res.session_id);
      }

      // Replace optimistic messages with real ones
      setMessages(res.messages);

      // Refresh session list (new session or updated title/time)
      await refreshSessions();
    } catch {
      // Remove optimistic message on error
      setMessages((prev) => prev.filter((m) => m.id !== optimistic.id));
    } finally {
      setSending(false);
    }
  }

  /* ── render ─────────────────────────────────────── */
  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelectSession={handleSelectSession}
        onNewChat={handleNewChat}
      />

      {/* Main panel */}
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

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-6 py-6">
          {messages.length === 0 ? (
            <div className="flex h-full items-center justify-center">
              <p className="max-w-md text-center text-sm leading-relaxed text-neutral-500">
                Ask a question about your pharmaceutical data.
              </p>
            </div>
          ) : (
            <div className="mx-auto max-w-2xl space-y-4">
              {messages.map((m) => (
                <div
                  key={m.id}
                  className={`flex ${
                    m.role === "user" ? "justify-end" : "justify-start"
                  }`}
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
              ))}
              <div ref={bottomRef} />
            </div>
          )}
        </div>

        {/* Input bar */}
        <div className="border-t border-border px-6 py-4">
          <form
            onSubmit={handleSend}
            className="mx-auto flex max-w-2xl gap-3"
          >
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

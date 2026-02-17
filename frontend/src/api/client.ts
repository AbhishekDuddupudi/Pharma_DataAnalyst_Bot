/**
 * Centralised API client.
 *
 * Every fetch goes through `request()` so credentials, auth headers,
 * and error handling are managed in one place.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
    credentials: "include",
  });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }

  return res.json() as Promise<T>;
}

/* ── Public helpers ─────────────────────────────────── */

export interface HealthResponse {
  status: string;
}

export interface VersionResponse {
  app: string;
  version: string;
  environment: string;
}

export interface User {
  id: number;
  email: string;
  display_name: string | null;
}

export interface LoginResponse {
  user: User;
}

export interface MeResponse {
  user: User | null;
}

export interface LogoutResponse {
  ok: boolean;
}

export function health(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

export function version(): Promise<VersionResponse> {
  return request<VersionResponse>("/version");
}

export function login(email: string, password: string): Promise<LoginResponse> {
  return request<LoginResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function logout(): Promise<LogoutResponse> {
  return request<LogoutResponse>("/auth/logout", { method: "POST" });
}

export function fetchMe(): Promise<MeResponse> {
  return request<MeResponse>("/auth/me");
}

/* ── Chat sessions & messages ──────────────────────── */

export interface Session {
  id: number;
  user_id: number;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface AssistantMetadata {
  sql_tasks?: { title: string; sql: string }[];
  tables?: { title: string; columns: string[]; rows: unknown[][] }[];
  chart?: ChartArtifact | null;
  assumptions?: string[];
  follow_ups?: string[];
}

export interface Message {
  id: number;
  session_id: number;
  role: "user" | "assistant";
  content: string;
  sql_query: string | null;
  metadata: AssistantMetadata | null;
  artifacts_json: {
    sql_tasks?: { title: string; sql: string; error?: string }[];
    tables?: { title: string; columns: string[]; rows: unknown[][] }[];
    chart?: ChartArtifact | null;
  } | null;
  assumptions: string[] | null;
  followups: string[] | null;
  metrics_json: {
    total_ms?: number;
    llm_ms?: number;
    db_ms?: number;
    rows_returned?: number;
  } | null;
  created_at: string;
}

export interface ChatResponse {
  session_id: number;
  answer: string;
  messages: Message[];
}

export function getSessions(): Promise<Session[]> {
  return request<Session[]>("/sessions");
}

export function createSession(): Promise<Session> {
  return request<Session>("/sessions", { method: "POST" });
}

export function getSessionMessages(sessionId: number): Promise<Message[]> {
  return request<Message[]>(`/sessions/${sessionId}/messages`);
}

export function sendChat(body: {
  session_id?: number;
  message: string;
}): Promise<ChatResponse> {
  return request<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/* ── SSE streaming chat ────────────────────────────── */

export interface SqlTask {
  title: string;
  sql: string;
  error?: string;
}

export interface TableArtifact {
  task_title: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
}

export interface ChartArtifact {
  available: boolean;
  chart_type?: string;
  x_column?: string;
  y_column?: string;
  title?: string;
}

export interface MetricsData {
  total_ms: number;
  llm_ms: number;
  db_ms: number;
  rows_returned: number;
  tokens_streamed: number;
  retries_used: number;
}

export interface RetryData {
  type: "validator" | "db";
  attempt: number;
  max: number;
  reason: string;
}

export interface AuditData {
  request_id: string;
  mode: string;
  tasks_count: number;
  retries_used: number;
  tables_used: string[];
  safety_checks_passed: boolean;
}

export interface CompleteData {
  ok: boolean;
  blocked?: boolean;
  needs_clarification?: boolean;
  questions?: string[];
  reason?: string;
}

export interface StreamChatCallbacks {
  onRequestId?: (data: { request_id: string }) => void;
  onSession?: (data: { session_id: number }) => void;
  onStatus?: (data: { step: string; message?: string }) => void;
  onToken?: (data: { text: string }) => void;
  onArtifactSql?: (data: { tasks: SqlTask[] }) => void;
  onArtifactTable?: (data: TableArtifact) => void;
  onArtifactChart?: (data: ChartArtifact) => void;
  onAnswerMeta?: (data: { assumptions: string[]; follow_ups: string[] }) => void;
  onRetry?: (data: RetryData) => void;
  onMetrics?: (data: MetricsData) => void;
  onAudit?: (data: AuditData) => void;
  onComplete?: (data: CompleteData) => void;
  onError?: (data: { message: string }) => void;
}

/**
 * POST /api/chat/stream — SSE streaming chat.
 *
 * Uses fetch + ReadableStream (not EventSource) so we can send a POST body.
 * Returns an AbortController so the caller can cancel the stream.
 */
export function streamChat(
  body: { session_id?: number; message: string },
  callbacks: StreamChatCallbacks,
): AbortController {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await fetch(`${BASE_URL}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!res.ok) {
        const text = await res.text();
        callbacks.onError?.({ message: `API ${res.status}: ${text}` });
        return;
      }

      const reader = res.body?.getReader();
      if (!reader) {
        callbacks.onError?.({ message: "No response body" });
        return;
      }

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Process complete SSE frames (double newline delimited)
        const frames = buffer.split("\n\n");
        // Keep the last (possibly incomplete) frame in buffer
        buffer = frames.pop() ?? "";

        for (const frame of frames) {
          if (!frame.trim()) continue;
          const { event, data } = parseSSEFrame(frame);
          if (!event || data === null) continue;
          dispatchSSEEvent(event, data, callbacks);
        }
      }

      // Process any remaining buffer
      if (buffer.trim()) {
        const { event, data } = parseSSEFrame(buffer);
        if (event && data !== null) {
          dispatchSSEEvent(event, data, callbacks);
        }
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      callbacks.onError?.({
        message: err instanceof Error ? err.message : "Stream failed",
      });
    }
  })();

  return controller;
}

function parseSSEFrame(frame: string): { event: string | null; data: unknown } {
  let event: string | null = null;
  const dataLines: string[] = [];

  for (const line of frame.split("\n")) {
    if (line.startsWith("event: ")) {
      event = line.slice(7).trim();
    } else if (line.startsWith("data: ")) {
      dataLines.push(line.slice(6));
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5));
    }
  }

  if (dataLines.length === 0) return { event, data: null };

  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return { event, data: null };
  }
}

function dispatchSSEEvent(
  event: string,
  data: unknown,
  cb: StreamChatCallbacks,
): void {
  const d = data as Record<string, unknown>;
  switch (event) {
    case "request_id":
      cb.onRequestId?.(d as { request_id: string });
      break;
    case "session":
      cb.onSession?.(d as { session_id: number });
      break;
    case "status":
      cb.onStatus?.(d as { step: string; message?: string });
      break;
    case "token":
      cb.onToken?.(d as { text: string });
      break;
    case "artifact_sql":
      cb.onArtifactSql?.(d as { tasks: SqlTask[] });
      break;
    case "artifact_table":
      cb.onArtifactTable?.(d as TableArtifact);
      break;
    case "artifact_chart":
      cb.onArtifactChart?.(d as ChartArtifact);
      break;
    case "answer_meta":
      cb.onAnswerMeta?.(d as { assumptions: string[]; follow_ups: string[] });
      break;
    case "retry":
      cb.onRetry?.(d as RetryData);
      break;
    case "metrics":
      cb.onMetrics?.(d as MetricsData);
      break;
    case "audit":
      cb.onAudit?.(d as AuditData);
      break;
    case "complete":
      cb.onComplete?.(d as CompleteData);
      break;
    case "error":
      cb.onError?.(d as { message: string });
      break;
  }
}

export default request;

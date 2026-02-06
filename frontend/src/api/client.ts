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

export interface Message {
  id: number;
  session_id: number;
  role: "user" | "assistant";
  content: string;
  sql_query: string | null;
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

export default request;

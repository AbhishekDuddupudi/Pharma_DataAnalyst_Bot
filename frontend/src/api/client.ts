/**
 * Centralised API client.
 *
 * Every fetch goes through `request()` so we can later add
 * credentials, auth headers, and error handling in one place.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
    // credentials: "include",  // enable once cookie-auth is wired up
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

export function health(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

export function version(): Promise<VersionResponse> {
  return request<VersionResponse>("/version");
}

export default request;

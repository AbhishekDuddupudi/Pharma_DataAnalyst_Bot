import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import type { ReactNode } from "react";
import type { User } from "../api/client";
import { fetchMe, login as apiLogin, logout as apiLogout } from "../api/client";

/* ── Types ─────────────────────────────────────────── */

interface AuthState {
  /** null = not logged in, undefined = still loading */
  user: User | null | undefined;
  loading: boolean;
  error: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

/* ── Context ───────────────────────────────────────── */

const AuthContext = createContext<AuthState | null>(null);

/* ── Provider ──────────────────────────────────────── */

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // On mount: check existing session
  useEffect(() => {
    fetchMe()
      .then((res) => setUser(res.user ?? null))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    setError(null);
    setLoading(true);
    try {
      const res = await apiLogin(email, password);
      setUser(res.user);
    } catch (err) {
      const msg =
        err instanceof Error && err.message.includes("401")
          ? "Invalid email or password"
          : "Login failed. Please try again.";
      setError(msg);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setUser(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, error, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

/* ── Hook ──────────────────────────────────────────── */

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within <AuthProvider>");
  }
  return ctx;
}

import { Navigate } from "react-router-dom";
import { useAuth } from "./AuthContext";
import type { ReactNode } from "react";

/**
 * Wrapper that redirects to /login when the user is not authenticated.
 * Shows nothing while the initial /me check is in flight.
 */
export default function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();

  if (loading || user === undefined) {
    // Initial session check still in progress
    return (
      <div className="flex h-screen items-center justify-center bg-surface">
        <span className="text-sm text-neutral-500">Loading...</span>
      </div>
    );
  }

  if (user === null) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}

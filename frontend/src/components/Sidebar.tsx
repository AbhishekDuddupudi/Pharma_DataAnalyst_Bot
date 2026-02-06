import { useAuth } from "../auth/AuthContext";
import type { Session } from "../api/client";

interface SidebarProps {
  sessions: Session[];
  activeSessionId: number | null;
  onSelectSession: (id: number) => void;
  onNewChat: () => void;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 7) return `${diffDay}d ago`;
  return d.toLocaleDateString();
}

export default function Sidebar({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewChat,
}: SidebarProps) {
  const { user, logout } = useAuth();

  const handleLogout = async () => {
    await logout();
  };

  return (
    <aside className="flex w-60 flex-col border-r border-border bg-surface-raised">
      {/* Brand */}
      <div className="flex h-14 items-center border-b border-border px-5">
        <span className="text-sm font-semibold tracking-wide text-neutral-100">
          Pharma Analyst
        </span>
      </div>

      {/* New Chat button */}
      <div className="px-3 pt-3">
        <button
          onClick={onNewChat}
          className="w-full rounded-md border border-border px-3 py-2 text-left text-sm font-medium text-neutral-300 transition-colors hover:bg-surface-overlay hover:text-white"
        >
          + New chat
        </button>
      </div>

      {/* Session list */}
      <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-3">
        {sessions.map((s) => {
          const active = s.id === activeSessionId;
          return (
            <button
              key={s.id}
              onClick={() => onSelectSession(s.id)}
              className={`flex w-full flex-col rounded-md px-3 py-2 text-left transition-colors ${
                active
                  ? "bg-surface-overlay text-white"
                  : "text-neutral-400 hover:bg-surface-overlay hover:text-neutral-200"
              }`}
            >
              <span className="truncate text-sm">
                {s.title ?? "Untitled chat"}
              </span>
              <span className="mt-0.5 text-xs text-neutral-500">
                {formatTime(s.updated_at)}
              </span>
            </button>
          );
        })}

        {sessions.length === 0 && (
          <p className="px-3 py-4 text-center text-xs text-neutral-500">
            No conversations yet
          </p>
        )}
      </nav>

      {/* Footer – user info + logout */}
      <div className="space-y-2 border-t border-border px-5 py-3">
        {user && (
          <p className="truncate text-xs text-neutral-400">
            {user.display_name ?? user.email}
          </p>
        )}
        <button
          onClick={handleLogout}
          className="text-xs text-neutral-500 transition-colors hover:text-neutral-300"
        >
          Logout
        </button>
      </div>
    </aside>
  );
}

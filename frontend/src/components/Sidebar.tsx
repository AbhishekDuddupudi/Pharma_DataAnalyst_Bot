import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const NAV_ITEMS = [
  { label: "Chat", path: "/" },
  // Future: { label: "History", path: "/history" },
] as const;

export default function Sidebar() {
  const { pathname } = useLocation();
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

      {/* Navigation */}
      <nav className="flex-1 space-y-1 px-3 py-4">
        {NAV_ITEMS.map(({ label, path }) => {
          const active = pathname === path;
          return (
            <Link
              key={path}
              to={path}
              className={`block rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                active
                  ? "bg-surface-overlay text-white"
                  : "text-neutral-400 hover:bg-surface-overlay hover:text-neutral-200"
              }`}
            >
              {label}
            </Link>
          );
        })}
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

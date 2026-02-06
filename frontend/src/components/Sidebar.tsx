import { Link, useLocation } from "react-router-dom";

const NAV_ITEMS = [
  { label: "Chat", path: "/" },
  // Future: { label: "History", path: "/history" },
] as const;

export default function Sidebar() {
  const { pathname } = useLocation();

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

      {/* Footer */}
      <div className="border-t border-border px-5 py-3">
        <span className="text-xs text-neutral-500">v0.1.0</span>
      </div>
    </aside>
  );
}

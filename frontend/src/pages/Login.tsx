import { Link } from "react-router-dom";

export default function Login() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-surface">
      <div className="w-full max-w-sm space-y-6 rounded-xl border border-border bg-surface-raised p-8">
        {/* Title */}
        <div className="space-y-1 text-center">
          <h1 className="text-lg font-semibold text-neutral-100">
            Pharma Analyst
          </h1>
          <p className="text-xs text-neutral-500">
            Sign in to continue
          </p>
        </div>

        {/* Form (placeholder – no submit logic) */}
        <form
          onSubmit={(e) => e.preventDefault()}
          className="space-y-4"
        >
          <div className="space-y-1.5">
            <label className="block text-xs font-medium text-neutral-400">
              Email
            </label>
            <input
              type="email"
              placeholder="you@example.com"
              className="input-base"
              disabled
            />
          </div>

          <div className="space-y-1.5">
            <label className="block text-xs font-medium text-neutral-400">
              Password
            </label>
            <input
              type="password"
              placeholder="••••••••"
              className="input-base"
              disabled
            />
          </div>

          <button type="submit" disabled className="btn-primary w-full opacity-60">
            Sign in
          </button>
        </form>

        <p className="text-center text-xs text-neutral-500">
          <Link to="/" className="text-accent hover:text-accent-hover transition-colors">
            Back to chat
          </Link>
        </p>
      </div>
    </div>
  );
}

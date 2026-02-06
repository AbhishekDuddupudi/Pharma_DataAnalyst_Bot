import { useEffect, useState } from "react";
import { health } from "../api/client";

export default function Chat() {
  const [backendOk, setBackendOk] = useState<boolean | null>(null);

  useEffect(() => {
    health()
      .then(() => setBackendOk(true))
      .catch(() => setBackendOk(false));
  }, []);

  return (
    <div className="flex flex-1 flex-col">
      {/* Header bar */}
      <header className="flex h-14 items-center border-b border-border px-6">
        <h1 className="text-sm font-semibold text-neutral-100">
          Chat
        </h1>
        <span className="ml-auto text-xs text-neutral-500">
          {backendOk === null
            ? "Connecting..."
            : backendOk
              ? "Backend connected"
              : "Backend unreachable"}
        </span>
      </header>

      {/* Message area (placeholder) */}
      <div className="flex flex-1 items-center justify-center">
        <p className="max-w-md text-center text-sm leading-relaxed text-neutral-500">
          Ask a question about your pharmaceutical data.
          <br />
          The chat interface will be wired up in the next iteration.
        </p>
      </div>

      {/* Input bar (placeholder) */}
      <div className="border-t border-border px-6 py-4">
        <div className="mx-auto flex max-w-2xl gap-3">
          <input
            type="text"
            placeholder="Type your question..."
            disabled
            className="input-base flex-1 opacity-60"
          />
          <button disabled className="btn-primary opacity-60">
            Send
          </button>
        </div>
      </div>
    </div>
  );
}

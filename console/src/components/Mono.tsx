import { useState } from "react";

/** Ids, hashes and numbers in the monospace face; `copy` adds a copy-to-clipboard button. */
export function Mono({ children, copy = false }: { children: string; copy?: boolean }) {
  const [copied, setCopied] = useState(false);

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(children);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  return (
    <span className="inline-flex items-center gap-1 font-mono text-[0.8125rem] tabular-nums">
      <span className="break-all">{children}</span>
      {copy && (
        <button
          type="button"
          onClick={onCopy}
          aria-label={`Copy ${children}`}
          className="rounded-sm border border-border px-1 text-[0.6875rem] text-muted hover:text-text focus-visible:outline-2 focus-visible:outline-accent"
        >
          {copied ? "copied" : "copy"}
        </button>
      )}
    </span>
  );
}

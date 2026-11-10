import { useEffect, useId, useRef, useState, type FocusEvent, type KeyboardEvent, type ReactNode } from "react";

import type { Schema } from "../../api/client";

type ResolvedCitation = Schema<"ResolvedCitation">;
type Passage = Schema<"PassageView">;

/** `A:5c2859d2…#by_split.val.auc` or `K:k_853dddea8b5f`: the chip text, short enough for prose. */
function chipLabel(marker: string): string {
  const inner = marker.slice(1, -1);
  if (!inner.startsWith("A:")) return inner;
  const target = inner.slice(2);
  const hash = target.indexOf("#");
  const id = hash === -1 ? target : target.slice(0, hash);
  const path = hash === -1 ? "" : target.slice(hash);
  return `A:${id.length > 8 ? `${id.slice(0, 8)}…` : id}${path}`;
}

function chipKind(marker: string): "A" | "K" {
  return marker.startsWith("[K:") ? "K" : "A";
}

const CHIP_TONE = {
  A: "border-accent/70 text-accent hover:bg-accent/10",
  K: "border-muted/60 text-muted hover:bg-muted/10",
  unresolved: "border-status-attention text-status-attention hover:bg-status-attention/10",
} as const;

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="flex flex-col gap-0.5">
      <span className="text-[0.6875rem] tracking-wide text-muted uppercase">{label}</span>
      {children}
    </span>
  );
}

const mono = "font-mono text-[0.75rem] break-all text-text";

function NoteLine({ note }: { note: string }) {
  return <span className="text-[0.75rem] text-status-awaiting">Note: {note}</span>;
}

function ArtifactBody({ citation }: { citation: ResolvedCitation }) {
  return (
    <>
      <Field label="Artifact kind">
        <span className={mono}>{citation.artifact_kind ?? "unknown"}</span>
      </Field>
      <Field label="Path">
        <span className={mono}>{citation.path ?? "(whole artifact)"}</span>
      </Field>
      <Field label="Value">
        <code className="block max-h-48 overflow-auto rounded-sm border border-border bg-bg px-2 py-1 font-mono text-[0.75rem] whitespace-pre-wrap text-text">
          {JSON.stringify(citation.value ?? null, null, 2)}
        </code>
      </Field>
      {citation.artifact_id != null && (
        <Field label="Artifact id">
          <span className={`${mono} text-muted`}>{citation.artifact_id}</span>
        </Field>
      )}
    </>
  );
}

/** Only web URLs become links; a repository path stays text, so it never resolves into the console. */
function SourceLink({ url }: { url: string }) {
  if (!/^https?:\/\//.test(url)) return <span className={mono}>{url}</span>;
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer noopener"
      className="font-mono text-[0.75rem] break-all text-accent underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-accent"
    >
      {url}
    </a>
  );
}

function PassageBody({ passage }: { passage: Passage }) {
  return (
    <>
      <span className="text-sm leading-snug font-semibold text-text">{passage.title}</span>
      <span className="text-[0.75rem] text-muted">{passage.section}</span>
      <span className="block max-h-48 overflow-auto border-l-2 border-border pl-2 text-[0.8125rem] leading-relaxed text-text">
        {passage.text}
      </span>
      <Field label="Source">
        <SourceLink url={passage.url} />
      </Field>
    </>
  );
}

function UnresolvedBody({ marker, citation }: { marker: string; citation?: ResolvedCitation }) {
  return (
    <>
      <span className="text-sm font-semibold text-status-attention">Unresolved citation</span>
      <span className={mono}>{citation?.target ?? marker.slice(3, -1)}</span>
      <NoteLine note={citation?.note ?? "not in this section's citation list"} />
    </>
  );
}

/** How long the popover outlives the pointer, so it can travel from the chip to the popover. */
const CLOSE_DELAY_MS = 150;

/**
 * One citation marker as a chip; hovering or focusing it opens a popover with what it points
 * at: the artifact value for `A`, the knowledge passage for `K`, or why it did not resolve.
 */
export function Citation({ marker, citation }: { marker: string; citation?: ResolvedCitation }) {
  const [open, setOpen] = useState(false);
  const popoverId = useId();
  const kind = citation?.kind ?? chipKind(marker);
  const resolved = citation?.resolved === true;
  const passage = citation?.passage ?? null;

  const wrapper = useRef<HTMLSpanElement>(null);
  const closeTimer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(closeTimer.current), []);

  function onMouseEnter() {
    window.clearTimeout(closeTimer.current);
    setOpen(true);
  }

  /**
   * Leaving with the pointer closes the popover after a short grace period, cancelled when the
   * pointer comes back (on the chip or the popover), and not at all while focus is inside it.
   */
  function onMouseLeave() {
    window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => {
      if (!wrapper.current?.contains(document.activeElement)) setOpen(false);
    }, CLOSE_DELAY_MS);
  }

  function onBlur(event: FocusEvent<HTMLSpanElement>) {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLSpanElement>) {
    if (event.key === "Escape" && open) {
      event.stopPropagation();
      setOpen(false);
    }
  }

  let body: ReactNode;
  if (!resolved || citation === undefined) {
    body = <UnresolvedBody marker={marker} citation={citation} />;
  } else if (kind === "K") {
    body =
      passage === null ? (
        <>
          <span className={mono}>{citation.target}</span>
          <NoteLine note="passage not available" />
        </>
      ) : (
        <PassageBody passage={passage} />
      );
  } else {
    body = <ArtifactBody citation={citation} />;
  }

  return (
    <span
      ref={wrapper}
      className="relative inline-block align-baseline"
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      onFocus={() => setOpen(true)}
      onBlur={onBlur}
      onKeyDown={onKeyDown}
    >
      <button
        type="button"
        aria-label={`Citation ${marker}`}
        aria-expanded={open}
        aria-controls={open ? popoverId : undefined}
        data-kind={kind}
        data-resolved={String(resolved)}
        onClick={() => setOpen(true)}
        className={`mx-0.5 inline-flex h-[1.125rem] cursor-help items-center rounded-sm border px-1 font-mono text-[0.6875rem] leading-none whitespace-nowrap transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent ${
          resolved ? CHIP_TONE[kind] : CHIP_TONE.unresolved
        }`}
      >
        {chipLabel(marker)}
      </button>
      {open && (
        <span
          id={popoverId}
          role="dialog"
          aria-label={`Citation ${marker}`}
          className="absolute top-full left-0 z-20 block w-[min(26rem,80vw)] pt-1"
        >
          {/* The transparent `pt-1` band bridges chip and box, so the pointer never leaves. */}
          <span className="flex flex-col gap-2 rounded-sm border border-border bg-panel-2 p-3 text-left font-sans shadow-lg shadow-black/40">
            {body}
            {resolved && citation?.note != null && <NoteLine note={citation.note} />}
          </span>
        </span>
      )}
    </span>
  );
}

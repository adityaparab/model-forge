import { useDocument } from "../../api/queries";
import type { Schema } from "../../api/client";
import { API_PREFIX } from "../../api/client";
import { Mono } from "../../components/Mono";
import { GroundingBadge } from "../../components/doc/GroundingBadge";
import { Markdown } from "../../components/doc/Markdown";
import { isBannered } from "../../components/doc/markers";
import { useRunDetail } from "./useRunDetail";

type SectionView = Schema<"SectionView">;

/** A section needs the author when it carries the renderer's banner or fails a grounding check. */
function needsAttention(section: SectionView): boolean {
  return isBannered(section.body_md) || (section.grounding !== null && !section.grounding.ok);
}

const downloadLink =
  "inline-flex h-7 items-center rounded-sm border border-border px-2.5 text-xs text-text hover:border-accent hover:text-accent focus-visible:outline-2 focus-visible:outline-accent";

function SectionNav({ sections }: { sections: SectionView[] }) {
  return (
    <nav aria-label="Sections" className="lg:sticky lg:top-4 lg:self-start">
      <h2 className="mb-2 text-[0.6875rem] tracking-wide text-muted uppercase">Sections</h2>
      <ol className="flex flex-col border-l border-border">
        {sections.map((section, index) => {
          const flagged = needsAttention(section);
          return (
            <li key={section.section_id}>
              <a
                href={`#${section.section_id}`}
                className={`-ml-px flex items-baseline gap-2 border-l-2 py-1 pr-2 pl-3 text-[0.8125rem] leading-snug hover:text-accent focus-visible:outline-2 focus-visible:outline-accent ${
                  flagged ? "border-status-awaiting text-text" : "border-transparent text-muted"
                }`}
              >
                <span className="font-mono text-[0.6875rem] text-muted tabular-nums">
                  {index + 1}
                </span>
                <span className="flex-1">{section.title}</span>
                {flagged && (
                  <span className="flex items-center gap-1 text-[0.6875rem] text-status-awaiting">
                    <span aria-hidden="true" className="size-1.5 rounded-full bg-status-awaiting" />
                    <span className="sr-only">needs attention</span>
                  </span>
                )}
              </a>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

function DocumentSection({ section, index }: { section: SectionView; index: number }) {
  const headingId = `${section.section_id}-heading`;
  return (
    <section
      id={section.section_id}
      aria-labelledby={headingId}
      className="flex scroll-mt-4 flex-col gap-3 border-t border-border pt-5 first:border-t-0 first:pt-0"
    >
      <header className="flex flex-col gap-2">
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-xs text-muted tabular-nums">{index + 1}.</span>
          <h2 id={headingId} className="text-base font-semibold text-text">
            {section.title}
          </h2>
          {!section.required && <span className="text-xs text-muted">optional</span>}
        </div>
        <GroundingBadge grounding={section.grounding} />
      </header>
      <Markdown body={section.body_md} citations={section.citations} />
    </section>
  );
}

/** The development document: its sections with grounding verdicts and inspectable citations. */
export function DocumentTab() {
  const { run } = useRunDetail();
  const document = useDocument(run.id);

  if (document.isError) {
    return (
      <p role="alert" className="rounded-sm border border-status-attention px-3 py-2 text-sm">
        Could not load the document: {document.error.message}
      </p>
    );
  }
  if (document.data === undefined) return <p className="text-muted">Loading the document...</p>;

  const { sections, template_id, document_id } = document.data;
  const href = (format: "html" | "md") =>
    `${API_PREFIX}/runs/${encodeURIComponent(run.id)}/document?format=${format}`;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <dl className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
          <div className="flex items-center gap-2">
            <dt className="text-muted">Template</dt>
            <dd>
              <Mono>{template_id}</Mono>
            </dd>
          </div>
          <div className="flex items-center gap-2">
            <dt className="text-muted">Document</dt>
            <dd>
              <Mono copy>{document_id}</Mono>
            </dd>
          </div>
        </dl>
        <div className="flex items-center gap-2">
          <a href={href("html")} download={`${document_id}.html`} className={downloadLink}>
            Download HTML
          </a>
          <a href={href("md")} download={`${document_id}.md`} className={downloadLink}>
            Download Markdown
          </a>
        </div>
      </div>
      <div className="grid gap-8 lg:grid-cols-[14rem_minmax(0,1fr)]">
        <SectionNav sections={sections} />
        <section aria-label="Document" className="flex max-w-[72ch] flex-col gap-8">
          {sections.map((section, index) => (
            <DocumentSection key={section.section_id} section={section} index={index} />
          ))}
        </section>
      </div>
    </div>
  );
}

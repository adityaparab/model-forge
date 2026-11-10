import type { Element, ElementContent, Root } from "hast";
import { useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Schema } from "../../api/client";
import { Citation } from "./Citation";
import { splitMarkers } from "./markers";

type ResolvedCitation = Schema<"ResolvedCitation">;

/** The attribute that tags the spans the marker transform makes (raw HTML is skipped, so only it). */
const MARKER_PROP = "dataMarker";
const MARKER_ATTR = "data-marker";

/** Elements whose text stays literal: a marker in code or in a link text is left as written. */
const LITERAL = new Set(["code", "pre", "a"]);

function transform(children: ElementContent[]): ElementContent[] {
  return children.flatMap((child): ElementContent[] => {
    if (child.type === "element") {
      if (!LITERAL.has(child.tagName)) child.children = transform(child.children);
      return [child];
    }
    if (child.type !== "text") return [child];
    return splitMarkers(child.value).map(
      (part): ElementContent =>
        part.kind === "text"
          ? { type: "text", value: part.text }
          : ({
              type: "element",
              tagName: "span",
              properties: { [MARKER_PROP]: part.marker },
              children: [],
            } satisfies Element),
    );
  });
}

/** A rehype plugin: each marker in a text node becomes `<span data-marker>`. */
function rehypeMarkers() {
  return (tree: Root) => {
    tree.children = tree.children.flatMap((child): Root["children"] =>
      child.type === "doctype" ? [child] : transform([child]),
    );
  };
}

/** A section body: GitHub-flavoured Markdown without raw HTML, markers as citation chips. */
export function Markdown({ body, citations }: { body: string; citations: ResolvedCitation[] }) {
  const byMarker = useMemo(
    () => new Map(citations.map((citation) => [citation.marker, citation])),
    [citations],
  );
  const components = useMemo(
    (): Components => ({
      span(props) {
        const marker: unknown = Reflect.get(props, MARKER_ATTR);
        if (typeof marker !== "string") {
          return <span className={props.className}>{props.children}</span>;
        }
        return <Citation marker={marker} citation={byMarker.get(marker)} />;
      },
    }),
    [byMarker],
  );

  return (
    <div className="doc-prose">
      <ReactMarkdown
        skipHtml
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeMarkers]}
        components={components}
      >
        {body}
      </ReactMarkdown>
    </div>
  );
}

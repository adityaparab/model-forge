/**
 * Citation markers in a section body: `[A:<artifact-id>#<path>]` points at a value inside a run
 * artifact, `[K:<citation-id>]` at a knowledge-base passage. The pattern is the renderer's
 * `MARKER`: a path may hold one level of nested brackets (`tests[disc.auc.oot].value`).
 */
const MARKER_SOURCE = String.raw`\[(A|K):((?:[^\[\]\s]|\[[^\[\]\s]*\])*)\]`;

export type MarkerPart = { kind: "text"; text: string } | { kind: "marker"; marker: string };

/** Splits `text` into its plain runs and its markers, in order; empty runs are dropped. */
export function splitMarkers(text: string): MarkerPart[] {
  const parts: MarkerPart[] = [];
  let last = 0;
  for (const match of text.matchAll(new RegExp(MARKER_SOURCE, "g"))) {
    if (match.index > last) parts.push({ kind: "text", text: text.slice(last, match.index) });
    parts.push({ kind: "marker", marker: match[0] });
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}

/** The banner the renderer puts at the top of a section the author has yet to supply. */
export const ATTENTION_BANNER = "> **REQUIRES AUTHOR ATTENTION**";

export function isBannered(bodyMd: string): boolean {
  return bodyMd.trimStart().startsWith(ATTENTION_BANNER);
}

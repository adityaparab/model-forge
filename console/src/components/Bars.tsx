export type Bar = { label: string; value: number };

/**
 * Horizontal bars as plain divs, each as wide as its share of the largest value; `format`
 * renders the value beside the bar and in its title.
 */
export function Bars({
  items,
  label,
  format = String,
}: {
  items: Bar[];
  label: string;
  format?: (value: number) => string;
}) {
  const max = Math.max(0, ...items.map((item) => item.value));
  return (
    <ul aria-label={label} className="flex flex-col gap-1">
      {items.map((item) => {
        const width = max > 0 ? (item.value / max) * 100 : 0;
        return (
          <li
            key={item.label}
            className="grid grid-cols-[6.5rem_1fr_5.5rem] items-center gap-2 text-xs"
          >
            <span className="font-mono text-muted tabular-nums">{item.label}</span>
            <span className="h-3 border border-border bg-panel">
              <span
                title={`${item.label}: ${format(item.value)}`}
                className="block h-full bg-status-running"
                style={{ width: `${width}%` }}
              />
            </span>
            <span className="text-right font-mono tabular-nums">{format(item.value)}</span>
          </li>
        );
      })}
    </ul>
  );
}

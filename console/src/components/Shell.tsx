import { NavLink, Outlet } from "react-router";

import { useConnectionState, type ConnectionState } from "../events/connection";
import { useIdentity } from "../identity";

const NAV = [
  { to: "/runs", label: "Runs" },
  { to: "/inventory", label: "Inventory" },
  { to: "/costs", label: "Costs" },
  { to: "/settings", label: "Settings" },
] as const;

const DOT: Record<ConnectionState, string> = {
  live: "bg-accent",
  reconnecting: "bg-status-awaiting animate-pulse",
  offline: "bg-status-queued",
};

function ConnectionIndicator() {
  const state = useConnectionState();
  return (
    <span
      role="status"
      aria-label="Connection"
      className="inline-flex items-center gap-2 text-xs text-muted"
    >
      <span aria-hidden="true" className={`size-2 rounded-full ${DOT[state]}`} />
      {state}
    </span>
  );
}

function IdentityInput() {
  const [identity, setIdentity] = useIdentity();
  return (
    <label className="flex items-center gap-2 text-xs text-muted">
      <span>Identity</span>
      <input
        aria-label="Identity"
        value={identity}
        onChange={(event) => setIdentity(event.target.value)}
        placeholder="your name"
        spellCheck={false}
        maxLength={64}
        className="h-7 w-56 rounded-sm border border-border bg-panel-2 px-2 font-mono text-sm text-text placeholder:text-muted/60 focus:border-accent focus:outline-none"
      />
    </label>
  );
}

/** The fixed left navigation, the top bar and the routed page. */
export function Shell() {
  return (
    <div className="grid min-h-screen grid-cols-[11rem_1fr] grid-rows-[3rem_1fr] bg-bg font-sans text-sm text-text">
      <nav
        aria-label="Primary"
        className="row-span-2 flex flex-col border-r border-border bg-panel"
      >
        <div className="flex h-12 items-center border-b border-border px-4 font-mono text-sm font-semibold tracking-wide">
          ModelForge
        </div>
        <ul className="flex flex-col py-2">
          {NAV.map(({ to, label }) => (
            <li key={to}>
              <NavLink
                to={to}
                className={({ isActive }) =>
                  `flex h-8 items-center border-l-2 px-4 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent ${
                    isActive
                      ? "border-accent bg-panel-2 text-text"
                      : "border-transparent text-muted hover:text-text"
                  }`
                }
              >
                {label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <header className="flex items-center justify-end gap-6 border-b border-border bg-panel px-4">
        <IdentityInput />
        <ConnectionIndicator />
      </header>
      <main className="min-w-0 overflow-auto p-4">
        <Outlet />
      </main>
    </div>
  );
}

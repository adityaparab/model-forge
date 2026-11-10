import { useState, type FormEvent, type ReactNode } from "react";

import { usePatchSettings, useSettings } from "../api/queries";
import type { Schema } from "../api/client";
import { Mono } from "../components/Mono";
import { useIdentity } from "../identity";
import { ReadOnlyConfig } from "./settings/ReadOnlyConfig";

type Settings = Schema<"SettingsResponse">;
type SettingsPatch = Schema<"SettingsPatch">;

const field =
  "h-7 rounded-sm border border-border bg-panel-2 px-2 text-sm text-text focus:border-accent focus:outline-none";
const head = "h-8 border-b border-border px-2 text-left text-xs font-medium whitespace-nowrap text-muted";
const cell = "h-8 border-b border-border px-2 whitespace-nowrap";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-sm border border-border p-3">
      <h2 className="text-sm font-semibold">{title}</h2>
      {children}
    </section>
  );
}

/** The identity input works before (and without) the settings; the role map needs them. */
function IdentitySection({ config }: { config: Settings["config"]["identity"] | undefined }) {
  const [identity, setIdentity] = useIdentity();
  return (
    <Section title="Identity">
      <label className="flex flex-col gap-1 text-xs text-muted">
        Acting identity
        <input
          aria-label="Acting identity"
          value={identity}
          onChange={(event) => setIdentity(event.target.value)}
          placeholder="your name"
          spellCheck={false}
          maxLength={64}
          className={`${field} w-64 font-mono`}
        />
      </label>
      {config === undefined ? null : config.mode === "roles" ? (
        <table aria-label="Role map" className="w-full max-w-xl border-collapse border border-border text-[0.8125rem]">
          <thead className="bg-panel">
            <tr>
              <th scope="col" className={head}>
                Identity
              </th>
              <th scope="col" className={head}>
                Roles
              </th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(config.roles).map(([name, roles]) => (
              <tr key={name}>
                <td className={`${cell} font-mono text-[0.75rem]`}>{name}</td>
                <td className={cell}>{roles.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="text-xs text-muted">
          Open mode: any identity may act, and writes are attributed to the name sent.
        </p>
      )}
    </Section>
  );
}

function EditableForm({ settings }: { settings: Settings }) {
  const patch = usePatchSettings();
  // null until edited: the server values show through until then
  const [profile, setProfile] = useState<string | null>(null);
  const [costCap, setCostCap] = useState<string | null>(null);
  const [autoReplay, setAutoReplay] = useState<boolean | null>(null);

  const { editable } = settings;
  const changes: SettingsPatch = {};
  if (profile !== null) changes.default_profile = profile;
  if (costCap !== null && costCap !== "") changes.default_cost_cap_usd = Number(costCap);
  if (autoReplay !== null) changes.auto_replay = autoReplay;
  const unchanged = Object.keys(changes).length === 0;

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    patch.mutate(changes, {
      onSuccess: () => {
        setProfile(null);
        setCostCap(null);
        setAutoReplay(null);
      },
    });
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-4 text-xs text-muted">
        <label className="flex flex-col gap-1">
          Default profile
          <select
            aria-label="Default profile"
            value={profile ?? editable.default_profile}
            onChange={(event) => setProfile(event.target.value)}
            className={`${field} font-mono`}
          >
            {settings.profiles.map(({ name }) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Default cost cap (USD)
          <input
            aria-label="Default cost cap (USD)"
            type="number"
            min={0}
            step={0.01}
            value={costCap ?? String(editable.default_cost_cap_usd)}
            onChange={(event) => setCostCap(event.target.value)}
            className={`${field} w-28 font-mono tabular-nums`}
          />
        </label>
        <label className="flex h-7 items-center gap-2">
          <input
            type="checkbox"
            checked={autoReplay ?? editable.auto_replay}
            onChange={(event) => setAutoReplay(event.target.checked)}
            className="accent-accent"
          />
          Auto replay
        </label>
      </div>
      {patch.isError && (
        <p
          role="alert"
          className="rounded-sm border border-status-attention bg-status-attention/10 px-3 py-2 text-sm text-text"
        >
          {patch.error.message}
        </p>
      )}
      <div>
        <button
          type="submit"
          disabled={patch.isPending || unchanged}
          className="inline-flex h-7 items-center rounded-sm bg-accent px-3 text-sm font-medium text-bg hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
        >
          {patch.isPending ? "Saving..." : "Save"}
        </button>
      </div>
    </form>
  );
}

const show = (value: unknown) => JSON.stringify(value) ?? "";

function AuditList({ audit }: { audit: Settings["audit"] }) {
  if (audit.length === 0) return <p className="text-xs text-muted">No settings changes yet.</p>;
  return (
    <table aria-label="Settings audit" className="w-full border-collapse border border-border text-[0.8125rem]">
      <thead className="bg-panel">
        <tr>
          {["Key", "Old", "New", "By", "When"].map((label) => (
            <th key={label} scope="col" className={head}>
              {label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {audit.map((row) => (
          <tr key={row.id} className="hover:bg-panel">
            <td className={`${cell} font-mono text-[0.75rem]`}>{row.key}</td>
            <td className={cell}>
              <Mono>{show(row.old_value)}</Mono>
            </td>
            <td className={cell}>
              <Mono>{show(row.new_value)}</Mono>
            </td>
            <td className={cell}>{row.updated_by}</td>
            <td className={cell}>
              <time dateTime={row.ts} className="font-mono text-[0.75rem] tabular-nums">
                {row.ts}
              </time>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Who acts, the server-side defaults an admin may change (with their audit), and the rest. */
export function SettingsPage() {
  const settings = useSettings();

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-base font-semibold">Settings</h1>
      <IdentitySection config={settings.data?.config.identity} />
      {settings.isError ? (
        <p role="alert" className="rounded-sm border border-status-attention px-3 py-2 text-sm">
          Could not load the settings: {settings.error.message}
        </p>
      ) : settings.data === undefined ? (
        <p className="text-muted">Loading the settings...</p>
      ) : (
        <>
          <Section title="Server defaults">
            <EditableForm settings={settings.data} />
            <AuditList audit={settings.data.audit} />
          </Section>
          <Section title="Configuration">
            <ReadOnlyConfig config={settings.data.config} profiles={settings.data.profiles} />
          </Section>
        </>
      )}
    </div>
  );
}

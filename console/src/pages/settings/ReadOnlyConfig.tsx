import type { ReactNode } from "react";

import type { Schema } from "../../api/client";
import { Mono } from "../../components/Mono";

type Config = Schema<"ConfigResponse">;
type ProfileView = Schema<"ProfileView">;

const head = "h-8 border-b border-border px-2 text-left text-xs font-medium whitespace-nowrap text-muted";
const cell = "h-8 border-b border-border px-2 whitespace-nowrap";
const mono = `${cell} font-mono text-[0.75rem]`;
const num = `${mono} text-right tabular-nums`;
const dash = <span className="text-muted">-</span>;

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">{title}</h3>
      {children}
    </section>
  );
}

/** A two-column key and value list in a bordered panel. */
function Pairs({ label, pairs }: { label: string; pairs: [string, ReactNode][] }) {
  return (
    <dl
      aria-label={label}
      className="grid grid-cols-[max-content_1fr] rounded-sm border border-border text-[0.8125rem]"
    >
      {pairs.map(([key, value]) => (
        <div key={key} className="col-span-2 grid grid-cols-subgrid border-b border-border last:border-b-0">
          <dt className="flex h-8 items-center px-2 text-xs text-muted">{key}</dt>
          <dd className="flex min-h-8 items-center px-2">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function RoutesTable({ profiles }: { profiles: ProfileView[] }) {
  return (
    <table aria-label="Profile routes" className="w-full border-collapse border border-border text-[0.8125rem]">
      <thead className="bg-panel">
        <tr>
          {["Profile", "Purpose", "Provider", "Model", "Effort", "Structured"].map((label) => (
            <th key={label} scope="col" className={head}>
              {label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {profiles.map((profile) =>
          profile.error !== null ? (
            <tr key={profile.name}>
              <td className={mono}>{profile.name}</td>
              <td colSpan={5} className={`${cell} text-status-attention`}>
                {profile.error}
              </td>
            </tr>
          ) : (
            Object.entries(profile.routes).map(([purpose, route]) => (
              <tr key={`${profile.name}/${purpose}`} className="hover:bg-panel">
                <td className={mono}>{profile.name}</td>
                <td className={cell}>{purpose}</td>
                <td className={`${cell} text-muted`}>{route.provider}</td>
                <td className={mono}>{route.model}</td>
                <td className={cell}>{route.effort}</td>
                <td className={mono}>{route.structured ?? dash}</td>
              </tr>
            ))
          ),
        )}
      </tbody>
    </table>
  );
}

function PricesTable({ profiles }: { profiles: ProfileView[] }) {
  const rows = profiles.flatMap((profile) =>
    Object.entries(profile.prices).map(([model, price]) => ({ profile: profile.name, model, price })),
  );
  if (rows.length === 0) return <p className="text-muted">No profile sets model prices.</p>;
  return (
    <table aria-label="Profile prices" className="w-full border-collapse border border-border text-[0.8125rem]">
      <thead className="bg-panel">
        <tr>
          <th scope="col" className={head}>
            Profile
          </th>
          <th scope="col" className={head}>
            Model
          </th>
          {["Input", "Output", "Cache read", "Cache write"].map((label) => (
            <th key={label} scope="col" className={`${head} text-right`}>
              {label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map(({ profile, model, price }) => (
          <tr key={`${profile}/${model}`} className="hover:bg-panel">
            <td className={mono}>{profile}</td>
            <td className={mono}>{model}</td>
            <td className={num}>{price.input.toFixed(2)}</td>
            <td className={num}>{price.output.toFixed(2)}</td>
            <td className={num}>{price.cache_read.toFixed(2)}</td>
            <td className={num}>{price.cache_write.toFixed(2)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function TraceLink({ url }: { url: string | null }) {
  if (url === null) return dash;
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="font-mono text-[0.8125rem] text-text underline underline-offset-2 hover:text-accent focus-visible:outline-2 focus-visible:outline-accent"
    >
      {url}
    </a>
  );
}

/** What the server runs with and the console cannot change: profiles, paths, tracing, versions. */
export function ReadOnlyConfig({ config, profiles }: { config: Config; profiles: ProfileView[] }) {
  const { tracing } = config;
  return (
    <div className="flex flex-col gap-5">
      <Block title="Profile routes">
        <RoutesTable profiles={profiles} />
      </Block>
      <Block title="Profile prices (USD per million tokens)">
        <PricesTable profiles={profiles} />
      </Block>
      <div className="grid gap-5 xl:grid-cols-2">
        <Block title="Directories">
          <Pairs
            label="Directories"
            pairs={Object.entries(config.directories).map(([name, path]) => [
              name,
              <Mono key={name}>{path}</Mono>,
            ])}
          />
        </Block>
        <div className="flex flex-col gap-5">
          <Block title="Tracing">
            <Pairs
              label="Tracing"
              pairs={[
                [
                  "endpoints",
                  tracing.endpoints.length === 0 ? (
                    <span className="text-muted">none</span>
                  ) : (
                    <ul className="flex flex-col py-1">
                      {tracing.endpoints.map((endpoint) => (
                        <li key={endpoint}>
                          <Mono>{endpoint}</Mono>
                        </li>
                      ))}
                    </ul>
                  ),
                ],
                ["content", <Mono key="content">{tracing.content ? "on" : "off"}</Mono>],
              ]}
            />
            {tracing.content && (
              <p className="text-xs text-status-awaiting">
                Content tracing is on: prompts and completions leave the process for the configured
                collectors.
              </p>
            )}
          </Block>
          <Block title="Trace UIs">
            <Pairs
              label="Trace UIs"
              pairs={[
                ["jaeger", <TraceLink key="jaeger" url={config.trace_urls.jaeger} />],
                ["phoenix", <TraceLink key="phoenix" url={config.trace_urls.phoenix} />],
              ]}
            />
          </Block>
          <Block title="Versions">
            <Pairs
              label="Versions"
              pairs={[
                ...Object.entries(config.versions).map(([name, version]): [string, ReactNode] => [
                  name,
                  <Mono key={name}>{version}</Mono>,
                ]),
                ["console", <Mono key="console">{config.console}</Mono>],
              ]}
            />
          </Block>
        </div>
      </div>
    </div>
  );
}

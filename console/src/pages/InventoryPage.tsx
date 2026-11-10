import { Link } from "react-router";

import { useInventory } from "../api/queries";
import type { Schema } from "../api/client";
import { Mono } from "../components/Mono";

type InventoryEntry = Schema<"InventoryEntry">;

const HEAD = [
  "Model id",
  "Name",
  "Version",
  "Owner",
  "Tier",
  "Status",
  "Approver",
  "Conditions",
  "Run",
];

const cell = "h-8 border-b border-border px-2 whitespace-nowrap";
const dash = <span className="text-muted">-</span>;

function Conditions({ conditions }: { conditions: InventoryEntry["conditions"] }) {
  if (conditions === null || conditions.length === 0) return dash;
  return (
    <ul className="flex flex-col py-1 whitespace-normal">
      {conditions.map((condition) => (
        <li key={condition}>{condition}</li>
      ))}
    </ul>
  );
}

/** The model inventory: one row per approved model version, linked to the run that built it. */
export function InventoryPage() {
  const inventory = useInventory();

  return (
    <section className="flex flex-col gap-3">
      <h1 className="text-base font-semibold">Inventory</h1>
      {inventory.isError ? (
        <p role="alert" className="rounded-sm border border-status-attention px-3 py-2 text-sm">
          Could not load the inventory: {inventory.error.message}
        </p>
      ) : inventory.data === undefined ? (
        <p className="text-muted">Loading the inventory...</p>
      ) : inventory.data.length === 0 ? (
        <p className="text-muted">No approved models yet: a run enters the inventory at sign-off.</p>
      ) : (
        <table aria-label="Inventory" className="w-full border-collapse border border-border text-left">
          <thead className="bg-panel text-xs text-muted">
            <tr>
              {HEAD.map((label) => (
                <th key={label} scope="col" className={`${cell} font-medium`}>
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {inventory.data.map((entry) => (
              <tr key={entry.model_id} className="align-top hover:bg-panel-2">
                <td className={cell}>
                  <Mono>{entry.model_id}</Mono>
                </td>
                <td className={cell} title={entry.intended_use}>
                  {entry.name}
                </td>
                <td className={cell}>
                  <Mono>{String(entry.version)}</Mono>
                </td>
                <td className={cell}>{entry.owner}</td>
                <td className={cell}>
                  <Mono>{String(entry.tier)}</Mono>
                </td>
                <td className={cell}>
                  <span className="inline-flex h-5 items-center rounded-sm border border-border px-2 text-xs">
                    {entry.status}
                  </span>
                </td>
                <td className={cell}>{entry.approver ?? dash}</td>
                <td className={cell}>
                  <Conditions conditions={entry.conditions} />
                </td>
                <td className={cell}>
                  <Link
                    to={`/runs/${encodeURIComponent(entry.run_id)}`}
                    className="text-text hover:text-accent focus-visible:outline-2 focus-visible:outline-accent"
                  >
                    <Mono>{entry.run_id}</Mono>
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

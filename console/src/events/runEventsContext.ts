import { createContext, useContext } from "react";

import type { Schema } from "../api/client";

/** The run page's live events, for the tabs that show them (the Overview's feed). */
export const RunEventsContext = createContext<Schema<"EventRecord">[]>([]);

export function useRunEventsFeed(): Schema<"EventRecord">[] {
  return useContext(RunEventsContext);
}

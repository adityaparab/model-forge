import { useOutletContext } from "react-router";

import type { Schema } from "../../api/client";

/** The `RunDetail` the run page hands its tabs through the outlet context. */
export function useRunDetail(): Schema<"RunDetail"> {
  return useOutletContext<Schema<"RunDetail">>();
}

import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router";

import { Shell } from "./components/Shell";
import { NewRunPage } from "./pages/NewRunPage";
import { RunPage } from "./pages/RunPage";
import { RunsPage } from "./pages/RunsPage";
import { FindingsTab } from "./pages/run/FindingsTab";
import { LineageTab } from "./pages/run/LineageTab";
import { LlmCallsTab } from "./pages/run/LlmCallsTab";
import { OverviewTab } from "./pages/run/OverviewTab";
import { ValidationTab } from "./pages/run/ValidationTab";

/** Stands in for a page until its task fills the route. */
function Placeholder({ title }: { title: string }) {
  return <h1 className="text-base font-semibold">{title}</h1>;
}

/** The document viewer carries react-markdown, so it loads in its own chunk on first visit. */
const DocumentTab = lazy(() =>
  import("./pages/run/DocumentTab").then((module) => ({ default: module.DocumentTab })),
);

export function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/runs" replace />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="runs/new" element={<NewRunPage />} />
        <Route path="runs/:runId" element={<RunPage />}>
          <Route index element={<OverviewTab />} />
          <Route
            path="document"
            element={
              <Suspense fallback={<p className="text-muted">Loading the document...</p>}>
                <DocumentTab />
              </Suspense>
            }
          />
          <Route path="validation" element={<ValidationTab />} />
          <Route path="findings" element={<FindingsTab />} />
          <Route path="lineage" element={<LineageTab />} />
          <Route path="llm-calls" element={<LlmCallsTab />} />
        </Route>
        <Route path="inventory" element={<Placeholder title="Inventory" />} />
        <Route path="costs" element={<Placeholder title="Costs" />} />
        <Route path="settings" element={<Placeholder title="Settings" />} />
        <Route path="*" element={<Placeholder title="Not found" />} />
      </Route>
    </Routes>
  );
}

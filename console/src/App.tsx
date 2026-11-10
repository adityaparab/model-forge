import { Navigate, Route, Routes } from "react-router";

import { Shell } from "./components/Shell";

/** Stands in for a page until its task fills the route. */
function Placeholder({ title }: { title: string }) {
  return <h1 className="text-base font-semibold">{title}</h1>;
}

export function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/runs" replace />} />
        <Route path="runs" element={<Placeholder title="Runs" />} />
        <Route path="runs/:runId" element={<Placeholder title="Run" />} />
        <Route path="inventory" element={<Placeholder title="Inventory" />} />
        <Route path="costs" element={<Placeholder title="Costs" />} />
        <Route path="settings" element={<Placeholder title="Settings" />} />
        <Route path="*" element={<Placeholder title="Not found" />} />
      </Route>
    </Routes>
  );
}

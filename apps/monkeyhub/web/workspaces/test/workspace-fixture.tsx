import { useState } from "react";
import { createRoot } from "react-dom/client";
import { ProjectWorkspace, type ProjectWorkspaceProps, type WorkspaceDesignContext } from "../src/app/ProjectWorkspace";
import { ErrorBoundary } from "../src/app/ErrorBoundary";
import { UserPreferencesProvider, usePreferences } from "./TestProviders";
import "../src/styles.css";

const receiveDesignContext = (context: WorkspaceDesignContext | null) => {
  Object.assign(window, { __workspaceDesignContext: context });
};

type Surface = ProjectWorkspaceProps["workspace"];
let comparisonRequests = 0;

function Fixture() {
  const query = new URLSearchParams(window.location.search);
  const [workspace, setWorkspace] = useState<Surface>(query.get("view") === "board" ? "board" : query.get("view") === "tree" ? "tree" : "arch");
  const [candidateRunId, setCandidateRunId] = useState(query.get("candidate"));
  const [refreshKey, setRefreshKey] = useState(0);
  const [active, setActive] = useState(query.get("active") !== "0");
  // #284: what a Hub result card hands the workspace; `?compare=<run>` asks once on load.
  const [comparisonRequest, setComparisonRequest] = useState(() => {
    const runId = query.get("compare");
    return runId ? { candidateRunId: runId, requestId: ++comparisonRequests } : null;
  });
  const preferences = usePreferences();
  Object.assign(window, { __workspaceFixture: {
    setWorkspace, setCandidateRunId, setActive,
    requestComparison: (runId: string) => setComparisonRequest({ candidateRunId: runId, requestId: ++comparisonRequests }),
    refresh: () => setRefreshKey(value => value + 1),
    setDeveloperMode: preferences.setDeveloperMode,
    setEventStreamVisible: preferences.setEventStreamVisible,
    setLanguage: preferences.setLanguage,
    setTheme: preferences.setTheme,
  } });
  return <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
    <nav aria-label="Test workspace host">
      <button type="button" data-testid="workspace-arch" onClick={() => setWorkspace("arch")}>Arch</button>
      <button type="button" data-testid="workspace-board" onClick={() => setWorkspace("board")}>Board</button>
      <button type="button" data-testid="workspace-tree" onClick={() => setWorkspace("tree")}>Tree</button>
      {/* #284: what a Hub does with comparisonRequest - ask again for this candidate, withdraw, hide the project. */}
      <button type="button" data-testid="host-compare" disabled={!candidateRunId} title={candidateRunId ?? undefined}
        onClick={() => setComparisonRequest({ candidateRunId: candidateRunId!, requestId: ++comparisonRequests })}>Compare candidate</button>
      <button type="button" data-testid="host-compare-cancel" onClick={() => setComparisonRequest(null)}>Cancel comparison</button>
      <label><input type="checkbox" data-testid="host-active" checked={active} onChange={(event) => setActive(event.currentTarget.checked)} /> Project active</label>
    </nav>
    <div style={{ flex: 1, minHeight: 0 }}>
      <ProjectWorkspace workspace={workspace} candidateRunId={candidateRunId} refreshKey={refreshKey} active={active} onWorkspaceChange={setWorkspace}
        comparisonRequest={comparisonRequest}
        onComparisonClose={() => {
          setComparisonRequest(null);
          Object.assign(window, { __workspaceComparisonClosed: ((window as { __workspaceComparisonClosed?: number }).__workspaceComparisonClosed ?? 0) + 1 });
        }}
        onDesignContextChange={receiveDesignContext} />
    </div>
  </div>;
}

createRoot(document.getElementById("root")!).render(
  <UserPreferencesProvider><ErrorBoundary><Fixture /></ErrorBoundary></UserPreferencesProvider>,
);

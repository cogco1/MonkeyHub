import { useState } from "react";
import { createRoot } from "react-dom/client";
import { ProjectWorkspace, type WorkspaceDesignContext } from "../src/app/ProjectWorkspace";
import { ErrorBoundary } from "../src/app/ErrorBoundary";
import { UserPreferencesProvider, usePreferences } from "./TestProviders";
import "../src/styles.css";

const receiveDesignContext = (context: WorkspaceDesignContext | null) => {
  Object.assign(window, { __workspaceDesignContext: context });
};

function Fixture() {
  const query = new URLSearchParams(window.location.search);
  const [workspace, setWorkspace] = useState<"arch" | "board">(query.get("view") === "board" ? "board" : "arch");
  const [candidateRunId, setCandidateRunId] = useState(query.get("candidate"));
  const [refreshKey, setRefreshKey] = useState(0);
  const preferences = usePreferences();
  Object.assign(window, { __workspaceFixture: {
    setWorkspace, setCandidateRunId,
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
    </nav>
    <div style={{ flex: 1, minHeight: 0 }}>
      <ProjectWorkspace workspace={workspace} candidateRunId={candidateRunId} refreshKey={refreshKey} onWorkspaceChange={setWorkspace}
        onDesignContextChange={receiveDesignContext} />
    </div>
  </div>;
}

createRoot(document.getElementById("root")!).render(
  <UserPreferencesProvider><ErrorBoundary><Fixture /></ErrorBoundary></UserPreferencesProvider>,
);

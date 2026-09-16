import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { Connected } from "./app/Connected";
import { ErrorBoundary } from "./app/ErrorBoundary";
import { UserPreferencesProvider } from "./features/settings/preferences";
import { installAnnotationViewLock } from "./workspaces/monkeyarch/annotationViewLock";
import "./styles.css";

installAnnotationViewLock();

const root = document.getElementById("root");
if (!root) throw new Error("ArchFlow Studio root element is missing.");

createRoot(root).render(
  <StrictMode>
    <UserPreferencesProvider>
      <ErrorBoundary>
        <Connected />
      </ErrorBoundary>
    </UserPreferencesProvider>
  </StrictMode>,
);

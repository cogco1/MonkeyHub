import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./app/App";
import { ErrorBoundary } from "./app/ErrorBoundary";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("ArchFlow Studio root element is missing.");

createRoot(root).render(
  <StrictMode>
    <ErrorBoundary label="shell">
      <App />
    </ErrorBoundary>
  </StrictMode>,
);


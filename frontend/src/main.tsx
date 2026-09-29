import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import { LiveProvider } from "./live";
import { OverlayProvider } from "./components/ui";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LiveProvider>
      <OverlayProvider>
        <App />
      </OverlayProvider>
    </LiveProvider>
  </StrictMode>,
);

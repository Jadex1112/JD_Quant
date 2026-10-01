import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import "./styles.css";

function applyTheme() {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem("jq-theme");
  } catch {
    /* storage may be unavailable */
  }
  const theme = stored === "light" ? "light" : "dark"; // dark unless the user switched to light
  document.documentElement.dataset.theme = theme;
}
applyTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);

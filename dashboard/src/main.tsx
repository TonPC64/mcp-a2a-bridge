import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
// @ts-expect-error Vite resolves CSS side-effect imports at runtime.
import "./index.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("Root element #root not found");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>
);

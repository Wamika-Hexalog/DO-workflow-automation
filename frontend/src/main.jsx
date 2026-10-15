import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// The brand face is Helvetica, which every Mac has. Geist Mono is bundled for codes, so the demo runs offline.
import "@fontsource/geist-mono/400.css";
import "@fontsource/geist-mono/500.css";
import "@fontsource/geist-mono/600.css";
import "./styles.css";
import App from "./App.jsx";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Geist and Geist Mono are bundled, so the demo runs without internet.
// Hauora, the brand display face, is listed first in --font-display and takes over once its files are added.
import "@fontsource/geist/400.css";
import "@fontsource/geist/500.css";
import "@fontsource/geist/600.css";
import "@fontsource/geist/700.css";
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

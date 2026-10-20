import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

// Note: <StrictMode> is intentionally not used. Its dev-only double mount would claim and then
// immediately release an image lease, which is confusing to debug against the real queue.
createRoot(document.getElementById("root") as HTMLElement).render(<App />);

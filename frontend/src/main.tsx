/**
 * ResearchOS - 入口文件
 */
import { createRoot } from "react-dom/client";
import App from "./App";
import { VisualStyleProvider } from "@/contexts/VisualStyleContext";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <VisualStyleProvider>
    <App />
  </VisualStyleProvider>
);

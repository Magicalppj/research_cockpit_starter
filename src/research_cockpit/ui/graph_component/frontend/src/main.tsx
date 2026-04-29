import React from "react";
import { createRoot } from "react-dom/client";
import GraphComponent from "./GraphComponent";
import "./styles.css";

const root = document.getElementById("root");

if (root) {
  createRoot(root).render(
    <React.StrictMode>
      <GraphComponent />
    </React.StrictMode>
  );
}

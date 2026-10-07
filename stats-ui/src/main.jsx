import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";

// An "error boundary": if anything inside <App> crashes while drawing, React
// would normally leave a blank window. This catches the crash, shows it, and
// writes it to the add-on's debug.log so it can be fixed.
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    window.ankiLog && window.ankiLog("render crash: " + error + " | " + (info.componentStack || "").trim().split("\n")[0]);
  }
  render() {
    if (this.state.error) {
      return (
        <div className="empty">
          Something went wrong drawing your stats.<br />
          <code>{String(this.state.error)}</code>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById("root")).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>
);
window.ankiLog && window.ankiLog("react started");

import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, ErrorBox } from "./ui";

/** A crashed page shows what happened and how to recover, instead of a blank screen. */
export default class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("page crashed", error, info.componentStack);
  }
  componentDidUpdate(prev: { resetKey?: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="mx-auto mt-16 max-w-lg space-y-4 text-center">
        <h2 className="text-lg font-semibold">This page hit a problem</h2>
        <ErrorBox error={this.state.error.message || "Unexpected error"} />
        <p className="text-sm text-ink-3">The rest of the app is unaffected. Try again, or open another page.</p>
        <Button onClick={() => this.setState({ error: null })}>Try again</Button>
      </div>
    );
  }
}

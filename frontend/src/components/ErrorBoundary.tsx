import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}
interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: unknown) {
    console.error("[ErrorBoundary]", error, info);
  }

  render() {
    if (this.state.error) {
      return this.props.fallback ?? (
        <div className="rounded-xl border border-danger/40 bg-card p-4 text-sm text-secondary">
          <p className="font-medium text-danger">Something went wrong rendering this block.</p>
          {this.state.error?.message && (
            <p className="mt-1 break-words text-xs text-muted">{String(this.state.error.message)}</p>
          )}
          <p className="mt-1 text-xs text-muted">Your conversation is safe — refresh to reload the UI.</p>
          <button
            onClick={() => {
              this.setState({ error: null });
              window.location.reload();
            }}
            className="mt-2 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
          >
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
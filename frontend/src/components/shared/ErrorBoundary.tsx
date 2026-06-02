import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Optional fallback override. Receives the error so it can show details. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Catches render-time exceptions in descendant components so a single broken
 * component doesn't blank the entire page (the temporal-dead-zone bug
 * that happened on 2026-05-13 motivated this).
 *
 * We deliberately do NOT auto-report to a service — privacy-by-default for the
 * test env. If/when an error tracker is wired up, log to it from componentDidCatch.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Keep console verbose for self-serve debugging while we don't have an
    // error tracker yet. The full stack is in error.stack; info.componentStack
    // tells you which component tree blew up.
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) return this.props.fallback(error, this.reset);

    return (
      <div className="min-h-[50vh] flex items-center justify-center p-8">
        <div className="bg-gray-900 border border-red-700 rounded-2xl p-6 max-w-lg w-full space-y-4 shadow-2xl">
          <div>
            <h2 className="text-white font-semibold text-lg">Something went wrong</h2>
            <p className="text-sm text-gray-400 mt-1">
              The page hit an unexpected error. Your data is safe — this was just a
              UI crash. Send Abdulrahman the details below via the 💬 Feedback
              button and try reloading.
            </p>
          </div>
          <pre className="text-[11px] font-mono bg-gray-950 border border-gray-800 rounded p-3 overflow-auto max-h-48 text-red-300 whitespace-pre-wrap">
            {error.name}: {error.message}
            {error.stack && (
              <>
                {"\n\n"}
                {error.stack.split("\n").slice(0, 6).join("\n")}
              </>
            )}
          </pre>
          <div className="flex gap-3 justify-end">
            <button
              onClick={this.reset}
              className="px-4 py-2 text-xs rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-200 transition-colors"
            >
              Try again
            </button>
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 text-xs rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition-colors"
            >
              Reload page
            </button>
          </div>
        </div>
      </div>
    );
  }
}

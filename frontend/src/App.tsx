import { useQueryClient } from "@tanstack/react-query";
import { useMe } from "./lib/auth";
import Onboarding from "./components/onboarding/Onboarding";
import Navbar from "./components/layout/Navbar";
import LeadsPage from "./pages/LeadsPage";
import FrameworkPage from "./pages/FrameworkPage";
import ArchivePage from "./pages/ArchivePage";
import FeedbackInboxPage from "./pages/FeedbackInboxPage";
import FeedbackButton from "./components/feedback/FeedbackButton";
import ErrorBoundary from "./components/shared/ErrorBoundary";
import useAppStore from "./store/useAppStore";

// Login is handled by the Raed platform proxy (Slack OTP) — there's no
// /login route in this app anymore. If a request reaches us without auth,
// the backend returns 401 and we show a friendly "not authenticated" panel.

function Dashboard() {
  const { activeTab } = useAppStore();
  return (
    <div className="h-screen flex flex-col bg-background">
      <Navbar />
      <div className="flex-1 overflow-hidden">
        <ErrorBoundary>
          {activeTab === "leads" && (
            <div className="h-full overflow-y-auto animate-fade-in">
              <LeadsPage />
            </div>
          )}
          {activeTab === "framework" && (
            <div className="h-full overflow-y-auto animate-fade-in">
              <FrameworkPage />
            </div>
          )}
          {activeTab === "archive" && (
            <div className="h-full overflow-y-auto animate-fade-in">
              <ArchivePage />
            </div>
          )}
          {activeTab === "feedback" && (
            <div className="h-full overflow-y-auto animate-fade-in">
              <FeedbackInboxPage />
            </div>
          )}
        </ErrorBoundary>
      </div>
      <FeedbackButton />
    </div>
  );
}

function NotAuthenticated() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-6">
      <div className="max-w-md text-center space-y-3">
        <h1 className="text-2xl font-semibold text-foreground">Not signed in</h1>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Open this app through{" "}
          <a
            className="text-info underline"
            href="https://auth.apps.raed.vc"
          >
            auth.apps.raed.vc
          </a>{" "}
          and sign in with your <code className="text-foreground">@raed.vc</code>{" "}
          Slack account. The platform proxy will gate your request and send you
          here.
        </p>
        <p className="text-muted-foreground text-xs">
          If you're running locally:{" "}
          <code className="text-foreground">
            localStorage.setItem('fake_email','you@raed.vc')
          </code>{" "}
          then reload.
        </p>
      </div>
    </div>
  );
}

export default function App() {
  const me = useMe();
  const qc = useQueryClient();
  if (me.isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <p className="text-sm text-muted-foreground animate-pulse">Loading…</p>
      </div>
    );
  }
  if (me.isError || !me.data) return <NotAuthenticated />;
  if (!me.data.onboarded) {
    return (
      <Onboarding
        user={me.data}
        onDone={() => qc.invalidateQueries({ queryKey: ["me"] })}
      />
    );
  }
  return <Dashboard />;
}

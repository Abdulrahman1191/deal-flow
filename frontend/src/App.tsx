import { useMe } from "./lib/auth";
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
    <div className="h-screen flex flex-col bg-gray-950">
      <Navbar />
      <div className="flex-1 overflow-hidden">
        <ErrorBoundary>
          {activeTab === "leads" && (
            <div className="h-full overflow-y-auto">
              <LeadsPage />
            </div>
          )}
          {activeTab === "framework" && (
            <div className="h-full overflow-y-auto">
              <FrameworkPage />
            </div>
          )}
          {activeTab === "archive" && (
            <div className="h-full overflow-y-auto">
              <ArchivePage />
            </div>
          )}
          {activeTab === "feedback" && (
            <div className="h-full overflow-y-auto">
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
    <div className="min-h-screen flex items-center justify-center bg-gray-950 p-6">
      <div className="max-w-md text-center space-y-3">
        <h1 className="text-2xl font-semibold text-white">Not signed in</h1>
        <p className="text-gray-400 text-sm leading-relaxed">
          Open this app through{" "}
          <a
            className="text-blue-400 underline"
            href="https://auth.apps.raed.vc"
          >
            auth.apps.raed.vc
          </a>{" "}
          and sign in with your <code className="text-gray-200">@raed.vc</code>{" "}
          Slack account. The platform proxy will gate your request and send you
          here.
        </p>
        <p className="text-gray-600 text-xs">
          If you're running locally:{" "}
          <code className="text-gray-400">
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
  if (me.isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-950">
        <p className="text-sm text-gray-500 animate-pulse">Loading…</p>
      </div>
    );
  }
  if (me.isError || !me.data) return <NotAuthenticated />;
  return <Dashboard />;
}

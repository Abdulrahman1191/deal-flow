import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import useAppStore from "../../store/useAppStore";
import { fetchFeedback } from "../../api/feedback";
import { useMe } from "../../lib/auth";

const baseTabs = [
  { id: "leads", label: "Deal Flow" },
  { id: "framework", label: "Framework" },
  { id: "portfolio", label: "Portfolio" },
  { id: "briefings", label: "Briefings" },
  { id: "archive", label: "Archive" },
] as const;

export default function Navbar() {
  const { activeTab, setActiveTab } = useAppStore();
  const me = useMe();
  const owner = !!me.data?.is_owner;
  const [menuOpen, setMenuOpen] = useState(false);

  const { data: feedback = [] } = useQuery({
    queryKey: ["feedback"],
    queryFn: fetchFeedback,
    enabled: owner,
    refetchInterval: 30_000,
    staleTime: 20_000,
  });
  const unresolved = feedback.filter((f) => !f.resolved_at).length;

  const tabs = owner
    ? [...baseTabs, { id: "feedback" as const, label: "Feedback" }]
    : baseTabs;

  const select = (id: (typeof tabs)[number]["id"]) => {
    setActiveTab(id);
    setMenuOpen(false);
  };

  return (
    <nav className="relative bg-card border-b border-border px-4 sm:px-6 h-14 flex items-center gap-3 sm:gap-8">
      <span className="font-heading text-foreground font-semibold tracking-tight shrink-0">
        Raed Ventures
        <span className="hidden sm:inline text-muted-foreground text-xs font-sans font-normal ml-1">
          AI Deal Flow
        </span>
      </span>

      {/* Desktop tabs */}
      <div className="hidden md:flex gap-1">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`relative whitespace-nowrap px-4 py-4 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab.id
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {tab.label}
            {tab.id === "feedback" && unresolved > 0 && (
              <span className="absolute top-3 right-1 flex h-4 w-4 items-center justify-center rounded-full bg-info text-[10px] font-bold text-white">
                {unresolved}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Desktop right side */}
      <div className="ml-auto hidden md:flex items-center gap-4 text-xs text-muted-foreground">
        {me.data && (
          <span>
            Signed in as <span className="text-foreground">{me.data.email}</span>
          </span>
        )}
        <a
          href="https://auth.apps.raed.vc"
          className="hover:text-foreground transition-colors"
          title="The platform handles sign-out — open auth.apps.raed.vc and log out there."
        >
          Account
        </a>
      </div>

      {/* Mobile hamburger */}
      <button
        onClick={() => setMenuOpen((o) => !o)}
        className="ml-auto md:hidden relative flex items-center justify-center h-9 w-9 -mr-1 rounded-lg text-foreground hover:bg-muted transition-colors"
        aria-label="Menu"
        aria-expanded={menuOpen}
      >
        {menuOpen ? (
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        ) : (
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M3 6h18M3 12h18M3 18h18" />
          </svg>
        )}
        {!menuOpen && owner && unresolved > 0 && (
          <span className="absolute top-1 right-1 h-2 w-2 rounded-full bg-info" />
        )}
      </button>

      {/* Mobile dropdown menu */}
      {menuOpen && (
        <>
          <div
            className="fixed inset-0 top-14 z-40 bg-foreground/20 md:hidden animate-fade-in"
            onClick={() => setMenuOpen(false)}
          />
          <div className="absolute left-0 right-0 top-14 z-50 md:hidden bg-card border-b border-border shadow-lg animate-slide-down">
            <div className="flex flex-col p-2">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => select(tab.id)}
                  className={`flex items-center justify-between rounded-lg px-4 py-3 text-sm font-medium transition-colors ${
                    activeTab === tab.id
                      ? "bg-muted text-foreground"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground"
                  }`}
                >
                  {tab.label}
                  {tab.id === "feedback" && unresolved > 0 && (
                    <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-info px-1.5 text-[10px] font-bold text-white">
                      {unresolved}
                    </span>
                  )}
                </button>
              ))}
            </div>
            <div className="border-t border-border px-4 py-3 flex items-center justify-between text-xs text-muted-foreground">
              {me.data && (
                <span className="truncate mr-3">
                  <span className="text-foreground">{me.data.email}</span>
                </span>
              )}
              <a
                href="https://auth.apps.raed.vc"
                className="shrink-0 font-medium text-foreground hover:text-primary transition-colors"
              >
                Account
              </a>
            </div>
          </div>
        </>
      )}
    </nav>
  );
}

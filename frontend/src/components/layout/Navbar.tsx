import { useQuery } from "@tanstack/react-query";
import useAppStore from "../../store/useAppStore";
import { fetchFeedback } from "../../api/feedback";
import { useMe } from "../../lib/auth";

const baseTabs = [
  { id: "leads", label: "Deal Flow" },
  { id: "framework", label: "Framework" },
  { id: "archive", label: "Archive" },
] as const;

export default function Navbar() {
  const { activeTab, setActiveTab } = useAppStore();
  const me = useMe();
  const owner = !!me.data?.is_owner;

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

  return (
    <nav className="bg-card border-b border-border px-6 py-0 flex items-center gap-8 h-14">
      <span className="font-heading text-foreground font-semibold tracking-tight mr-4">
        Raed Ventures{" "}
        <span className="text-muted-foreground text-xs font-sans font-normal ml-1">AI Deal Flow</span>
      </span>
      <div className="flex gap-1">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`relative px-4 py-4 text-sm font-medium border-b-2 transition-colors ${
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
      <div className="ml-auto flex items-center gap-4 text-xs text-muted-foreground">
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
    </nav>
  );
}

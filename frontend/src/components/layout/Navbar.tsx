import { useQuery, useQueryClient } from "@tanstack/react-query";
import useAppStore from "../../store/useAppStore";
import { fetchFeedback } from "../../api/feedback";
import { fetchTeam } from "../../api/users";
import { fetchLeads } from "../../api/leads";
import { AWAITING_DECK_QUERY_KEY } from "../../pages/AwaitingDeckPage";
import { useMe } from "../../lib/auth";

const baseTabs = [
  { id: "leads", label: "Deal Flow" },
  { id: "framework", label: "Framework" },
  { id: "archive", label: "Archive" },
  { id: "awaiting_deck", label: "Awaiting Deck" },
] as const;

export default function Navbar() {
  const { activeTab, setActiveTab, viewAs, setViewAs } = useAppStore();
  const me = useMe();
  const owner = !!me.data?.is_owner;
  const qc = useQueryClient();

  const { data: feedback = [] } = useQuery({
    queryKey: ["feedback"],
    queryFn: fetchFeedback,
    enabled: owner,
    refetchInterval: 30_000,
    staleTime: 20_000,
  });
  const unresolved = feedback.filter((f) => !f.resolved_at).length;

  // Count badge for the "Awaiting Deck" tab — same query key as the page
  // itself, so the two share one cache entry instead of double-fetching.
  const { data: awaitingDeck } = useQuery({
    queryKey: AWAITING_DECK_QUERY_KEY,
    queryFn: () => fetchLeads({ status: "awaiting_deck", page_size: 1000 }),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
  const awaitingDeckCount = awaitingDeck?.total ?? 0;

  // Admin "view as" QA mode (issue #52). The dropdown itself is owner-gated
  // below; this query is too, so non-admins never hit /users/team (which
  // 403s them anyway).
  const { data: team = [] } = useQuery({
    queryKey: ["team"],
    queryFn: fetchTeam,
    enabled: owner,
    staleTime: 5 * 60 * 1000,
  });

  const handleViewAsChange = (email: string) => {
    setViewAs(email || null);
    qc.invalidateQueries({ queryKey: ["leads"] });
    qc.invalidateQueries({ queryKey: ["archive"] });
    qc.invalidateQueries({ queryKey: ["send-queue"] });
  };

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
            {tab.id === "awaiting_deck" && awaitingDeckCount > 0 && (
              <span className="absolute top-3 right-1 flex h-4 w-4 items-center justify-center rounded-full bg-warning text-[10px] font-bold text-white">
                {awaitingDeckCount}
              </span>
            )}
          </button>
        ))}
      </div>
      <div className="ml-auto flex items-center gap-4 text-xs text-muted-foreground">
        {owner && (
          <label className="flex items-center gap-1.5">
            <span>Viewing:</span>
            <select
              value={viewAs ?? ""}
              onChange={(e) => handleViewAsChange(e.target.value)}
              className="bg-card border border-border rounded-md px-2 py-1 text-xs text-foreground focus:outline-none focus:border-ring"
              data-testid="view-as-select"
            >
              <option value="">My board</option>
              {team.map((email) => (
                <option key={email} value={email}>
                  {email}
                </option>
              ))}
            </select>
          </label>
        )}
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

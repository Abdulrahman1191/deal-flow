import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { completeOnboarding, fetchMe, type CurrentUser } from "../../lib/auth";
import { fetchLeads, syncMyLeads } from "../../api/leads";

interface Props {
  user: CurrentUser;
  /** Called when the user finishes onboarding — parent re-fetches `me`. */
  onDone: () => void;
}

const features = [
  { title: "Deal Flow", body: "Your inbound deals, auto-scored YES / MAYBE / REJECT with the reasoning behind each call." },
  { title: "Framework", body: "The investment framework the AI uses — so you always know how a deal was judged." },
  { title: "Archive", body: "Everything you've actioned, grouped by outcome." },
  { title: "Feedback", body: "Tell us what's off — your notes train the scoring over time." },
];

export default function Onboarding({ user, onDone }: Props) {
  const [step, setStep] = useState<"welcome" | "syncing">("welcome");
  const [elapsed, setElapsed] = useState(0);

  const firstName = (user.name || user.email).split(/[ @.]/)[0];
  const greet = firstName.charAt(0).toUpperCase() + firstName.slice(1);

  // Poll leads + me only while syncing.
  const { data: leads } = useQuery({
    queryKey: ["leads"],
    queryFn: () => fetchLeads({ page_size: 1 }),
    enabled: step === "syncing",
    refetchInterval: 2500,
  });
  const { data: me } = useQuery({
    queryKey: ["me-onboard-poll"],
    queryFn: fetchMe,
    enabled: step === "syncing",
    refetchInterval: 3000,
  });

  useEffect(() => {
    if (step !== "syncing") return;
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, [step]);

  const start = async () => {
    setStep("syncing");
    // Mark onboarded so the welcome never reappears, then kick off the import.
    completeOnboarding().catch(() => {});
    syncMyLeads().catch(() => {});
  };

  const total = leads?.total ?? 0;
  const done = total > 0;
  const noCopper = !done && elapsed >= 12 && me?.copper_linked === false;
  const slow = !done && !noCopper && elapsed >= 22;
  const settled = done || noCopper || slow;

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-6">
      <div className="w-full max-w-lg bg-card border border-border rounded-2xl shadow-xl p-8 animate-scale-in">
        {step === "welcome" ? (
          <div className="space-y-6">
            <div className="space-y-2">
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Raed Ventures · AI Deal Flow
              </p>
              <h1 className="font-heading text-2xl font-semibold text-foreground">
                Welcome, {greet}
              </h1>
              <p className="text-sm text-muted-foreground leading-relaxed">
                This is your personal deal-flow workspace. We'll pull the deals
                assigned to you in Copper and score them for you. Here's what
                you'll find:
              </p>
            </div>

            <div className="space-y-3">
              {features.map((f, i) => (
                <div
                  key={f.title}
                  style={{ animationDelay: `${i * 60}ms` }}
                  className="flex gap-3 animate-fade-in-up"
                >
                  <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-primary" />
                  <div>
                    <p className="text-sm font-semibold text-foreground">{f.title}</p>
                    <p className="text-xs text-muted-foreground leading-relaxed">{f.body}</p>
                  </div>
                </div>
              ))}
            </div>

            <button
              onClick={start}
              className="w-full rounded-lg bg-primary hover:bg-primary/90 text-white text-sm font-medium py-3 transition-colors"
            >
              Get started
            </button>
          </div>
        ) : (
          <div className="space-y-6 text-center py-4">
            {!settled ? (
              <>
                <Spinner />
                <div className="space-y-1">
                  <h2 className="font-heading text-lg font-semibold text-foreground">
                    Pulling your deals from Copper…
                  </h2>
                  <p className="text-sm text-muted-foreground">
                    {total > 0 ? `Imported ${total} so far` : "This usually takes a few seconds."}
                  </p>
                </div>
              </>
            ) : done ? (
              <>
                <SuccessMark />
                <div className="space-y-1">
                  <h2 className="font-heading text-lg font-semibold text-foreground">
                    You're all set
                  </h2>
                  <p className="text-sm text-muted-foreground">
                    Imported {total} {total === 1 ? "deal" : "deals"} assigned to you.
                  </p>
                </div>
              </>
            ) : (
              <>
                <InfoMark />
                <div className="space-y-1">
                  <h2 className="font-heading text-lg font-semibold text-foreground">
                    {noCopper ? "No Copper deals yet" : "Still importing…"}
                  </h2>
                  <p className="text-sm text-muted-foreground leading-relaxed">
                    {noCopper
                      ? `We couldn't find deals assigned to ${user.email} in Copper yet. New deals will appear automatically — you can also use the "Sync my leads" button anytime.`
                      : "Your deals are still importing in the background and will appear on the board shortly."}
                  </p>
                </div>
              </>
            )}

            <button
              onClick={onDone}
              disabled={!settled}
              className="w-full rounded-lg bg-primary hover:bg-primary/90 text-white text-sm font-medium py-3 transition-colors disabled:opacity-50"
            >
              {settled ? "Go to dashboard" : "Setting things up…"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <svg className="mx-auto animate-spin text-primary" width="40" height="40" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.15" strokeWidth="3" />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

function SuccessMark() {
  return (
    <div className="mx-auto h-12 w-12 rounded-full bg-success/10 flex items-center justify-center animate-scale-in">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#1F2533" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20 6 9 17l-5-5" />
      </svg>
    </div>
  );
}

function InfoMark() {
  return (
    <div className="mx-auto h-12 w-12 rounded-full bg-muted flex items-center justify-center">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-muted-foreground">
        <circle cx="12" cy="12" r="10" /><path d="M12 16v-4M12 8h.01" />
      </svg>
    </div>
  );
}

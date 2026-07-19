import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Lead } from "../../types/lead";
import Badge from "../shared/Badge";
import ConfidenceBar from "../shared/ConfidenceBar";
import ReasoningBox from "./ReasoningBox";
import ActionButtons from "./ActionButtons";
import EmailModal from "./EmailModal";
import { useToast } from "../shared/Toast";
import { overrideBucket, rateAssessment, reassess, type OverrideReason } from "../../api/assessments";
import { findLinkedin, updateLead } from "../../api/leads";
import ReasonModal from "./ReasonModal";
import FeedbackModal from "./FeedbackModal";

interface Props {
  lead: Lead;
  /** Position in its column — used to stagger the entrance animation. */
  index?: number;
}

const bucketVariant: Record<string, "yes" | "maybe" | "reject"> = {
  YES: "yes",
  MAYBE: "maybe",
  REJECT: "reject",
};

export default function LeadCard({ lead, index = 0 }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [showEmailModal, setShowEmailModal] = useState(false);
  // When set, a ReasonModal is open for this bucket. The user must save with
  // at least a tag or note, or cancel (no override at all).
  const [pendingBucket, setPendingBucket] = useState<"YES" | "MAYBE" | "REJECT" | null>(null);
  // Which rating's FeedbackModal is open ("up" | "down"), or null when closed.
  const [showFeedback, setShowFeedback] = useState<"up" | "down" | null>(null);
  const qc = useQueryClient();
  const toast = useToast();

  const { assessment } = lead;

  const reassessMutation = useMutation({
    mutationFn: () => reassess(lead.id),
    // Optimistic: flip the lead's status to "pending" right now so the card
    // shows "Reassessing…" without waiting for the next 15s poll.
    onMutate: async () => {
      await qc.cancelQueries({ queryKey: ["leads"] });
      const snapshot = qc.getQueryData(["leads"]);
      qc.setQueryData(["leads"], (prev: { items?: Lead[] } | undefined) =>
        prev?.items
          ? {
              ...prev,
              items: prev.items.map((l) =>
                l.id === lead.id ? { ...l, status: "pending" } : l,
              ),
            }
          : prev,
      );
      return { snapshot };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.snapshot) qc.setQueryData(["leads"], ctx.snapshot);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["send-queue"] });
    },
  });

  // While the lead is being reassessed by Celery, the backend status is
  // 'pending' or 'processing'. Show that on the card too.
  const reassessInFlight =
    reassessMutation.isPending ||
    (!!assessment && (lead.status === "pending" || lead.status === "processing"));

  const overrideMutation = useMutation({
    mutationFn: ({ bucket, reasonData }: { bucket: "YES" | "MAYBE" | "REJECT"; reasonData?: OverrideReason; silent?: boolean }) =>
      overrideBucket(lead.id, bucket, reasonData),
    // Optimistic: move the card to the new bucket column immediately. The
    // backend draft regeneration takes ~10s — without this the user thinks
    // nothing happened.
    onMutate: async ({ bucket: newBucket }) => {
      const prevBucket = assessment?.user_override ?? assessment?.bucket;
      await qc.cancelQueries({ queryKey: ["leads"] });
      const snapshot = qc.getQueryData(["leads"]);
      qc.setQueryData(["leads"], (prev: { items?: Lead[] } | undefined) =>
        prev?.items
          ? {
              ...prev,
              items: prev.items.map((l) =>
                l.id === lead.id && l.assessment
                  ? {
                      ...l,
                      assessment: {
                        ...l.assessment,
                        bucket: newBucket,
                        user_override: newBucket,
                        user_override_at: new Date().toISOString(),
                      },
                    }
                  : l,
              ),
            }
          : prev,
      );
      return { snapshot, prevBucket };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.snapshot) qc.setQueryData(["leads"], ctx.snapshot);
    },
    onSuccess: (_data, vars, ctx) => {
      if (vars.silent) return;
      const prev = ctx?.prevBucket as "YES" | "MAYBE" | "REJECT" | undefined;
      toast(`Moved ${lead.company_name} to ${vars.bucket}`, {
        action:
          prev && prev !== vars.bucket
            ? { label: "Undo", onClick: () => overrideMutation.mutate({ bucket: prev, silent: true }) }
            : undefined,
      });
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["send-queue"] });
      qc.invalidateQueries({ queryKey: ["archive"] });
    },
  });

  const rateMutation = useMutation({
    mutationFn: ({ rating, reasonData }: { rating: "up" | "down"; reasonData?: OverrideReason }) =>
      rateAssessment(lead.id, rating, reasonData),
    // Optimistic: light up the chosen thumb immediately.
    onMutate: async ({ rating }) => {
      await qc.cancelQueries({ queryKey: ["leads"] });
      const snapshot = qc.getQueryData(["leads"]);
      qc.setQueryData(["leads"], (prev: { items?: Lead[] } | undefined) =>
        prev?.items
          ? {
              ...prev,
              items: prev.items.map((l) =>
                l.id === lead.id && l.assessment
                  ? {
                      ...l,
                      assessment: {
                        ...l.assessment,
                        user_rating: rating,
                        user_rating_at: new Date().toISOString(),
                      },
                    }
                  : l,
              ),
            }
          : prev,
      );
      return { snapshot };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.snapshot) qc.setQueryData(["leads"], ctx.snapshot);
    },
    onSuccess: () => toast("Thanks — your feedback was recorded"),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["leads"] });
    },
  });

  const findLinkedinMutation = useMutation({
    mutationFn: () => findLinkedin(lead.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["leads"] }),
  });

  const saveLinkedinMutation = useMutation({
    mutationFn: (url: string) =>
      updateLead(lead.id, { company_linkedin_url: url || null }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["leads"] }),
  });

  const [linkedinDraft, setLinkedinDraft] = useState<string>(
    lead.company_linkedin_url ?? "",
  );

  const aiBucket = assessment?.bucket; // the AI's original call
  const bucket = assessment?.user_override ?? assessment?.bucket; // effective
  const isOverridden = !!assessment?.user_override_at;
  const rating = assessment?.user_rating ?? null;

  return (
    <div
      style={{ animationDelay: `${Math.min(index * 40, 240)}ms` }}
      className="bg-card border border-border rounded-2xl p-5 space-y-3.5 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 animate-fade-in-up"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold text-foreground text-sm">{lead.company_name}</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {lead.stage ?? "—"} · {lead.region ?? "—"}
          </p>
          {lead.applied_at && (
            <p className="text-[10px] text-muted-foreground mt-0.5">
              Applied{" "}
              {new Date(lead.applied_at).toLocaleDateString("en-GB", {
                day: "numeric",
                month: "short",
                year: "numeric",
              })}
            </p>
          )}
          {(lead.company_linkedin_url || lead.website) && (
            <div className="flex items-center gap-2 mt-1 text-xs">
              {lead.company_linkedin_url && (
                <a
                  href={lead.company_linkedin_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-info hover:underline"
                  data-testid="company-linkedin-link"
                >
                  LinkedIn ↗
                </a>
              )}
              {lead.website && (
                <a
                  href={lead.website.startsWith("http") ? lead.website : `https://${lead.website}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-muted-foreground hover:text-foreground hover:underline"
                >
                  Website ↗
                </a>
              )}
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-1">
          {bucket && <Badge label={bucket} variant={bucketVariant[bucket]} />}
          {isOverridden && (
            <span
              className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium"
              title={`Manually overridden${assessment?.user_override_at ? " at " + new Date(assessment.user_override_at).toLocaleString("en-GB") : ""}`}
            >
              Your override
            </span>
          )}
        </div>
      </div>

      {/* AI recommendation */}
      {assessment && (
        <div className="rounded-xl bg-muted/40 border border-border p-3 space-y-2.5">
          <div className="flex items-center justify-between" data-testid="rating-controls">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              AI recommendation
            </span>
            <div className="flex items-center gap-1">
              <span className="text-[10px] text-muted-foreground mr-1">Rate:</span>
              <button
                onClick={() => setShowFeedback("up")}
                disabled={rateMutation.isPending}
                title="The AI got this right"
                data-testid="rate-up"
                className={`grid place-items-center h-6 w-6 rounded-md transition-colors ${
                  rating === "up" ? "bg-foreground text-white" : "text-muted-foreground hover:bg-muted"
                }`}
              >
                <ThumbIcon up />
              </button>
              <button
                onClick={() => setShowFeedback("down")}
                disabled={rateMutation.isPending}
                title="The AI got this wrong"
                data-testid="rate-down"
                className={`grid place-items-center h-6 w-6 rounded-md transition-colors ${
                  rating === "down" ? "bg-foreground text-white" : "text-muted-foreground hover:bg-muted"
                }`}
              >
                <ThumbIcon />
              </button>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {aiBucket && <Badge label={aiBucket} variant={bucketVariant[aiBucket]} />}
            <div className="flex-1">
              <ConfidenceBar score={assessment.confidence_score} bucket={aiBucket ?? ""} />
            </div>
          </div>
          {assessment.summary && (
            <p className="text-xs text-muted-foreground leading-relaxed">{assessment.summary}</p>
          )}
          <ReasoningBox
            positive_signals={assessment.positive_signals}
            red_flags={assessment.red_flags}
            data_gaps={assessment.data_gaps}
          />
        </div>
      )}

      {/* Your decision */}
      {assessment && (
        <div className="space-y-1.5" data-testid="bucket-override-controls">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Your decision
          </span>
          <div className="flex items-center gap-1.5">
            {(["YES", "MAYBE", "REJECT"] as const).map((b) => {
              const active = bucket === b;
              return (
                <button
                  key={b}
                  disabled={active || overrideMutation.isPending}
                  onClick={() => setPendingBucket(b)}
                  className={`text-[10px] uppercase font-semibold tracking-wider px-3 py-1.5 rounded-full border transition-colors ${
                    active
                      ? "bg-foreground text-white border-foreground cursor-default"
                      : "bg-card text-muted-foreground border-border hover:text-foreground hover:border-foreground cursor-pointer"
                  } disabled:opacity-100`}
                  data-testid={`override-${b.toLowerCase()}`}
                >
                  {b}
                </button>
              );
            })}
            {overrideMutation.isPending && (
              <span className="text-[10px] text-muted-foreground ml-1 animate-pulse">re-drafting…</span>
            )}
          </div>
        </div>
      )}

      {!assessment && lead.status === "processing" && (
        <p className="text-xs text-muted-foreground animate-pulse">Researching & scoring…</p>
      )}
      {!assessment && lead.status === "pending" && (
        <p className="text-xs text-muted-foreground">Queued for assessment</p>
      )}

      {/* Actions */}
      <div className="flex items-center justify-between pt-1">
        <ActionButtons
          lead={lead}
          onApprove={() => setShowEmailModal(true)}
          onReassess={() => reassessMutation.mutate()}
          reassessing={reassessInFlight}
        />
        <div className="flex items-center gap-3">
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            {expanded ? "Less ▲" : "More ▼"}
          </button>
        </div>
      </div>

      {showEmailModal && assessment && (
        <EmailModal lead={lead} onClose={() => setShowEmailModal(false)} />
      )}

      {pendingBucket && (
        <ReasonModal
          bucket={pendingBucket}
          companyName={lead.company_name}
          onSubmit={(reasonData) => {
            overrideMutation.mutate({ bucket: pendingBucket, reasonData });
            setPendingBucket(null);
          }}
          onCancel={() => setPendingBucket(null)}
        />
      )}

      {showFeedback && assessment && (
        <FeedbackModal
          companyName={lead.company_name}
          aiBucket={bucket ?? ""}
          rating={showFeedback}
          onSubmit={(reasonData) => {
            rateMutation.mutate({ rating: showFeedback, reasonData });
            setShowFeedback(null);
          }}
          onCancel={() => setShowFeedback(null)}
        />
      )}

      {expanded && (
        <div className="pt-2 border-t border-border space-y-3">
          {/* Editable LinkedIn URL */}
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                Company LinkedIn
              </span>
              <button
                onClick={() => findLinkedinMutation.mutate()}
                disabled={findLinkedinMutation.isPending}
                className="text-[10px] text-info hover:underline disabled:opacity-50"
                data-testid="find-linkedin-btn"
              >
                {findLinkedinMutation.isPending ? "Searching…" : "Find ↻"}
              </button>
            </div>
            <input
              type="text"
              value={linkedinDraft}
              onChange={(e) => setLinkedinDraft(e.target.value)}
              onBlur={() => {
                if (linkedinDraft !== (lead.company_linkedin_url ?? "")) {
                  saveLinkedinMutation.mutate(linkedinDraft.trim());
                }
              }}
              placeholder="https://linkedin.com/company/..."
              className="w-full bg-background border border-border rounded-lg px-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-ring"
              data-testid="linkedin-input"
            />
            {findLinkedinMutation.data?.source && (
              <p className="text-[10px] text-muted-foreground">
                Updated via {findLinkedinMutation.data.source.replace("_", " ")}.
              </p>
            )}
            {findLinkedinMutation.data && !findLinkedinMutation.data.company_linkedin_url && (
              <p className="text-[10px] text-muted-foreground">
                Auto-discovery found nothing. Paste a URL above to set manually.
              </p>
            )}
          </div>

          {/* Pitch deck status */}
          <div className="flex items-center justify-between text-xs">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Pitch deck
            </span>
            {lead.pitch_deck_filename ? (
              lead.pitch_deck_drive_id ? (
                <span className="text-foreground flex items-center gap-2">
                  <a
                    href={`/api/v1/leads/${lead.id}/pitch-deck`}
                    target="_blank"
                    rel="noreferrer"
                    title={lead.pitch_deck_filename}
                    className="text-info hover:underline text-xs"
                  >
                    View PDF ↗
                  </a>
                  {lead.pitch_deck_ingested_at && (
                    <span className="text-muted-foreground">
                      · {new Date(lead.pitch_deck_ingested_at).toLocaleDateString("en-GB")}
                    </span>
                  )}
                </span>
              ) : (
                <span
                  className="text-muted-foreground text-xs"
                  title={`${lead.pitch_deck_filename} — Drive sync hasn't run yet; ping Abdulrahman.`}
                >
                  on file, sync pending
                </span>
              )
            ) : (
              <span className="text-muted-foreground">not yet ingested</span>
            )}
          </div>

          {/* Scoring breakdown */}
          {assessment?.scoring_breakdown && (
            <div className="space-y-1.5">
              {Object.entries(assessment.scoring_breakdown).map(([key, val]) => (
                <div key={key} className="flex justify-between text-xs">
                  <span className="text-muted-foreground capitalize">{key.replace(/_/g, " ")}</span>
                  <span className="text-foreground">{val.score}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ThumbIcon({ up = false }: { up?: boolean }) {
  return (
    <svg
      width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      className={up ? "" : "rotate-180"}
    >
      <path d="M7 10v12" />
      <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" />
    </svg>
  );
}

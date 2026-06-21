import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Lead } from "../../types/lead";
import Badge from "../shared/Badge";
import ConfidenceBar from "../shared/ConfidenceBar";
import ReasoningBox from "./ReasoningBox";
import ActionButtons from "./ActionButtons";
import EmailModal from "./EmailModal";
import { overrideBucket, rateAssessment, reassess, type OverrideReason } from "../../api/assessments";
import { archiveNoReply, findLinkedin, updateLead } from "../../api/leads";
import ReasonModal from "./ReasonModal";
import FeedbackModal from "./FeedbackModal";

interface Props {
  lead: Lead;
}

const borderColor: Record<string, string> = {
  YES: "border-l-success",
  MAYBE: "border-l-warning",
  REJECT: "border-l-error",
};

const bucketVariant: Record<string, "yes" | "maybe" | "reject"> = {
  YES: "yes",
  MAYBE: "maybe",
  REJECT: "reject",
};

export default function LeadCard({ lead }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [showEmailModal, setShowEmailModal] = useState(false);
  // When set, a ReasonModal is open for this bucket. The user can save with
  // reason data, skip (no reason), or cancel (no override at all).
  const [pendingBucket, setPendingBucket] = useState<"YES" | "MAYBE" | "REJECT" | null>(null);
  // Which rating's FeedbackModal is open ("up" | "down"), or null when closed.
  const [showFeedback, setShowFeedback] = useState<"up" | "down" | null>(null);
  const qc = useQueryClient();

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
    mutationFn: ({ bucket, reasonData }: { bucket: "YES" | "MAYBE" | "REJECT"; reasonData?: OverrideReason }) =>
      overrideBucket(lead.id, bucket, reasonData),
    // Optimistic: move the card to the new bucket column immediately. The
    // backend draft regeneration takes ~10s — without this the user thinks
    // nothing happened.
    onMutate: async ({ bucket: newBucket }) => {
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
      return { snapshot };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.snapshot) qc.setQueryData(["leads"], ctx.snapshot);
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
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["leads"] });
    },
  });

  const skipMutation = useMutation({
    mutationFn: () => archiveNoReply(lead.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["archive"] });
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

  const bucket = assessment?.user_override ?? assessment?.bucket;
  const isOverridden = !!assessment?.user_override_at;
  const rating = assessment?.user_rating ?? null;

  return (
    <div
      className={`bg-card border border-border border-l-4 ${borderColor[bucket ?? ""] ?? "border-l-border"} rounded-xl p-4 space-y-3`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold text-foreground text-sm">{lead.company_name}</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {lead.stage ?? "—"} · {lead.region ?? "—"}
          </p>
          {(lead.company_linkedin_url || lead.website) && (
            <div className="flex items-center gap-2 mt-1 text-xs">
              {lead.company_linkedin_url && (
                <a
                  href={lead.company_linkedin_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-info hover:text-info hover:underline"
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
              className="text-[10px] uppercase tracking-wider text-primary font-medium"
              title={`Manually overridden${assessment?.user_override_at ? " at " + new Date(assessment.user_override_at).toLocaleString("en-GB") : ""}`}
            >
              Manual
            </span>
          )}
        </div>
      </div>

      {assessment && (
        <div className="flex items-center gap-1" data-testid="bucket-override-controls">
          {(["YES", "MAYBE", "REJECT"] as const).map((b) => {
            const active = bucket === b;
            const color = {
              YES: active ? "bg-success/20 text-success ring-1 ring-success/40" : "bg-muted/50 text-muted-foreground hover:text-success hover:bg-success/10",
              MAYBE: active ? "bg-warning/20 text-warning ring-1 ring-warning/40" : "bg-muted/50 text-muted-foreground hover:text-warning hover:bg-warning/10",
              REJECT: active ? "bg-error/20 text-error ring-1 ring-error/40" : "bg-muted/50 text-muted-foreground hover:text-error hover:bg-error/10",
            }[b];
            return (
              <button
                key={b}
                disabled={active || overrideMutation.isPending}
                onClick={() => setPendingBucket(b)}
                className={`text-[10px] uppercase font-semibold tracking-wider px-2 py-1 rounded transition-colors ${color} ${active ? "cursor-default" : "cursor-pointer"} disabled:opacity-100`}
                data-testid={`override-${b.toLowerCase()}`}
              >
                {b}
              </button>
            );
          })}
          {overrideMutation.isPending && (
            <span className="text-[10px] text-muted-foreground ml-1 animate-pulse">re-drafting…</span>
          )}

          {/* Thumbs up/down — rate the AI recommendation (training signal). */}
          <div className="flex items-center gap-1 ml-auto" data-testid="rating-controls">
            <button
              onClick={() => setShowFeedback("up")}
              disabled={rateMutation.isPending}
              title="The AI got this right — add optional feedback"
              data-testid="rate-up"
              className={`text-sm px-1.5 py-0.5 rounded transition-colors ${
                rating === "up"
                  ? "bg-success/20 text-success ring-1 ring-success/40"
                  : "text-muted-foreground hover:text-success hover:bg-success/10"
              }`}
            >
              👍
            </button>
            <button
              onClick={() => setShowFeedback("down")}
              disabled={rateMutation.isPending}
              title="The AI got this wrong — give feedback"
              data-testid="rate-down"
              className={`text-sm px-1.5 py-0.5 rounded transition-colors ${
                rating === "down"
                  ? "bg-warning/20 text-warning ring-1 ring-warning/40"
                  : "text-muted-foreground hover:text-warning hover:bg-warning/10"
              }`}
            >
              👎
            </button>
          </div>
        </div>
      )}

      {assessment && (
        <ConfidenceBar score={assessment.confidence_score} bucket={bucket ?? ""} />
      )}

      {assessment?.summary && (
        <p className="text-xs text-muted-foreground leading-relaxed">{assessment.summary}</p>
      )}

      {assessment && (
        <ReasoningBox
          positive_signals={assessment.positive_signals}
          red_flags={assessment.red_flags}
          data_gaps={assessment.data_gaps}
        />
      )}

      {!assessment && lead.status === "processing" && (
        <p className="text-xs text-muted-foreground animate-pulse">Researching & scoring…</p>
      )}
      {!assessment && lead.status === "pending" && (
        <p className="text-xs text-muted-foreground">Queued for assessment</p>
      )}

      <div className="flex items-center justify-between pt-1">
        <ActionButtons
          lead={lead}
          onApprove={() => setShowEmailModal(true)}
          onReassess={() => reassessMutation.mutate()}
          reassessing={reassessInFlight}
        />
        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              if (confirm(`Archive ${lead.company_name} without sending any email?`)) {
                skipMutation.mutate();
              }
            }}
            disabled={skipMutation.isPending}
            className="text-xs text-muted-foreground hover:text-error transition-colors disabled:opacity-50"
            data-testid="archive-no-reply-btn"
            title="Skip the email and archive this lead. Sets Copper status to Unqualified."
          >
            {skipMutation.isPending ? "Archiving…" : "Skip ⤬"}
          </button>
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-muted-foreground hover:text-muted-foreground transition-colors"
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
          onSkip={() => {
            overrideMutation.mutate({ bucket: pendingBucket });
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
          onSkip={() => {
            rateMutation.mutate({ rating: showFeedback });
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
                className="text-[10px] text-info hover:text-info disabled:opacity-50"
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
              className="w-full bg-background border border-border rounded px-2 py-1 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-border"
              data-testid="linkedin-input"
            />
            {findLinkedinMutation.data?.source && (
              <p className="text-[10px] text-muted-foreground">
                Updated via {findLinkedinMutation.data.source.replace("_", " ")}.
              </p>
            )}
            {findLinkedinMutation.data && !findLinkedinMutation.data.company_linkedin_url && (
              <p className="text-[10px] text-warning">
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
                // Drive ID is mapped — clicking opens Drive's native viewer in a
                // new tab. The browser follows the 307 from our backend, lands
                // on drive.google.com/file/d/<id>/view, Drive verifies the
                // user's @raed.vc session via its own cookies.
                <span className="text-foreground flex items-center gap-2">
                  <a
                    href={`/api/v1/leads/${lead.id}/pitch-deck`}
                    target="_blank"
                    rel="noreferrer"
                    title={lead.pitch_deck_filename}
                    className="text-info hover:text-info hover:underline text-xs"
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
                // Filename is on file but the Drive sync hasn't run yet — this
                // is the post-migration state until scripts/sync_drive_to_db.py
                // runs (gated on platform DB access).
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

          {/* Scoring breakdown (existing) */}
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

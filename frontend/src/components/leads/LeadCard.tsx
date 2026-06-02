import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Lead } from "../../types/lead";
import Badge from "../shared/Badge";
import ConfidenceBar from "../shared/ConfidenceBar";
import ReasoningBox from "./ReasoningBox";
import ActionButtons from "./ActionButtons";
import EmailModal from "./EmailModal";
import { overrideBucket, reassess, type OverrideReason } from "../../api/assessments";
import { archiveNoReply, findLinkedin, updateLead } from "../../api/leads";
import client from "../../api/client";
import ReasonModal from "./ReasonModal";

interface Props {
  lead: Lead;
}

const borderColor: Record<string, string> = {
  YES: "border-l-green-500",
  MAYBE: "border-l-yellow-500",
  REJECT: "border-l-red-500",
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

  return (
    <div
      className={`bg-gray-900 border border-gray-800 border-l-4 ${borderColor[bucket ?? ""] ?? "border-l-gray-700"} rounded-xl p-4 space-y-3`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold text-white text-sm">{lead.company_name}</p>
          <p className="text-xs text-gray-500 mt-0.5">
            {lead.stage ?? "—"} · {lead.region ?? "—"}
          </p>
          {(lead.company_linkedin_url || lead.website) && (
            <div className="flex items-center gap-2 mt-1 text-xs">
              {lead.company_linkedin_url && (
                <a
                  href={lead.company_linkedin_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-400 hover:text-blue-300 hover:underline"
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
                  className="text-gray-400 hover:text-gray-200 hover:underline"
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
              className="text-[10px] uppercase tracking-wider text-purple-400 font-medium"
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
              YES: active ? "bg-green-500/20 text-green-300 ring-1 ring-green-500/40" : "bg-gray-800/50 text-gray-500 hover:text-green-400 hover:bg-green-500/10",
              MAYBE: active ? "bg-yellow-500/20 text-yellow-300 ring-1 ring-yellow-500/40" : "bg-gray-800/50 text-gray-500 hover:text-yellow-400 hover:bg-yellow-500/10",
              REJECT: active ? "bg-red-500/20 text-red-300 ring-1 ring-red-500/40" : "bg-gray-800/50 text-gray-500 hover:text-red-400 hover:bg-red-500/10",
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
            <span className="text-[10px] text-gray-500 ml-1 animate-pulse">re-drafting…</span>
          )}
        </div>
      )}

      {assessment && (
        <ConfidenceBar score={assessment.confidence_score} bucket={bucket ?? ""} />
      )}

      {assessment?.summary && (
        <p className="text-xs text-gray-400 leading-relaxed">{assessment.summary}</p>
      )}

      {assessment && (
        <ReasoningBox
          positive_signals={assessment.positive_signals}
          red_flags={assessment.red_flags}
          data_gaps={assessment.data_gaps}
        />
      )}

      {!assessment && lead.status === "processing" && (
        <p className="text-xs text-gray-500 animate-pulse">Researching & scoring…</p>
      )}
      {!assessment && lead.status === "pending" && (
        <p className="text-xs text-gray-500">Queued for assessment</p>
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
            className="text-xs text-gray-600 hover:text-red-400 transition-colors disabled:opacity-50"
            data-testid="archive-no-reply-btn"
            title="Skip the email and archive this lead. Sets Copper status to Unqualified."
          >
            {skipMutation.isPending ? "Archiving…" : "Skip ⤬"}
          </button>
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-gray-600 hover:text-gray-400 transition-colors"
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

      {expanded && (
        <div className="pt-2 border-t border-gray-800 space-y-3">
          {/* Editable LinkedIn URL */}
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-[10px] uppercase tracking-wider text-gray-500">
                Company LinkedIn
              </span>
              <button
                onClick={() => findLinkedinMutation.mutate()}
                disabled={findLinkedinMutation.isPending}
                className="text-[10px] text-blue-400 hover:text-blue-300 disabled:opacity-50"
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
              className="w-full bg-gray-950 border border-gray-800 rounded px-2 py-1 text-xs text-gray-200 placeholder:text-gray-700 focus:outline-none focus:border-gray-600"
              data-testid="linkedin-input"
            />
            {findLinkedinMutation.data?.source && (
              <p className="text-[10px] text-gray-500">
                Updated via {findLinkedinMutation.data.source.replace("_", " ")}.
              </p>
            )}
            {findLinkedinMutation.data && !findLinkedinMutation.data.company_linkedin_url && (
              <p className="text-[10px] text-yellow-500">
                Auto-discovery found nothing. Paste a URL above to set manually.
              </p>
            )}
          </div>

          {/* Pitch deck status */}
          <div className="flex items-center justify-between text-xs">
            <span className="text-[10px] uppercase tracking-wider text-gray-500">
              Pitch deck
            </span>
            {lead.pitch_deck_filename ? (
              <span className="text-gray-300 flex items-center gap-2">
                <button
                  className="text-blue-400 hover:text-blue-300 hover:underline text-xs"
                  title={lead.pitch_deck_filename}
                  onClick={async () => {
                    const res = await client.get(`/leads/${lead.id}/pitch-deck`, { responseType: "blob" });
                    const url = URL.createObjectURL(res.data);
                    window.open(url, "_blank");
                    setTimeout(() => URL.revokeObjectURL(url), 60_000);
                  }}
                >
                  View PDF ↗
                </button>
                {lead.pitch_deck_ingested_at && (
                  <span className="text-gray-600">
                    · {new Date(lead.pitch_deck_ingested_at).toLocaleDateString("en-GB")}
                  </span>
                )}
              </span>
            ) : (
              <span className="text-gray-600">not yet ingested</span>
            )}
          </div>

          {/* Scoring breakdown (existing) */}
          {assessment?.scoring_breakdown && (
            <div className="space-y-1.5">
              {Object.entries(assessment.scoring_breakdown).map(([key, val]) => (
                <div key={key} className="flex justify-between text-xs">
                  <span className="text-gray-500 capitalize">{key.replace(/_/g, " ")}</span>
                  <span className="text-gray-300">{val.score}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

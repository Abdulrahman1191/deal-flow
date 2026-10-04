import { useMemo, useState } from "react";
import axios from "axios";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchTeam } from "../api/users";
import {
  executeBulkReassign,
  previewBulkReassign,
  type BulkReassignResult,
  type BulkReassignPreviewResult,
  type ReassignBucket,
} from "../api/reassign";
import Modal from "../components/shared/Modal";
import { useToast } from "../components/shared/Toast";

const BUCKETS: { value: ReassignBucket | ""; label: string }[] = [
  { value: "", label: "Any bucket" },
  { value: "YES", label: "YES" },
  { value: "MAYBE", label: "MAYBE" },
  { value: "REJECT", label: "REJECT" },
];

/** Email local-part, capitalized, for plain-language sentences ("Yomna's leads"). */
function displayName(email: string): string {
  const local = email.split("@")[0] ?? email;
  return local.charAt(0).toUpperCase() + local.slice(1);
}

function formatList(names: string[]): string {
  if (names.length === 0) return "";
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-background border border-border rounded-xl p-3">
      <p className="text-xs text-muted-foreground mb-1">{label}</p>
      <p className="text-lg font-semibold text-foreground">{value}</p>
    </div>
  );
}

function sameOwners(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const sortedA = [...a].sort();
  const sortedB = [...b].sort();
  return sortedA.every((e, i) => e === sortedB[i]);
}

function BreakdownList({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).filter(([, n]) => n > 0);
  if (entries.length === 0) return <p className="text-xs text-muted-foreground">None</p>;
  return (
    <ul className="space-y-1">
      {entries.map(([key, n]) => (
        <li key={key} className="flex items-center justify-between text-sm">
          <span className="text-foreground">{key}</span>
          <span className="text-muted-foreground">{n}</span>
        </li>
      ))}
    </ul>
  );
}

export default function ReassignLeadsPage() {
  const qc = useQueryClient();
  const toast = useToast();

  const { data: team = [], isLoading, isError, error } = useQuery({
    queryKey: ["team"],
    queryFn: fetchTeam,
    staleTime: 5 * 60 * 1000,
  });

  const [fromOwner, setFromOwner] = useState("");
  const [toOwners, setToOwners] = useState<string[]>([]);
  const [bucket, setBucket] = useState<ReassignBucket | "">("");
  const [includeConverted, setIncludeConverted] = useState(false);
  const [preview, setPreview] = useState<BulkReassignPreviewResult | null>(null);
  const [result, setResult] = useState<BulkReassignResult | null>(null);
  const [showConfirm, setShowConfirm] = useState(false);

  const targets = team.filter((email) => email !== fromOwner);

  const updateFromOwner = (email: string) => {
    setFromOwner(email);
    setToOwners((prev) => prev.filter((e) => e !== email));
    setPreview(null);
    setResult(null);
  };

  const toggleToOwner = (email: string) => {
    setToOwners((prev) => (prev.includes(email) ? prev.filter((e) => e !== email) : [...prev, email]));
    setPreview(null);
    setResult(null);
  };

  const updateBucket = (value: ReassignBucket | "") => {
    setBucket(value);
    setPreview(null);
    setResult(null);
  };

  const updateIncludeConverted = (value: boolean) => {
    setIncludeConverted(value);
    setPreview(null);
    setResult(null);
  };

  type PreviewVars = {
    from_owner: string;
    to_owners: string[];
    bucket: ReassignBucket | "";
    include_converted: boolean;
  };

  const previewMutation = useMutation({
    mutationFn: (vars: PreviewVars) =>
      previewBulkReassign({
        from_owner: vars.from_owner,
        to_owners: vars.to_owners,
        bucket: vars.bucket || undefined,
        include_converted: vars.include_converted,
      }),
    onSuccess: (data, vars) => {
      const stale =
        vars.from_owner !== fromOwner ||
        vars.bucket !== bucket ||
        vars.include_converted !== includeConverted ||
        !sameOwners(vars.to_owners, toOwners);
      if (stale) return;
      setPreview(data);
    },
    onError: () => toast("Couldn't load the preview — please try again."),
  });

  const confirmMutation = useMutation({
    mutationFn: () =>
      executeBulkReassign({
        from_owner: fromOwner,
        to_owners: toOwners,
        bucket: bucket || undefined,
        include_converted: includeConverted,
        confirm_count: preview!.count,
      }),
    onSuccess: (data) => {
      setResult(data);
      setPreview(null);
      setShowConfirm(false);
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["archive"] });
      qc.invalidateQueries({ queryKey: ["associates-performance"] });
    },
    onError: (err: unknown) => {
      setShowConfirm(false);
      if (axios.isAxiosError(err) && err.response?.status === 409) {
        setPreview(null);
        toast("The board changed since your preview — please re-run the preview.");
      } else {
        toast("Couldn't complete the reassignment — please try again.");
      }
    },
  });

  const targetNames = useMemo(() => formatList(toOwners.map(displayName)), [toOwners]);
  const confirmSentence = preview
    ? `Move ${preview.count} of ${displayName(fromOwner)}'s leads to ${targetNames}?`
    : "";

  const canPreview = !!fromOwner && toOwners.length > 0 && !previewMutation.isPending;

  if (isLoading) return <p className="p-4 sm:p-6 text-sm text-muted-foreground">Loading team…</p>;
  if (isError) {
    const status = (error as { response?: { status?: number } })?.response?.status;
    if (status === 403) {
      return <p className="p-4 sm:p-6 text-sm text-muted-foreground">Reassign leads is admin-only.</p>;
    }
    return <p className="p-4 sm:p-6 text-sm text-error">Failed to load the team list.</p>;
  }

  return (
    <div className="p-4 sm:p-6 max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">Reassign leads</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Move an associate's pipeline to one or more colleagues — for example, while they're out of
          office. Preview the split before anything moves.
        </p>
      </div>

      <div className="bg-card border border-border rounded-xl p-4 sm:p-5 space-y-4">
        <div className="grid sm:grid-cols-2 gap-4">
          <label className="block">
            <span className="text-xs font-medium text-muted-foreground">From</span>
            <select
              value={fromOwner}
              onChange={(e) => updateFromOwner(e.target.value)}
              className="mt-1 w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-ring"
            >
              <option value="">Select an associate…</option>
              {team.map((email) => (
                <option key={email} value={email}>
                  {email}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-xs font-medium text-muted-foreground">Bucket filter</span>
            <select
              value={bucket}
              onChange={(e) => updateBucket(e.target.value as ReassignBucket | "")}
              className="mt-1 w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-ring"
            >
              {BUCKETS.map((b) => (
                <option key={b.value} value={b.value}>
                  {b.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div>
          <span className="text-xs font-medium text-muted-foreground">To (select one or more)</span>
          {targets.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">
              Choose a "From" associate first — their colleagues will show up here.
            </p>
          ) : (
            <div className="mt-2 flex flex-wrap gap-2">
              {targets.map((email) => (
                <label
                  key={email}
                  className={`flex items-center gap-2 text-sm px-3 py-1.5 rounded-lg border cursor-pointer select-none transition-colors ${
                    toOwners.includes(email)
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-border bg-background text-muted-foreground hover:text-foreground"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={toOwners.includes(email)}
                    onChange={() => toggleToOwner(email)}
                    className="h-3.5 w-3.5 rounded border-border accent-primary"
                  />
                  {email}
                </label>
              ))}
            </div>
          )}
        </div>

        <label className="flex items-start gap-2 text-sm cursor-pointer select-none">
          <input
            type="checkbox"
            checked={includeConverted}
            onChange={(e) => updateIncludeConverted(e.target.checked)}
            className="h-3.5 w-3.5 mt-0.5 rounded border-border accent-primary"
          />
          <span className="text-foreground">
            Also include converted leads and leads whose outreach was already sent
            <span className="block text-xs text-muted-foreground mt-0.5">
              Off by default — these already belong to someone. Only check this if that relationship
              should move too.
            </span>
          </span>
        </label>

        <div className="flex items-center gap-3 pt-1">
          <button
            onClick={() =>
              previewMutation.mutate({ from_owner: fromOwner, to_owners: toOwners, bucket, include_converted: includeConverted })
            }
            disabled={!canPreview}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-muted hover:bg-border text-foreground transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {previewMutation.isPending ? "Previewing…" : "Preview"}
          </button>
          <button
            onClick={() => setShowConfirm(true)}
            disabled={!preview || preview.count === 0}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Confirm reassignment
          </button>
        </div>
      </div>

      {preview && (
        <section className="bg-card border border-border rounded-xl p-4 sm:p-5 space-y-4">
          <h2 className="text-sm font-semibold text-foreground">Preview</h2>
          {preview.count === 0 ? (
            <div className="border border-dashed border-border rounded-xl p-6 text-center">
              <p className="text-sm text-muted-foreground">
                No leads match these filters — nothing would move.
              </p>
            </div>
          ) : (
            <>
              <StatCard label="Total leads to move" value={String(preview.count)} />
              <div className="grid sm:grid-cols-3 gap-4">
                <div>
                  <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                    By status
                  </h3>
                  <BreakdownList counts={preview.by_status} />
                </div>
                <div>
                  <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                    By bucket
                  </h3>
                  <BreakdownList counts={preview.by_bucket} />
                </div>
                <div>
                  <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                    By target
                  </h3>
                  <BreakdownList counts={preview.by_target} />
                </div>
              </div>
            </>
          )}
        </section>
      )}

      {result && (
        <section className="bg-card border border-border rounded-xl p-4 sm:p-5 space-y-4">
          <h2 className="text-sm font-semibold text-foreground">Result</h2>
          <StatCard label="Moved" value={String(result.moved)} />
          <div>
            <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
              Split applied
            </h3>
            <BreakdownList counts={result.by_target} />
          </div>
          {result.failed.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold text-error uppercase tracking-wider mb-2">
                Failed ({result.failed.length})
              </h3>
              <ul className="space-y-1.5">
                {result.failed.map((f) => (
                  <li key={f.lead_id} className="text-sm bg-error/10 text-error rounded-lg px-3 py-2">
                    <span className="font-mono text-xs">{f.lead_id}</span> — {f.error}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="text-xs text-muted-foreground">
            The change is pushed to Copper in the background — the boards and the Associates table
            will reflect it within about five minutes.
          </p>
        </section>
      )}

      {showConfirm && preview && (
        <Modal title="Confirm reassignment" onClose={() => setShowConfirm(false)}>
          <div className="space-y-4">
            <p className="text-sm text-foreground leading-relaxed">{confirmSentence}</p>
            <p className="text-xs text-muted-foreground">
              This writes immediately and pushes each change to Copper. There's no undo — reversing
              it means running another reassignment in the other direction.
            </p>
            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => setShowConfirm(false)}
                className="px-4 py-2 text-sm font-medium rounded-lg text-muted-foreground hover:text-foreground transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => confirmMutation.mutate()}
                disabled={confirmMutation.isPending}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {confirmMutation.isPending ? "Moving…" : `Move ${preview.count} leads`}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

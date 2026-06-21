import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createCompany } from "../../api/portfolio";
import type { Decision, OutcomeStatus, PortfolioVocab } from "../../types/portfolio";

interface Props {
  vocab: PortfolioVocab;
  onClose: () => void;
}

const DEFAULT_DECISION: Decision = "FUNDED";
const DEFAULT_STATUS: OutcomeStatus = "too_early";

export default function AddCompanyModal({ vocab, onClose }: Props) {
  const qc = useQueryClient();

  const [name, setName] = useState("");
  const [sector, setSector] = useState("");
  const [region, setRegion] = useState("");
  const [website, setWebsite] = useState("");
  const [foundersCsv, setFoundersCsv] = useState("");
  const [description, setDescription] = useState("");
  const [decision, setDecision] = useState<Decision>(DEFAULT_DECISION);
  const [initialStatus, setInitialStatus] = useState<OutcomeStatus>(DEFAULT_STATUS);
  const [decisionAt, setDecisionAt] = useState("");
  const [rationale, setRationale] = useState("");
  const [investedAmount, setInvestedAmount] = useState("");

  const mut = useMutation({
    mutationFn: () =>
      createCompany({
        name: name.trim(),
        sector: sector.trim() || null,
        region: region.trim() || null,
        website: website.trim() || null,
        founder_names:
          foundersCsv
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean) || null,
        description: description.trim() || null,
        our_decision: decision,
        decision_at: decisionAt || null,
        decision_rationale: rationale.trim() || null,
        invested_amount_usd: investedAmount ? Number(investedAmount) : null,
        initial_status: initialStatus,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["portfolio-companies"] });
      qc.invalidateQueries({ queryKey: ["portfolio-stats"] });
      onClose();
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/50 backdrop-blur-sm p-4">
      <div className="bg-card border border-border rounded-2xl w-full max-w-2xl shadow-2xl flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <p className="text-foreground font-semibold">Add portfolio company</p>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          {/* Name */}
          <Field label="Name *">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="input"
              placeholder="e.g. Lean Technologies"
            />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Sector">
              <input
                value={sector}
                onChange={(e) => setSector(e.target.value)}
                className="input"
                placeholder="fintech, deeptech, etc."
              />
            </Field>
            <Field label="Region">
              <input
                value={region}
                onChange={(e) => setRegion(e.target.value)}
                className="input"
                placeholder="Saudi Arabia, UAE…"
              />
            </Field>
          </div>

          <Field label="Website">
            <input
              value={website}
              onChange={(e) => setWebsite(e.target.value)}
              className="input"
              placeholder="leantech.me"
            />
          </Field>

          <Field label="Founders (comma-separated)">
            <input
              value={foundersCsv}
              onChange={(e) => setFoundersCsv(e.target.value)}
              className="input"
              placeholder="Hisham Al-Falih, Aditya Sarkar"
            />
          </Field>

          <Field label="Description / thesis at investment time">
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="input resize-none"
            />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Our decision">
              <select
                value={decision}
                onChange={(e) => setDecision(e.target.value as Decision)}
                className="input"
              >
                {vocab.decisions.map((d) => (
                  <option key={d} value={d}>
                    {d.replace("_", " ")}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Decision date">
              <input
                type="date"
                value={decisionAt}
                onChange={(e) => setDecisionAt(e.target.value)}
                className="input"
              />
            </Field>
          </div>

          <Field label="Decision rationale (why we did/didn't fund)">
            <textarea
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              rows={3}
              className="input resize-none"
              placeholder="What made this attractive — or why we passed"
            />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Current status">
              <select
                value={initialStatus}
                onChange={(e) => setInitialStatus(e.target.value as OutcomeStatus)}
                className="input"
              >
                {vocab.outcomes.map((o) => (
                  <option key={o} value={o}>
                    {o.replace("_", " ")}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Invested amount (USD)">
              <input
                type="number"
                value={investedAmount}
                onChange={(e) => setInvestedAmount(e.target.value)}
                className="input"
                placeholder="optional"
              />
            </Field>
          </div>

          {mut.isError && (
            <p className="text-xs text-error">Failed — check your inputs.</p>
          )}
        </div>

        <div className="flex items-center justify-between px-5 py-4 border-t border-border">
          <button onClick={onClose} className="text-xs text-muted-foreground hover:text-foreground">
            Cancel
          </button>
          <button
            onClick={() => mut.mutate()}
            disabled={!name.trim() || mut.isPending}
            className="px-5 py-2 text-sm rounded-lg bg-primary hover:bg-primary/90 text-white disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {mut.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
      <style>{`
        .input {
          width: 100%;
          background: #030712;
          border: 1px solid #1f2937;
          border-radius: 0.5rem;
          padding: 0.5rem 0.75rem;
          font-size: 0.875rem;
          color: #f3f4f6;
        }
        .input:focus { outline: none; border-color: #4b5563; }
      `}</style>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-[10px] uppercase tracking-wider text-muted-foreground block mb-1">
        {label}
      </label>
      {children}
    </div>
  );
}

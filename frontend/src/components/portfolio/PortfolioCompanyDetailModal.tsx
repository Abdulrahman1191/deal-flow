import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addOutcome,
  addSignal,
  deleteCompany,
  deleteSignal,
  fetchCompany,
} from "../../api/portfolio";
import type {
  Direction,
  OutcomeStatus,
  PortfolioSignal,
  PortfolioVocab,
} from "../../types/portfolio";

interface Props {
  companyId: string;
  vocab: PortfolioVocab;
  onClose: () => void;
}

export default function PortfolioCompanyDetailModal({ companyId, vocab, onClose }: Props) {
  const qc = useQueryClient();
  const { data: company, isLoading } = useQuery({
    queryKey: ["portfolio-company", companyId],
    queryFn: () => fetchCompany(companyId),
  });

  const [showSignalForm, setShowSignalForm] = useState(false);
  const [showOutcomeForm, setShowOutcomeForm] = useState(false);

  const refetchAll = () => {
    qc.invalidateQueries({ queryKey: ["portfolio-company", companyId] });
    qc.invalidateQueries({ queryKey: ["portfolio-companies"] });
    qc.invalidateQueries({ queryKey: ["portfolio-stats"] });
  };

  const deleteMut = useMutation({
    mutationFn: () => deleteCompany(companyId),
    onSuccess: () => {
      refetchAll();
      onClose();
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/50 backdrop-blur-sm p-4">
      <div className="bg-card border border-border rounded-2xl w-full max-w-3xl shadow-2xl flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <div>
            <p className="text-foreground font-semibold">{company?.name ?? "…"}</p>
            <p className="text-xs text-muted-foreground">
              {company ? `${company.our_decision.replace("_", " ")} · ${company.current_status.replace("_", " ")}` : ""}
            </p>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground text-lg">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-6">
          {isLoading || !company ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-4 text-sm">
                <Field label="Sector">{company.sector ?? "—"}</Field>
                <Field label="Region">{company.region ?? "—"}</Field>
                <Field label="Founders">{company.founder_names?.join(", ") ?? "—"}</Field>
                <Field label="Website">
                  {company.website ? (
                    <a
                      href={company.website.startsWith("http") ? company.website : `https://${company.website}`}
                      target="_blank"
                      rel="noreferrer"
                      className="text-info hover:underline"
                    >
                      {company.website} ↗
                    </a>
                  ) : (
                    "—"
                  )}
                </Field>
                <Field label="Decision date">{company.decision_at ?? "—"}</Field>
                <Field label="Invested (USD)">
                  {company.invested_amount_usd
                    ? `$${company.invested_amount_usd.toLocaleString()}`
                    : "—"}
                </Field>
              </div>

              {company.description && (
                <Field label="Description">
                  <p className="text-sm text-foreground leading-relaxed">{company.description}</p>
                </Field>
              )}
              {company.decision_rationale && (
                <Field label="Decision rationale">
                  <p className="text-sm text-foreground leading-relaxed">{company.decision_rationale}</p>
                </Field>
              )}

              {/* Signals */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    Signals ({company.signals.length})
                  </p>
                  <button
                    onClick={() => setShowSignalForm(true)}
                    className="text-xs text-info hover:text-info"
                  >
                    + Add signal
                  </button>
                </div>
                {company.signals.length === 0 ? (
                  <p className="text-xs text-muted-foreground">No signals yet. Add a positive or negative observation about this company.</p>
                ) : (
                  <ul className="space-y-1">
                    {company.signals.map((s) => (
                      <SignalRow key={s.id} signal={s} companyId={companyId} onChange={refetchAll} />
                    ))}
                  </ul>
                )}
                {showSignalForm && (
                  <SignalForm
                    vocab={vocab}
                    companyId={companyId}
                    onClose={() => setShowSignalForm(false)}
                    onSaved={() => {
                      setShowSignalForm(false);
                      refetchAll();
                    }}
                  />
                )}
              </div>

              {/* Outcomes */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    Outcome history ({company.outcomes.length})
                  </p>
                  <button
                    onClick={() => setShowOutcomeForm(true)}
                    className="text-xs text-info hover:text-info"
                  >
                    + Update status
                  </button>
                </div>
                <ul className="space-y-1">
                  {company.outcomes.map((o) => (
                    <li key={o.id} className="text-xs text-muted-foreground flex items-center gap-2">
                      <span className="text-muted-foreground w-32 shrink-0">
                        {new Date(o.recorded_at).toLocaleDateString("en-GB")}
                      </span>
                      <span className="text-foreground">{o.status.replace("_", " ")}</span>
                      {o.notes && <span className="text-muted-foreground">— {o.notes}</span>}
                    </li>
                  ))}
                </ul>
                {showOutcomeForm && (
                  <OutcomeForm
                    vocab={vocab}
                    companyId={companyId}
                    onClose={() => setShowOutcomeForm(false)}
                    onSaved={() => {
                      setShowOutcomeForm(false);
                      refetchAll();
                    }}
                  />
                )}
              </div>
            </>
          )}
        </div>

        <div className="flex items-center justify-between px-5 py-4 border-t border-border">
          <button
            onClick={() => {
              if (confirm(`Delete ${company?.name}? This cannot be undone.`)) {
                deleteMut.mutate();
              }
            }}
            className="text-xs text-error hover:text-error"
          >
            Delete company
          </button>
          <button onClick={onClose} className="px-4 py-2 text-sm rounded-lg bg-muted hover:bg-border text-foreground">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">{label}</p>
      <div className="text-sm text-foreground">{children}</div>
    </div>
  );
}

function SignalRow({
  signal,
  companyId,
  onChange,
}: {
  signal: PortfolioSignal;
  companyId: string;
  onChange: () => void;
}) {
  const del = useMutation({
    mutationFn: () => deleteSignal(companyId, signal.id),
    onSuccess: onChange,
  });
  const tone = signal.direction === "POSITIVE" ? "text-success" : "text-error";
  return (
    <li className="flex items-center gap-2 text-xs py-1 group">
      <span className={`${tone} w-3 text-center font-semibold`}>
        {signal.direction === "POSITIVE" ? "+" : "−"}
      </span>
      <span className="text-foreground w-48 truncate">
        {signal.signal_type.replace(/_/g, " ")}
      </span>
      <span className="text-muted-foreground">weight {signal.weight}/5</span>
      {signal.note && <span className="text-muted-foreground truncate flex-1">— {signal.note}</span>}
      <button
        onClick={() => del.mutate()}
        className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-error transition-opacity"
      >
        ✕
      </button>
    </li>
  );
}

function SignalForm({
  vocab,
  companyId,
  onClose,
  onSaved,
}: {
  vocab: PortfolioVocab;
  companyId: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [type, setType] = useState(vocab.signal_types[0] ?? "founder_execution");
  const [direction, setDirection] = useState<Direction>("POSITIVE");
  const [weight, setWeight] = useState(3);
  const [note, setNote] = useState("");

  const mut = useMutation({
    mutationFn: () =>
      addSignal(companyId, {
        signal_type: type,
        direction,
        weight,
        note: note.trim() || null,
      }),
    onSuccess: onSaved,
  });

  return (
    <div className="mt-3 bg-background border border-border rounded-lg p-3 space-y-2">
      <div className="grid grid-cols-3 gap-2">
        <select value={type} onChange={(e) => setType(e.target.value)} className="bg-card border border-border rounded px-2 py-1 text-xs text-foreground">
          {vocab.signal_types.map((t) => (
            <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
          ))}
        </select>
        <select value={direction} onChange={(e) => setDirection(e.target.value as Direction)} className="bg-card border border-border rounded px-2 py-1 text-xs text-foreground">
          <option value="POSITIVE">+ Positive</option>
          <option value="NEGATIVE">− Negative</option>
        </select>
        <select value={weight} onChange={(e) => setWeight(Number(e.target.value))} className="bg-card border border-border rounded px-2 py-1 text-xs text-foreground">
          {[1, 2, 3, 4, 5].map((w) => (
            <option key={w} value={w}>weight {w}</option>
          ))}
        </select>
      </div>
      <input
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="optional note (e.g. specific event that made this signal evident)"
        className="w-full bg-card border border-border rounded px-2 py-1 text-xs text-foreground"
      />
      <div className="flex justify-end gap-2">
        <button onClick={onClose} className="text-xs text-muted-foreground hover:text-foreground">Cancel</button>
        <button onClick={() => mut.mutate()} disabled={mut.isPending} className="text-xs px-3 py-1 rounded bg-primary hover:bg-primary/90 text-white disabled:opacity-50">Save signal</button>
      </div>
    </div>
  );
}

function OutcomeForm({
  vocab,
  companyId,
  onClose,
  onSaved,
}: {
  vocab: PortfolioVocab;
  companyId: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [status, setStatus] = useState<OutcomeStatus>("growing");
  const [notes, setNotes] = useState("");

  const mut = useMutation({
    mutationFn: () =>
      addOutcome(companyId, {
        status,
        notes: notes.trim() || null,
      }),
    onSuccess: onSaved,
  });

  return (
    <div className="mt-3 bg-background border border-border rounded-lg p-3 space-y-2">
      <select value={status} onChange={(e) => setStatus(e.target.value as OutcomeStatus)} className="bg-card border border-border rounded px-2 py-1 text-xs text-foreground">
        {vocab.outcomes.map((o) => (
          <option key={o} value={o}>{o.replace(/_/g, " ")}</option>
        ))}
      </select>
      <input
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="optional note (what happened that changed the status)"
        className="w-full bg-card border border-border rounded px-2 py-1 text-xs text-foreground"
      />
      <div className="flex justify-end gap-2">
        <button onClick={onClose} className="text-xs text-muted-foreground hover:text-foreground">Cancel</button>
        <button onClick={() => mut.mutate()} disabled={mut.isPending} className="text-xs px-3 py-1 rounded bg-primary hover:bg-primary/90 text-white disabled:opacity-50">Save status</button>
      </div>
    </div>
  );
}
